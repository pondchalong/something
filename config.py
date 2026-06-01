import os
from dotenv import load_dotenv

load_dotenv()

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.getenv("BINANCE_SECRET_KEY", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
TIMEFRAME = os.getenv("TIMEFRAME", "15m")

# จำนวน candle ที่ดึงมาคำนวณ indicator
CANDLE_LIMIT = 500  # ATR(200) ต้องการ 200+ candles

# Risk settings
RISK_REWARD_RATIO = 2.0    # TP = 2x SL
ATR_MULTIPLIER = 1.5       # SL = 1.5x ATR
