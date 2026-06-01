"""
Python translation จาก Pine Script source code จริง:
1. VIDYA          — Volumatic Variable Index Dynamic Average [BigBeluga]   CC BY-NC-SA 4.0
2. Adaptive ST    — Machine Learning Adaptive SuperTrend [AlgoAlpha]       MPL 2.0
3. SMC            — Mxwll Price Action Suite [Mxwll Capital]               MPL 2.0
4. MTF MACD       — CM MACD Custom Indicator MTF V2 [ChrisMoody]
"""

import numpy as np
import pandas as pd
import pandas_ta as ta


# ============================================================
# 1. VIDYA — BigBeluga (ตรงตาม source)
#    - ATR(200) สำหรับ band
#    - Trend: crossover/under ของ price vs upper/lower band
#    - Volume: reset เมื่อ trend เปลี่ยน, สะสม buy/sell แยก
# ============================================================
def calc_vidya(src: pd.Series, vidya_length: int = 10, vidya_momentum: int = 20) -> pd.Series:
    mom = src.diff()
    sum_pos = mom.clip(lower=0).rolling(vidya_momentum).sum()
    sum_neg = (-mom.clip(upper=0)).rolling(vidya_momentum).sum()
    total = sum_pos + sum_neg
    # abs_cmo = |100 * (pos - neg) / (pos + neg)|
    abs_cmo = (100 * (sum_pos - sum_neg) / total.replace(0, np.nan)).abs().fillna(0)
    alpha = 2 / (vidya_length + 1)

    vidya_arr = np.zeros(len(src))
    vidya_arr[0] = src.iloc[0]
    cmo_arr = abs_cmo.values
    src_arr = src.values

    for i in range(1, len(src)):
        k = alpha * cmo_arr[i] / 100
        vidya_arr[i] = k * src_arr[i] + (1 - k) * vidya_arr[i - 1]

    vidya = pd.Series(vidya_arr, index=src.index)
    return vidya.rolling(15).mean()


def calc_vidya_full(df: pd.DataFrame, vidya_length: int = 10, vidya_momentum: int = 20,
                    band_distance: float = 2.0) -> pd.DataFrame:
    df = df.copy()
    src = df["close"]

    vidya_val = calc_vidya(src, vidya_length, vidya_momentum)
    df["vidya"] = vidya_val

    # ATR(200) ตาม source code จริง
    atr200 = ta.atr(df["high"], df["low"], df["close"], length=200)
    df["upper_band"] = vidya_val + atr200 * band_distance
    df["lower_band"] = vidya_val - atr200 * band_distance

    # Trend: crossover/under ของ close vs band
    is_trend_up = pd.Series(False, index=df.index)
    trend_arr = np.zeros(len(df), dtype=bool)

    for i in range(1, len(df)):
        prev_up = trend_arr[i - 1]
        c = src.iloc[i]
        ub = df["upper_band"].iloc[i]
        lb = df["lower_band"].iloc[i]
        prev_c = src.iloc[i - 1]
        prev_ub = df["upper_band"].iloc[i - 1]
        prev_lb = df["lower_band"].iloc[i - 1]

        # crossover(source, upper_band): prev <= upper, curr > upper
        if prev_c <= prev_ub and c > ub:
            trend_arr[i] = True
        # crossunder(source, lower_band): prev >= lower, curr < lower
        elif prev_c >= prev_lb and c < lb:
            trend_arr[i] = False
        else:
            trend_arr[i] = prev_up

    df["vidya_trend_up"] = trend_arr

    # smoothed_value = lower_band if up, upper_band if down, na on trend change
    smoothed = np.full(len(df), np.nan)
    for i in range(1, len(df)):
        trend_changed = trend_arr[i] != trend_arr[i - 1]
        if not trend_changed:
            smoothed[i] = df["lower_band"].iloc[i] if trend_arr[i] else df["upper_band"].iloc[i]
    df["vidya_smoothed"] = smoothed

    # Volume tracking ตาม source: reset on trend change, สะสม buy/sell
    up_vol = np.zeros(len(df))
    dn_vol = np.zeros(len(df))
    closes = df["close"].values
    opens = df["open"].values
    volumes = df["volume"].values

    for i in range(1, len(df)):
        trend_changed = trend_arr[i] != trend_arr[i - 1]
        if trend_changed:
            up_vol[i] = 0
            dn_vol[i] = 0
        else:
            up_vol[i] = up_vol[i - 1] + (volumes[i] if closes[i] > opens[i] else 0)
            dn_vol[i] = dn_vol[i - 1] + (volumes[i] if closes[i] < opens[i] else 0)

    df["vidya_buy_vol"] = up_vol
    df["vidya_sell_vol"] = dn_vol
    avg_vol = (up_vol + dn_vol) / 2
    # delta_volume % = (buy - sell) / avg * 100
    with np.errstate(divide="ignore", invalid="ignore"):
        delta_pct = np.where(avg_vol != 0,
                             (up_vol - dn_vol) / avg_vol * 100, 0)
    df["vidya_delta_pct"] = delta_pct

    return df


# ============================================================
# 2. ML Adaptive SuperTrend — AlgoAlpha (ตรงตาม source)
#    - K-means init ด้วย percentile (ไม่ใช่ sklearn)
#    - factor = 3.0, ใช้ centroid เป็น ATR
#    - dir: 1 = bearish (price < ST), -1 = bullish (price > ST)
# ============================================================
def _kmeans_3_clusters(values: np.ndarray, highvol: float = 0.75,
                       midvol: float = 0.5, lowvol: float = 0.25) -> tuple:
    """K-means 3 clusters ตาม AlgoAlpha Pine Script (iterative จนกว่าจะ converge)"""
    lower = values.min()
    upper = values.max()

    # Initial guesses จาก percentile range
    a_mean = lower + (upper - lower) * highvol
    b_mean = lower + (upper - lower) * midvol
    c_mean = lower + (upper - lower) * lowvol

    for _ in range(100):  # max iterations
        prev_a, prev_b, prev_c = a_mean, b_mean, c_mean

        hv, mv, lv = [], [], []
        for v in values:
            d1 = abs(v - a_mean)
            d2 = abs(v - b_mean)
            d3 = abs(v - c_mean)
            if d1 < d2 and d1 < d3:
                hv.append(v)
            elif d2 < d1 and d2 < d3:
                mv.append(v)
            else:
                lv.append(v)

        a_mean = np.mean(hv) if hv else a_mean
        b_mean = np.mean(mv) if mv else b_mean
        c_mean = np.mean(lv) if lv else c_mean

        if a_mean == prev_a and b_mean == prev_b and c_mean == prev_c:
            break

    return a_mean, b_mean, c_mean  # high, medium, low vol centroids


def _pine_supertrend(highs, lows, closes, factor: float, atr_vals: np.ndarray) -> tuple:
    """Pine Script supertrend implementation"""
    n = len(closes)
    hl2 = (highs + lows) / 2

    upper_band = hl2 + factor * atr_vals
    lower_band = hl2 - factor * atr_vals

    final_upper = upper_band.copy()
    final_lower = lower_band.copy()
    supertrend = np.full(n, np.nan)
    direction = np.ones(n, dtype=int)  # 1 = bearish start

    for i in range(1, n):
        if np.isnan(atr_vals[i - 1]):
            direction[i] = 1
            supertrend[i] = upper_band[i]
            continue

        # Adjust bands (Pine: don't widen in continuation)
        final_lower[i] = (lower_band[i]
                          if lower_band[i] > final_lower[i - 1] or closes[i - 1] < final_lower[i - 1]
                          else final_lower[i - 1])
        final_upper[i] = (upper_band[i]
                          if upper_band[i] < final_upper[i - 1] or closes[i - 1] > final_upper[i - 1]
                          else final_upper[i - 1])

        # Direction
        if supertrend[i - 1] == final_upper[i - 1]:
            direction[i] = -1 if closes[i] > final_upper[i] else 1
        else:
            direction[i] = 1 if closes[i] < final_lower[i] else -1

        supertrend[i] = final_lower[i] if direction[i] == -1 else final_upper[i]

    return supertrend, direction


def calc_adaptive_supertrend(df: pd.DataFrame, atr_length: int = 10,
                              training_period: int = 100, factor: float = 3.0) -> pd.DataFrame:
    df = df.copy()
    atr = ta.atr(df["high"], df["low"], df["close"], length=atr_length)
    df["atr_raw"] = atr

    n = len(df)
    assigned_centroids = np.full(n, np.nan)
    cluster_ids = np.full(n, -1, dtype=int)  # 0=high, 1=mid, 2=low

    for i in range(training_period - 1, n):
        window = atr.iloc[max(0, i - training_period + 1): i + 1].dropna().values
        if len(window) < 3:
            continue

        hv_c, mv_c, lv_c = _kmeans_3_clusters(window)
        centroids = [hv_c, mv_c, lv_c]

        curr_atr = atr.iloc[i]
        if np.isnan(curr_atr):
            continue
        dists = [abs(curr_atr - c) for c in centroids]
        cluster = dists.index(min(dists))

        assigned_centroids[i] = centroids[cluster]
        cluster_ids[i] = cluster

    df["st_centroid"] = assigned_centroids
    df["st_cluster"] = cluster_ids
    df["volatility"] = pd.Series(cluster_ids, index=df.index).map(
        {0: "HIGH", 1: "MEDIUM", 2: "LOW", -1: "UNKNOWN"})

    # SuperTrend: factor=3.0, atr = centroid (not raw ATR)
    st, direction = _pine_supertrend(
        df["high"].values, df["low"].values, df["close"].values,
        factor, assigned_centroids
    )

    df["supertrend"] = st
    # Pine: dir=-1 = bullish (ST below price), dir=1 = bearish (ST above price)
    # แปลงให้ intuitive: 1 = bullish, -1 = bearish
    df["supertrend_dir"] = np.where(direction == -1, 1, -1)
    df["st_flip_bull"] = (df["supertrend_dir"] == 1) & (df["supertrend_dir"].shift(1) == -1)
    df["st_flip_bear"] = (df["supertrend_dir"] == -1) & (df["supertrend_dir"].shift(1) == 1)

    return df


# ============================================================
# 3. Mxwll SMC — BoS, CHoCH, Order Blocks, FVG (ตรงตาม source)
#    - calculatePivots: lookback window pivot detection
#    - FVG: 3 bars เดียวกันทิศ + gap จริง
#    - OB: thin zone ใกล้ swing high/low
# ============================================================
def _calculate_pivots(highs: np.ndarray, lows: np.ndarray, length: int):
    """
    Pine: ดู `length` bars ที่แล้ว, หา max high / min low
    เมื่อ high ใหม่ > max → topSwing, low ใหม่ < min → botSwing
    """
    n = len(highs)
    top_swings = np.zeros(n)
    bot_swings = np.zeros(n)
    intra = np.zeros(n, dtype=int)

    for i in range(length + 1, n):
        window_h = highs[i - length: i]
        window_l = lows[i - length: i]
        up = window_h.max()
        dn = window_l.min()
        c_hi = highs[i]
        c_lo = lows[i]

        prev_intra = intra[i - 1]
        if c_hi > up:
            intra[i] = 0
        elif c_lo < dn:
            intra[i] = 1
        else:
            intra[i] = prev_intra

        if intra[i] == 0 and prev_intra != 0:
            top_swings[i] = c_hi
        if intra[i] == 1 and prev_intra != 1:
            bot_swings[i] = c_lo

    return top_swings, bot_swings


def calc_smc(df: pd.DataFrame, ext_sens: int = 25, int_sens: int = 3) -> pd.DataFrame:
    df = df.copy()
    n = len(df)
    highs = df["high"].values
    lows = df["low"].values
    closes = df["close"].values
    opens = df["open"].values

    # External structure
    big_upper, big_lower = _calculate_pivots(highs, lows, ext_sens)
    # Internal structure
    small_upper, small_lower = _calculate_pivots(highs, lows, int_sens)

    # Structure labels
    bos_ext = [None] * n
    choch_ext = [None] * n
    bos_int = [None] * n
    choch_int = [None] * n

    # External BoS/CHoCH tracking
    up_axis = 0.0; up_axis2 = 0; upside = 1
    dn_axis = 0.0; dn_axis2 = 0; downside = 1
    moving = 0  # 1=last broke up, -1=last broke down

    for i in range(ext_sens + 1, n):
        if big_upper[i] != 0:
            upside = 1
            up_axis = big_upper[i]
            up_axis2 = i - ext_sens

        if big_lower[i] != 0:
            downside = 1
            dn_axis = big_lower[i]
            dn_axis2 = i - ext_sens

        # Crossover close > upaxis
        if (i > 0 and closes[i - 1] <= up_axis and closes[i] > up_axis and up_axis != 0):
            if upside != 0:
                if moving < 0:
                    choch_ext[i] = "BULL"
                else:
                    bos_ext[i] = "BULL"
                upside = 0
                moving = 1

        # Crossunder close < dnaxis
        if (i > 0 and closes[i - 1] >= dn_axis and closes[i] < dn_axis and dn_axis != 0):
            if downside != 0:
                if moving > 0:
                    choch_ext[i] = "BEAR"
                else:
                    bos_ext[i] = "BEAR"
                downside = 0
                moving = -1

    # Internal BoS/CHoCH tracking
    up_axis_s = 0.0; up_axis2_s = 0; upside_s = 1
    dn_axis_s = 0.0; dn_axis2_s = 0; downside_s = 1
    moving_s = 0

    for i in range(int_sens + 1, n):
        if small_upper[i] != 0:
            upside_s = 1
            up_axis_s = small_upper[i]
            up_axis2_s = i - int_sens

        if small_lower[i] != 0:
            downside_s = 1
            dn_axis_s = small_lower[i]
            dn_axis2_s = i - int_sens

        if (i > 0 and closes[i - 1] <= up_axis_s and closes[i] > up_axis_s and up_axis_s != 0):
            if upside_s != 0:
                if moving_s < 0:
                    choch_int[i] = "BULL"
                else:
                    bos_int[i] = "BULL"
                upside_s = 0
                moving_s = 1

        if (i > 0 and closes[i - 1] >= dn_axis_s and closes[i] < dn_axis_s and dn_axis_s != 0):
            if downside_s != 0:
                if moving_s > 0:
                    choch_int[i] = "BEAR"
                else:
                    bos_int[i] = "BEAR"
                downside_s = 0
                moving_s = -1

    df["bos"] = bos_ext
    df["choch"] = choch_ext
    df["bos_int"] = bos_int
    df["choch_int"] = choch_int

    # Order Blocks (thin zones ใกล้ swing high/low ตาม source)
    ob_bear_high = np.full(n, np.nan)  # bearish OB top
    ob_bear_low = np.full(n, np.nan)   # bearish OB bottom (= top * 0.998)
    ob_bull_high = np.full(n, np.nan)  # bullish OB top (= bottom * 1.002)
    ob_bull_low = np.full(n, np.nan)   # bullish OB bottom

    for i in range(ext_sens + 1, n):
        if big_upper[i] != 0:
            x1 = i - ext_sens
            ob_bear_high[x1] = big_upper[i]
            ob_bear_low[x1] = big_upper[i] * 0.998

        if big_lower[i] != 0:
            x1 = i - ext_sens
            ob_bull_low[x1] = big_lower[i]
            ob_bull_high[x1] = big_lower[i] * 1.002

    # Clear OB ถ้า price ทะลุ
    for i in range(1, n):
        c = closes[i]
        for j in range(i):
            if not np.isnan(ob_bear_high[j]) and c >= ob_bear_high[j]:
                ob_bear_high[j] = np.nan
                ob_bear_low[j] = np.nan
            if not np.isnan(ob_bull_low[j]) and c <= ob_bull_low[j]:
                ob_bull_high[j] = np.nan
                ob_bull_low[j] = np.nan

    df["ob_bear_high"] = ob_bear_high
    df["ob_bear_low"] = ob_bear_low
    df["ob_bull_high"] = ob_bull_high
    df["ob_bull_low"] = ob_bull_low

    # FVG: 3 bars เดียวกันทิศ + gap (ตาม Mxwll fvg() function)
    # fvgMat.row(0).sum() == 3 → all bullish, == -3 → all bearish
    fvg_bull = np.zeros(n, dtype=bool)
    fvg_bear = np.zeros(n, dtype=bool)
    fvg_top = np.full(n, np.nan)
    fvg_bot = np.full(n, np.nan)

    signs = np.sign(closes - opens)  # +1 bullish, -1 bearish

    for i in range(2, n):
        s = signs[i] + signs[i - 1] + signs[i - 2]  # sum of 3 signs

        if s == 3:  # all bullish
            # bullish FVG: gap between high[i-2] and low[i]
            y = lows[i]        # low of newest bar
            y1 = highs[i - 2]  # high of oldest bar
            if y > y1:  # gap exists
                fvg_bull[i] = True
                fvg_bot[i] = y1
                fvg_top[i] = y

        elif s == -3:  # all bearish
            # bearish FVG: gap between low[i-2] and high[i]
            y = lows[i - 2]   # low of oldest bar
            y1 = highs[i]     # high of newest bar
            if y > y1:  # gap exists (low[i-2] > high[i])
                fvg_bear[i] = True
                fvg_top[i] = y
                fvg_bot[i] = y1

    df["fvg_bull"] = fvg_bull
    df["fvg_bear"] = fvg_bear
    df["fvg_top"] = fvg_top
    df["fvg_bot"] = fvg_bot

    return df


# ============================================================
# 4. CM MACD MTF — ChrisMoody (ตรงตาม source)
#    - 4-color histogram: grow_above, fall_above, grow_below, fall_below
#    - Cross UP/DN signals ตาม Pine: signal[1] >= macd[1] and signal < macd
# ============================================================
def calc_mtf_macd(df_htf: pd.DataFrame, fast: int = 12, slow: int = 26,
                  signal: int = 9) -> pd.DataFrame:
    df_htf = df_htf.copy()
    macd_data = ta.macd(df_htf["close"], fast=fast, slow=slow, signal=signal)
    macd_line = macd_data[f"MACD_{fast}_{slow}_{signal}"]
    signal_line = macd_data[f"MACDs_{fast}_{slow}_{signal}"]
    hist = macd_line - signal_line

    df_htf["htf_macd"] = macd_line
    df_htf["htf_signal"] = signal_line
    df_htf["htf_hist"] = hist

    # Trend
    df_htf["htf_trend_up"] = macd_line > signal_line

    # Cross signals ตาม Pine: signal[1] >= macd[1] and signal < macd
    prev_sig = signal_line.shift(1)
    prev_mac = macd_line.shift(1)
    df_htf["htf_cross_up"] = (prev_sig >= prev_mac) & (signal_line < macd_line)
    df_htf["htf_cross_dn"] = (prev_sig <= prev_mac) & (signal_line > macd_line)

    # 4-color histogram logic ตาม Pine
    hist_prev = hist.shift(1)
    grow_above = (hist > hist_prev) & (hist > 0)
    fall_above = (hist < hist_prev) & (hist > 0)
    grow_below = (hist < hist_prev) & (hist <= 0)
    fall_below = (hist > hist_prev) & (hist <= 0)

    hist_state = pd.Series("neutral", index=df_htf.index)
    hist_state[grow_above] = "grow_above"
    hist_state[fall_above] = "fall_above"
    hist_state[grow_below] = "grow_below"
    hist_state[fall_below] = "fall_below"
    df_htf["htf_hist_state"] = hist_state
    df_htf["htf_macd_bull"] = df_htf["htf_trend_up"]

    return df_htf


def align_htf_to_ltf(df_ltf: pd.DataFrame, df_htf: pd.DataFrame) -> pd.DataFrame:
    df_ltf = df_ltf.copy()
    htf_cols = ["htf_macd", "htf_signal", "htf_hist", "htf_trend_up",
                "htf_cross_up", "htf_cross_dn", "htf_hist_state", "htf_macd_bull"]
    merged = pd.merge_asof(
        df_ltf.reset_index(),
        df_htf[htf_cols].reset_index(),
        on="timestamp",
        direction="backward",
    ).set_index("timestamp")
    for col in htf_cols:
        df_ltf[col] = merged[col]
    return df_ltf
