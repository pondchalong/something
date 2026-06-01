import ccxt
import pandas as pd
from config import BINANCE_API_KEY, BINANCE_SECRET_KEY, SYMBOL, TIMEFRAME, CANDLE_LIMIT


def get_exchange():
    exchange = ccxt.binance({
        "apiKey": BINANCE_API_KEY,
        "secret": BINANCE_SECRET_KEY,
        "options": {"defaultType": "future"},
    })
    exchange.set_sandbox_mode(True)  # ใช้ testnet
    return exchange


def fetch_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT) -> pd.DataFrame:
    exchange = get_exchange()
    raw = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df


def fetch_ticker(symbol=SYMBOL) -> dict:
    exchange = get_exchange()
    return exchange.fetch_ticker(symbol)


HTF_MAP = {"1m": "15m", "5m": "1h", "15m": "1h", "30m": "4h", "1h": "4h", "4h": "1d"}

def fetch_htf_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT) -> pd.DataFrame:
    htf = HTF_MAP.get(timeframe, "1h")
    return fetch_ohlcv(symbol=symbol, timeframe=htf, limit=limit)
