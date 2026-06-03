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
TRADE_LOG = os.path.join(_LOG_DIR, "trade_log.json")

# Binance testnet ไม่เสถียร (502/timeout บ่อย) → retry transient errors
_TRANSIENT_KW = ("502", "503", "-1007", "timeout", "bad gateway", "backend", "temporarily")


def _is_transient(e) -> bool:
    if isinstance(e, ccxt.NetworkError):  # รวม ExchangeNotAvailable, RequestTimeout
        return True
    msg = str(e).lower()
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


def get_open_position(ex, symbol=SYMBOL):
    """คืน position ที่เปิดอยู่ (ถ้ามี) — None ถ้าไม่มี"""
    try:
        positions = ex.fetch_positions([symbol])
        for p in positions:
            contracts = float(p.get("contracts") or 0)
            if abs(contracts) > 0:
                return p
    except Exception as e:
        logger.warning(f"fetch_positions ไม่ได้: {e}")
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
        if not get_open_position(ex, symbol):
            logger.error(f"entry ไม่สำเร็จ (testnet down?): {str(e)[:80]}")
            return {"status": "failed", "reason": "entry_error"}
        logger.warning("entry timeout แต่ position เปิดจริง — ไปตั้ง SL/TP ต่อ")

    # ยืนยัน position เปิดจริงก่อนตั้ง SL/TP
    time.sleep(1)
    if not get_open_position(ex, symbol):
        logger.error("ไม่พบ position หลัง entry — ยกเลิก")
        return {"status": "failed", "reason": "no_position_after_entry"}

    # SL + TP (reduce-only). ถ้าตั้งไม่ได้ → ปิด position กันเปลือย (no SL/TP = อันตราย)
    try:
        _retry(lambda: ex.create_order(symbol, "STOP_MARKET", opp, size, None,
                                       {"stopPrice": signal["sl"], "reduceOnly": True}))
        _retry(lambda: ex.create_order(symbol, "TAKE_PROFIT_MARKET", opp, size, None,
                                       {"stopPrice": signal["tp"], "reduceOnly": True}))
    except Exception as e:
        logger.error(f"ตั้ง SL/TP ไม่ได้ → ปิด position กันเปลือย: {str(e)[:80]}")
        try:
            _retry(lambda: ex.create_order(symbol, "market", opp, size, None, {"reduceOnly": True}))
        except Exception as e2:
            logger.error(f"ปิด position ไม่ได้! เช็ค manual ด่วน: {str(e2)[:80]}")
        return {"status": "failed", "reason": "sltp_failed_closed"}

    entry = {
        "mode": "LIVE_DEMO", "action": signal["signal"], "symbol": symbol,
        "entry": signal["price"], "sl": signal["sl"], "tp": signal["tp"], "size": size,
    }
    logger.info(f"[LIVE_DEMO] เข้า {signal['signal']} {symbol} size={size} @ ~{signal['price']}")
    _append_log(entry)
    return {"status": "executed", **entry}


def load_trade_log() -> list:
    if os.path.exists(TRADE_LOG):
        with open(TRADE_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    return []
