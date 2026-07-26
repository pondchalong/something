"""
Demo executor — เข้า trade บน Binance testnet ตาม signal

ปลอดภัย:
- DRY_RUN=True (default) → log อย่างเดียว ไม่ยิง order จริง (ทดสอบ logic ก่อน)
- 1 position ต่อครั้ง (เช็ค open position ก่อนเข้าใหม่)
- Position sizing risk-based: size = (balance × RISK_PER_TRADE) / sl_distance
"""
import json
import os
import time
from datetime import datetime

import ccxt

from config import SYMBOL, RISK_PER_TRADE, DRY_RUN, DATA_DIR
from data.fetcher import get_testnet_exchange
from utils.logger import logger

# DATA_DIR (Railway Volume) → log ถาวร; ไม่ตั้ง (local) → folder ของ module
_LOG_DIR = DATA_DIR if DATA_DIR else os.path.dirname(__file__)
os.makedirs(_LOG_DIR, exist_ok=True)
TRADE_LOG = os.path.join(_LOG_DIR, "trade_log.json")     # closed trades (มี outcome)
OPEN_TRADE = os.path.join(_LOG_DIR, "open_trade.json")   # ไม้ที่กำลังถือ (track MFE/MAE)
FEE = 0.0004  # 0.04% taker
# exit ห่างจาก SL/TP เกินกี่ R ถึงถือว่า "ไม่ได้จบที่ SL/TP" (เผื่อ slippage ปกติ)
_EXIT_TOL_R = 0.15

# Binance testnet ไม่เสถียร (502/timeout บ่อย) → retry transient errors
_TRANSIENT_KW = ("502", "503", "-1007", "timeout", "bad gateway", "backend", "temporarily")
# rate limit / IP ban (418 teapot, 429, -1003) → ห้าม retry: ยิ่ง retry ยิ่งโดนแบนนานขึ้น
_RATELIMIT_KW = ("-1003", "418", "429", "too many request", "ip banned", "way too many")


class PositionQueryError(RuntimeError):
    """
    query position ไม่สำเร็จ — **คนละเรื่องกับ "ไม่มี position"**

    เดิม get_open_position() คืน None ทั้งสองกรณี → caller เข้าใจผิดว่าไม้ปิดแล้ว
    ทั้งที่แค่ testnet ตอบไม่ได้ชั่วคราว → บันทึกไม้ปิดผิด + ปิดไม้ที่ยังดีอยู่ทิ้ง
    (ดู CLAUDE.md "exit price ที่บันทึกเชื่อไม่ได้")
    """


def _is_transient(e) -> bool:
    msg = str(e).lower()
    # rate-limit/ban มาก่อน: ccxt ห่อ 418/429 เป็น DDoSProtection (subclass NetworkError)
    # ถ้าปล่อยให้ retry จะยิงซ้ำตอนโดนแบน → ban นานขึ้น. ต้องหยุดยิงให้ ban หมดอายุเอง
    if isinstance(e, ccxt.DDoSProtection) or any(k in msg for k in _RATELIMIT_KW):
        return False
    if isinstance(e, ccxt.NetworkError):  # รวม ExchangeNotAvailable, RequestTimeout
        return True
    return any(k in msg for k in _TRANSIENT_KW)


def _retry(fn, tries: int = 3, delay: float = 2.0):
    """ลองซ้ำเฉพาะ transient error (testnet ล่ม/ช้า) — error อื่น raise ทันที"""
    last = None
    for i in range(tries):
        try:
            return fn()
        except Exception as e:
            if not _is_transient(e):
                raise
            last = e
            logger.warning(f"transient error (try {i+1}/{tries}): {str(e)[:70]}")
            time.sleep(delay)
    raise last


def _append_log(entry: dict) -> None:
    log = []
    if os.path.exists(TRADE_LOG):
        with open(TRADE_LOG, "r", encoding="utf-8") as f:
            log = json.load(f)
    entry["time"] = datetime.now().isoformat(timespec="seconds")
    log.append(entry)
    with open(TRADE_LOG, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=2)


def _cancel_all(ex, symbol=SYMBOL):
    """
    cancel ทั้ง regular + conditional orders
    สำคัญ: Binance futures SL/TP เป็น conditional order — cancel_all_orders ปกติ
    ไม่ลบ ต้องเรียกซ้ำด้วย params={'stop': True} (ไม่งั้น SL/TP ค้างสะสม)
    """
    for params in ({}, {"stop": True}):
        try:
            ex.cancel_all_orders(symbol, params=params)
        except Exception as e:
            logger.warning(f"cancel orders {params or 'regular'}: {str(e)[:40]}")


def cancel_open_orders(ex, symbol=SYMBOL):
    """
    ล้าง order ที่ค้างทั้งหมด — ต้องเรียก **ทุกครั้งที่ไม้ปิด**

    Binance ไม่ได้ยกเลิก SL/TP ฝั่งที่เหลือให้อัตโนมัติเสมอ: พอ SL ทำงาน order TP
    ของไม้นั้นอาจค้างอยู่ แล้วไปปิดไม้ถัดไปที่ราคาซึ่งไม่เกี่ยวกับไม้นั้นเลย
    """
    _cancel_all(ex, symbol)


def get_open_position(ex, symbol=SYMBOL):
    """
    คืน position ที่เปิดอยู่ — **None = ยืนยันแล้วว่าไม่มี position จริงๆ**

    query ไม่สำเร็จ → raise PositionQueryError (ไม่คืน None) เพราะ "ไม่รู้" กับ
    "ไม่มี" ต้องแยกกัน: testnet /fapi/v3/positionRisk 502/timeout บ่อย ถ้าคืน None
    เวลา query พลาด caller จะเข้าใจว่าไม้ปิดแล้ว → บันทึกผลผิด + ปิดไม้ที่ยังดีทิ้ง
    """
    try:
        positions = _retry(lambda: ex.fetch_positions([symbol]))
    except Exception as e:
        logger.warning(f"fetch_positions ไม่ได้ (หลัง retry): {str(e)[:80]}")
        raise PositionQueryError(str(e)[:120]) from e
    for p in positions:
        contracts = float(p.get("contracts") or 0)
        if abs(contracts) > 0:
            return p
    return None


def execute_signal(signal: dict, symbol=SYMBOL) -> dict:
    """
    รับ signal จาก generate_signal() → เข้า order บน testnet
    คืน dict สรุปผล (executed / skipped / dry_run)
    """
    side = "buy" if signal["signal"] == "LONG" else "sell"

    # --- DRY RUN: log อย่างเดียว ---
    if DRY_RUN:
        entry = {
            "mode": "DRY_RUN", "action": signal["signal"], "symbol": symbol,
            "entry": signal["price"], "sl": signal["sl"], "tp": signal["tp"],
            "winrate": signal.get("winrate"), "confluence": signal.get("confluence"),
        }
        logger.info(f"[DRY_RUN] {signal['signal']} {symbol} @ {signal['price']} "
                    f"SL={signal['sl']} TP={signal['tp']}")
        _append_log(entry)
        return {"status": "dry_run", **entry}

    # --- REAL ORDER (Binance testnet) ---
    ex = get_testnet_exchange()
    ex.load_markets()

    if get_open_position(ex, symbol):
        logger.info("มี position เปิดอยู่แล้ว — ข้าม signal นี้")
        return {"status": "skipped", "reason": "position_open"}

    # Position sizing (risk-based)
    balance = _retry(lambda: ex.fetch_balance()["USDT"]["free"])
    sl_distance = abs(signal["price"] - signal["sl"])
    if sl_distance <= 0:
        return {"status": "skipped", "reason": "invalid_sl"}
    raw_size = (balance * RISK_PER_TRADE) / sl_distance
    size = float(ex.amount_to_precision(symbol, raw_size))
    if size <= 0:
        return {"status": "skipped", "reason": "size_too_small"}

    opp = "sell" if side == "buy" else "buy"

    # Market entry (retry transient errors). ถ้า timeout = execution unknown →
    # verify ว่าเปิด position จริงไหม ก่อนตัดสิน (กันเปิดซ้ำ / position เปลือย)
    try:
        _retry(lambda: ex.create_order(symbol, "market", side, size))
    except Exception as e:
        time.sleep(1)
        try:
            opened = get_open_position(ex, symbol)
        except PositionQueryError:
            # ไม่รู้ว่า entry ติดไหม → ห้ามเดา. ถ้าติดจริง รอบหน้า live_demo จะเจอ
            # position ที่ไม่มี state = orphan แล้วปิดให้เอง (ปลอดภัยกว่าเดาว่าไม่ติด)
            logger.error(f"entry error + verify ไม่ได้ — รอ orphan cleanup: {str(e)[:60]}")
            return {"status": "failed", "reason": "entry_unverified"}
        if not opened:
            logger.error(f"entry ไม่สำเร็จ (testnet down?): {str(e)[:80]}")
            return {"status": "failed", "reason": "entry_error"}
        logger.warning("entry timeout แต่ position เปิดจริง — ไปตั้ง SL/TP ต่อ")

    # ยืนยัน position เปิดจริง + ดึง entry จริงก่อนตั้ง SL/TP
    time.sleep(1)
    try:
        pos = get_open_position(ex, symbol)
    except PositionQueryError:
        logger.error("verify position หลัง entry ไม่ได้ → ยังไม่ได้ตั้ง SL/TP! "
                     "รอบหน้า orphan cleanup จะปิดให้")
        return {"status": "failed", "reason": "verify_failed"}
    if not pos:
        logger.error("ไม่พบ position หลัง entry — ยกเลิก")
        return {"status": "failed", "reason": "no_position_after_entry"}

    # คำนวณ SL/TP จาก entry "จริง" ไม่ใช่ signal price:
    # market order fill หลัง signal ~1-2s → ราคาขยับ (slippage/drift) ทำให้ stopPrice
    # เดิมไปอยู่ผิดข้างของ mark price → Binance reject -2021 "would immediately trigger".
    # ยึด distance เดิม (R:R คงที่) แต่วัดจาก entry จริง → SL/TP อยู่ถูกข้างเสมอ
    entry_price = float(pos.get("entryPrice") or signal["price"])
    sl_dist = abs(signal["price"] - signal["sl"])
    tp_dist = abs(signal["tp"] - signal["price"])
    if signal["signal"] == "LONG":
        sl_price, tp_price = entry_price - sl_dist, entry_price + tp_dist
    else:
        sl_price, tp_price = entry_price + sl_dist, entry_price - tp_dist
    sl_price = float(ex.price_to_precision(symbol, sl_price))
    tp_price = float(ex.price_to_precision(symbol, tp_price))

    # SL + TP (reduce-only). ถ้าตั้งไม่ได้ → ปิด position กันเปลือย (no SL/TP = อันตราย)
    try:
        _retry(lambda: ex.create_order(symbol, "STOP_MARKET", opp, size, None,
                                       {"stopPrice": sl_price, "reduceOnly": True}))
        _retry(lambda: ex.create_order(symbol, "TAKE_PROFIT_MARKET", opp, size, None,
                                       {"stopPrice": tp_price, "reduceOnly": True}))
    except Exception as e:
        logger.error(f"ตั้ง SL/TP ไม่ได้ → ปิด position กันเปลือย: {str(e)[:80]}")
        try:
            close_position(ex, symbol)   # cancel conditional ที่ค้าง + market close
        except Exception as e2:
            logger.error(f"ปิด position ไม่ได้! เช็ค manual ด่วน: {str(e2)[:80]}")
        return {"status": "failed", "reason": "sltp_failed_closed"}

    entry = {
        "mode": "LIVE_DEMO", "action": signal["signal"], "symbol": symbol,
        "entry": entry_price, "sl": sl_price, "tp": tp_price, "size": size,
    }
    # ไม่ log ตอนเปิด — live_demo เก็บใน open_trade.json + record_closed_trade() log ตอนปิด
    # (มี outcome + MFE/MAE ครบ) กัน log ซ้ำ
    logger.info(f"[LIVE_DEMO] เข้า {signal['signal']} {symbol} size={size} @ ~{signal['price']}")
    return {"status": "executed", **entry}


def load_trade_log() -> list:
    if os.path.exists(TRADE_LOG):
        with open(TRADE_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


# ============================================================
# Trade lifecycle tracking (MFE/MAE + outcome)
# ============================================================
def load_open_trade():
    if os.path.exists(OPEN_TRADE):
        with open(OPEN_TRADE, "r", encoding="utf-8") as f:
            return json.load(f)
    return None


def save_open_trade(t: dict):
    with open(OPEN_TRADE, "w", encoding="utf-8") as f:
        json.dump(t, f, indent=2)


def clear_open_trade():
    if os.path.exists(OPEN_TRADE):
        os.remove(OPEN_TRADE)


def new_open_trade(signal: dict, size=None, entry=None, sl=None, tp=None) -> dict:
    """
    สร้าง state ของไม้ที่เพิ่งเปิด (MFE/MAE เริ่มที่ราคา entry)

    entry/sl/tp: ถ้าให้มา (จาก fill จริง ใน execute_signal) ใช้แทนค่าจาก signal —
    กัน slippage ทำให้ stats (MFE/MAE/pnl) + exit_reason เพี้ยนจากราคาที่เข้าจริง
    """
    entry_price = entry if entry is not None else signal["price"]
    return {
        "action": signal["signal"], "entry": entry_price,
        "sl": sl if sl is not None else signal["sl"],
        "tp": tp if tp is not None else signal["tp"], "size": size, "n_levels": 1,
        "entry_time": datetime.now().isoformat(timespec="seconds"),
        # epoch ms ตรงๆ — ใช้กรอง fill ตอนปิดไม้ ไม่ต้องเดา timezone ของ entry_time
        "entry_ms": int(time.time() * 1000),
        "mfe_price": entry_price, "mae_price": entry_price,
        "confluence": signal.get("confluence"), "winrate_est": signal.get("winrate"),
    }


def update_excursion(t: dict, high: float, low: float):
    """update ราคาที่ไปไกลสุดทั้งทางได้เปรียบ (MFE) และเสียเปรียบ (MAE)"""
    if t["action"] == "LONG":
        t["mfe_price"] = max(t["mfe_price"], high)   # ไปบวกสุด
        t["mae_price"] = min(t["mae_price"], low)    # ไปลบสุด
    else:  # SHORT
        t["mfe_price"] = min(t["mfe_price"], low)
        t["mae_price"] = max(t["mae_price"], high)


def _find_exit_fill(ex, t: dict, symbol=SYMBOL):
    """
    หา fill ที่ปิดไม้นี้จริง — ต้องเป็น fill ฝั่งตรงข้ามที่เกิด **หลังเปิดไม้** เท่านั้น

    เดิมหยิบ "fill ฝั่งตรงข้ามตัวล่าสุด" โดยไม่ดูเวลา → ถ้า fill ของไม้นี้ยังไม่ขึ้น
    (testnet ช้า) หรือไม้ยังไม่ปิดจริง จะได้ fill ของ **ไม้ก่อนหน้า** มาแทน
    → exit price ซ้ำข้ามไม้ + PnL เพี้ยน (ข้อมูลจริงเจอ 8 ไม้, ไม้นึงบันทึก -3.15R
    ทั้งที่ SL cap ไว้ ~-1.1R)

    คืน (price, n_fills) — (None, 0) ถ้าหาไม่เจอ: **ไม่เดา** ให้ caller mark ว่าเชื่อไม่ได้
    """
    entry_ms = t.get("entry_ms")
    if entry_ms is None:                     # ไม้เก่าที่บันทึกก่อนมี entry_ms
        try:
            entry_ms = int(datetime.fromisoformat(t["entry_time"]).timestamp() * 1000)
        except (ValueError, TypeError, KeyError):
            return None, 0
    since = int(entry_ms) - 60_000           # เผื่อ clock skew เล็กน้อย
    try:
        fills = _retry(lambda: ex.fetch_my_trades(symbol, since=since, limit=100))
    except Exception as e:
        logger.warning(f"ดึง fills ไม่ได้: {str(e)[:60]}")
        return None, 0

    exit_side = "sell" if t["action"] == "LONG" else "buy"
    cands = [f for f in fills
             if f.get("side") == exit_side and int(f.get("timestamp") or 0) >= since]
    if not cands:
        return None, 0
    # market close อาจแตกเป็นหลาย fill → ถ่วงน้ำหนักด้วยขนาด
    tot = sum(float(f.get("amount") or 0) for f in cands)
    if tot <= 0:
        return float(cands[-1]["price"]), len(cands)
    vwap = sum(float(f["price"]) * float(f.get("amount") or 0) for f in cands) / tot
    return vwap, len(cands)


def record_closed_trade(ex, t: dict, symbol=SYMBOL, exit_price=None, exit_reason=None) -> dict:
    """
    ไม้ปิดแล้ว → คำนวณ outcome + MFE/MAE% → บันทึก trade_log
    exit_price/exit_reason: ถ้าให้มา (เช่นตอน reverse) ใช้เลย; ไม่งั้นหาจาก fill จริง

    หา exit ไม่เจอ → บันทึกเป็น record ที่ **ไม่มี pnl_pct** + `exit_unreliable=True`
    (ดีกว่าเดาราคาแล้วปล่อยให้ตัวเลขเท็จไปปนในสถิติ — dashboard/trade_stats กรองทิ้งเอง)
    """
    entry = t["entry"]
    if exit_price is None:
        exit_price, n_fills = _find_exit_fill(ex, t, symbol)
        if exit_price is None:
            logger.error(f"หา exit fill ของไม้ {t['action']} @ {entry} ไม่เจอ "
                         f"→ บันทึกเป็น 'เชื่อไม่ได้' (ไม่นับในสถิติ)")
            closed = {
                "mode": "LIVE_DEMO", "action": t["action"], "symbol": symbol,
                "entry": round(entry, 2), "exit": None,
                "sl": t["sl"], "tp": t["tp"], "size": t.get("size"),
                "entry_time": t["entry_time"],
                "exit_time": datetime.now().isoformat(timespec="seconds"),
                "exit_reason": "unknown", "exit_unreliable": True,
                "confluence": t.get("confluence"),
            }
            _append_log(closed)
            return closed
        logger.info(f"exit fill: {exit_price:.2f} (จาก {n_fills} fill)")

    if t["action"] == "LONG":
        pnl_pct = (exit_price - entry) / entry
        mfe_pct = (t["mfe_price"] - entry) / entry   # บวก = ไปถูกทาง
        mae_pct = (t["mae_price"] - entry) / entry   # ลบ = ไปผิดทาง
    else:
        pnl_pct = (entry - exit_price) / entry
        mfe_pct = (entry - t["mfe_price"]) / entry
        mae_pct = (entry - t["mae_price"]) / entry
    pnl_pct -= 2 * FEE

    # exit_reason จากราคาจริง: ไม้ปกติต้องจบใกล้ SL หรือ TP — ถ้าไม่ใกล้ทั้งคู่
    # แปลว่าถูกปิดกลางทาง (orphan cleanup / order ของไม้เก่าค้าง) → ต้องรู้ ไม่ใช่เดาว่า SL
    sl_dist = abs(entry - t["sl"])
    off_sl = abs(exit_price - t["sl"]) / sl_dist if sl_dist > 0 else 99.0
    off_tp = abs(exit_price - t["tp"]) / sl_dist if sl_dist > 0 else 99.0
    off_r = round(min(off_sl, off_tp), 3)
    if exit_reason is None:
        if off_r > _EXIT_TOL_R:
            exit_reason = "other"
            logger.warning(f"exit {exit_price:.2f} ไม่ตรงทั้ง SL {t['sl']} และ TP {t['tp']} "
                           f"(ห่าง {off_r:.2f}R) — ไม้นี้ถูกปิดกลางทาง")
        else:
            exit_reason = "SL" if off_sl < off_tp else "TP"
    reason = exit_reason

    try:
        dur = (datetime.now() - datetime.fromisoformat(t["entry_time"])).total_seconds() / 60
    except Exception:
        dur = None

    closed = {
        "mode": "LIVE_DEMO", "action": t["action"], "symbol": symbol,
        "entry": round(entry, 2), "exit": round(exit_price, 2),
        "sl": t["sl"], "tp": t["tp"], "size": t.get("size"),
        "entry_time": t["entry_time"],
        "exit_time": datetime.now().isoformat(timespec="seconds"),
        "duration_min": round(dur, 1) if dur is not None else None,
        "exit_reason": reason, "won": bool(pnl_pct > 0),
        "pnl_pct": round(pnl_pct, 4),
        "mfe_pct": round(mfe_pct, 4), "mae_pct": round(mae_pct, 4),
        "confluence": t.get("confluence"),
        "exit_off_r": off_r,   # exit ห่างจาก SL/TP กี่ R (0 = ตรงเป๊ะ) — ใช้ตรวจคุณภาพข้อมูล
    }
    _append_log(closed)
    logger.info(f"[CLOSED] {t['action']} {'WIN' if closed['won'] else 'LOSS'} "
                f"pnl={pnl_pct*100:+.2f}% exit~{reason} "
                f"MFE={mfe_pct*100:+.2f}% MAE={mae_pct*100:+.2f}%")
    return closed


def close_position(ex, symbol=SYMBOL):
    """ปิด position ปัจจุบัน (market reduce-only) + cancel SL/TP orders ที่ค้าง → คืน exit price"""
    pos = get_open_position(ex, symbol)
    if not pos:
        return None
    c = abs(float(pos["contracts"]))
    side = "sell" if pos["side"] == "long" else "buy"
    _cancel_all(ex, symbol)
    _retry(lambda: ex.create_order(symbol, "market", side, c, None, {"reduceOnly": True}))
    # exit price จาก fill ล่าสุด
    try:
        fills = ex.fetch_my_trades(symbol, limit=5)
        for f in reversed(fills):
            if f.get("side") == side:
                return float(f["price"])
    except Exception:
        pass
    return None


def add_to_position(ex, signal: dict, max_pyramid: int, symbol=SYMBOL) -> dict:
    """
    Pyramid — เพิ่มไม้ทางเดียวกัน: market add (risk แบ่ง = RISK_PER_TRADE/max_pyramid)
    → recompute SL/TP จาก avg entry (position entryPrice หลัง add) ด้วย total size
    """
    side = "buy" if signal["signal"] == "LONG" else "sell"
    sl_dist = abs(signal["price"] - signal["sl"])
    tp_dist = abs(signal["tp"] - signal["price"])
    if sl_dist <= 0:
        return {"status": "skipped", "reason": "invalid_sl"}

    balance = _retry(lambda: ex.fetch_balance()["USDT"]["free"])
    add_size = (balance * (RISK_PER_TRADE / max_pyramid)) / sl_dist
    add_size = float(ex.amount_to_precision(symbol, add_size))
    if add_size <= 0:
        return {"status": "skipped", "reason": "size_too_small"}

    _retry(lambda: ex.create_order(symbol, "market", side, add_size))

    try:
        pos = get_open_position(ex, symbol)
    except PositionQueryError:
        logger.error("pyramid: verify position ไม่ได้ → SL/TP ยังเป็นของ size เดิม")
        return {"status": "failed", "reason": "verify_failed"}
    if not pos:
        return {"status": "failed", "reason": "no_position_after_add"}
    avg = float(pos["entryPrice"])
    total_size = abs(float(pos["contracts"]))

    if signal["signal"] == "LONG":
        new_sl, new_tp = avg - sl_dist, avg + tp_dist
    else:
        new_sl, new_tp = avg + sl_dist, avg - tp_dist

    opp = "sell" if side == "buy" else "buy"
    try:
        _cancel_all(ex, symbol)
        _retry(lambda: ex.create_order(symbol, "STOP_MARKET", opp, total_size, None,
                                       {"stopPrice": round(new_sl, 2), "reduceOnly": True}))
        _retry(lambda: ex.create_order(symbol, "TAKE_PROFIT_MARKET", opp, total_size, None,
                                       {"stopPrice": round(new_tp, 2), "reduceOnly": True}))
    except Exception as e:
        logger.error(f"pyramid: ตั้ง SL/TP ใหม่ไม่ได้ → ปิด position กันเปลือย: {str(e)[:60]}")
        close_position(ex, symbol)
        return {"status": "failed", "reason": "sltp_failed_closed"}

    logger.info(f"[PYRAMID] +{signal['signal']} avg={avg:.2f} size={total_size} SL={new_sl:.2f} TP={new_tp:.2f}")
    return {"status": "added", "avg_entry": avg, "size": total_size, "sl": new_sl, "tp": new_tp}
