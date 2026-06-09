"""
Backtest engine — จำลอง strategy บนข้อมูลย้อนหลัง

หลักการ:
- add_indicators ทั้ง df ครั้งเดียว (indicators ทุกตัวเป็น causal = ใช้แค่อดีต ไม่ lookahead)
- loop ทีละ candle: ถ้าไม่มี position → ดู signal; ถ้ามี position → เช็ค SL/TP hit
- entry = close ของ candle ที่เกิด signal, exit เริ่มเช็คจาก candle ถัดไป
- reuse generate_signal() เดิม 100%
"""
import argparse
import pandas as pd
from data.fetcher import fetch_ohlcv, fetch_htf_ohlcv
from analysis.indicators import add_indicators
from analysis.signals import generate_signal
from strategy.params import DEFAULT_PARAMS, StrategyParams
from backtest.metrics import compute_metrics, BacktestResult

FEE = 0.0004      # 0.04% taker (Binance futures) — คิด 2 ครั้ง (เข้า+ออก)
WARMUP = 250      # ข้าม candle แรกที่ indicator ยัง NaN (VIDYA ใช้ ATR200)


def _check_exit(pos: dict, high: float, low: float):
    """คืน (exit_price, reason) ถ้าโดน SL/TP ใน candle นี้ — ถ้าโดนทั้งคู่ สมมติ SL ก่อน (conservative)"""
    if pos["side"] == "LONG":
        if low <= pos["sl"]:
            return pos["sl"], "SL"
        if high >= pos["tp"]:
            return pos["tp"], "TP"
    else:  # SHORT
        if high >= pos["sl"]:
            return pos["sl"], "SL"
        if low <= pos["tp"]:
            return pos["tp"], "TP"
    return None


def _recompute_sltp(pos: dict):
    """คำนวณ SL/TP ใหม่จาก avg entry (ใช้ระยะ sl_dist/tp_dist ของ signal ล่าสุด)"""
    if pos["side"] == "LONG":
        pos["sl"] = pos["avg_entry"] - pos["sl_dist"]
        pos["tp"] = pos["avg_entry"] + pos["tp_dist"]
    else:
        pos["sl"] = pos["avg_entry"] + pos["sl_dist"]
        pos["tp"] = pos["avg_entry"] - pos["tp_dist"]


def _open_pos(sig: dict, ts) -> dict:
    return {
        "side": sig["signal"], "entries": [sig["price"]], "avg_entry": sig["price"],
        "n_levels": 1,
        "sl": sig["sl"], "tp": sig["tp"],
        "sl_dist": abs(sig["price"] - sig["sl"]), "tp_dist": abs(sig["tp"] - sig["price"]),
        "entry_time": ts, "mfe_price": sig["price"], "mae_price": sig["price"],
    }


def _add_pyramid(pos: dict, sig: dict):
    """เพิ่มไม้ทางเดียวกัน → เฉลี่ย entry + recompute SL/TP จาก avg (size เท่ากันต่อ level)"""
    pos["entries"].append(sig["price"])
    pos["avg_entry"] = sum(pos["entries"]) / len(pos["entries"])
    pos["n_levels"] += 1
    pos["sl_dist"] = abs(sig["price"] - sig["sl"])
    pos["tp_dist"] = abs(sig["tp"] - sig["price"])
    _recompute_sltp(pos)


def _record(trades: list, pos: dict, exit_price: float, exit_reason: str, ts, fee: float):
    avg = pos["avg_entry"]
    n = pos["n_levels"]
    if pos["side"] == "LONG":
        pnl = (exit_price - avg) / avg
        mfe_dist = pos["mfe_price"] - avg
        mae_dist = avg - pos["mae_price"]
    else:
        pnl = (avg - exit_price) / avg
        mfe_dist = avg - pos["mfe_price"]
        mae_dist = pos["mae_price"] - avg
    pnl -= fee * (n + 1)   # fee ต่อ entry (n ไม้) + exit (1)
    trades.append({
        "entry_time": str(pos["entry_time"]), "exit_time": str(ts),
        "side": pos["side"], "entry": round(avg, 2), "levels": n,
        "exit": round(exit_price, 2), "sl": round(pos["sl"], 2),
        "tp": round(pos["tp"], 2), "pnl_pct": round(pnl, 4),
        "result": "win" if pnl > 0 else "loss", "exit_reason": exit_reason,
        "mfe_pct_of_tp": round(mfe_dist / pos["tp_dist"], 3) if pos["tp_dist"] > 0 else 0.0,
        "mae_pct_of_sl": round(mae_dist / pos["sl_dist"], 3) if pos["sl_dist"] > 0 else 0.0,
    })


def simulate(df: pd.DataFrame, params=DEFAULT_PARAMS, fee: float = FEE,
             start: int = WARMUP, end: int = None) -> BacktestResult:
    """
    df = ต้อง add_indicators แล้ว. backtest ช่วง index [start, end)
    Position management: SL/TP (priority) → reverse (signal ตรงข้าม) →
    pyramid (signal เดิมทาง + n_levels < max_pyramid → เฉลี่ย entry)
    """
    trades = []
    pos = None
    end = len(df) if end is None else end

    for i in range(start, end):
        row = df.iloc[i]
        ts = df.index[i]
        sig = generate_signal(df.iloc[:i + 1], params)   # เรียกครั้งเดียว/candle

        if pos is not None:
            # update MFE/MAE (เก็บราคา high/low สุด — คำนวณ pct ตอน exit จาก avg)
            pos["mfe_price"] = (max(pos["mfe_price"], row["high"]) if pos["side"] == "LONG"
                                else min(pos["mfe_price"], row["low"]))
            pos["mae_price"] = (min(pos["mae_price"], row["low"]) if pos["side"] == "LONG"
                                else max(pos["mae_price"], row["high"]))

            hit = _check_exit(pos, row["high"], row["low"])
            if hit:
                exit_price, reason = hit           # 1) SL/TP priority
                _record(trades, pos, exit_price, reason, ts, fee)
                pos = None
            elif sig and sig["signal"] != pos["side"]:
                if params.reverse:                 # 2) signal ตรงข้าม → reverse
                    _record(trades, pos, row["close"], "reverse", ts, fee)
                    pos = _open_pos(sig, ts)
            elif sig and sig["signal"] == pos["side"] and pos["n_levels"] < params.max_pyramid:
                _add_pyramid(pos, sig)             # 3) signal เดิมทาง → pyramid

        if pos is None and sig:
            pos = _open_pos(sig, ts)

    return compute_metrics(trades, params.to_dict())


def run_backtest(df: pd.DataFrame, params=DEFAULT_PARAMS, df_htf: pd.DataFrame = None,
                 fee: float = FEE, warmup: int = WARMUP) -> BacktestResult:
    """add_indicators + backtest ทั้ง df (ใช้ตอนรัน backtest เดี่ยว)"""
    df = add_indicators(df, df_htf, params)
    return simulate(df, params, fee, warmup, len(df))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--timeframe", default=DEFAULT_PARAMS.timeframe)
    ap.add_argument("--limit", type=int, default=1500)
    args = ap.parse_args()

    print(f"Fetching {args.symbol} {args.timeframe} x{args.limit} ...")
    df = fetch_ohlcv(symbol=args.symbol, timeframe=args.timeframe, limit=args.limit)
    df_htf = fetch_htf_ohlcv(symbol=args.symbol, timeframe=args.timeframe, limit=args.limit)

    print("Running backtest (default params) ...")
    result = run_backtest(df, DEFAULT_PARAMS, df_htf)
    print(result.summary())
    print(f"\nFirst 3 trades:")
    for t in result.trades[:3]:
        print(f"  {t['side']} {t['entry_time']} entry={t['entry']} exit={t['exit']} {t['result']} {t['pnl_pct']*100:+.2f}%")


if __name__ == "__main__":
    main()
