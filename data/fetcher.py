import ccxt
import pandas as pd
from config import BYBIT_API_KEY, BYBIT_SECRET_KEY, SYMBOL, TIMEFRAME, CANDLE_LIMIT


def get_public_exchange():
    """
    Public exchange — ไม่มี API key
    ใช้ดึง OHLCV / ticker เท่านั้น ไม่มี geo-restriction
    """
    return ccxt.bybit({
        "options": {"defaultType": "linear"},
    })


def get_private_exchange():
    """
    Private exchange — มี API key
    สำหรับ Phase 2 (execute orders บน demo account)
    """
    return ccxt.bybit({
        "apiKey": BYBIT_API_KEY,
        "secret": BYBIT_SECRET_KEY,
        "options": {"defaultType": "linear"},
    })


def fetch_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT) -> pd.DataFrame:
    exchange = get_public_exchange()
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df


def fetch_ticker(symbol=SYMBOL) -> dict:
    exchange = get_public_exchange()
    return exchange.fetch_ticker(symbol)


HTF_MAP = {"1m": "15m", "5m": "1h", "15m": "1h", "30m": "4h", "1h": "4h", "4h": "1d"}

def fetch_htf_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT) -> pd.DataFrame:
    htf = HTF_MAP.get(timeframe, "1h")
    return fetch_ohlcv(symbol=symbol, timeframe=htf, limit=limit)
