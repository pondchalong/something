"""
ทดสอบ analysis/export_records.py — การจับคู่ไม้กับ SL/TP + merge ข้อมูล (offline)

รัน: py -3.12 test_export_records.py
"""
from analysis.export_records import ERAS, _ms, era_of, match_stops, merge

T0 = 1_790_000_000_000
MIN = 60_000


def approx(a, b, tol=1e-6):
    assert abs(a - b) < tol, f"expected {b}, got {a}"


def trade(side="LONG", entry=100.0, exit_=104.0, close_ids=("x1",), dur=60):
    return {"side": side, "entry": entry, "exit": exit_, "entry_ts": T0,
            "exit_ts": T0 + dur * MIN, "close_order_ids": list(close_ids)}


def algo(type_, trigger, side="sell", after_s=2, actual_id="", actual=None, status="CANCELED"):
    return {"algo_id": f"{type_}{trigger}{after_s}", "type": type_, "side": side,
            "trigger": trigger, "status": status, "create_ms": T0 + after_s * 1000,
            "actual_order_id": actual_id, "actual_price": actual}


def test_tp_exit_with_slippage():
    """ไม้ LONG ชน TP: R จาก SL, slippage = ขายได้ต่ำกว่า trigger"""
    a = [algo("STOP_MARKET", 98.0), algo("TAKE_PROFIT_MARKET", 104.0, actual_id="x1",
                                         actual=103.896, status="FINISHED")]
    m = match_stops(trade(exit_=103.896), a)
    assert m["exit_via"] == "TP"
    approx(m["risk"], 2.0)
    approx(m["exit_slip_bps"], (104.0 - 103.896) / 104.0 * 10000)   # 10 bps เสียเปรียบ


def test_short_sl_slippage_sign():
    """ไม้ SHORT ชน SL: ซื้อคืนแพงกว่า trigger = เสียเปรียบ (ค่าบวก)"""
    a = [algo("STOP_MARKET", 102.0, side="buy", actual_id="x1", actual=102.05, status="FINISHED"),
         algo("TAKE_PROFIT_MARKET", 96.0, side="buy")]
    m = match_stops(trade(side="SHORT", exit_=102.05), a)
    assert m["exit_via"] == "SL"
    assert m["exit_slip_bps"] > 0


def test_no_stops_when_none_placed():
    """ตั้ง SL/TP ไม่ผ่าน (-2021) → ไม่มี conditional order เลย"""
    m = match_stops(trade(exit_=100.0, dur=1), [])
    assert m["exit_via"] == "no_stops" and m["risk"] is None


def test_no_history_is_not_a_bug():
    """ไม้เก่ากว่าประวัติ SL/TP ที่ exchange เก็บไว้ → ไม่รู้ ไม่ใช่ ไม่ได้ตั้ง"""
    m = match_stops(trade(), [], history_start_ms=T0 + 86_400_000)
    assert m["exit_via"] == "no_history"
    m = match_stops(trade(), [], history_start_ms=T0 - 1)
    assert m["exit_via"] == "no_stops", "อยู่ในช่วงที่มีประวัติแต่หาไม่เจอ = ไม่ได้ตั้งจริง"


def test_bot_market_close():
    """มี SL/TP แต่ขาปิดเป็น order ของบอทเอง (orphan / reverse)"""
    a = [algo("STOP_MARKET", 98.0), algo("TAKE_PROFIT_MARKET", 104.0)]
    m = match_stops(trade(exit_=101.0, close_ids=("bot9",)), a)
    assert m["exit_via"] == "bot_market"


def test_stale_order_from_previous_trade():
    """บั๊กยุค E0: SL/TP ที่ค้างจากไม้ก่อนหน้ามาปิดไม้นี้"""
    old = algo("TAKE_PROFIT_MARKET", 103.0, after_s=-3600, actual_id="x1", actual=103.0,
               status="FINISHED")
    a = [old, algo("STOP_MARKET", 98.0), algo("TAKE_PROFIT_MARKET", 104.0)]
    m = match_stops(trade(exit_=103.0), a)
    assert m["exit_via"] == "stale_order"


def test_ignores_stops_of_wrong_side():
    """order ฝั่งเดียวกับขาเข้า (buy ของไม้ LONG) ไม่ใช่ SL/TP ของไม้นี้"""
    a = [algo("STOP_MARKET", 98.0, side="buy"), algo("TAKE_PROFIT_MARKET", 104.0, side="buy")]
    assert match_stops(trade(), a)["exit_via"] == "no_stops"


def test_risk_from_tp_when_sl_missing():
    """ข้อมูลเก่าที่ order SL หมดอายุไปแล้ว → ใช้ TP / R:R แทน"""
    m = match_stops(trade(), [algo("TAKE_PROFIT_MARKET", 104.0)])
    approx(m["risk"], 2.0)


def test_merge_keeps_expired_records():
    """ของที่หมดอายุบน exchange แล้ว (ไม่อยู่ในการดึงรอบใหม่) ต้องไม่หายจากไฟล์"""
    old = [{"trade_id": "1", "ts": 1}, {"trade_id": "2", "ts": 2}]
    new = [{"trade_id": "2", "ts": 2, "x": "อัปเดต"}, {"trade_id": "3", "ts": 3}]
    m = merge(old, new, "trade_id")
    assert [r["trade_id"] for r in m] == ["1", "2", "3"]
    assert m[1].get("x") == "อัปเดต", "id ซ้ำ → ของใหม่ทับ"


def test_era_boundaries():
    for start, name, _ in ERAS:
        assert era_of(_ms(start)) == name
    assert era_of(_ms(ERAS[1][0]) - 1) == ERAS[0][1]


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
