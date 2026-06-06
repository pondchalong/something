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
    execute_signal, get_open_position,
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
            sig = generate_signal(df, params)
            latest = df.iloc[-1]
            current = df.index[-1]

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
                logger.info(f"ถือ {open_trade['action']} | "
                            f"MFE {open_trade['mfe_price']:.2f} MAE {open_trade['mae_price']:.2f}")

            elif open_trade and not has_pos:
                # ไม้ปิดแล้ว (SL/TP โดน) → บันทึก outcome
                record_closed_trade(ex, open_trade)
                clear_open_trade()

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
