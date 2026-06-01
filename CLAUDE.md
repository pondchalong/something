# CLAUDE.md — Trade Signal Bot

เอกสาร context หลักของ project นี้ สำหรับ Claude และผู้พัฒนา

---

## ⚠️ Maintenance Rule (อ่านก่อน)

**ทุกครั้งที่มีการเปลี่ยนแปลง code หรือ requirement ที่สำคัญต่อ context ของ project ต้อง update ไฟล์ CLAUDE.md นี้ด้วยเสมอ**

สิ่งที่ถือว่า "สำคัญ" และต้อง update:
- เปลี่ยน data source / exchange / API
- เพิ่ม/ลบ/แก้ indicator หรือ signal logic
- เปลี่ยน architecture, ไฟล์หลัก, หรือ data flow
- เปลี่ยน deployment (platform, env vars, entry point)
- เปลี่ยน requirement (Phase scope, เป้าหมาย)
- เพิ่ม dependency หรือเปลี่ยน Python version
- เจอ gotcha / known issue ใหม่ ที่คนอื่นควรรู้

การเปลี่ยนเล็กน้อย (refactor ภายใน, แก้ typo, ปรับ style) ไม่ต้อง update

---

## Project Overview

เครื่องมือวิเคราะห์ + แจ้งเตือนสัญญาณเทรด crypto (BTC/USDT) แบบ realtime
รันบน cloud 24/7 เปิดดูจากมือถือได้ ส่ง alert ผ่าน Telegram

**สถานะปัจจุบัน: Phase 1 เสร็จ + deploy แล้ว**

### Requirements

| Phase | Scope | สถานะ |
|---|---|---|
| **Phase 1 (min)** | เครื่องมือวิเคราะห์ + แจ้งเตือนจุดเข้า/ออก เป็นสัญญาณเทรด บอกความเสี่ยง + winrate | ✅ เสร็จ |
| **Phase 2 (max)** | Bot ที่เข้า trade เอง + เรียนรู้เองบน test account เพื่อหา strategy ที่ทำกำไรดีสุด | ⏳ ยังไม่เริ่ม |

ผู้พัฒนาเพิ่งเริ่มเรียน Python — อธิบาย concept เมื่อจำเป็น

---

## Tech Stack

- **Python 3.12** (สำคัญ: ห้ามใช้ 3.14 — pandas-ta/numba ยังไม่ support, ดู Known Issues)
- **ccxt** — unified API หลาย exchange
- **pandas / numpy** — data + คำนวณ
- **pandas-ta** — technical indicators
- **scikit-learn** — เผื่อใช้ (K-means เขียนเองใน SuperTrend)
- **python-telegram-bot / requests** — alert
- **streamlit / plotly** — dashboard
- **Railway** — cloud hosting

---

## Architecture

```
something/
├── main.py                  # Bot loop: fetch → indicators → signal → Telegram alert (ทุก 60s)
├── start.py                 # Railway entry point: รัน bot + dashboard พร้อมกัน
├── dashboard.py             # Streamlit web app (chart, SMC tab, signal history, exchange dropdown)
├── config.py                # env vars + settings
├── test_connection.py       # ทดสอบ exchange + indicators + signal + Telegram
├── requirements.txt         # pinned deps
├── runtime.txt / Procfile   # Railway config
├── .env / .env.example      # secrets (gitignored)
├── data/
│   └── fetcher.py           # Multi-exchange fallback (ดู Data Layer)
├── analysis/
│   ├── indicators.py        # EMA, RSI, MACD, BB, ATR + เรียก advanced
│   ├── indicators_advanced.py  # 4 indicators จาก TradingView (ดู Indicators)
│   └── signals.py           # Signal engine + confluence scoring
├── alerts/
│   └── telegram.py          # ส่ง Telegram message
├── utils/
│   └── logger.py            # logging
└── sourcecode_indicators/   # Pine Script ต้นฉบับ (gitignored, ใช้ reference)
```

**Data flow:**
```
fetcher (OHLCV) → indicators (LTF + HTF) → signals (confluence) → alert/dashboard
```

---

## Data Layer (data/fetcher.py)

**Multi-exchange fallback** — เหตุผล: Railway server อยู่ US, Binance + Bybit block US (HTTP 451 / CloudFront 403)

- `EXCHANGE_PRIORITY = ["kraken", "coinbase", "kucoin", "gateio", "binance", "bybit"]`
- เรียงตาม US-friendly ก่อน (Kraken/Coinbase เป็น US-based ไม่มี geo-block)
- ลองทีละตัวจนกว่าจะได้ → cache ตัวที่ใช้ได้ → fallback อัตโนมัติถ้า down
- ใช้ **public data เท่านั้น** (OHLCV, ticker) ไม่ต้อง API key
- `fetch_ohlcv(exchange=None)` → None = auto fallback, ระบุชื่อ = บังคับใช้ตัวนั้น
- `current_exchange()` → ชื่อ exchange ที่ใช้อยู่
- `get_private_exchange()` → Bybit + API key เก็บไว้สำหรับ Phase 2 (execute orders)

**Higher timeframe (HTF)** สำหรับ MTF MACD: `HTF_MAP` map LTF → HTF (เช่น 15m → 1h)

---

## Indicators

### Basic (analysis/indicators.py)
EMA20/50, RSI(14), MACD(12,26,9), Bollinger Bands(20,2), ATR(14)

### Advanced (analysis/indicators_advanced.py)
แปลงจาก **Pine Script source จริง** (ไฟล์ใน `sourcecode_indicators/`):

| Indicator | ที่มา | License | จุดสำคัญ |
|---|---|---|---|
| **VIDYA** | Volumatic VIDYA [BigBeluga] | CC BY-NC-SA 4.0 | ATR(200) bands, trend จาก crossover price/band, volume delta reset ทุก trend change |
| **ML Adaptive SuperTrend** | [AlgoAlpha] | MPL 2.0 | K-means 3 cluster (percentile init, เขียนเอง ไม่ใช้ sklearn), factor=3.0, ใช้ centroid เป็น ATR |
| **SMC Suite** | Mxwll Price Action Suite | MPL 2.0 | BoS/CHoCH (internal sens=3 + external sens=25), Order Blocks, FVG (3-bar same direction + gap) |
| **MTF MACD** | CM MACD MTF V2 [ChrisMoody] | — | MACD บน HTF, 4-color histogram (grow/fall × above/below) |

**สำคัญ:** VIDYA ใช้ ATR(200) → ต้องมี candle > 215 ตัว ถึงจะมีค่า valid
→ `CANDLE_LIMIT = 500` ใน config.py (Coinbase cap ที่ ~298 ก็ยังพอ แต่ valid น้อยกว่า)

---

## Signal Engine (analysis/signals.py)

- **Trigger:** SuperTrend flip (หลัก) หรือ MACD histogram cross + EMA alignment (รอง)
- **Confluence score 0–8:** นับจาก EMA, RSI zone, MACD, SuperTrend, VIDYA, HTF MACD, SMC structure
- **กรอง:** ถ้า confluence < 3 → ไม่ส่งสัญญาณ (ลด false signal)
- **Output:** signal (LONG/SHORT), entry, SL, TP, R:R, winrate (40–85% จาก score), risk (จาก volatility cluster + RSI)
- **SL/TP:** SL = ATR × `ATR_MULTIPLIER` (1.5), TP = SL × `RISK_REWARD_RATIO` (2.0)

---

## Alerts (alerts/telegram.py)

- ส่ง Markdown message: signal, entry, SL, TP, R:R, winrate, risk, RSI, ATR
- ต้องตั้ง `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
- **Gotcha:** ต้องกด `/start` ใน Telegram bot ก่อน ไม่งั้นส่งไม่ได้ ("chat not found")

---

## Dashboard (dashboard.py)

Streamlit web app:
- **Sidebar:** dropdown เลือก exchange (Auto fallback หรือเจาะจง)
- **Metrics:** ราคา, RSI, SuperTrend, Volatility, VIDYA, HTF MACD, Vol Delta
- **Signal box:** LONG/SHORT พร้อม SL/TP/winrate/confluence/risk
- **Tab 1 (Main):** Candlestick + EMA/VIDYA/SuperTrend/BB, RSI, MACD, Vol Delta
- **Tab 2 (SMC):** FVG zones, Order Blocks, BoS/CHoCH labels
- **Signal history table** + auto-refresh ทุก 30s

---

## Deployment (Railway)

- **Platform:** Railway (cloud, 24/7)
- **Entry:** `start.py` รัน bot (thread) + dashboard (main) พร้อมกัน
- **URL:** https://web-production-e07d8.up.railway.app/
- **Auto-deploy:** push เข้า branch ที่ผูกไว้ → redeploy อัตโนมัติ (ต้องเปิด Auto Deploy ใน Settings)
- **GitHub:** pondchalong/something — work branch `something_1`, merge เข้า `main` ผ่าน PR

### Environment Variables (Railway → Variables)
```
TELEGRAM_BOT_TOKEN   = (จาก BotFather)
TELEGRAM_CHAT_ID     = (จาก @userinfobot)
SYMBOL               = BTC/USDT
TIMEFRAME            = 15m
BYBIT_API_KEY        = (Phase 2 เท่านั้น)
BYBIT_SECRET_KEY     = (Phase 2 เท่านั้น)
```
Phase 1 ไม่ต้องใช้ exchange API key (public data)

---

## Local Development

```powershell
# รัน dashboard (Windows, ต้องใช้ py -3.12)
py -3.12 -m streamlit run dashboard.py --server.port 8501 --server.headless true

# รัน bot (Telegram alert loop)
py -3.12 main.py

# ทดสอบ connection ทั้งหมด
py -3.12 test_connection.py

# ติดตั้ง deps
py -3.12 -m pip install -r requirements.txt
```

---

## Known Issues / Gotchas

- **Python version:** ต้องใช้ 3.12 — 3.14 ใหม่เกินไป (pandas-ta, numba build fail). บน Windows เรียกด้วย `py -3.12`
- **pip:** บน Windows ใช้ `python -m pip` / `py -3.12 -m pip` (pip ไม่อยู่ใน PATH)
- **Geo-restriction:** Binance + Bybit block US (Railway) → ใช้ multi-exchange fallback (Kraken/Coinbase US-based)
- **Coinbase candle cap:** ให้สูงสุด ~298 candles/request (น้อยกว่าที่ขอ 500) — พอสำหรับ ATR(200) แต่ valid น้อย
- **pandas-ta column names:** BB columns เป็น `BBU_20_2.0_2.0` (มี `_2.0` ซ้ำ) ใน version 0.4.x
- **Telegram:** ต้องกด `/start` กับ bot ก่อน ส่งครั้งแรกถึงจะได้
- **Railway auto-deploy:** ถ้า deploy ค้าง commit เก่า → เช็ค Auto Deploy ON + branch ที่ผูก, trigger redeploy manual

---

## Roadmap — Phase 2 (ยังไม่เริ่ม)

- Auto-execute trade บน Bybit demo account (ใช้ `get_private_exchange()`)
- Backtest engine (backtesting.py / vectorbt)
- Strategy optimizer (optuna grid search / RL)
- Performance logger (PnL, drawdown, winrate จริง)
- Auto-promote strategy ที่ดีสุด (manual approve ก่อน live)
