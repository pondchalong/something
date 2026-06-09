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


def _open_pos(sig: dict, ts) -> dict:
    return {
        "side": sig["signal"], "entry": sig["price"],
        "sl": sig["sl"], "tp": sig["tp"], "entry_time": ts,
        "mfe": 0.0, "mae": 0.0,
    }


def _record(trades: list, pos: dict, exit_price: float, exit_reason: str, ts, fee: float):
    if pos["side"] == "LONG":
        pnl = (exit_price - pos["entry"]) / pos["entry"] - 2 * fee
    else:
        pnl = (pos["entry"] - exit_price) / pos["entry"] - 2 * fee
    tp_dist = abs(pos["tp"] - pos["entry"])
    sl_dist = abs(pos["sl"] - pos["entry"])
    trades.append({
        "entry_time": str(pos["entry_time"]), "exit_time": str(ts),
        "side": pos["side"], "entry": round(pos["entry"], 2),
        "exit": round(exit_price, 2), "sl": round(pos["sl"], 2),
        "tp": round(pos["tp"], 2), "pnl_pct": round(pnl, 4),
        "result": "win" if pnl > 0 else "loss", "exit_reason": exit_reason,
        "mfe_pct_of_tp": round(pos["mfe"] / tp_dist, 3) if tp_dist > 0 else 0.0,
        "mae_pct_of_sl": round(pos["mae"] / sl_dist, 3) if sl_dist > 0 else 0.0,
    })


def simulate(df: pd.DataFrame, params=DEFAULT_PARAMS, fee: float = FEE,
             start: int = WARMUP, end: int = None) -> BacktestResult:
    """
    df = ต้อง add_indicators แล้ว. backtest ช่วง index [start, end)
    แยกจาก add_indicators เพื่อให้ optimizer คำนวณ indicator ครั้งเดียวบน full df
    แล้ว backtest train/test portion โดย test ได้ indicator ที่ warm จาก train
    """
    trades = []
    pos = None
    end = len(df) if end is None else end

    for i in range(start, end):
        row = df.iloc[i]
        ts = df.index[i]
        sig = generate_signal(df.iloc[:i + 1], params)   # เรียกครั้งเดียว/candle

        # 1) มี position → อัปเดต MFE/MAE แล้วเช็ค exit (SL/TP ก่อน, แล้วค่อย reverse)
        if pos is not None:
            if pos["side"] == "LONG":
                pos["mfe"] = max(pos["mfe"], row["high"] - pos["entry"])
                pos["mae"] = max(pos["mae"], pos["entry"] - row["low"])
            else:
                pos["mfe"] = max(pos["mfe"], pos["entry"] - row["low"])
                pos["mae"] = max(pos["mae"], row["high"] - pos["entry"])

            hit = _check_exit(pos, row["high"], row["low"])
            if hit:
                # SL/TP มาก่อน (priority)
                exit_price, reason = hit
                _record(trades, pos, exit_price, reason, ts, fee)
                pos = None
            elif params.reverse and sig and sig["signal"] != pos["side"]:
                # ยังไม่โดน SL/TP + signal กลับข้าง → ปิดที่ close แล้วเปิดใหม่ทันที
                _record(trades, pos, row["close"], "reverse", ts, fee)
                pos = _open_pos(sig, ts)

        # 2) ไม่มี position + มี signal → เปิดใหม่
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
