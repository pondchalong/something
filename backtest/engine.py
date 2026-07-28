"""
Backtest engine — จำลอง strategy บนข้อมูลย้อนหลัง

หลักการ:
- add_indicators ทั้ง df ครั้งเดียว (indicators ทุกตัวเป็น causal = ใช้แค่อดีต ไม่ lookahead)
- loop ทีละ candle: ถ้าไม่มี position → ดู signal; ถ้ามี position → เช็ค SL/TP hit + exit management
- entry = close ของ candle ที่เกิด signal, exit เริ่มเช็คจาก candle ถัดไป
- reuse generate_signal() เดิม 100%

Exit management (profit protection) — ทำงานต่อจาก SL/TP เดิม, conservative ไม่ lookahead ภายในแท่ง:
- partial TP : ราคาถึง partial_tp_r → ปิดบางส่วน (book กำไรจริง) + (option) ขยับที่เหลือเป็นทุน
- breakeven  : favorable excursion ถึง be_trigger_r → ขยับ SL เป็นทุน
- trailing   : favorable ถึง trail_trigger_r → SL ตาม high-water ห่าง trail_dist_r
หลักกัน lookahead: เช็ค SL/TP/partial ของ "ระดับที่ตั้งไว้จากแท่งก่อน" ก่อน แล้วค่อย
อัปเดต trail/BE จาก high/low ของแท่งนี้ → มีผลกับแท่งถัดไปเท่านั้น
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
SLIPPAGE = 0.0    # ราคาที่ได้จริงแย่กว่าที่ตั้งใจกี่ % (0 = สมมติได้ราคาเป๊ะแบบเดิม)


def _entry_fill(price: float, side: str, slip: float) -> float:
    """ราคาเข้าจริงหลัง slippage — market order ซื้อแพงกว่า/ขายถูกกว่าที่เห็นเสมอ"""
    return price * (1 + slip) if side == "LONG" else price * (1 - slip)


def _exit_fill(price: float, side: str, slip: float) -> float:
    """ราคาออกจริงหลัง slippage — SL/TP ของเราเป็น STOP_MARKET/TAKE_PROFIT_MARKET
    = พอ trigger แล้วกลายเป็น market order → ไม่ได้ราคาที่ตั้งไว้เป๊ะ"""
    return price * (1 - slip) if side == "LONG" else price * (1 + slip)


def _raw_pnl(pos: dict, price: float) -> float:
    """gross return จาก avg_entry ถึง price (ยังไม่หัก fee)"""
    avg = pos["avg_entry"]
    return (price - avg) / avg if pos["side"] == "LONG" else (avg - price) / avg


def _set_partial(pos: dict, params) -> None:
    """ระดับราคาที่จะปิดบางส่วน = entry ± partial_tp_r × sl_dist"""
    d = params.partial_tp_r * pos["sl_dist"]
    pos["partial_price"] = pos["avg_entry"] + d if pos["side"] == "LONG" else pos["avg_entry"] - d


def _move_be(pos: dict, params) -> None:
    """ขยับ SL เป็นทุน (+ buffer ถ้าตั้ง) — ล็อกไม่ให้กลับมาขาดทุน"""
    b = params.be_buffer_r * pos["sl_dist"]
    pos["sl"] = pos["avg_entry"] + b if pos["side"] == "LONG" else pos["avg_entry"] - b
    pos["be_moved"] = True


def _update_trail_be(pos: dict, params) -> None:
    """อัปเดต BE/trailing จาก high-water (mfe_price) — เรียกท้ายแท่ง มีผลแท่งถัดไป"""
    sl_dist = pos["sl_dist"]
    if sl_dist <= 0:
        return
    avg = pos["avg_entry"]
    fav = (pos["mfe_price"] - avg) if pos["side"] == "LONG" else (avg - pos["mfe_price"])
    fav_r = fav / sl_dist

    if params.breakeven and not pos["be_moved"] and fav_r >= params.be_trigger_r:
        _move_be(pos, params)

    if params.trail and fav_r >= params.trail_trigger_r:
        if pos["side"] == "LONG":
            cand = pos["mfe_price"] - params.trail_dist_r * sl_dist
            if cand > pos["sl"]:                 # ขยับเฉพาะทางกำไร (ไม่คลาย SL)
                pos["sl"], pos["trailed"] = cand, True
        else:
            cand = pos["mfe_price"] + params.trail_dist_r * sl_dist
            if cand < pos["sl"]:
                pos["sl"], pos["trailed"] = cand, True


def _book_partial(pos: dict, price: float, frac: float, fee: float) -> None:
    """ปิดบางส่วน frac ของ position → เก็บ pnl จริง (หัก fee เฉพาะส่วนที่ปิด)"""
    pos["realized"] += frac * _raw_pnl(pos, price) - fee * frac
    pos["remaining"] -= frac
    pos["partial_done"] = True


def _recompute_sltp(pos: dict):
    """คำนวณ SL/TP ใหม่จาก avg entry (ใช้ระยะ sl_dist/tp_dist ของ signal ล่าสุด)"""
    if pos["side"] == "LONG":
        pos["sl"] = pos["avg_entry"] - pos["sl_dist"]
        pos["tp"] = pos["avg_entry"] + pos["tp_dist"]
    else:
        pos["sl"] = pos["avg_entry"] + pos["sl_dist"]
        pos["tp"] = pos["avg_entry"] - pos["tp_dist"]


def _open_pos(sig: dict, ts, params, slip: float = 0.0) -> dict:
    entry = _entry_fill(sig["price"], sig["signal"], slip)
    pos = {
        "side": sig["signal"], "entries": [entry], "avg_entry": entry,
        "n_levels": 1,
        "sl": sig["sl"], "tp": sig["tp"],
        "sl_dist": abs(sig["price"] - sig["sl"]), "tp_dist": abs(sig["tp"] - sig["price"]),
        "entry_time": ts, "mfe_price": entry, "mae_price": entry,
        # exit management state
        "remaining": 1.0, "realized": 0.0,
        "partial_done": False, "be_moved": False, "trailed": False,
    }
    if slip:
        # เหมือน executor จริง: ตั้ง SL/TP จาก entry ที่ fill ได้จริง (ระยะเท่าเดิม → R:R คงที่)
        _recompute_sltp(pos)
    _set_partial(pos, params)
    return pos


def _add_pyramid(pos: dict, sig: dict, params, slip: float = 0.0):
    """เพิ่มไม้ทางเดียวกัน → เฉลี่ย entry + recompute SL/TP จาก avg (size เท่ากันต่อ level)"""
    pos["entries"].append(_entry_fill(sig["price"], pos["side"], slip))
    pos["avg_entry"] = sum(pos["entries"]) / len(pos["entries"])
    pos["n_levels"] += 1
    pos["sl_dist"] = abs(sig["price"] - sig["sl"])
    pos["tp_dist"] = abs(sig["tp"] - sig["price"])
    _recompute_sltp(pos)
    _set_partial(pos, params)


def _record(trades: list, pos: dict, exit_price: float, exit_reason: str, ts, fee: float):
    """ปิดส่วนที่เหลือ → รวมกับ partial ที่เคย book → 1 trade record
    pnl = realized(partials) + remaining×raw(exit) − fee(exit ส่วนที่เหลือ) − fee(entry × n_levels)
    (ผลรวม fee = fee×(n+1) เท่าเดิมเมื่อไม่มี partial: realized=0, remaining=1)"""
    avg = pos["avg_entry"]
    n = pos["n_levels"]
    final_frac = pos["remaining"]
    pnl = pos["realized"] + final_frac * _raw_pnl(pos, exit_price) - fee * final_frac - fee * n

    if pos["side"] == "LONG":
        mfe_dist = pos["mfe_price"] - avg
        mae_dist = avg - pos["mae_price"]
    else:
        mfe_dist = avg - pos["mfe_price"]
        mae_dist = pos["mae_price"] - avg
    trades.append({
        "entry_time": str(pos["entry_time"]), "exit_time": str(ts),
        "side": pos["side"], "entry": round(avg, 2), "levels": n,
        "exit": round(exit_price, 2), "sl": round(pos["sl"], 2),
        "tp": round(pos["tp"], 2), "pnl_pct": round(pnl, 4),
        "result": "win" if pnl > 0 else "loss", "exit_reason": exit_reason,
        "partial": pos["partial_done"],
        "mfe_pct_of_tp": round(mfe_dist / pos["tp_dist"], 3) if pos["tp_dist"] > 0 else 0.0,
        "mae_pct_of_sl": round(mae_dist / pos["sl_dist"], 3) if pos["sl_dist"] > 0 else 0.0,
    })


def simulate(df: pd.DataFrame, params=DEFAULT_PARAMS, fee: float = FEE,
             start: int = WARMUP, end: int = None,
             slippage: float = SLIPPAGE) -> BacktestResult:
    """
    df = ต้อง add_indicators แล้ว. backtest ช่วง index [start, end)
    ลำดับต่อแท่ง (ถือ position):
      1) SL/TP/partial ของระดับที่ตั้งจากแท่งก่อน (priority SL > partial > TP)
      2) reverse (signal ตรงข้าม) / pyramid (signal เดิมทาง)
      3) อัปเดต trail/BE จาก high/low แท่งนี้ (มีผลแท่งถัดไป)

    slippage = ส่วนต่างระหว่างราคาที่ตั้งใจกับราคาที่ได้จริง (ทุกไม้เป็น market order)
    คิดฝั่งที่เสียเปรียบเราเสมอ ทั้งขาเข้าและขาออก → ต้นทุนจริง ≈ (fee + slippage) × 2
    """
    trades = []
    pos = None
    end = len(df) if end is None else end

    for i in range(start, end):
        row = df.iloc[i]
        ts = df.index[i]
        high, low, close = row["high"], row["low"], row["close"]
        sig = generate_signal(df.iloc[:i + 1], params)   # เรียกครั้งเดียว/candle

        if pos is not None:
            # update MFE/MAE (เก็บราคา high/low สุด — คำนวณ pct ตอน exit จาก avg)
            pos["mfe_price"] = (max(pos["mfe_price"], high) if pos["side"] == "LONG"
                                else min(pos["mfe_price"], low))
            pos["mae_price"] = (min(pos["mae_price"], low) if pos["side"] == "LONG"
                                else max(pos["mae_price"], high))

            if pos["side"] == "LONG":
                sl_hit, tp_hit = low <= pos["sl"], high >= pos["tp"]
                partial_hit = high >= pos["partial_price"]
            else:
                sl_hit, tp_hit = high >= pos["sl"], low <= pos["tp"]
                partial_hit = low <= pos["partial_price"]

            # 1) SL/TP/partial — priority SL > partial > TP
            #    (ระดับที่ "แตะ" ใช้ตัดสินว่า trigger ไหม แต่ราคาที่ได้จริงโดน slippage)
            if sl_hit:
                reason = "trail" if pos["trailed"] else ("BE" if pos["be_moved"] else "SL")
                _record(trades, pos, _exit_fill(pos["sl"], pos["side"], slippage),
                        reason, ts, fee)
                pos = None
            elif params.partial_tp and not pos["partial_done"] and partial_hit:
                _book_partial(pos, _exit_fill(pos["partial_price"], pos["side"], slippage),
                              params.partial_tp_pct, fee)
                if params.partial_be:
                    _move_be(pos, params)
            elif tp_hit:
                _record(trades, pos, _exit_fill(pos["tp"], pos["side"], slippage),
                        "TP", ts, fee)
                pos = None

            # 2) reverse / pyramid  +  3) trail/BE (เฉพาะถ้ายังถืออยู่)
            if pos is not None and not sl_hit and not tp_hit:
                if sig and sig["signal"] != pos["side"] and params.reverse:
                    _record(trades, pos, _exit_fill(close, pos["side"], slippage),
                            "reverse", ts, fee)
                    pos = _open_pos(sig, ts, params, slippage)
                else:
                    if sig and sig["signal"] == pos["side"] and pos["n_levels"] < params.max_pyramid:
                        _add_pyramid(pos, sig, params, slippage)
                    _update_trail_be(pos, params)

        if pos is None and sig:
            pos = _open_pos(sig, ts, params, slippage)

    return compute_metrics(trades, params.to_dict())


def run_backtest(df: pd.DataFrame, params=DEFAULT_PARAMS, df_htf: pd.DataFrame = None,
                 fee: float = FEE, warmup: int = WARMUP,
                 slippage: float = SLIPPAGE) -> BacktestResult:
    """add_indicators + backtest ทั้ง df (ใช้ตอนรัน backtest เดี่ยว)"""
    df = add_indicators(df, df_htf, params)
    return simulate(df, params, fee, warmup, len(df), slippage)


def _per_trade_pct(result) -> float:
    """gross ต่อไม้ (ยังไม่หัก fee) — ตัวเลขที่เทียบกับ reconcile ได้ตรงๆ"""
    n = len(result.trades)
    if not n:
        return 0.0
    return (sum(t["pnl_pct"] for t in result.trades) / n + 2 * FEE) * 100


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--timeframe", default=DEFAULT_PARAMS.timeframe)
    ap.add_argument("--limit", type=int, default=1500)
    ap.add_argument("--slippage", type=float, default=SLIPPAGE * 10000,
                    help="slippage ต่อข้าง หน่วย bps (1 bps = 0.01%%) default 0")
    ap.add_argument("--sweep", action="store_true",
                    help="รันหลายค่า slippage เทียบกัน → ดูว่า edge ทนได้แค่ไหน")
    args = ap.parse_args()

    print(f"Fetching {args.symbol} {args.timeframe} x{args.limit} ...")
    df = fetch_ohlcv(symbol=args.symbol, timeframe=args.timeframe, limit=args.limit)
    df_htf = fetch_htf_ohlcv(symbol=args.symbol, timeframe=args.timeframe, limit=args.limit)

    if args.sweep:
        df_ind = add_indicators(df, df_htf, DEFAULT_PARAMS)
        print(f"\n{'slip/ข้าง':>10} {'ไม้':>5} {'return':>9} {'winrate':>8} "
              f"{'PF':>6} {'Sharpe':>7} {'gross/ไม้':>10} {'net/ไม้':>8}")
        for bps in (0, 1, 2, 5, 10, 20):
            r = simulate(df_ind, DEFAULT_PARAMS, FEE, WARMUP, len(df_ind), bps / 10000)
            m, n = r.metrics, len(r.trades)
            net = (sum(t["pnl_pct"] for t in r.trades) / n * 100) if n else 0.0
            print(f"{bps:>7} bps {n:>5} {m['total_return'] * 100:>+8.2f}% "
                  f"{m['winrate']:>7.1f}% {m['profit_factor']:>6.2f} {m['sharpe']:>7.2f} "
                  f"{_per_trade_pct(r):>+9.3f}% {net:>+7.3f}%")
        return

    slip = args.slippage / 10000
    print(f"Running backtest (default params, slippage {args.slippage:g} bps/ข้าง) ...")
    result = run_backtest(df, DEFAULT_PARAMS, df_htf, slippage=slip)
    print(result.summary())
    print(f"\nFirst 3 trades:")
    for t in result.trades[:3]:
        print(f"  {t['side']} {t['entry_time']} entry={t['entry']} exit={t['exit']} {t['result']} {t['pnl_pct']*100:+.2f}%")


if __name__ == "__main__":
    main()
