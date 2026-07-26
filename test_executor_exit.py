"""
ทดสอบ lifecycle ของไม้ใน executor ด้วย exchange ปลอม (offline — ไม่ต่อ testnet)

ครอบคลุมบั๊กที่เจอจากข้อมูลจริง 93 ไม้ (ดู CLAUDE.md "exit price ที่บันทึกเชื่อไม่ได้"):
- get_open_position() ต้องแยก "ไม่มี position" ออกจาก "query ไม่สำเร็จ"
- หา exit fill ต้องดูเวลาด้วย ไม่งั้นได้ fill ของไม้ก่อนหน้ามาเป็นราคาปิด
- หา exit ไม่เจอ → ต้อง mark ว่าเชื่อไม่ได้ ไม่ใช่เดาราคา

รัน: py -3.12 test_executor_exit.py
"""
import json
import os
import tempfile

import ccxt

import trading.executor as ex_mod
from trading.executor import (
    PositionQueryError, get_open_position, _find_exit_fill, record_closed_trade,
)

ENTRY_MS = 1_800_000_000_000          # เวลาเปิดไม้ (epoch ms สมมติ)
MINUTE = 60_000

# ไม้ LONG: entry 100, SL 98 (1R = 2), TP 104
TRADE = {
    "action": "LONG", "entry": 100.0, "sl": 98.0, "tp": 104.0, "size": 1.0,
    "entry_time": "2027-01-15T10:00:00", "entry_ms": ENTRY_MS,
    "mfe_price": 103.0, "mae_price": 99.0, "confluence": 5, "n_levels": 1,
}


class FakeExchange:
    """exchange ปลอม: กำหนดได้ว่า fetch_positions/fetch_my_trades จะตอบอะไรหรือพัง"""

    def __init__(self, positions=None, fills=None, fail_positions=False):
        self._positions = positions or []
        self._fills = fills or []
        self.fail_positions = fail_positions
        self.cancelled = []

    def fetch_positions(self, symbols=None):
        if self.fail_positions:
            raise ccxt.ExchangeNotAvailable("binance GET /fapi/v3/positionRisk 502 Bad Gateway")
        return self._positions

    def fetch_my_trades(self, symbol, since=None, limit=None):
        return [f for f in self._fills if since is None or f["timestamp"] >= since]

    def cancel_all_orders(self, symbol, params=None):
        self.cancelled.append(params or {})


def fill(ts_offset_min, side, price, amount=1.0):
    return {"timestamp": ENTRY_MS + ts_offset_min * MINUTE, "side": side,
            "price": price, "amount": amount}


def approx(a, b, tol=1e-6):
    assert abs(a - b) < tol, f"expected {b}, got {a}"


# ============================================================
def test_position_query_failure_raises_not_none():
    """query พัง ต้อง raise — ถ้าคืน None caller จะนึกว่าไม้ปิดแล้ว (บั๊กเดิม)"""
    ex = FakeExchange(fail_positions=True)
    ex_mod.time.sleep = lambda *_: None          # ไม่ต้องรอ retry จริง
    try:
        get_open_position(ex, "BTC/USDT")
    except PositionQueryError:
        return
    raise AssertionError("ต้อง raise PositionQueryError ไม่ใช่คืน None")


def test_no_position_returns_none():
    """ไม่มี position จริงๆ → None (ปกติ)"""
    ex = FakeExchange(positions=[{"contracts": 0}])
    assert get_open_position(ex, "BTC/USDT") is None


def test_open_position_returned():
    ex = FakeExchange(positions=[{"contracts": 0.5, "side": "long", "entryPrice": 100.0}])
    pos = get_open_position(ex, "BTC/USDT")
    assert pos and float(pos["contracts"]) == 0.5


def test_exit_fill_ignores_older_fill_of_previous_trade():
    """
    บั๊กเดิม: หยิบ 'fill ฝั่งตรงข้ามตัวล่าสุด' โดยไม่ดูเวลา → ได้ราคาปิดของไม้ก่อนหน้า
    (ข้อมูลจริงเจอ exit ซ้ำกัน 8 ไม้ + ไม้นึง -3.15R ทั้งที่ SL cap ~-1.1R)
    """
    old = fill(-600, "sell", 62122.2)            # ไม้ก่อนหน้า ปิดไป 10 ชม.ก่อน
    ex = FakeExchange(fills=[old])
    price, n = _find_exit_fill(ex, TRADE, "BTC/USDT")
    assert price is None and n == 0, f"ต้องไม่หยิบ fill เก่ามาใช้ ได้ {price}"


def test_exit_fill_picks_fill_after_entry():
    ex = FakeExchange(fills=[fill(-600, "sell", 62122.2), fill(30, "sell", 104.0)])
    price, n = _find_exit_fill(ex, TRADE, "BTC/USDT")
    approx(price, 104.0)
    assert n == 1


def test_exit_fill_ignores_same_side_entry_fill():
    """fill ฝั่งเดียวกับ entry (buy ของ LONG) ไม่ใช่ราคาปิด"""
    ex = FakeExchange(fills=[fill(0, "buy", 100.0), fill(20, "sell", 98.0)])
    price, _ = _find_exit_fill(ex, TRADE, "BTC/USDT")
    approx(price, 98.0)


def test_exit_fill_vwap_across_partial_fills():
    """market close แตกเป็นหลาย fill → ถ่วงน้ำหนักด้วยขนาด"""
    ex = FakeExchange(fills=[fill(10, "sell", 104.0, 0.75), fill(10, "sell", 100.0, 0.25)])
    price, n = _find_exit_fill(ex, TRADE, "BTC/USDT")
    approx(price, 104.0 * 0.75 + 100.0 * 0.25)
    assert n == 2


# ============================================================
def _record(fills, tmp):
    ex_mod.TRADE_LOG = tmp
    ex = FakeExchange(fills=fills)
    return record_closed_trade(ex, dict(TRADE), symbol="BTC/USDT")


def test_record_marks_unreliable_when_no_fill(tmp_log):
    """หา exit ไม่เจอ → ห้ามเดาราคา ต้อง mark ว่าเชื่อไม่ได้ + ไม่มี pnl_pct"""
    c = _record([fill(-600, "sell", 55.0)], tmp_log)
    assert c["exit_unreliable"] is True
    assert c["exit"] is None
    assert "pnl_pct" not in c, "record ที่เชื่อไม่ได้ต้องไม่มี pnl_pct (กันปนในสถิติ)"
    assert c["exit_reason"] == "unknown"


def test_record_tp_hit(tmp_log):
    c = _record([fill(30, "sell", 104.0)], tmp_log)
    assert c["exit_reason"] == "TP" and c["won"] is True
    approx(c["pnl_pct"], round(0.04 - 0.0008, 4))
    approx(c["exit_off_r"], 0.0)


def test_record_sl_hit(tmp_log):
    c = _record([fill(30, "sell", 98.0)], tmp_log)
    assert c["exit_reason"] == "SL" and c["won"] is False
    approx(c["pnl_pct"], round(-0.02 - 0.0008, 4))


def test_record_flags_midway_close_as_other(tmp_log):
    """ปิดกลางทาง (ไม่ใช่ SL/TP) ต้องไม่ถูกเดาว่าเป็น SL — ต้องรู้ว่าผิดปกติ"""
    c = _record([fill(30, "sell", 101.0)], tmp_log)
    assert c["exit_reason"] == "other", f"ควรเป็น 'other' ได้ {c['exit_reason']}"
    approx(c["exit_off_r"], 1.5)          # ห่างจาก TP 3 จุด = 1.5R


def test_record_writes_to_log(tmp_log):
    _record([fill(30, "sell", 104.0)], tmp_log)
    with open(tmp_log, encoding="utf-8") as f:
        log = json.load(f)
    assert len(log) == 1 and log[0]["exit_reason"] == "TP"


if __name__ == "__main__":
    ex_mod.time.sleep = lambda *_: None
    tests = [(k, v) for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    with tempfile.TemporaryDirectory() as d:
        for name, fn in tests:
            path = os.path.join(d, f"{name}.json")
            try:
                fn(path) if fn.__code__.co_argcount else fn()
                print(f"  ✅ {name}")
            except AssertionError as e:
                failed += 1
                print(f"  ❌ {name}: {e}")
    print(f"\n{len(tests)-failed}/{len(tests)} ผ่าน")
    raise SystemExit(1 if failed else 0)
