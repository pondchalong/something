import pandas as pd
import pandas_ta as ta
from analysis.indicators_advanced import (
    calc_vidya_full,
    calc_adaptive_supertrend,
    calc_smc,
    calc_mtf_macd,
    align_htf_to_ltf,
)
from strategy.params import DEFAULT_PARAMS


def add_indicators(df: pd.DataFrame, df_htf: pd.DataFrame = None, params=DEFAULT_PARAMS) -> pd.DataFrame:
    # EMA: trend direction (column ชื่อ ema20/ema50 คงเดิม, length มาจาก params)
    df["ema20"] = ta.ema(df["close"], length=params.ema_fast)
    df["ema50"] = ta.ema(df["close"], length=params.ema_slow)

    # RSI: overbought/oversold
    df["rsi"] = ta.rsi(df["close"], length=params.rsi_len)

    # MACD: momentum
    macd = ta.macd(df["close"], fast=12, slow=26, signal=9)
    df["macd"] = macd["MACD_12_26_9"]
    df["macd_signal"] = macd["MACDs_12_26_9"]
    df["macd_hist"] = macd["MACDh_12_26_9"]

    # Bollinger Bands: volatility
    bb = ta.bbands(df["close"], length=20, std=2)
    df["bb_upper"] = bb["BBU_20_2.0_2.0"]
    df["bb_mid"] = bb["BBM_20_2.0_2.0"]
    df["bb_lower"] = bb["BBL_20_2.0_2.0"]

    # ATR(14): สำหรับคำนวณ SL/TP (คงที่ ไม่ optimize)
    df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)

    # --- Advanced indicators ---
    df = calc_vidya_full(df)
    df = calc_adaptive_supertrend(df, atr_length=params.st_atr_len, factor=params.st_factor)
    df = calc_smc(df)

    if df_htf is not None:
        df_htf = calc_mtf_macd(df_htf)
        df = align_htf_to_ltf(df, df_htf)

    return df
