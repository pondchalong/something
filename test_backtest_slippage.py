"""
ทดสอบ slippage model ใน backtest/engine.py (offline — ไม่ต้อง fetch OHLCV)

ทำไมต้องมี: backtest เดิมสมมติว่าได้ราคาที่ตั้งใจเป๊ะทุกไม้ แต่ของจริงทุกไม้เป็น
market order (เข้า = market, SL/TP = STOP_MARKET/TAKE_PROFIT_MARKET ที่ trigger
แล้วกลายเป็น market) → ราคาที่ได้แย่กว่าเสมอ. reconcile 26 ก.ค. พบว่า backtest
คาด gross +0.20%/ไม้ แต่ของจริง -0.02% → ต้องมีตัวแปรนี้ถึงจะเทียบกันได้

รัน: py -3.12 test_backtest_slippage.py
"""
import pandas as pd

import backtest.engine as engine
from backtest.engine import FEE, simulate
from strategy.params import DEFAULT_PARAMS

BPS = 0.0001


def approx(a, b, tol=1e-4):
    assert abs(a - b) < tol, f"expected {b}, got {a}"


def _df(bars):
    """bars = [(high, low, close), ...] — index เป็นเวลาจริงเพื่อให้ record อ่านง่าย"""
    idx = pd.date_range("2026-06-01", periods=len(bars), freq="15min")
    return pd.DataFrame([{"high": h, "low": lo, "close": c} for h, lo, c in bars], index=idx)


def _run(bars, sig, slip, params=DEFAULT_PARAMS):
    """ยิง signal ที่แท่ง index 0 แท่งเดียว แล้วปล่อยให้ engine เดินต่อ"""
    original = engine.generate_signal
    fired = []

    def fake(df_slice, _params):
        if len(df_slice) == 1 and not fired:
            fired.append(1)
            return sig
        return None

    engine.generate_signal = fake
    try:
        return simulate(_df(bars), params, FEE, 0, len(bars), slip)
    finally:
        engine.generate_signal = original


LONG = {"signal": "LONG", "price": 100.0, "sl": 99.0, "tp": 102.0}
SHORT = {"signal": "SHORT", "price": 100.0, "sl": 101.0, "tp": 98.0}
# แท่ง 0 = แท่งที่เกิด signal, แท่ง 1 = ราคาวิ่งไปโดน TP (สำหรับ LONG)
TP_BARS = [(100.0, 100.0, 100.0), (103.0, 99.5, 102.5)]
SL_BARS = [(100.0, 100.0, 100.0), (100.2, 98.0, 98.5)]


def test_zero_slippage_unchanged():
    """slip=0 ต้องได้ผลเท่าเดิมเป๊ะ (ของเก่าทั้งหมดยังเทียบกันได้)"""
    t, = _run(TP_BARS, LONG, 0.0).trades
    approx(t["entry"], 100.0)
    approx(t["exit"], 102.0)
    approx(t["pnl_pct"], 0.02 - 2 * FEE)


def test_long_exit_fill_is_worse_than_the_level():
    """
    ต้นทุนที่ "จ่ายตรงๆ" คือขาออก — ขาเข้าไม่กินกำไรเพราะ SL/TP ขยับตาม entry จริง
    (R:R คงเดิม เหมือน executor) แต่ไปโผล่เป็น "TP ไกลขึ้น" แทน ดู test ถัดไป
    """
    slip = 10 * BPS
    t, = _run(TP_BARS, LONG, slip).trades
    entry = 100.0 * (1 + slip)                    # 100.10
    tp = entry + 2.0                              # ตั้ง TP จาก entry จริง ระยะเท่าเดิม
    exit_ = tp * (1 - slip)
    approx(t["entry"], round(entry, 2))
    approx(t["exit"], round(exit_, 2), tol=0.01)
    approx(t["pnl_pct"], (exit_ - entry) / entry - 2 * FEE)

    base, = _run(TP_BARS, LONG, 0.0).trades
    approx(base["pnl_pct"] - t["pnl_pct"], slip, tol=2e-4)


def test_entry_slippage_moves_the_target_out_of_reach():
    """
    ต้นทุนจริงของ slippage ขาเข้า = TP เลื่อนหนีไป → ไม้ที่เคยชน TP พอดีๆ กลายเป็นไม่ถึง
    (นี่คือเหตุผลว่าทำไม backtest ที่ได้ราคาเป๊ะถึงมองโลกในแง่ดีเกินจริง)
    """
    # high = 102.05 → ถึง TP เดิม (102.0) แต่ไม่ถึง TP ใหม่ (102.1)
    bars = [(100.0, 100.0, 100.0), (102.05, 99.5, 102.0)]
    assert len(_run(bars, LONG, 0.0).trades) == 1, "ไม่มี slippage ต้องปิดที่ TP"
    assert _run(bars, LONG, 10 * BPS).trades == [], "มี slippage แล้ว TP ต้องไม่ถึง"


def test_short_slippage_is_mirrored():
    """SHORT: ขายได้ถูกกว่า + ซื้อคืนแพงกว่า → เสียเปรียบเหมือนกัน"""
    slip = 10 * BPS
    bars = [(100.0, 100.0, 100.0), (100.5, 97.0, 97.5)]
    t, = _run(bars, SHORT, slip).trades
    entry = 100.0 * (1 - slip)
    approx(t["entry"], round(entry, 2))
    assert t["entry"] < 100.0, "SHORT ต้องขายได้ถูกกว่าราคาที่เห็น"
    assert t["exit"] > entry - 2.0, "ซื้อคืนต้องแพงกว่า TP ที่ตั้งไว้"
    base, = _run(bars, SHORT, 0.0).trades
    approx(base["pnl_pct"] - t["pnl_pct"], slip, tol=2e-4)


def test_sl_tp_keep_distance_from_real_entry():
    """SL/TP ต้องขยับตาม entry จริง (เหมือน executor) → R:R คงที่ ไม่ใช่ระยะเพี้ยน"""
    slip = 20 * BPS
    t, = _run(SL_BARS, LONG, slip).trades
    entry = 100.0 * (1 + slip)
    approx(t["sl"], round(entry - 1.0, 2), tol=0.01)
    approx(t["tp"], round(entry + 2.0, 2), tol=0.01)
    approx((t["tp"] - t["entry"]) / (t["entry"] - t["sl"]), 2.0, tol=0.02)


def test_stop_loss_costs_more_than_the_level():
    """โดน SL แล้วยังขาดทุนเกินระดับ SL อีกนิด (stop = market order)"""
    slip = 10 * BPS
    t, = _run(SL_BARS, LONG, slip).trades
    assert t["exit_reason"] == "SL"
    assert t["exit"] < t["sl"], "LONG โดน SL ต้อง fill ต่ำกว่าระดับ SL"
    base, = _run(SL_BARS, LONG, 0.0).trades
    assert t["pnl_pct"] < base["pnl_pct"], "ขาดทุนต้องมากกว่ากรณีไม่มี slippage"


def test_slippage_can_flip_a_winning_backtest():
    """
    ประเด็นหลักทั้งหมด: edge บางกว่าต้นทุน → slippage นิดเดียวพลิกกำไรเป็นขาดทุน
    ไม้นี้กำไร 0.10% หลัง fee — เจอ slippage 20 bps ก็ติดลบทันที
    """
    thin = {"signal": "LONG", "price": 100.0, "sl": 99.0, "tp": 100.18}
    bars = [(100.0, 100.0, 100.0), (100.5, 99.9, 100.4)]
    clean, = _run(bars, thin, 0.0).trades
    slipped, = _run(bars, thin, 20 * BPS).trades
    assert clean["pnl_pct"] > 0, f"ก่อนใส่ slippage ต้องกำไร ได้ {clean['pnl_pct']}"
    assert slipped["pnl_pct"] < 0, f"หลังใส่ slippage ต้องขาดทุน ได้ {slipped['pnl_pct']}"


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {name}: {e}")
    print(f"\n{len(tests)-failed}/{len(tests)} ผ่าน")
    raise SystemExit(1 if failed else 0)
