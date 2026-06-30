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

**สถานะปัจจุบัน: Phase 1 เสร็จ + deploy แล้ว · Phase 2 โครงสร้างเสร็จ (รอ testnet key + deploy)**

### Requirements

| Phase | Scope | สถานะ |
|---|---|---|
| **Phase 1 (min)** | เครื่องมือวิเคราะห์ + แจ้งเตือนจุดเข้า/ออก เป็นสัญญาณเทรด บอกความเสี่ยง + winrate | ✅ เสร็จ |
| **Phase 2 (max)** | Bot ที่เข้า trade เอง + เรียนรู้เองบน test account เพื่อหา strategy ที่ทำกำไรดีสุด | 🔨 backtest + optimizer ใช้ได้, executor dry-run ใช้ได้ — รอ Binance testnet key + deploy |

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
- **optuna** — strategy optimizer (Phase 2)
- **Railway** — cloud hosting

---

## Architecture

```
something/
├── main.py                  # Bot loop: fetch → indicators → signal → Telegram alert (ทุก 60s)
├── start.py                 # Railway entry: รัน live_demo (signal+alert+execute) + dashboard
├── dashboard.py             # Streamlit web app (chart, SMC tab, signal history, exchange dropdown)
├── config.py                # env vars + settings
├── test_connection.py       # ทดสอบ exchange + indicators + signal + Telegram
├── requirements.txt         # pinned deps
├── runtime.txt / Procfile   # Railway config
├── .env / .env.example      # secrets (gitignored)
├── data/
│   └── fetcher.py           # Multi-exchange fallback (ดู Data Layer)
├── analysis/
│   ├── indicators.py        # EMA, RSI, MACD, BB, ATR + เรียก advanced (รับ params)
│   ├── indicators_advanced.py  # 4 indicators จาก TradingView (ดู Indicators)
│   └── signals.py           # Signal engine + confluence scoring (รับ params)
├── alerts/
│   └── telegram.py          # ส่ง Telegram message
├── utils/
│   └── logger.py            # logging
├── strategy/                # Phase 2
│   ├── params.py            # StrategyParams dataclass + DEFAULT + load/save active
│   └── active_params.json   # strategy ที่ใช้จริง (committed — sync ไป Railway)
├── backtest/                # Phase 2
│   ├── engine.py            # simulate() + run_backtest() (reuse generate_signal)
│   ├── metrics.py           # winrate, return, drawdown, Sharpe, equity curve
│   ├── optimizer.py         # Optuna study + train/test split
│   ├── results.py           # save/load result + candidate
│   └── results/             # ผล backtest + candidate.json (gitignored, runtime)
├── trading/                 # Phase 2
│   ├── executor.py          # execute_signal() + trade lifecycle tracking (MFE/MAE, outcome)
│   ├── live_demo.py         # live demo loop (state machine: ถือ→track, ปิด→record, ว่าง→execute)
│   ├── open_trade.json      # ไม้ที่กำลังถือ + MFE/MAE running (gitignored, runtime)
│   └── trade_log.json       # closed trades + outcome (gitignored, runtime/volume)
└── sourcecode_indicators/   # Pine Script ต้นฉบับ (gitignored, ใช้ reference)
```

**Data flow:**
```
Phase 1:  fetcher (OHLCV) → indicators (LTF+HTF, params) → signals (confluence, params) → alert/dashboard
Phase 2:  historical → backtest engine → metrics → optimizer (Optuna) → candidate → [approve] → active_params → executor (testnet)
```

---

## Data Layer (data/fetcher.py)

**Multi-exchange fallback** — Railway region = Southeast Asia → ทุก exchange ใช้ได้ (ไม่มี geo-block แล้ว)

- `EXCHANGE_PRIORITY = ["binance", "bybit", "kraken", "coinbase", "kucoin", "gateio"]`
- **Binance primary** — liquidity สูงสุด + candle ไม่ cap; ตัวที่เหลือ = fallback robustness ถ้า primary down
- ลองทีละตัวจนกว่าจะได้ → cache ตัวที่ใช้ได้ → fallback อัตโนมัติถ้า down
- *ประวัติ:* เดิมเริ่มที่ Kraken/Coinbase (US-based) สมัย Railway อยู่ US ที่ Binance/Bybit โดน block (451/403) — แก้ด้วยการย้าย region เป็น SEA
- ใช้ **public data เท่านั้น** (OHLCV, ticker) ไม่ต้อง API key
- `fetch_ohlcv(exchange=None)` → None = auto fallback, ระบุชื่อ = บังคับใช้ตัวนั้น
- `current_exchange()` → ชื่อ exchange ที่ใช้อยู่
- `get_testnet_exchange()` → Binance testnet (sandbox) + API key — Phase 2 execute orders
- `get_private_exchange()` → Bybit (สำรอง เผื่อสลับ exchange execute)

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
- **กรอง:** ถ้า confluence < `params.confluence_min` → ไม่ส่งสัญญาณ (ลด false signal)
- **Quality filters (จาก data analysis):**
  - `params.htf_filter` — เทรดเฉพาะตาม HTF MACD trend
  - `params.skip_high_vol` — ไม่เทรดตอน volatility=HIGH (HIGH vol winrate ~0%)
  - `params.macd_only` — ใช้เฉพาะ MACD cross trigger, ตัด SuperTrend flip (**ST_flip winrate 21% vs MACD cross 50%** — ST flip บน 15m = noise)
  - *backtest (limit 4000): macd_only +2.8% PF2.22 Sharpe+1.23 (ดีสุด + มีเหตุผลเชิงตรรกะ) · skip_high_vol -1.4% (overfit period สั้น) · macd_only ครอบคลุม skip_high_vol (ผลเท่ากัน)*
  - *re-validate 30 มิ.ย. 2026 (5000 แท่ง/52d, **paginated จริง** — ก่อนหน้านี้ `--limit 4000` ได้แค่ 1000 เพราะ fetcher ไม่ paginate, ดู Known Issues): **ACTIVE (macd_only+skip_high_vol) +9% PF1.48 vs OLD (ไม่มี filter) -5.5% PF0.90 DD10.8%** — filter ตัดไม้ขยะครึ่งนึง (164→74 ไม้) พลิกขาดทุนเป็นกำไร. ยืนยันว่า "เทรดน้อย-คัดดี" ดีกว่า "เทรดถี่"*
  - ⚠️ **ระวัง overfit:** filter หาจาก historical period เดียว — skip_high_vol ดูดีช่วงสั้นแต่หายช่วงยาว. macd_only น่าเชื่อกว่าเพราะมีเหตุผล แต่ sample เล็ก — **forward validate ด้วย demo จริงเสมอ**
  - 📌 **demo CSV (7–29 มิ.ย.) ที่ขาดทุนเป็นของผสม:** skip_high_vol เพิ่งเปิด 11 มิ.ย., macd_only เพิ่งเปิด 16 มิ.ย. → ไม้ช่วงต้นรัน params เก่า (ไม่มี filter). ไม่ใช่หลักฐานว่า strategy ปัจจุบันแย่
- **Output:** signal (LONG/SHORT), entry, SL, TP, R:R, winrate (40–85% จาก score), risk (จาก volatility cluster + RSI)
- **SL/TP:** SL = ATR × `params.atr_multiplier`, TP = SL × `params.risk_reward`
- **Parameterized (Phase 2):** `generate_signal(df, params)` + `add_indicators(df, df_htf, params)` รับ `StrategyParams` (default = ค่าเดิม → Phase 1 ทำงานเหมือนเดิม). optimizer ปรับ params ได้

---

## Alerts (alerts/telegram.py)

- `send_alert(signal)` — เปิดไม้/มีสัญญาณ: signal, entry, SL, TP, R:R, winrate, risk, confluence, RSI, ATR
- `send_closed_alert(closed)` — ไม้ปิด (SL/TP/reverse): win/loss, exit price/reason, PnL%, MFE%/MAE%, duration
- `send_skip_alert(signal, holding)` — มี signal แต่ถือไม้อยู่ → ข้าม (รู้ว่าพลาดจังหวะไหน)
- live_demo เรียกครบ: เปิด → `send_alert` · ปิด → `send_closed_alert` · signal ระหว่างถือ → `send_skip_alert`
- ต้องตั้ง `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID`
- **Gotcha:** ต้องกด `/start` ใน Telegram bot ก่อน ไม่งั้นส่งไม่ได้ ("chat not found")

---

## Dashboard (dashboard.py)

Streamlit web app — **Sidebar page navigation:** Live Signal / Backtest / Optimizer / Demo Trades

**Live Signal** (ใช้ active params):
- Sidebar: dropdown เลือก exchange (Auto fallback หรือเจาะจง)
- Metrics: ราคา, RSI, SuperTrend, Volatility, VIDYA, HTF MACD, Vol Delta
- Signal box: LONG/SHORT พร้อม SL/TP/winrate/confluence/risk
- Tab Main: Candlestick + EMA/VIDYA/SuperTrend/BB, RSI, MACD, Vol Delta
- Tab SMC: FVG zones, Order Blocks, BoS/CHoCH labels
- Signal history table + auto-refresh ทุก 30s

**Backtest / Optimizer / Demo Trades** (Phase 2):
- Backtest: ปุ่มรัน backtest (active params) → metrics + equity curve + trade list
- Optimizer: แสดง candidate (best params, train vs test, overfit warning) + ปุ่ม **Apply** (manual approve → เขียน active_params.json)
- Demo Trades: open position + summary stats (winrate, total/avg PnL, avg MFE/MAE, "แพ้แต่เคยบวก", ชนะโดน TP) + trade table + **Export CSV**

---

## Phase 2 — Backtest / Optimizer / Executor

**Backtest (backtest/engine.py):**
- `simulate(df, params, fee, start, end)` — loop candle [start,end), reuse `generate_signal()` 100%, จำลอง SL/TP hit, fee 0.04%×2
- `run_backtest(df, params, df_htf)` — add_indicators + simulate ทั้ง df
- indicators ทุกตัวเป็น **causal** (ใช้แค่อดีต) → ไม่มี lookahead bias
- entry = close ของ candle ที่เกิด signal, exit เช็คจาก candle ถัดไป (high/low แตะ SL/TP; โดนทั้งคู่ = SL ก่อน conservative)

**Optimizer (backtest/optimizer.py):**
- Optuna maximize Sharpe — search: `atr_multiplier, risk_reward, confluence_min, st_factor, timeframe`
- **Train/test split 70/30:** add_indicators บน full df ครั้งเดียว → simulate train [warmup,k) + test [k,n) (test ได้ indicator ที่ warm จาก train — กันปัญหา warmup กิน test set)
- penalize ถ้า train trades < 10; overfit check: test Sharpe < train×0.5 → เตือน
- best params → `save_candidate()` (รอ approve, ไม่ auto-apply)
- รัน: `py -3.12 -m backtest.optimizer --trials 100`

**Executor (trading/executor.py):**
- `execute_signal(signal)` — market entry + SL (STOP_MARKET) + TP (TAKE_PROFIT_MARKET) reduce-only บน Binance testnet
- **Binance testnet connect:** ccxt 4.5+ ตัด `set_sandbox_mode()` สำหรับ futures → `get_testnet_exchange()` ใช้ `binanceusdm` + override `fapi*` endpoints เป็น `urls['test']` เอง + `fetchCurrencies=False` (ดู Known Issues). verified order จริงแล้ว
- **Position sizing risk-based:** `size = (balance × RISK_PER_TRADE) / sl_distance`
- **SL/TP คำนวณจาก entry จริง (ไม่ใช่ signal price):** หลัง market fill ดึง `entryPrice` จาก position แล้ว recompute SL/TP จาก distance เดิม (R:R คงที่) วัดจาก entry จริง — กัน slippage/drift ทำ stopPrice ไปอยู่ผิดข้าง → error -2021 (ดู Known Issues). entry/sl/tp จริงส่งกลับใน result → `new_open_trade()` ใช้ track MFE/MAE/stats ให้ตรง
- 1 position ต่อครั้ง · **`DRY_RUN=True` (default) = log อย่างเดียว ไม่ยิง order** → ทดสอบ logic ก่อนเปิดจริง
- **Position management (reverse/pyramid: backtest + executor + live_demo ใช้ logic เดียวกัน):**
  - **Reverse (`params.reverse`, default False):** ถือไม้ + signal กลับข้าง → ปิด (`close_position()`) + เปิดตรงข้าม (exit_reason="reverse")
  - **Pyramid (`params.max_pyramid`, default 1):** ถือไม้ + signal เดิมทาง + level < max → เพิ่มไม้ (`add_to_position()`): เฉลี่ย entry, recompute SL/TP จาก avg, risk แบ่ง (`RISK_PER_TRADE/max_pyramid` ต่อ level)
  - **Priority:** SL/TP > reverse > pyramid
  - *หมายเหตุ: backtest พบว่าทั้ง reverse + pyramid แย่กว่าบน strategy ปัจจุบัน (reverse -4%, pyramid -9% DD 12%) — เปิดเมื่อ optimize ยืนยันเท่านั้น*
- **Exit management / profit protection (params default OFF — ปัจจุบันมีใน `backtest/engine.py` เท่านั้น, ยังไม่ port เข้า executor/live_demo):**
  - **Breakeven (`breakeven`, `be_trigger_r`, `be_buffer_r`):** กำไรถึง `be_trigger_r` R → ขยับ SL เป็นทุน
  - **Trailing (`trail`, `trail_trigger_r`, `trail_dist_r`):** กำไรถึง trigger → SL ตาม high-water ห่าง `trail_dist_r` R (ขยับเฉพาะทางกำไร ไม่คลาย)
  - **Partial TP (`partial_tp`, `partial_tp_r`, `partial_tp_pct`, `partial_be`):** ถึง `partial_tp_r` R → ปิดบางส่วน + (option) ขยับที่เหลือเป็นทุน
  - engine เช็ค conservative: SL/TP/partial ของระดับแท่งก่อน → ค่อยอัปเดต trail/BE จาก high/low แท่งนี้ (มีผลแท่งถัดไป, ไม่ lookahead). fee model + MFE/MAE คงเดิม (ปิด features = ผลเท่าเดิมเป๊ะ)
  - ⚠️ **finding (5000 แท่ง/52d, paginated):** exit management **ลด return/Sharpe ทุกแบบ** — baseline SL/TP→2R ดีสุด (+9% PF1.48 Sharpe1.43) เพราะ edge มาจากไม้ที่วิ่งยาวถึง 2R, trail/partial ไป "ตัดกำไรเร็ว" ฆ่า runner. winrate ขึ้นจริง (52–70%) แต่ expectancy ลด. ตัวเดียวที่น่าสนใจ = **BE+trail** (ลด MaxDD แลก return นิดหน่อย) ถ้าต้องการ equity เรียบ. **อย่าเปิด default — เปิดเมื่อ optimize/regime ใหม่ยืนยัน**
- `trading/live_demo.py` — state machine: ถือไม้→update MFE/MAE ทุก loop, position หาย→`record_closed_trade()`, ว่าง+signal→execute
- **Trade stats (MFE/MAE):** เปิดไม้ → `open_trade.json` track high/low ระหว่างถือ; ปิด → บันทึก outcome ลง `trade_log.json`: win/loss, exit price/reason (SL/TP), pnl%, **MFE%** (ไปได้เปรียบสุด), **MAE%** (ไปเสียเปรียบสุด), duration. ดู + Export CSV ใน dashboard Demo Trades

**Self-learning scope:** ตอนนี้ = optimize params ของ strategy ปัจจุบัน (ยังไม่ใช่ RL discover strategy ใหม่)

**Promote (manual approve):** optimizer หา candidate → ดูใน dashboard → กด Apply → executor ใช้ params ใหม่ (ต้อง approve เสมอ ไม่ auto)

---

## Deployment (Railway)

- **Platform:** Railway (cloud, 24/7)
- **Region:** Southeast Asia (สำคัญ — ย้ายมาจาก US เพื่อแก้ geo-block ของ Binance/Bybit)
- **Entry:** `start.py` รัน `trading.live_demo` (thread) + dashboard (main) พร้อมกัน. live_demo = signal + Telegram alert + auto-execute (DRY_RUN guard). *(main.py = legacy signal-only, เก็บไว้)*
- **URL:** https://web-production-e07d8.up.railway.app/
- **Auto-deploy:** push เข้า branch ที่ผูกไว้ → redeploy อัตโนมัติ (ต้องเปิด Auto Deploy ใน Settings)
- **GitHub:** pondchalong/something — work branch `something_1`, merge เข้า `main` ผ่าน PR

### Environment Variables (Railway → Variables)
```
TELEGRAM_BOT_TOKEN        = (จาก BotFather)
TELEGRAM_CHAT_ID          = (จาก @userinfobot)
SYMBOL                    = BTC/USDT
TIMEFRAME                 = 15m
# Phase 2 (auto-execute)
BINANCE_TESTNET_API_KEY   = (จาก testnet.binancefuture.com)
BINANCE_TESTNET_SECRET    = (จาก testnet.binancefuture.com)
RISK_PER_TRADE            = 0.01   # 1% ต่อไม้
DRY_RUN                   = true   # true = ไม่ยิง order จริง (ตั้ง false เมื่อพร้อม)
DATA_DIR                  = /data  # Railway Volume mount → trade_log ถาวร (ไม่ตั้ง = ephemeral)
```
Phase 1 ไม่ต้องใช้ exchange API key (public data). Phase 2 ต้องมี Binance testnet key

**Persistent log (Railway Volume):** trade_log.json บน Railway filesystem = ephemeral (หายเมื่อ redeploy). เก็บถาวร → สร้าง Volume mount `/data` + ตั้ง env `DATA_DIR=/data`. executor เขียน log ที่ `DATA_DIR` ถ้าตั้ง (ดู `config.DATA_DIR`). active_params มาจาก git อยู่แล้ว ไม่ต้อง volume

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

# --- Phase 2 ---
# backtest (active params)
py -3.12 -m backtest.engine --limit 1500

# optimizer (หา strategy ดีสุด — ใช้เวลาหลายนาที)
py -3.12 -m backtest.optimizer --trials 100 --limit 2000

# live demo (DRY_RUN=true = ทดสอบ ไม่ยิง order จริง)
py -3.12 -m trading.live_demo
```

---

## Known Issues / Gotchas

- **Python version:** ต้องใช้ 3.12 — 3.14 ใหม่เกินไป (pandas-ta, numba build fail). บน Windows เรียกด้วย `py -3.12`
- **pip:** บน Windows ใช้ `python -m pip` / `py -3.12 -m pip` (pip ไม่อยู่ใน PATH)
- **Geo-restriction:** Binance + Bybit block US — *แก้แล้ว* ด้วยการตั้ง Railway region = Southeast Asia (ทุก exchange ใช้ได้). Multi-exchange fallback ยังคงไว้เพื่อ robustness
- **Coinbase candle cap:** ให้สูงสุด ~298 candles/request (น้อยกว่าที่ขอ 500) — พอสำหรับ ATR(200) แต่ valid น้อย; Binance (primary) ไม่ cap
- **fetch_ohlcv pagination (แก้แล้ว 30 มิ.ย. 2026):** เดิม `fetch_ohlcv()` ส่ง `limit` ตรงเข้า ccxt → Binance cap 1000 แท่ง/request **เงียบๆ** = `backtest`/`optimizer --limit 4000` จริงๆ ได้แค่ 1000 (backtest เก่าทั้งหมด effective ≤1000 แท่ง). **แก้:** `limit > PER_REQUEST(1000)` → `_fetch_ohlcv_paged()` paginate ด้วย `since` (lock exchange ตัวเดียว, fallback ถ้า fail กลางทาง) + dedupe/sort/tail. `enableRateLimit` กัน 418/-1003 ตอนยิงหลายหน้า
- **pandas-ta column names:** BB columns เป็น `BBU_20_2.0_2.0` (มี `_2.0` ซ้ำ) ใน version 0.4.x
- **Telegram:** ต้องกด `/start` กับ bot ก่อน ส่งครั้งแรกถึงจะได้
- **ccxt Binance futures testnet:** ccxt 4.5+ `set_sandbox_mode(True)` บน binance/binanceusdm futures → raise `NotSupported` (deprecated). แก้ใน `get_testnet_exchange()`: ใช้ `binanceusdm` + copy `urls['test']` fapi endpoints → `urls['api']` + `options.fetchCurrencies=False` (เลี่ยง sapi ที่ไม่มี testnet URL). Bybit testnet ยัง support set_sandbox_mode ปกติ (เก็บเป็น fallback)
- **Binance testnet ไม่เสถียร:** 502 Bad Gateway / -1007 timeout บ่อย (testnet best-effort). `executor._retry()` ลองซ้ำ transient errors 3 ครั้ง + verify position หลัง entry (กัน position เปลือยตอน "execution unknown") + ปิด position ถ้าตั้ง SL/TP ไม่ได้
- **`fetch_positions` (`/fapi/v3/positionRisk`) พลาดชั่วคราว:** testnet 502/timeout ทำให้ query position fail. `get_open_position()` ห่อด้วย `_retry()` แล้ว — ถ้าไม่ retry จะคืน None ทั้งที่แค่พลาดชั่วคราว → caller เข้าใจผิดว่า "ไม่มี position" (live_demo อาจบันทึกไม้ปิดทั้งที่ยังถืออยู่ / เปิดไม้ซ้ำ)
- **Error 418 / -1003 "Way too many requests" (IP ban):** ยิง request ถี่เกิน → Binance auto-ban IP (ทั้ง testnet + public OHLCV เพราะ IP เดียวกัน). **แก้ 2 ชั้น:** (1) `enableRateLimit=True` ทุก ccxt instance (testnet + public fetcher) → ccxt throttle เอง; (2) `_is_transient()` ถือว่า rate-limit/ban (DDoSProtection, 418/429/-1003) = **ไม่ retry** — ถ้า retry ตอนโดนแบนจะยิ่งยืดเวลาแบน, ต้องหยุดยิงให้ ban หมดอายุเอง (loop sleep 60s ต่อรอบอยู่แล้ว)
- **Error -2021 "Order would immediately trigger":** ตั้ง SL/TP แล้ว stopPrice อยู่ผิดข้างของ mark price → Binance reject. เกิดเพราะตั้ง SL/TP จาก `signal["price"]` (close แท่ง signal) แต่ market fill จริงช้ากว่า ~1-2s ราคาขยับ. **แก้:** `execute_signal()` ดึง `entryPrice` จริงจาก position แล้ว recompute SL/TP จาก entry นั้น (distance เดิม) → stopPrice อยู่ถูกข้างเสมอ. ถ้ายัง fail (ตลาดวิ่งแรงมาก) → ปิด position กันเปลือยเหมือนเดิม
- **Binance futures conditional orders (SL/TP):** STOP_MARKET/TAKE_PROFIT_MARKET **ไม่อยู่ใน `fetch_open_orders()` ปกติ** — ต้อง `params={'stop': True}`. และ `cancel_all_orders()` ปกติ **ไม่ลบ** conditional ด้วย → ต้อง cancel ซ้ำด้วย stop param (ดู `executor._cancel_all()`) ไม่งั้น SL/TP ค้างสะสม. *เคย diagnose ผิดว่า position "เปลือย" เพราะ query นี้*
- **Railway auto-deploy:** ถ้า deploy ค้าง commit เก่า → เช็ค Auto Deploy ON + branch ที่ผูก, trigger redeploy manual

---

## Roadmap — Phase 2

**เสร็จแล้ว (โครงสร้าง):**
- ✅ Custom backtest engine + metrics (reuse generate_signal)
- ✅ Optuna optimizer + train/test split (กัน overfit)
- ✅ Demo executor บน Binance testnet (DRY_RUN guard, risk-based sizing)
- ✅ Dashboard: Backtest / Optimizer / Demo Trades + Apply (manual approve)

**เหลือ:**
- ⏳ user สมัคร Binance testnet + ขอ API key → ตั้ง env vars
- ⏳ ทดสอบ order จริงบน testnet (DRY_RUN=false) 1 ไม้
- ⏳ deploy live_demo บน Railway (เพิ่ม start option หรือแยก service)
- ⏳ optimize trials เยอะ (100+) หา params ที่ robust (test ยัง positive)
- 🔮 อนาคต: RL discover strategy ใหม่, walk-forward optimization, slippage model
