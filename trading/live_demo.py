"""
Live demo loop — เข้า trade เองบน Binance testnet + track ผลละเอียด

State machine (real mode):
- ถือ position อยู่     → update MFE/MAE จาก candle ล่าสุด
- position หายไป (ปิด)  → บันทึก outcome (win/loss, PnL, MFE/MAE) ลง trade_log
- ว่าง + มี signal      → execute + save open_trade

DRY_RUN=True → log signal เฉยๆ (ไม่มี position จริงให้ track)
ใช้ params จาก strategy/active_params.json
"""
import time
from data.fetcher import fetch_ohlcv, fetch_htf_ohlcv, get_testnet_exchange
from analysis.indicators import add_indicators
from analysis.signals import generate_signal
from trading.executor import (
    execute_signal, get_open_position, close_position, add_to_position,
    load_open_trade, save_open_trade, clear_open_trade,
    new_open_trade, update_excursion, record_closed_trade,
)
from strategy.params import load_active
from alerts.telegram import send_alert
from utils.logger import logger
from config import SYMBOL, DRY_RUN

POLL_INTERVAL = 60


def run():
    params = load_active()
    logger.info(f"Live Demo เริ่ม | {SYMBOL} | tf={params.timeframe} | DRY_RUN={DRY_RUN}")
    logger.info(f"Active params: {params.to_dict()}")

    ex = None
    if not DRY_RUN:
        ex = get_testnet_exchange()
        ex.load_markets()

    last_candle = None

    while True:
        try:
            df = fetch_ohlcv(timeframe=params.timeframe)
            df_htf = fetch_htf_ohlcv(timeframe=params.timeframe)
            df = add_indicators(df, df_htf, params)

            # ใช้แท่ง "ปิดแล้ว" (iloc[:-1]) สำหรับ signal — แท่ง iloc[-1] ยังก่อตัว
            # (indicator ไม่ final → signal กระพริบ + พลาด signal ตอนแท่งปิด)
            # ตรงกับ backtest/replay ที่ใช้แท่งปิด
            df_closed = df.iloc[:-1]
            sig = generate_signal(df_closed, params)
            current = df_closed.index[-1]   # timestamp แท่งปิดล่าสุด
            latest = df.iloc[-1]            # แท่งปัจจุบัน (real-time) — ใช้ track MFE/MAE

            # --- DRY_RUN: log signal เฉยๆ ---
            if DRY_RUN:
                if sig and current != last_candle:
                    logger.info(f"พบสัญญาณ: {sig['signal']} @ {sig['price']}")
                    execute_signal(sig)
                    send_alert(sig)
                    last_candle = current
                else:
                    logger.info(f"ไม่มีสัญญาณ | candle: {current}")
                time.sleep(POLL_INTERVAL)
                continue

            # --- REAL mode: track lifecycle ---
            open_trade = load_open_trade()
            has_pos = get_open_position(ex, SYMBOL) is not None

            if open_trade and has_pos:
                # ถือไม้อยู่ → update MFE/MAE
                update_excursion(open_trade, latest["high"], latest["low"])
                save_open_trade(open_trade)

                # reverse: signal กลับข้าง + เปิด params.reverse → ปิดเดิม + เปิดตรงข้าม
                if (params.reverse and sig and sig["signal"] != open_trade["action"]
                        and current != last_candle):
                    logger.info(f"REVERSE: ปิด {open_trade['action']} → เปิด {sig['signal']}")
                    exit_price = close_position(ex, SYMBOL)
                    record_closed_trade(ex, open_trade, exit_price=exit_price, exit_reason="reverse")
                    clear_open_trade()
                    result = execute_signal(sig)
                    if result.get("status") == "executed":
                        save_open_trade(new_open_trade(sig, result.get("size")))
                        send_alert(sig)
                        logger.info("REVERSE สำเร็จ — เปิดไม้ตรงข้าม")
                    else:
                        logger.warning(f"REVERSE: เปิดไม้ใหม่ไม่สำเร็จ: {result.get('status')}")
                    last_candle = current
                elif (params.max_pyramid > 1 and sig and sig["signal"] == open_trade["action"]
                      and open_trade.get("n_levels", 1) < params.max_pyramid
                      and current != last_candle):
                    # pyramid: signal เดิมทาง + ยังไม่ถึง max → เพิ่มไม้ (เฉลี่ย entry)
                    logger.info(f"PYRAMID: +{sig['signal']} level {open_trade.get('n_levels',1)+1}")
                    info = add_to_position(ex, sig, params.max_pyramid)
                    if info.get("status") == "added":
                        open_trade["n_levels"] = open_trade.get("n_levels", 1) + 1
                        open_trade["entry"] = info["avg_entry"]
                        open_trade["sl"] = info["sl"]
                        open_trade["tp"] = info["tp"]
                        open_trade["size"] = info["size"]
                        save_open_trade(open_trade)
                        send_alert(sig)
                        logger.info(f"PYRAMID สำเร็จ — level {open_trade['n_levels']}")
                    else:
                        logger.warning(f"PYRAMID add ไม่สำเร็จ: {info.get('status')} {info.get('reason','')}")
                    last_candle = current
                else:
                    logger.info(f"ถือ {open_trade['action']} | "
                                f"MFE {open_trade['mfe_price']:.2f} MAE {open_trade['mae_price']:.2f}")

            elif open_trade and not has_pos:
                # ไม้ปิดแล้ว (SL/TP โดน) → บันทึก outcome
                record_closed_trade(ex, open_trade)
                clear_open_trade()

            elif has_pos and not open_trade:
                # orphan position — มี position แต่ไม่มี state (เปิดก่อน tracking /
                # SL-TP ตั้งไม่ได้ / state หาย) → ปิดทิ้ง กัน deadlock + position เปลือย
                logger.warning("พบ orphan position (ไม่มี open_trade state) → ปิดทิ้ง")
                p = get_open_position(ex, SYMBOL)
                try:
                    c = abs(float(p["contracts"]))
                    cs = "sell" if p["side"] == "long" else "buy"
                    ex.create_order(SYMBOL, "market", cs, c, None, {"reduceOnly": True})
                    logger.info("ปิด orphan สำเร็จ — รอบหน้าเปิดไม้ใหม่ได้")
                except Exception as e:
                    logger.error(f"ปิด orphan ไม่ได้: {str(e)[:60]}")

            elif not has_pos and sig and current != last_candle:
                # ว่าง + มี signal → เปิดใหม่
                logger.info(f"พบสัญญาณ: {sig['signal']} @ {sig['price']}")
                result = execute_signal(sig)
                if result.get("status") == "executed":
                    save_open_trade(new_open_trade(sig, result.get("size")))
                    send_alert(sig)
                    logger.info("เปิดไม้ + เริ่ม track MFE/MAE")
                else:
                    logger.warning(f"execute ไม่สำเร็จ: {result.get('status')} {result.get('reason','')}")
                last_candle = current
            else:
                logger.info(f"idle | candle: {current} | pos={has_pos}")

        except Exception as e:
            logger.error(f"Error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
