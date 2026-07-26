"""
ทดสอบ analysis/trade_stats.py ด้วยตัวเลขที่คำนวณมือได้ (ไม่ต้องต่อ network)

รัน: py -3.12 test_trade_stats.py
"""
from analysis.trade_stats import (
    closed_trades, to_metric_trades, _r_multiple, _mfe_r,
    breakdown, data_quality, exit_management_whatif, exit_integrity, analyze,
)

FEE = 0.0004

# ไม้ LONG: entry 100, SL 98 (1R = 2), TP 104 (2R = 4)
# ชนะโดน TP → pnl = 4/100 - 2×0.0004 = 0.0392 → 0.0392×100/2 = 1.96R
WIN = {
    "mode": "LIVE_DEMO", "action": "LONG", "entry": 100.0, "exit": 104.0,
    "sl": 98.0, "tp": 104.0, "pnl_pct": 0.0392, "won": True, "exit_reason": "TP",
    "mfe_pct": 0.04, "mae_pct": -0.01,        # เคยลง 99 → MAE = 1 จาก SL 2 = 50%
    "entry_time": "2026-07-01T10:00:00", "exit_time": "2026-07-01T12:00:00",
    "confluence": 5,
}
# ไม้ SHORT: entry 200, SL 204 (1R = 4), TP 192 (2R = 8)
# แพ้โดน SL → pnl = -4/200 - 0.0008 = -0.0208 → -0.0208×200/4 = -1.04R
LOSS = {
    "mode": "LIVE_DEMO", "action": "SHORT", "entry": 200.0, "exit": 204.0,
    "sl": 204.0, "tp": 192.0, "pnl_pct": -0.0208, "won": False, "exit_reason": "SL",
    "mfe_pct": 0.03, "mae_pct": -0.02,        # เคยลงไป 194 → MFE = 6 จาก TP 8 = 75%
    "entry_time": "2026-07-02T11:00:00", "exit_time": "2026-07-02T13:00:00",
    "confluence": 3,
}
DRY = {"mode": "DRY_RUN", "action": "LONG", "entry": 100.0, "sl": 98.0, "tp": 104.0}


def approx(a, b, tol=1e-3):
    assert abs(a - b) < tol, f"expected {b}, got {a}"


def test_filters_dry_run():
    got = closed_trades([DRY, WIN, LOSS, {"mode": "LIVE_DEMO", "pnl_pct": None}])
    assert len(got) == 2, f"ควรเหลือ 2 ไม้ปิดแล้ว ได้ {len(got)}"
    assert got[0]["entry_time"] < got[1]["entry_time"], "ต้องเรียงตามเวลา"


def test_r_multiple():
    approx(_r_multiple(WIN), 1.96)
    approx(_r_multiple(LOSS), -1.04)


def test_mfe_r():
    approx(_mfe_r(WIN), 2.0)      # MFE 4 จุด / 1R = 2 จุด
    approx(_mfe_r(LOSS), 1.5)     # MFE 6 จุด / 1R = 4 จุด


def test_adapter_converts_excursion_units():
    """demo เก็บ % ของ entry — backtest ใช้ % ของระยะ TP/SL"""
    w, l = to_metric_trades([WIN, LOSS])
    approx(w["mfe_pct_of_tp"], 1.0)     # แตะ TP พอดี
    approx(w["mae_pct_of_sl"], 0.5)     # ลงไปครึ่งทาง SL
    approx(l["mfe_pct_of_tp"], 0.75)    # เคยไป 75% ของทาง TP
    approx(l["mae_pct_of_sl"], 1.0)     # โดน SL เต็ม
    assert w["result"] == "win" and l["result"] == "loss"
    assert w["side"] == "LONG" and l["side"] == "SHORT"


def test_metrics_match_hand_calc():
    a = analyze([DRY, WIN, LOSS])
    m = a["overall"]
    assert m["num_trades"] == 2
    approx(m["winrate"], 50.0)
    # compound: (1+0.0392)(1-0.0208) - 1
    approx(m["total_return"], (1.0392 * 0.9792) - 1, tol=1e-4)
    approx(m["profit_factor"], 0.0392 / 0.0208, tol=1e-2)
    approx(m["expectancy"], 0.5 * 0.0392 + 0.5 * -0.0208, tol=1e-3)
    approx(a["avg_r"], (1.96 - 1.04) / 2, tol=1e-2)
    # ไม้แพ้เคยกำไร 75% ของ TP → near-miss (≥70%) = 100% ของไม้แพ้
    approx(m["avg_mfe_loss"], 0.75)
    approx(m["near_tp_miss_pct"], 100.0)


def test_breakdown_groups():
    b = breakdown([WIN, LOSS], lambda t: t["action"], "ทิศทาง")
    assert set(b["groups"]) == {"LONG", "SHORT"}
    approx(b["groups"]["LONG"]["winrate"], 100.0)
    approx(b["groups"]["SHORT"]["winrate"], 0.0)
    approx(b["groups"]["LONG"]["avg_r"], 1.96, tol=1e-2)


def test_data_quality_flags_bad_exit():
    bad = {**LOSS, "exit": 200.0}           # exit == entry → ดึงราคาปิดไม่ได้
    q = data_quality([DRY, WIN, bad], closed_trades([DRY, WIN, bad]))
    assert q["dry_run_records"] == 1
    assert q["exit_equals_entry"] == 1
    assert q["closed_trades"] == 2


def test_exit_whatif_counts():
    w = exit_management_whatif([WIN, LOSS])
    assert w["losses"] == 1 and w["wins"] == 1
    assert w["loss_reached_1r"] == 1        # LOSS เคยไป 1.5R
    assert w["wins_with_deep_mae"] == 0     # WIN MAE 50% < 70%


def test_exit_integrity_accepts_clean_sl_tp():
    """WIN ปิดที่ TP พอดี, LOSS ปิดที่ SL พอดี → ไม่ควรถูกจับว่าน่าสงสัย"""
    ei = exit_integrity([WIN, LOSS])
    assert ei["suspicious"] == 0, f"ไม้สะอาดถูกจับผิด: {ei['worst']}"
    assert ei["duplicate_exit_price"] == 0


def test_exit_integrity_flags_midway_exit():
    """ปิดกลางทาง (ไม่ใช่ทั้ง SL และ TP) = บันทึกผิด/ถูกตัดกลางทาง"""
    mid = {**WIN, "exit": 101.0, "pnl_pct": 0.0092}   # entry 100 SL 98 TP 104
    ei = exit_integrity([mid])
    assert ei["suspicious"] == 1
    approx(ei["worst"][0]["off_r"], 1.5)              # ห่างจาก TP 3 จุด = 1.5R
    assert ei["suspicious_reached_2r"] == 1           # MFE ถึง 2R → น่าจะได้ TP จริง


def test_exit_integrity_flags_duplicate_exit():
    """exit price ซ้ำเป๊ะข้ามไม้ = หยิบ fill ของไม้ก่อนหน้ามาใช้"""
    a = {**WIN, "exit": 104.0}
    b = {**WIN, "entry": 103.0, "sl": 101.0, "tp": 107.0, "exit": 104.0,
         "entry_time": "2026-07-03T10:00:00"}
    ei = exit_integrity([a, b])
    assert ei["duplicate_exit_price"] == 2


def test_empty_log():
    a = analyze([DRY])
    assert a.get("error"), "log ที่มีแต่ DRY_RUN ต้องคืน error"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✅ {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  ❌ {t.__name__}: {e}")
    print(f"\n{len(tests)-failed}/{len(tests)} ผ่าน")
    raise SystemExit(1 if failed else 0)
