"""
Live demo loop — เข้า trade เองบน Binance testnet ตาม active strategy

ใช้ params จาก strategy/active_params.json (strategy ที่ approve แล้ว)
DRY_RUN=True → log อย่างเดียว (ทดสอบก่อน)
"""
import time
from data.fetcher import fetch_ohlcv, fetch_htf_ohlcv
from analysis.indicators import add_indicators
from analysis.signals import generate_signal
from trading.executor import execute_signal
from strategy.params import load_active
from alerts.telegram import send_alert
from utils.logger import logger
from config import SYMBOL, DRY_RUN

POLL_INTERVAL = 60


def run():
    params = load_active()
    logger.info(f"Live Demo เริ่ม | {SYMBOL} | tf={params.timeframe} | DRY_RUN={DRY_RUN}")
    logger.info(f"Active params: {params.to_dict()}")
    last_candle = None

    while True:
        try:
            df = fetch_ohlcv(timeframe=params.timeframe)
            df_htf = fetch_htf_ohlcv(timeframe=params.timeframe)
            df = add_indicators(df, df_htf, params)
            sig = generate_signal(df, params)
            current = df.index[-1]

            if sig and current != last_candle:
                logger.info(f"พบสัญญาณ: {sig['signal']} @ {sig['price']}")
                result = execute_signal(sig)
                logger.info(f"execute result: {result.get('status')}")
                send_alert(sig)  # แจ้ง Telegram ด้วย
                last_candle = current
            else:
                logger.info(f"ไม่มีสัญญาณ | candle: {current}")

        except Exception as e:
            logger.error(f"Error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
