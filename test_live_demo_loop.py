"""
ทดสอบ state machine ของ live_demo ด้วย exchange/data ปลอม (offline)

โฟกัสที่บั๊กหลักจากข้อมูลจริง: **query position พลาดชั่วคราว ต้องไม่ถูกตีความว่าไม้ปิดแล้ว**
(เดิม get_open_position คืน None ตอน testnet 502 → บันทึกไม้ปิดด้วยราคามั่ว →
ล้าง state → รอบถัดไปเจอ position ไม่มี state = orphan → ปิดไม้ที่ยังดีทิ้ง)

รัน: py -3.12 test_live_demo_loop.py
"""
import json
import os
import tempfile

import pandas as pd

import trading.executor as ex_mod
import trading.live_demo as ld
from trading.executor import PositionQueryError


class _StopLoop(Exception):
    """ใช้เบรกลูป while True ของ live_demo หลังครบจำนวนรอบที่ต้องการ"""


def _fake_df():
    idx = pd.date_range("2027-01-15 10:00", periods=3, freq="15min")
    return pd.DataFrame({"open": [100.0] * 3, "high": [103.0] * 3,
                         "low": [99.0] * 3, "close": [101.0] * 3, "volume": [1.0] * 3},
                        index=idx)


OPEN_TRADE = {
    "action": "LONG", "entry": 100.0, "sl": 98.0, "tp": 104.0, "size": 1.0,
    "entry_time": "2027-01-15T10:00:00", "entry_ms": 1_800_000_000_000,
    "mfe_price": 100.0, "mae_price": 100.0, "confluence": 5, "n_levels": 1,
}


class FakeEx:
    def __init__(self):
        self.cancelled = []
        self.closed_orders = []

    def load_markets(self):
        pass

    def cancel_all_orders(self, symbol, params=None):
        self.cancelled.append(params or {})

    def create_order(self, *a, **k):
        self.closed_orders.append(a)
        return {}


def run_one_loop(tmpdir, position_result, signal=None, n_loops=1):
    """
    รัน live_demo 1 รอบด้วยของปลอม แล้วคืน (trade_log, open_trade ที่เหลือ, exchange)
    position_result: callable → position dict / None / raise PositionQueryError
    """
    ex_mod.TRADE_LOG = os.path.join(tmpdir, "trade_log.json")
    ex_mod.OPEN_TRADE = os.path.join(tmpdir, "open_trade.json")
    ex_mod.time.sleep = lambda *_: None

    fake_ex = FakeEx()
    calls = {"n": 0}

    def fake_sleep(*_):
        calls["n"] += 1
        if calls["n"] >= n_loops:
            raise _StopLoop

    ld.DRY_RUN = False
    ld.time.sleep = fake_sleep
    ld.get_testnet_exchange = lambda: fake_ex
    ld.fetch_ohlcv = lambda **k: _fake_df()
    ld.fetch_htf_ohlcv = lambda **k: _fake_df()
    ld.add_indicators = lambda df, df_htf, params: df
    ld.generate_signal = lambda df, params: signal
    ld.get_open_position = lambda ex, sym: position_result()
    ld.send_alert = ld.send_closed_alert = lambda *a, **k: True
    ld.send_skip_alert = lambda *a, **k: True

    try:
        ld.run()
    except _StopLoop:
        pass
    except Exception as e:                     # ลูปจับ exception เองอยู่แล้ว
        raise AssertionError(f"live_demo พังโดยไม่คาดคิด: {type(e).__name__}: {e}")

    log = []
    if os.path.exists(ex_mod.TRADE_LOG):
        with open(ex_mod.TRADE_LOG, encoding="utf-8") as f:
            log = json.load(f)
    remaining = None
    if os.path.exists(ex_mod.OPEN_TRADE):
        with open(ex_mod.OPEN_TRADE, encoding="utf-8") as f:
            remaining = json.load(f)
    return log, remaining, fake_ex


# ============================================================
def test_query_failure_does_not_close_trade(tmpdir):
    """
    บั๊กหลัก: query position พลาด → ต้องไม่บันทึกไม้ปิด และต้องไม่ล้าง open_trade
    """
    ex_mod.OPEN_TRADE = os.path.join(tmpdir, "open_trade.json")
    with open(ex_mod.OPEN_TRADE, "w", encoding="utf-8") as f:
        json.dump(OPEN_TRADE, f)

    def boom():
        raise PositionQueryError("502 Bad Gateway")

    log, remaining, _ = run_one_loop(tmpdir, boom)
    assert log == [], f"ห้ามบันทึกไม้ปิดตอน query พลาด — ได้ {log}"
    assert remaining is not None, "ห้ามล้าง open_trade ตอน query พลาด (ไม้ยังถืออยู่)"
    assert remaining["action"] == "LONG"


def test_real_close_records_and_cancels_orders(tmpdir):
    """
    ไม้ปิดจริง (ยืนยันแล้วว่าไม่มี position) → บันทึกผล + ล้าง state + **cancel order ค้าง**
    (ไม่ cancel = SL/TP ของไม้เก่าไปปิดไม้ถัดไปที่ราคาไม่เกี่ยวข้อง)
    """
    ex_mod.OPEN_TRADE = os.path.join(tmpdir, "open_trade.json")
    with open(ex_mod.OPEN_TRADE, "w", encoding="utf-8") as f:
        json.dump(OPEN_TRADE, f)

    # ไม่มี fill ให้หา → record จะเป็น 'เชื่อไม่ได้' แต่ต้องบันทึก + cancel ครบ
    ld.record_closed_trade = lambda ex, t, **k: {
        "mode": "LIVE_DEMO", "action": t["action"], "entry": t["entry"],
        "exit": 98.0, "pnl_pct": -0.0208, "won": False, "exit_reason": "SL",
        "mfe_pct": 0.0, "mae_pct": -0.02, "symbol": "BTC/USDT",
    }
    log, remaining, fake_ex = run_one_loop(tmpdir, lambda: None)
    assert remaining is None, "ไม้ปิดแล้วต้องล้าง open_trade"
    assert fake_ex.cancelled, "ต้องเรียก cancel order ค้างหลังไม้ปิด"
    assert {"stop": True} in fake_ex.cancelled, \
        f"ต้อง cancel conditional order (stop=True) ด้วย — ได้ {fake_ex.cancelled}"


def test_holding_position_updates_excursion(tmpdir):
    """ถือไม้อยู่ + ไม่มี signal → update MFE/MAE ไม่บันทึกอะไรลง log"""
    ex_mod.OPEN_TRADE = os.path.join(tmpdir, "open_trade.json")
    with open(ex_mod.OPEN_TRADE, "w", encoding="utf-8") as f:
        json.dump(OPEN_TRADE, f)

    import trading.live_demo as _ld
    _ld.record_closed_trade = ex_mod.record_closed_trade      # คืนของจริง
    pos = {"contracts": 1.0, "side": "long", "entryPrice": 100.0}
    log, remaining, _ = run_one_loop(tmpdir, lambda: pos)
    assert log == [], "ยังถือไม้อยู่ ต้องไม่มี record ปิด"
    assert remaining["mfe_price"] == 103.0, f"MFE ต้อง update เป็น high=103 ได้ {remaining}"
    assert remaining["mae_price"] == 99.0


if __name__ == "__main__":
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for name, fn in tests:
        with tempfile.TemporaryDirectory() as d:
            try:
                fn(d)
                print(f"  ✅ {name}")
            except AssertionError as e:
                failed += 1
                print(f"  ❌ {name}: {e}")
    print(f"\n{len(tests)-failed}/{len(tests)} ผ่าน")
    raise SystemExit(1 if failed else 0)
