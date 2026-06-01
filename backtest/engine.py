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
    """คืน (exit_price, result) ถ้าโดน SL/TP ใน candle นี้ — ถ้าโดนทั้งคู่ สมมติ SL ก่อน (conservative)"""
    if pos["side"] == "LONG":
        if low <= pos["sl"]:
            return pos["sl"], "loss"
        if high >= pos["tp"]:
            return pos["tp"], "win"
    else:  # SHORT
        if high >= pos["sl"]:
            return pos["sl"], "loss"
        if low <= pos["tp"]:
            return pos["tp"], "win"
    return None


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

        # 1) มี position → เช็ค exit ด้วย high/low ของ candle นี้
        if pos is not None:
            hit = _check_exit(pos, row["high"], row["low"])
            if hit:
                exit_price, result = hit
                if pos["side"] == "LONG":
                    pnl = (exit_price - pos["entry"]) / pos["entry"] - 2 * fee
                else:
                    pnl = (pos["entry"] - exit_price) / pos["entry"] - 2 * fee
                trades.append({
                    "entry_time": str(pos["entry_time"]), "exit_time": str(ts),
                    "side": pos["side"], "entry": round(pos["entry"], 2),
                    "exit": round(exit_price, 2), "sl": round(pos["sl"], 2),
                    "tp": round(pos["tp"], 2), "pnl_pct": round(pnl, 4), "result": result,
                })
                pos = None

        # 2) ไม่มี position → ดู signal ที่ candle นี้ (ใช้ data ถึง i เท่านั้น)
        if pos is None:
            sig = generate_signal(df.iloc[:i + 1], params)
            if sig:
                pos = {
                    "side": sig["signal"], "entry": sig["price"],
                    "sl": sig["sl"], "tp": sig["tp"], "entry_time": ts,
                }

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
