"""
ทดสอบ analysis/reconcile.py ด้วย fill สังเคราะห์ (offline — ไม่ต้องต่อ Binance)

รัน: py -3.12 test_reconcile.py
"""
from analysis.reconcile import (FETCH_WINDOW_MS, PAGE, fetch_fills,
                                group_into_trades, normalize_fill, reconcile)

T0 = 1_800_000_000_000
MIN = 60_000
DAY = 86_400_000


def fill(ts_min, side, price, qty, realized=0.0, comm=0.0):
    return {"ts": T0 + ts_min * MIN, "side": side, "price": price, "qty": qty,
            "realized_pnl": realized, "commission": comm, "order_id": f"o{ts_min}"}


def approx(a, b, tol=1e-6):
    assert abs(a - b) < tol, f"expected {b}, got {a}"


def test_normalize_reads_realized_pnl_from_info():
    """realizedPnl/commission อยู่ใน info ของ Binance ไม่ใช่ field ที่ ccxt normalize"""
    raw = {"timestamp": T0, "side": "SELL", "price": "104.0", "amount": "1.0", "order": "123",
           "info": {"realizedPnl": "4.0", "commission": "0.0416", "orderId": "123"}}
    f = normalize_fill(raw)
    approx(f["realized_pnl"], 4.0)
    approx(f["commission"], 0.0416)
    assert f["side"] == "sell" and f["order_id"] == "123"


def test_long_round_trip():
    fills = [fill(0, "buy", 100.0, 1.0, comm=0.04),
             fill(30, "sell", 104.0, 1.0, realized=4.0, comm=0.0416)]
    t, = group_into_trades(fills)
    assert t["side"] == "LONG"
    approx(t["entry"], 100.0)
    approx(t["exit"], 104.0)
    approx(t["net_pnl"], 4.0 - 0.0816)
    approx(t["pnl_pct"], (4.0 - 0.0816) / 100.0)
    assert not t.get("open_position")


def test_short_round_trip():
    fills = [fill(0, "sell", 100.0, 1.0, comm=0.04),
             fill(20, "buy", 98.0, 1.0, realized=2.0, comm=0.039)]
    t, = group_into_trades(fills)
    assert t["side"] == "SHORT"
    approx(t["entry"], 100.0)
    approx(t["exit"], 98.0)
    approx(t["net_pnl"], 2.0 - 0.079)


def test_partial_fills_weighted_average():
    """เข้า/ออกหลายไม้ย่อย → entry/exit ต้องถ่วงน้ำหนักด้วยขนาด"""
    fills = [fill(0, "buy", 100.0, 0.5, comm=0.02),
             fill(1, "buy", 102.0, 0.5, comm=0.0204),
             fill(30, "sell", 104.0, 1.0, realized=3.0, comm=0.0416)]
    t, = group_into_trades(fills)
    approx(t["entry"], 101.0)
    approx(t["exit"], 104.0)
    approx(t["qty"], 1.0)
    assert t["n_fills"] == 3


def test_scale_out_in_two_fills():
    """ปิดทีละครึ่ง → ยังนับเป็นไม้เดียว ปิดตอน position กลับเป็น 0"""
    fills = [fill(0, "buy", 100.0, 1.0),
             fill(10, "sell", 102.0, 0.5, realized=1.0),
             fill(20, "sell", 106.0, 0.5, realized=3.0)]
    t, = group_into_trades(fills)
    approx(t["exit"], 104.0)          # (102×0.5 + 106×0.5) / 1
    approx(t["net_pnl"], 4.0)


def test_two_separate_trades():
    fills = [fill(0, "buy", 100.0, 1.0), fill(10, "sell", 104.0, 1.0, realized=4.0),
             fill(60, "sell", 110.0, 1.0), fill(70, "buy", 108.0, 1.0, realized=2.0)]
    a, b = group_into_trades(fills)
    assert a["side"] == "LONG" and b["side"] == "SHORT"
    approx(a["exit"], 104.0)
    approx(b["entry"], 110.0)


def test_open_position_flagged():
    """ไม้ที่ยังถืออยู่ต้อง mark ไว้ ไม่เอาไปนับเป็นไม้ปิด"""
    fills = [fill(0, "buy", 100.0, 1.0), fill(10, "sell", 104.0, 1.0, realized=4.0),
             fill(60, "buy", 105.0, 1.0)]
    trades = group_into_trades(fills)
    assert len(trades) == 2
    assert trades[-1].get("open_position") is True


# ============================================================
# การดึง fill (จำลองพฤติกรรมจริงของ Binance)
# ============================================================
class FakeBinance:
    """
    เลียนแบบ /fapi/v1/userTrades ที่ทำให้ reconcile รอบแรกได้ข้อมูลไม่ครบ:
    - คืนเฉพาะ fill ในช่วง [since, endTime]
    - ถ้าไม่ส่ง endTime มา → บังคับเป็น since + 7 วัน (นี่คือกับดัก)
    - คืนสูงสุด 1000 รายการต่อครั้ง
    """
    def __init__(self, raw_fills):
        self.raw = sorted(raw_fills, key=lambda r: r["timestamp"])
        self.calls = []

    def fetch_my_trades(self, symbol, since=None, limit=None, params=None):
        params = params or {}
        end = int(params.get("endTime") or (since + 7 * DAY))
        self.calls.append((since, end))
        hit = [r for r in self.raw if since <= r["timestamp"] <= end]
        return hit[:limit or PAGE]


def _raw(ts, side="buy", price=100.0, qty=1.0, oid=None, tid=None):
    return {"id": tid or f"t{ts}", "timestamp": ts, "side": side, "price": price,
            "amount": qty, "order": oid or f"o{ts}",
            "info": {"realizedPnl": "0", "commission": "0"}}


def test_fetch_fills_keeps_identical_fills_of_same_order():
    """
    เคสบั๊กจริง: order เดียวแตกเป็นหลาย fill ที่ ราคา/ขนาด/เวลา เท่ากันเป๊ะ
    dedupe แบบเดิม (ไม่ใช้ trade id) ทิ้ง fill จริง → position ไม่กลับเป็น 0
    → ไม้ทั้งหมดถูกเหมารวมเป็นก้อนเดียว
    """
    same = [_raw(T0, qty=0.25, oid="o1", tid=f"t{i}") for i in range(4)]
    ex = FakeBinance(same + [_raw(T0 + MIN, "sell", 104.0, 1.0, oid="o2", tid="t9")])
    got = fetch_fills(ex, "BTC/USDT", T0, until_ms=T0 + DAY)
    assert len(got) == 5, f"ต้องเก็บ fill ครบ 5 ได้ {len(got)}"
    t, = group_into_trades(got)
    assert not t.get("open_position"), "position ต้องปิดสนิท"
    approx(t["qty"], 1.0)


def test_fetch_fills_spans_more_than_seven_days():
    """
    เคสบั๊กจริง: ขอ 60 วัน แต่ Binance คืนแค่ 7 วันแรกแล้วเงียบ
    → loop เดิม break ทิ้ง ได้ fill แค่ช่วงต้น (ตกไป 90%)
    """
    ex = FakeBinance([_raw(T0 + d * DAY) for d in range(60)])
    got = fetch_fills(ex, "BTC/USDT", T0, until_ms=T0 + 60 * DAY)
    assert len(got) == 60, f"ควรได้ครบ 60 fill ได้ {len(got)}"
    assert got[0]["ts"] == T0 and got[-1]["ts"] == T0 + 59 * DAY


def test_fetch_fills_no_duplicates_and_sorted():
    ex = FakeBinance([_raw(T0 + d * DAY) for d in range(30)])
    got = fetch_fills(ex, "BTC/USDT", T0, until_ms=T0 + 30 * DAY)
    keys = [(f["order_id"], f["ts"]) for f in got]
    assert len(keys) == len(set(keys)), "ห้ามมี fill ซ้ำจากหน้าต่างที่คาบเกี่ยวกัน"
    assert keys == sorted(keys, key=lambda k: k[1])


def test_fetch_fills_pages_within_one_window():
    """หน้าต่างเดียวมี fill เกิน 1000 → ต้องเลื่อน cursor ต่อ ไม่ใช่ข้ามหน้าต่าง"""
    fills = [_raw(T0 + i * 1000, oid=f"x{i}") for i in range(PAGE + 250)]
    ex = FakeBinance(fills)
    got = fetch_fills(ex, "BTC/USDT", T0, until_ms=T0 + FETCH_WINDOW_MS)
    assert len(got) == PAGE + 250, f"ควรได้ครบ {PAGE+250} ได้ {len(got)}"


def test_fetch_fills_stops_at_until():
    """ไม่ยิง request เลยเวลาที่ขอ (กันยิงถี่จนโดน rate limit)"""
    ex = FakeBinance([_raw(T0)])
    fetch_fills(ex, "BTC/USDT", T0, until_ms=T0 + 3 * DAY)
    assert len(ex.calls) == 1, f"3 วัน = 1 request ได้ {len(ex.calls)}"
    assert all(end <= T0 + 3 * DAY for _, end in ex.calls)


# ============================================================
def _api_trade(ts_min=0):
    return group_into_trades([fill(ts_min, "buy", 100.0, 1.0, comm=0.04),
                              fill(ts_min + 30, "sell", 104.0, 1.0,
                                   realized=4.0, comm=0.0416)])


def test_reconcile_matches_and_agrees():
    api = _api_trade()
    log = [{"action": "LONG", "entry": 100.0, "exit": 104.0, "pnl_pct": 0.0392,
            "entry_ms": T0, "entry_time": "2027-01-15T10:00:00"}]
    r = reconcile(api, log)
    assert r["matched"] == 1 and r["unmatched_log"] == 0
    assert r["exit_mismatch"] == 0, f"ควรตรงกัน ได้ {r['worst_exit_mismatch']}"
    assert r["pnl_mismatch"] == 0


def test_reconcile_flags_wrong_exit_price():
    """เคสบั๊กจริง: log บันทึก exit เป็นราคาของไม้ก่อนหน้า"""
    api = _api_trade()
    log = [{"action": "LONG", "entry": 100.0, "exit": 62122.2, "pnl_pct": -0.0315,
            "entry_ms": T0, "entry_time": "2027-01-15T10:00:00"}]
    r = reconcile(api, log)
    assert r["matched"] == 1
    assert r["exit_mismatch"] == 1
    assert r["pnl_mismatch"] == 1
    assert r["worst_exit_mismatch"][0]["real_exit"] == 104.0


def test_reconcile_flags_phantom_log_trade():
    """log บันทึกไม้ปิด แต่ exchange ไม่มีไม้นั้นเลย = ไม้ผีจากบั๊ก query position"""
    api = _api_trade()
    log = [
        {"action": "LONG", "entry": 100.0, "exit": 104.0, "pnl_pct": 0.0392, "entry_ms": T0},
        {"action": "LONG", "entry": 999.0, "exit": 990.0, "pnl_pct": -0.01,
         "entry_ms": T0 + 500 * MIN},
    ]
    r = reconcile(api, log)
    assert r["matched"] == 1
    assert r["unmatched_log"] == 1


def test_reconcile_flags_missing_log_trade():
    """exchange มีไม้ แต่ bot ไม่ได้บันทึก"""
    api = _api_trade(0) + _api_trade(200)
    log = [{"action": "LONG", "entry": 100.0, "exit": 104.0, "pnl_pct": 0.0392, "entry_ms": T0}]
    r = reconcile(api, log)
    assert r["matched"] == 1
    assert r["unmatched_api"] == 1


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
