import pandas as pd
from config import ATR_MULTIPLIER, RISK_REWARD_RATIO


def _confluence_score(signal_type: str, row: pd.Series) -> int:
    """
    นับ confluence จากทุก indicator
    ยิ่งสูง = สัญญาณน่าเชื่อถือมากขึ้น (max ~8)
    """
    score = 0
    is_long = signal_type == "LONG"

    # EMA trend
    if is_long and row.get("ema20", 0) > row.get("ema50", 0):
        score += 1
    elif not is_long and row.get("ema20", 0) < row.get("ema50", 0):
        score += 1

    # RSI zone
    rsi = row.get("rsi", 50)
    if is_long and 30 < rsi < 60:
        score += 1
    elif not is_long and 40 < rsi < 70:
        score += 1

    # MACD histogram direction
    hist = row.get("macd_hist", 0)
    if is_long and hist > 0:
        score += 1
    elif not is_long and hist < 0:
        score += 1

    # SuperTrend direction
    st_dir = row.get("supertrend_dir", 0)
    if is_long and st_dir == 1:
        score += 1
    elif not is_long and st_dir == -1:
        score += 1

    # VIDYA trend
    vidya = row.get("vidya", None)
    close = row.get("close", 0)
    if vidya is not None and not pd.isna(vidya):
        if is_long and close > vidya:
            score += 1
        elif not is_long and close < vidya:
            score += 1

    # HTF MACD (ถ้ามี)
    htf_bull = row.get("htf_macd_bull", None)
    if htf_bull is not None and not pd.isna(htf_bull):
        if is_long and htf_bull:
            score += 1
        elif not is_long and not htf_bull:
            score += 1

    # SMC structure
    choch = row.get("choch", None)
    bos = row.get("bos", None)
    if is_long and (choch == "BULL" or bos == "BULL"):
        score += 1
    elif not is_long and (choch == "BEAR" or bos == "BEAR"):
        score += 1

    return score


def _winrate_from_score(score: int, max_score: int = 8) -> float:
    pct = score / max_score
    return round(40 + pct * 45, 1)  # 40%–85%


def _risk_level(row: pd.Series) -> str:
    volatility = row.get("volatility", "MEDIUM")
    rsi = row.get("rsi", 50)
    if volatility == "HIGH" or rsi > 75 or rsi < 25:
        return "HIGH"
    elif volatility == "LOW" and 35 < rsi < 65:
        return "LOW"
    return "MEDIUM"


def generate_signal(df: pd.DataFrame) -> dict | None:
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    close = latest["close"]
    ema20 = latest.get("ema20", None)
    ema50 = latest.get("ema50", None)
    macd_hist = latest.get("macd_hist", 0)
    prev_macd_hist = prev.get("macd_hist", 0)
    st_dir = latest.get("supertrend_dir", 0)
    prev_st_dir = prev.get("supertrend_dir", 0)
    rsi = latest.get("rsi", 50)
    atr = latest.get("atr", 0)
    st_flip_bull = latest.get("st_flip_bull", False)
    st_flip_bear = latest.get("st_flip_bear", False)

    signal_type = None

    # Primary trigger: SuperTrend flip (แรงสุด)
    if st_flip_bull and rsi < 70:
        signal_type = "LONG"
    elif st_flip_bear and rsi > 30:
        signal_type = "SHORT"

    # Secondary trigger: MACD histogram cross + EMA alignment
    elif (ema20 is not None and ema50 is not None
          and macd_hist > 0 and prev_macd_hist <= 0
          and ema20 > ema50 and rsi < 65):
        signal_type = "LONG"
    elif (ema20 is not None and ema50 is not None
          and macd_hist < 0 and prev_macd_hist >= 0
          and ema20 < ema50 and rsi > 35):
        signal_type = "SHORT"

    if not signal_type:
        return None

    score = _confluence_score(signal_type, latest)
    # กรองสัญญาณคุณภาพต่ำ (confluence < 3 จาก 8)
    if score < 3:
        return None

    sl_distance = atr * ATR_MULTIPLIER
    tp_distance = sl_distance * RISK_REWARD_RATIO

    if signal_type == "LONG":
        sl = round(close - sl_distance, 2)
        tp = round(close + tp_distance, 2)
    else:
        sl = round(close + sl_distance, 2)
        tp = round(close - tp_distance, 2)

    return {
        "signal": signal_type,
        "price": close,
        "sl": sl,
        "tp": tp,
        "rr": RISK_REWARD_RATIO,
        "winrate": _winrate_from_score(score),
        "risk": _risk_level(latest),
        "confluence": score,
        "rsi": round(rsi, 1),
        "atr": round(atr, 2),
        "volatility": latest.get("volatility", "MEDIUM"),
        "st_dir": "BULL" if st_dir == 1 else "BEAR",
        "htf_macd": "BULL" if latest.get("htf_macd_bull") else "BEAR",
    }
