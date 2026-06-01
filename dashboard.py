import streamlit as st
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import time
from datetime import datetime

from data.fetcher import (
    fetch_ohlcv, fetch_htf_ohlcv, fetch_ticker, current_exchange, EXCHANGE_PRIORITY,
)
from analysis.indicators import add_indicators
from analysis.signals import generate_signal
from config import SYMBOL, TIMEFRAME, DRY_RUN

# Phase 2
from strategy.params import load_active, save_active, StrategyParams
from backtest.engine import run_backtest
from backtest.results import save_result, load_result, load_candidate
from trading.executor import load_trade_log

st.set_page_config(page_title="Trade Signal Dashboard", page_icon="📈", layout="wide")
st.title("📈 Trade Signal Dashboard")
caption_slot = st.empty()

if "signal_history" not in st.session_state:
    st.session_state.signal_history = []

# --- Sidebar: เลือก exchange ---
st.sidebar.header("⚙️ Settings")
AUTO_LABEL = "Auto (fallback)"
exchange_choice = st.sidebar.selectbox(
    "Exchange (public data)",
    [AUTO_LABEL] + EXCHANGE_PRIORITY,
    index=0,
    help="Auto = ลองทีละตัวตาม priority. เลือกเจาะจง = บังคับใช้ตัวนั้น (Binance/Bybit อาจ block บาง region)",
)
selected_exchange = None if exchange_choice == AUTO_LABEL else exchange_choice

# --- Sidebar: page navigation (Phase 2) ---
page = st.sidebar.radio("หน้า", ["Live Signal", "Backtest", "Optimizer", "Demo Trades"])


def _equity_chart(equity_curve):
    fig = go.Figure()
    fig.add_trace(go.Scatter(y=equity_curve, mode="lines", line=dict(color="#00c853", width=2)))
    fig.update_layout(height=300, template="plotly_dark", margin=dict(l=0, r=0, t=10, b=0),
                      yaxis_title="Equity (x เริ่มต้น)")
    return fig


def _show_metrics(m: dict):
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Trades", m.get("num_trades", 0))
    c1.metric("Win Rate", f"{m.get('winrate', 0):.1f}%")
    c2.metric("Total Return", f"{m.get('total_return', 0)*100:+.1f}%")
    c2.metric("Max Drawdown", f"{m.get('max_drawdown', 0)*100:.1f}%")
    c3.metric("Profit Factor", f"{m.get('profit_factor', 0):.2f}")
    c3.metric("Sharpe", f"{m.get('sharpe', 0):.2f}")
    c4.metric("Avg Win", f"{m.get('avg_win', 0)*100:+.2f}%")
    c4.metric("Avg Loss", f"{m.get('avg_loss', 0)*100:+.2f}%")


def render_backtest():
    st.header("📊 Backtest")
    params = load_active()
    st.caption(f"Active params: {params.to_dict()}")
    limit = st.number_input("จำนวน candle", 500, 5000, 1500, step=500)
    if st.button("▶️ รัน Backtest (active params)"):
        with st.spinner("กำลัง backtest... (อาจใช้เวลาสักครู่)"):
            df = fetch_ohlcv(timeframe=params.timeframe, limit=int(limit))
            df_htf = fetch_htf_ohlcv(timeframe=params.timeframe, limit=int(limit))
            res = run_backtest(df, params, df_htf)
            save_result("latest_backtest", res)
        st.success("เสร็จ")

    try:
        res = load_result("latest_backtest")
    except Exception:
        res = None
    if not res:
        st.info("ยังไม่มีผล — กดปุ่มรัน Backtest ด้านบน")
        return
    st.subheader(f"ผลล่าสุด ({res.get('timestamp', '')})")
    _show_metrics(res["metrics"])
    st.plotly_chart(_equity_chart(res["equity_curve"]), width="stretch")
    if res["trades"]:
        st.subheader("Trades")
        st.dataframe(pd.DataFrame(res["trades"][::-1]), width="stretch", hide_index=True)


def render_optimizer():
    st.header("🧠 Optimizer — หา strategy ที่ดีสุด")
    st.caption("รัน optimizer ผ่าน CLI: `py -3.12 -m backtest.optimizer --trials 100` (ใช้เวลาหลายนาที)")
    cand = load_candidate()
    if not cand:
        st.info("ยังไม่มี candidate — รัน optimizer ก่อน")
        return
    st.subheader(f"Candidate (best params) — {cand.get('timestamp', '')}")
    st.json(cand["params"])

    tm, te = cand["train_metrics"], cand["test_metrics"]
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Train (70%)**")
        st.metric("Sharpe", f"{tm['sharpe']:.2f}")
        st.metric("Return", f"{tm['total_return']*100:+.1f}%")
        st.metric("Win Rate", f"{tm['winrate']:.1f}%")
    with col2:
        st.markdown("**Test (30%) — out of sample**")
        st.metric("Sharpe", f"{te['sharpe']:.2f}")
        st.metric("Return", f"{te['total_return']*100:+.1f}%")
        st.metric("Win Rate", f"{te['winrate']:.1f}%")

    # Overfit check
    if tm["sharpe"] > 0 and te["sharpe"] < tm["sharpe"] * 0.5:
        st.warning("⚠️ Test ต่ำกว่า Train มาก — strategy อาจ overfit ใช้ด้วยความระวัง")
    elif te["sharpe"] > 0:
        st.success("✓ Test ยัง positive — robust พอควร")
    else:
        st.error("Test ไม่กำไร — ไม่แนะนำให้ apply")

    st.divider()
    st.markdown("**Apply strategy** — executor (live demo) จะใช้ params นี้")
    if st.button("✅ Apply candidate เป็น active strategy"):
        save_active(StrategyParams.from_dict(cand["params"]))
        st.success("Apply แล้ว — live demo รอบถัดไปจะใช้ params นี้")


def render_demo():
    st.header("🤖 Live Demo Trades (Binance testnet)")
    st.caption(f"DRY_RUN = {DRY_RUN}  ({'log อย่างเดียว ไม่ยิง order จริง' if DRY_RUN else 'ยิง order จริงบน testnet'})")
    params = load_active()
    st.caption(f"Active params: {params.to_dict()}")
    log = load_trade_log()
    if not log:
        st.info("ยังไม่มี trade — รัน `py -3.12 -m trading.live_demo`")
        return
    st.metric("จำนวน trade ทั้งหมด", len(log))
    st.dataframe(pd.DataFrame(log[::-1]), width="stretch", hide_index=True)


# Dispatch Phase 2 pages (Live Signal = code ด้านล่าง)
if page == "Backtest":
    render_backtest(); st.stop()
if page == "Optimizer":
    render_optimizer(); st.stop()
if page == "Demo Trades":
    render_demo(); st.stop()


active = load_active()

@st.cache_data(ttl=30)
def get_data(exchange, params_dict):
    params = StrategyParams.from_dict(params_dict)
    df = fetch_ohlcv(exchange=exchange, timeframe=params.timeframe)
    df_htf = fetch_htf_ohlcv(exchange=exchange, timeframe=params.timeframe)
    df = add_indicators(df, df_htf, params)
    return df

@st.cache_data(ttl=10)
def get_ticker(exchange):
    return fetch_ticker(exchange=exchange)

try:
    df = get_data(selected_exchange, active.to_dict())
    ticker = get_ticker(selected_exchange)
    signal = generate_signal(df, active)
    latest = df.iloc[-1]
    error = None
except Exception as e:
    error = str(e)

caption_slot.caption(f"{SYMBOL} | {active.timeframe} | {current_exchange()} (public data)")

if error:
    st.error(f"เชื่อมต่อไม่ได้: {error}")
    st.stop()

if signal:
    last = st.session_state.signal_history[-1] if st.session_state.signal_history else None
    if not last or last["time"] != str(df.index[-1]):
        st.session_state.signal_history.append({"time": str(df.index[-1]), **signal})
        st.session_state.signal_history = st.session_state.signal_history[-50:]

# ============================================================
# ROW 1: Metrics
# ============================================================
c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
price_change = ticker.get("percentage", 0) or 0
c1.metric("BTC/USDT", f"${ticker['last']:,.2f}", f"{price_change:+.2f}%")
c2.metric("RSI", f"{latest['rsi']:.1f}")
c3.metric("SuperTrend", latest.get("st_dir", "—") if signal else ("BULL" if latest.get("supertrend_dir") == 1 else "BEAR"))
c4.metric("Volatility", latest.get("volatility", "—"))
c5.metric("VIDYA", f"{latest['vidya']:.2f}" if pd.notna(latest.get("vidya")) else "—")
htf_bull = latest.get("htf_macd_bull")
c6.metric(f"HTF MACD", "BULL ▲" if htf_bull else "BEAR ▼")
c7.metric("Vol Delta", f"{latest.get('vol_delta', 0):,.0f}")

st.divider()

# ============================================================
# ROW 2: Signal + Chart
# ============================================================
left, right = st.columns([1, 3])

with left:
    if signal:
        color = "#00c853" if signal["signal"] == "LONG" else "#d50000"
        emoji = "📈" if signal["signal"] == "LONG" else "📉"
        risk_color = {"LOW": "#00c853", "MEDIUM": "#ffab00", "HIGH": "#d50000"}[signal["risk"]]
        st.markdown(f"""
        <div style="background:{color}22;border:2px solid {color};border-radius:12px;padding:20px;text-align:center">
            <h2 style="color:{color};margin:0">{emoji} {signal['signal']}</h2>
            <p style="font-size:22px;margin:6px 0"><b>${signal['price']:,.2f}</b></p>
            <hr style="border-color:{color}44">
            <p>🛑 SL: <b>${signal['sl']:,.2f}</b></p>
            <p>🎯 TP: <b>${signal['tp']:,.2f}</b></p>
            <p>⚖️ R:R = 1:{signal['rr']}</p>
            <hr style="border-color:{color}44">
            <p>Win Rate: <b>{signal['winrate']}%</b></p>
            <p>Confluence: <b>{signal['confluence']}/8</b></p>
            <p>Risk: <b style="color:{risk_color}">{signal['risk']}</b></p>
            <hr style="border-color:{color}44">
            <p style="font-size:12px">ST: {signal.get('st_dir','—')} | HTF: {signal.get('htf_macd','—')}</p>
        </div>
        """, unsafe_allow_html=True)
    else:
        st.markdown("""
        <div style="background:#1e1e1e;border:2px solid #444;border-radius:12px;padding:30px;text-align:center">
            <h3 style="color:#888;margin:0">⏳ ไม่มีสัญญาณ</h3>
            <p style="color:#555">รอสัญญาณใหม่...</p>
        </div>
        """, unsafe_allow_html=True)
    st.caption(f"อัพเดท: {datetime.now().strftime('%H:%M:%S')}")

with right:
    tab1, tab2 = st.tabs(["📊 Main Chart", "📈 SMC + Structure"])

    with tab1:
        fig = make_subplots(
            rows=4, cols=1, shared_xaxes=True,
            row_heights=[0.5, 0.17, 0.17, 0.16],
            vertical_spacing=0.02,
        )

        # Candlestick
        fig.add_trace(go.Candlestick(
            x=df.index, open=df["open"], high=df["high"],
            low=df["low"], close=df["close"], name="Price",
            increasing_line_color="#00c853", decreasing_line_color="#d50000",
        ), row=1, col=1)

        # EMA
        fig.add_trace(go.Scatter(x=df.index, y=df["ema20"], name="EMA20",
                                 line=dict(color="#2196f3", width=1.5)), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["ema50"], name="EMA50",
                                 line=dict(color="#ff9800", width=1.5)), row=1, col=1)

        # VIDYA
        if "vidya" in df.columns:
            fig.add_trace(go.Scatter(x=df.index, y=df["vidya"], name="VIDYA",
                                     line=dict(color="#e91e63", width=2, dash="dot")), row=1, col=1)

        # SuperTrend
        if "supertrend" in df.columns:
            bull_st = df[df["supertrend_dir"] == 1]
            bear_st = df[df["supertrend_dir"] == -1]
            fig.add_trace(go.Scatter(x=bull_st.index, y=bull_st["supertrend"], name="ST Bull",
                                     mode="markers", marker=dict(color="#00c853", size=3, symbol="circle")), row=1, col=1)
            fig.add_trace(go.Scatter(x=bear_st.index, y=bear_st["supertrend"], name="ST Bear",
                                     mode="markers", marker=dict(color="#d50000", size=3, symbol="circle")), row=1, col=1)

        # Bollinger Bands
        fig.add_trace(go.Scatter(x=df.index, y=df["bb_upper"], name="BB Upper",
                                 line=dict(color="#9c27b0", width=1, dash="dot"), showlegend=False), row=1, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["bb_lower"], name="BB Lower",
                                 line=dict(color="#9c27b0", width=1, dash="dot"),
                                 fill="tonexty", fillcolor="rgba(156,39,176,0.04)", showlegend=False), row=1, col=1)

        # Signal markers
        for s in st.session_state.signal_history:
            clr = "#00c853" if s["signal"] == "LONG" else "#d50000"
            sym = "triangle-up" if s["signal"] == "LONG" else "triangle-down"
            fig.add_trace(go.Scatter(x=[s["time"]], y=[s["price"]], mode="markers",
                                     marker=dict(symbol=sym, size=14, color=clr),
                                     name=s["signal"], showlegend=False), row=1, col=1)

        # RSI
        fig.add_trace(go.Scatter(x=df.index, y=df["rsi"], name="RSI",
                                 line=dict(color="#00bcd4", width=1.5)), row=2, col=1)
        fig.add_hline(y=70, line_dash="dot", line_color="#d50000", row=2, col=1)
        fig.add_hline(y=30, line_dash="dot", line_color="#00c853", row=2, col=1)

        # MACD
        colors = ["#00c853" if v >= 0 else "#d50000" for v in df["macd_hist"]]
        fig.add_trace(go.Bar(x=df.index, y=df["macd_hist"], name="MACD Hist",
                             marker_color=colors, showlegend=False), row=3, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["macd"], name="MACD",
                                 line=dict(color="#2196f3", width=1)), row=3, col=1)
        fig.add_trace(go.Scatter(x=df.index, y=df["macd_signal"], name="Signal",
                                 line=dict(color="#ff9800", width=1)), row=3, col=1)

        # VIDYA Vol Delta
        if "vol_delta" in df.columns:
            delta_colors = ["#00c853" if v >= 0 else "#d50000" for v in df["vol_delta"].fillna(0)]
            fig.add_trace(go.Bar(x=df.index, y=df["vol_delta"], name="Vol Delta",
                                 marker_color=delta_colors), row=4, col=1)

        fig.update_layout(height=600, template="plotly_dark",
                          xaxis_rangeslider_visible=False,
                          margin=dict(l=0, r=0, t=10, b=0),
                          legend=dict(orientation="h", y=1.02))
        fig.update_yaxes(title_text="RSI", row=2, col=1)
        fig.update_yaxes(title_text="MACD", row=3, col=1)
        fig.update_yaxes(title_text="Vol Δ", row=4, col=1)
        st.plotly_chart(fig, width='stretch')

    with tab2:
        fig2 = go.Figure()
        fig2.add_trace(go.Candlestick(
            x=df.index, open=df["open"], high=df["high"],
            low=df["low"], close=df["close"], name="Price",
            increasing_line_color="#00c853", decreasing_line_color="#d50000",
        ))

        # FVG zones
        if "fvg_bull" in df.columns:
            for idx, row_data in df[df["fvg_bull"]].iterrows():
                if pd.notna(row_data.get("fvg_top")) and pd.notna(row_data.get("fvg_bot")):
                    fig2.add_hrect(y0=row_data["fvg_bot"], y1=row_data["fvg_top"],
                                   fillcolor="rgba(0,200,83,0.15)", line_width=0)
            for idx, row_data in df[df["fvg_bear"]].iterrows():
                if pd.notna(row_data.get("fvg_top")) and pd.notna(row_data.get("fvg_bot")):
                    fig2.add_hrect(y0=row_data["fvg_bot"], y1=row_data["fvg_top"],
                                   fillcolor="rgba(213,0,0,0.15)", line_width=0)

        # Order Blocks
        if "ob_bull" in df.columns:
            ob_bull_df = df[df["ob_bull"]].tail(5)
            for idx, row_data in ob_bull_df.iterrows():
                fig2.add_hrect(y0=row_data["low"], y1=row_data["high"],
                               fillcolor="rgba(33,150,243,0.2)",
                               line=dict(color="#2196f3", width=1, dash="dot"))

            ob_bear_df = df[df["ob_bear"]].tail(5)
            for idx, row_data in ob_bear_df.iterrows():
                fig2.add_hrect(y0=row_data["low"], y1=row_data["high"],
                               fillcolor="rgba(255,152,0,0.2)",
                               line=dict(color="#ff9800", width=1, dash="dot"))

        # BoS / CHoCH labels
        if "bos" in df.columns:
            bos_df = df[df["bos"].notna()].tail(10)
            for idx, row_data in bos_df.iterrows():
                clr = "#00c853" if row_data["bos"] == "BULL" else "#d50000"
                fig2.add_annotation(x=idx, y=row_data["high"],
                                    text=f"BoS {row_data['bos']}",
                                    showarrow=True, arrowhead=2,
                                    font=dict(color=clr, size=10), arrowcolor=clr)

            choch_df = df[df["choch"].notna()].tail(5)
            for idx, row_data in choch_df.iterrows():
                clr = "#00c853" if row_data["choch"] == "BULL" else "#d50000"
                fig2.add_annotation(x=idx, y=row_data["low"],
                                    text=f"CHoCH {row_data['choch']}",
                                    showarrow=True, arrowhead=2, ay=30,
                                    font=dict(color=clr, size=10), arrowcolor=clr)

        fig2.update_layout(height=550, template="plotly_dark",
                           xaxis_rangeslider_visible=False,
                           margin=dict(l=0, r=0, t=10, b=0),
                           title="SMC: Order Blocks (blue=bull / orange=bear) | FVG (green/red zones) | BoS / CHoCH")
        st.plotly_chart(fig2, width='stretch')

# ============================================================
# ROW 3: Signal history
# ============================================================
if st.session_state.signal_history:
    st.subheader("📋 ประวัติสัญญาณ")
    hist_df = pd.DataFrame(st.session_state.signal_history[::-1])
    cols = ["time", "signal", "price", "sl", "tp", "rr", "winrate", "confluence", "risk", "volatility", "rsi"]
    cols = [c for c in cols if c in hist_df.columns]
    hist_df = hist_df[cols]
    hist_df.columns = [c.replace("_", " ").title() for c in cols]
    st.dataframe(hist_df, width='stretch', hide_index=True)

st.divider()
if st.checkbox("Auto refresh (30 วินาที)", value=True):
    time.sleep(30)
    st.rerun()
