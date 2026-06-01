import time
from data.fetcher import fetch_ohlcv, fetch_htf_ohlcv
from analysis.indicators import add_indicators
from analysis.signals import generate_signal
from alerts.telegram import send_alert
from utils.logger import logger
from config import SYMBOL, TIMEFRAME

# รอกี่วินาทีระหว่างแต่ละรอบ (15m candle = เช็กทุก 60 วินาที)
POLL_INTERVAL = 60


def run():
    logger.info(f"เริ่ม Signal Bot | {SYMBOL} | {TIMEFRAME}")
    last_signal_time = None

    while True:
        try:
            df = fetch_ohlcv()
            df_htf = fetch_htf_ohlcv()
            df = add_indicators(df, df_htf)
            signal = generate_signal(df)

            current_candle = df.index[-1]

            if signal and current_candle != last_signal_time:
                logger.info(f"พบสัญญาณ: {signal['signal']} @ {signal['price']}")
                sent = send_alert(signal)
                if sent:
                    logger.info("ส่ง Telegram สำเร็จ")
                else:
                    logger.warning("ส่ง Telegram ไม่สำเร็จ — เช็ก token/chat_id")
                last_signal_time = current_candle
            else:
                logger.info(f"ไม่มีสัญญาณ | candle: {current_candle} | RSI: {df['rsi'].iloc[-1]:.1f}")

        except Exception as e:
            logger.error(f"Error: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
