"""
ทดสอบพื้น SL ขั้นต่ำ (params.min_sl_pct) + เพดาน notional ของ executor — offline

ที่มา: ข้อมูลจริง 28 ก.ค.–15 ส.ค. 2026 มี 7/26 ไม้ที่ตั้ง SL/TP ไม่ได้เลย (Binance
reject -2021) แล้ว executor ปิด position ทิ้ง = เสีย fee ฟรี 102.71 USDT
เกิดตอน ATR ต่ำมาก (5 bps) → SL แคบจนชิด mark price + size ระเบิดเป็น leverage 15x

รัน: py -3.12 test_sl_floor.py
"""
from analysis.signals import sl_tp_distances
from strategy.params import StrategyParams

P = StrategyParams(atr_multiplier=1.5, risk_reward=2.0, min_sl_pct=0.0015)


def approx(a, b, tol=1e-6):
    assert abs(a - b) < tol, f"expected {b}, got {a}"


# ============================================================
# พื้น SL
# ============================================================
def test_normal_atr_untouched():
    """ATR ปกติ (0.25% ของราคา) → พื้นไม่ควรเข้ามายุ่ง"""
    sl, tp = sl_tp_distances(63000.0, 105.0, P)     # 1.5×105 = 157.5 = 0.25%
    approx(sl, 157.5)
    approx(tp, 315.0)


def test_tiny_atr_lifted_to_floor():
    """เคสจริง 1 ส.ค.: ATR 33 จุด (5.3 bps) → SL 49.9 จุด ต้องถูกดันเป็น 0.15%"""
    sl, tp = sl_tp_distances(63000.0, 33.26, P)
    approx(sl, 63000.0 * 0.0015)                    # 94.5
    assert sl > 33.26 * 1.5, "ต้องกว้างกว่าที่ ATR ให้"


def test_rr_preserved_when_floor_applies():
    """พื้นดัน SL ออก → TP ต้องขยับตาม R:R เดิม ไม่ใช่ค้างที่เดิม"""
    sl, tp = sl_tp_distances(63000.0, 10.0, P)
    approx(tp / sl, P.risk_reward)


def test_floor_caps_fee_per_r():
    """
    ประเด็นเชิงเศรษฐศาสตร์: fee/R = 0.08% / (SL เป็นสัดส่วนของราคา)
    พื้น 0.15% → fee ต้องไม่เกิน 0.53R ต่อให้ ATR เล็กแค่ไหน
    """
    for atr in (1.0, 5.0, 20.0, 33.26):
        sl, _ = sl_tp_distances(63000.0, atr, P)
        fee_r = 0.0008 / (sl / 63000.0)
        assert fee_r <= 0.54, f"ATR {atr}: fee {fee_r:.2f}R เกินเพดาน"


def test_floor_caps_leverage():
    """size = risk/sl_dist → พื้น 0.15% ทำให้ leverage ไม่เกิน ~6.7x (risk 1%)"""
    sl, _ = sl_tp_distances(63000.0, 1.0, P)
    lev = (0.01 / (sl / 63000.0))
    assert lev <= 6.7, f"leverage {lev:.1f}x เกินเพดาน"


def test_zero_floor_is_old_behaviour():
    """min_sl_pct=0 → ผลเท่าเดิมเป๊ะ (ไว้ reproduce backtest เก่า)"""
    old = StrategyParams(atr_multiplier=1.5, risk_reward=2.0, min_sl_pct=0.0)
    sl, tp = sl_tp_distances(63000.0, 5.0, old)
    approx(sl, 7.5)
    approx(tp, 15.0)


def test_default_params_have_floor_on():
    """default ต้องเปิดพื้นไว้ — deploy ใหม่ที่ไม่มี active_params.json ก็ต้องปลอดภัย"""
    assert StrategyParams().min_sl_pct > 0


# ============================================================
# เพดาน notional ของ executor
# ============================================================
class FakeEx:
    """exchange ปลอมพอให้ execute_signal เดินถึงจุดคำนวณ size แล้วหยุด"""
    def __init__(self, balance):
        self.balance = balance
        self.orders = []

    def load_markets(self): pass
    def fetch_positions(self, symbols): return []
    def fetch_balance(self): return {"USDT": {"free": self.balance}}
    def amount_to_precision(self, symbol, amt): return f"{float(amt):.4f}"

    def create_order(self, symbol, type_, side, amount, price=None, params=None):
        self.orders.append({"type": type_, "side": side, "amount": amount})
        raise RuntimeError("หยุดหลังบันทึก size — ไม่ต้องเดินต่อ")


def _size_from(sl_distance, balance=2400.0, price=63000.0):
    import trading.executor as ex_mod
    fake = FakeEx(balance)
    ex_mod.get_testnet_exchange = lambda: fake
    ex_mod.DRY_RUN = False
    sig = {"signal": "LONG", "price": price, "sl": price - sl_distance,
           "tp": price + sl_distance * 2}
    try:
        ex_mod.execute_signal(sig)
    except Exception:
        pass
    return fake.orders[0]["amount"] if fake.orders else None


def test_executor_caps_notional():
    """SL แคบผิดปกติ (0.02%) → size ต้องโดนตัดที่ MAX_NOTIONAL_MULT ไม่ใช่ปล่อยระเบิด"""
    from config import MAX_NOTIONAL_MULT
    size = _size_from(sl_distance=12.6)             # 0.02% ของ 63000
    assert size is not None, "ควรยิง order (โดน cap) ไม่ใช่ข้ามไม้"
    lev = size * 63000.0 / 2400.0
    assert lev <= MAX_NOTIONAL_MULT + 0.01, f"leverage {lev:.1f}x เกินเพดาน"


def test_executor_leaves_normal_size_alone():
    """SL ปกติ → size ต้องเท่าสูตรเดิม (risk/sl_dist) ไม่โดนแตะ"""
    size = _size_from(sl_distance=157.5)
    approx(size, round(2400.0 * 0.01 / 157.5, 4), tol=1e-4)


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
