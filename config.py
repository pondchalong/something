import os
from dotenv import load_dotenv

load_dotenv()

BYBIT_API_KEY = os.getenv("BYBIT_API_KEY", "")
BYBIT_SECRET_KEY = os.getenv("BYBIT_SECRET_KEY", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SYMBOL = os.getenv("SYMBOL", "BTC/USDT")
TIMEFRAME = os.getenv("TIMEFRAME", "15m")

CANDLE_LIMIT = 500  # ATR(200) ต้องการ 200+ candles

RISK_REWARD_RATIO = 2.0
ATR_MULTIPLIER = 1.5

# --- Phase 2: Auto-execute (Binance testnet) ---
# .strip() กัน whitespace/newline ที่ติดมาตอน paste ใน Railway Variables
BINANCE_TESTNET_API_KEY = os.getenv("BINANCE_TESTNET_API_KEY", "").strip()
BINANCE_TESTNET_SECRET = os.getenv("BINANCE_TESTNET_SECRET", "").strip()
RISK_PER_TRADE = float(os.getenv("RISK_PER_TRADE", "0.01"))   # 1% ของ balance ต่อไม้
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"      # true = log อย่างเดียว ไม่ยิง order จริง

# Railway Volume (persistent disk) สำหรับ runtime data ที่ต้องเก็บถาวร เช่น trade_log
# ตั้ง DATA_DIR = mount path ของ volume (เช่น /data) → log ไม่หายเมื่อ redeploy
# ไม่ตั้ง (local) → ใช้ folder ของ module เอง
DATA_DIR = os.getenv("DATA_DIR", "").strip()
