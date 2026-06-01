import ccxt
import pandas as pd
from config import BYBIT_API_KEY, BYBIT_SECRET_KEY, SYMBOL, TIMEFRAME, CANDLE_LIMIT

# เรียงตาม US-friendliness (Kraken/Coinbase = US-based, ไม่มี geo-block)
# ลองทีละตัวจนกว่าจะได้ — robust ไม่ว่า deploy region ไหน
EXCHANGE_PRIORITY = ["kraken", "coinbase", "kucoin", "gateio", "binance", "bybit"]

_exchange_cache = {}      # name -> ccxt instance
_working_name = None      # exchange ที่ใช้ได้ล่าสุด


def _get_instance(name: str):
    if name not in _exchange_cache:
        _exchange_cache[name] = getattr(ccxt, name)()
    return _exchange_cache[name]


def _ordered_names():
    """เริ่มจากตัวที่ใช้ได้ล่าสุดก่อน แล้วตามด้วยที่เหลือ"""
    if _working_name:
        return [_working_name] + [n for n in EXCHANGE_PRIORITY if n != _working_name]
    return list(EXCHANGE_PRIORITY)


def _fetch_with_fallback(method: str, *args, **kwargs):
    global _working_name
    last_err = None
    for name in _ordered_names():
        try:
            ex = _get_instance(name)
            result = getattr(ex, method)(*args, **kwargs)
            if _working_name != name:
                _working_name = name
                print(f"[fetcher] using exchange: {name}")
            return result
        except Exception as e:
            last_err = e
            if _working_name == name:
                _working_name = None  # ตัวที่เคยใช้ได้ down → reset
            continue
    raise RuntimeError(f"ทุก exchange ใช้ไม่ได้ — ตัวสุดท้าย: {last_err}")


def fetch_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT) -> pd.DataFrame:
    raw = _fetch_with_fallback("fetch_ohlcv", symbol, timeframe=timeframe, limit=limit)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df


def fetch_ticker(symbol=SYMBOL) -> dict:
    return _fetch_with_fallback("fetch_ticker", symbol)


def current_exchange() -> str:
    return _working_name or "—"


HTF_MAP = {"1m": "15m", "5m": "1h", "15m": "1h", "30m": "4h", "1h": "4h", "4h": "1d"}

def fetch_htf_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT) -> pd.DataFrame:
    htf = HTF_MAP.get(timeframe, "1h")
    return fetch_ohlcv(symbol=symbol, timeframe=htf, limit=limit)


def get_private_exchange():
    """สำหรับ Phase 2 — execute orders บน Bybit demo account"""
    return ccxt.bybit({
        "apiKey": BYBIT_API_KEY,
        "secret": BYBIT_SECRET_KEY,
        "options": {"defaultType": "linear"},
    })
