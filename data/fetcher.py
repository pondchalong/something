import ccxt
import pandas as pd
from config import (
    BYBIT_API_KEY, BYBIT_SECRET_KEY, SYMBOL, TIMEFRAME, CANDLE_LIMIT,
    BINANCE_TESTNET_API_KEY, BINANCE_TESTNET_SECRET,
)

# Railway region = Southeast Asia → ทุก exchange ใช้ได้ (ไม่มี geo-block)
# Binance primary: liquidity สูงสุด + candle ไม่ cap
# ตัวที่เหลือ = fallback robustness ถ้า primary down
EXCHANGE_PRIORITY = ["binance", "bybit", "kraken", "coinbase", "kucoin", "gateio"]

_exchange_cache = {}      # name -> ccxt instance
_working_name = None      # exchange ที่ใช้ได้ล่าสุด


def _get_instance(name: str):
    # enableRateLimit: ให้ ccxt throttle เอง กัน 418/-1003 (IP ban) — IP เดียวโดนแบน
    # ทุก endpoint ของ exchange นั้น (public OHLCV โดนด้วย ไม่ใช่แค่ testnet)
    if name not in _exchange_cache:
        _exchange_cache[name] = getattr(ccxt, name)({"enableRateLimit": True})
    return _exchange_cache[name]


def _ordered_names():
    """เริ่มจากตัวที่ใช้ได้ล่าสุดก่อน แล้วตามด้วยที่เหลือ"""
    if _working_name:
        return [_working_name] + [n for n in EXCHANGE_PRIORITY if n != _working_name]
    return list(EXCHANGE_PRIORITY)


def _fetch_with_fallback(method: str, *args, preferred=None, **kwargs):
    """
    preferred=None → ลองตาม priority (fallback อัตโนมัติ)
    preferred="kraken" → บังคับใช้ตัวนั้นเท่านั้น, fail = error ชัดเจน
    """
    global _working_name

    if preferred:
        ex = _get_instance(preferred)
        result = getattr(ex, method)(*args, **kwargs)
        _working_name = preferred
        return result

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


def fetch_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT, exchange=None) -> pd.DataFrame:
    raw = _fetch_with_fallback("fetch_ohlcv", symbol, timeframe=timeframe, limit=limit, preferred=exchange)
    df = pd.DataFrame(raw, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
    df.set_index("timestamp", inplace=True)
    return df


def fetch_ticker(symbol=SYMBOL, exchange=None) -> dict:
    return _fetch_with_fallback("fetch_ticker", symbol, preferred=exchange)


def current_exchange() -> str:
    return _working_name or "—"


HTF_MAP = {"1m": "15m", "5m": "1h", "15m": "1h", "30m": "4h", "1h": "4h", "4h": "1d"}

def fetch_htf_ohlcv(symbol=SYMBOL, timeframe=TIMEFRAME, limit=CANDLE_LIMIT, exchange=None) -> pd.DataFrame:
    htf = HTF_MAP.get(timeframe, "1h")
    return fetch_ohlcv(symbol=symbol, timeframe=htf, limit=limit, exchange=exchange)


# fapi endpoints ที่ต้อง override เป็น testnet
_FAPI_KEYS = ["fapiPublic", "fapiPublicV2", "fapiPublicV3",
              "fapiPrivate", "fapiPrivateV2", "fapiPrivateV3", "fapiData"]

def get_testnet_exchange():
    """
    Phase 2 — execute orders บน Binance futures testnet (demo)
    ต้องตั้ง BINANCE_TESTNET_API_KEY/SECRET (ขอจาก testnet.binancefuture.com)

    หมายเหตุ: ccxt 4.5+ ตัด set_sandbox_mode สำหรับ futures (raise NotSupported)
    → override fapi endpoints เป็น testnet เอง + ปิด fetchCurrencies (เลี่ยง sapi
      ที่ไม่มี testnet URL). ใช้ binanceusdm (futures-only class)
    """
    # diagnostic: log ความยาว key (ไม่เปิด key เต็ม) — ช่วยเช็คว่า env ถูกตั้งไหม
    print(f"[testnet] api_key len={len(BINANCE_TESTNET_API_KEY)} "
          f"secret len={len(BINANCE_TESTNET_SECRET)}")
    if not BINANCE_TESTNET_API_KEY or not BINANCE_TESTNET_SECRET:
        raise ValueError(
            "BINANCE_TESTNET_API_KEY/SECRET ยังไม่ถูกตั้ง — เช็ค Railway Variables "
            "(ชื่อต้องตรงเป๊ะ ไม่มี quote/space)"
        )

    # enableRateLimit: ให้ ccxt throttle เอง กัน 418/-1003 "too many requests" (IP ban)
    ex = ccxt.binanceusdm({
        "apiKey": BINANCE_TESTNET_API_KEY,
        "secret": BINANCE_TESTNET_SECRET,
        "enableRateLimit": True,
        "options": {"defaultType": "future", "fetchCurrencies": False},
    })
    for k in _FAPI_KEYS:
        if k in ex.urls["test"]:
            ex.urls["api"][k] = ex.urls["test"][k]
    return ex


def get_private_exchange():
    """(สำรอง) Bybit demo — เผื่อสลับ exchange execute ภายหลัง"""
    return ccxt.bybit({
        "apiKey": BYBIT_API_KEY,
        "secret": BYBIT_SECRET_KEY,
        "options": {"defaultType": "linear"},
    })
