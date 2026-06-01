"""
Demo executor — เข้า trade บน Binance testnet ตาม signal

ปลอดภัย:
- DRY_RUN=True (default) → log อย่างเดียว ไม่ยิง order จริง (ทดสอบ logic ก่อน)
- 1 position ต่อครั้ง (เช็ค open position ก่อนเข้าใหม่)
- Position sizing risk-based: size = (balance × RISK_PER_TRADE) / sl_distance
"""
import json
import os
from datetime import datetime

from config import SYMBOL, RISK_PER_TRADE, DRY_RUN
from data.fetcher import get_testnet_exchange
from utils.logger import logger

TRADE_LOG = os.path.join(os.path.dirname(__file__), "trade_log.json")


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

    if get_open_position(ex, symbol):
        logger.info("มี position เปิดอยู่แล้ว — ข้าม signal นี้")
        return {"status": "skipped", "reason": "position_open"}

    # Position sizing (risk-based)
    balance = ex.fetch_balance()["USDT"]["free"]
    sl_distance = abs(signal["price"] - signal["sl"])
    if sl_distance <= 0:
        return {"status": "skipped", "reason": "invalid_sl"}
    risk_amount = balance * RISK_PER_TRADE
    raw_size = risk_amount / sl_distance
    size = float(ex.amount_to_precision(symbol, raw_size))
    if size <= 0:
        return {"status": "skipped", "reason": "size_too_small"}

    opp = "sell" if side == "buy" else "buy"

    # Market entry
    order = ex.create_order(symbol, "market", side, size)
    # SL (stop-market, reduce-only) + TP (take-profit-market, reduce-only)
    ex.create_order(symbol, "STOP_MARKET", opp, size,
                    params={"stopPrice": signal["sl"], "reduceOnly": True})
    ex.create_order(symbol, "TAKE_PROFIT_MARKET", opp, size,
                    params={"stopPrice": signal["tp"], "reduceOnly": True})

    entry = {
        "mode": "LIVE_DEMO", "action": signal["signal"], "symbol": symbol,
        "entry": signal["price"], "sl": signal["sl"], "tp": signal["tp"],
        "size": size, "order_id": order.get("id"),
    }
    logger.info(f"[LIVE_DEMO] เข้า {signal['signal']} {symbol} size={size} @ ~{signal['price']}")
    _append_log(entry)
    return {"status": "executed", **entry}


def load_trade_log() -> list:
    if os.path.exists(TRADE_LOG):
        with open(TRADE_LOG, "r", encoding="utf-8") as f:
            return json.load(f)
    return []
