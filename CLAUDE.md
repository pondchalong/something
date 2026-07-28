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

**สถานะปัจจุบัน (26 ก.ค. 2026): Phase 1 เสร็จ + deploy · Phase 2 รัน live demo บน testnet จริงมาแล้ว ~7 สัปดาห์ (log 93 ไม้ / จริง 123 ไม้)**
→ **execution มีบั๊กทำให้ exit price เพี้ยน ~50% ของไม้** (แก้แล้ว 26 ก.ค.) ทำให้ข้อมูลชุดแรก **ใช้วัดผล strategy ไม่ได้** ต้องเก็บใหม่
→ **reconcile กับ fill จริงแล้ว (26 ก.ค.): ผลจริง -24.27% แย่กว่า log** และ**แม้ตัดไม้ที่เป็นบั๊กออกหมด gross ก็ยังติดลบ** = ยังไม่มีหลักฐานว่า strategy มี edge จริง (ดู "ผล reconcile รอบแรก")
→ **อ่าน "Roadmap — งานที่ต้องทำต่อ" ท้ายไฟล์ก่อนเริ่มงาน**

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
├── test_trade_stats.py      # unit test ของ trade_stats (offline ไม่ต้องต่อ network)
├── test_executor_exit.py    # unit test trade lifecycle (exchange ปลอม, offline)
├── test_live_demo_loop.py   # test state machine ของ live_demo (offline)
├── test_backtest_slippage.py # unit test slippage model ของ backtest engine (offline)
├── test_reconcile.py        # unit test reconcile + การดึง fill (offline)
├── requirements.txt         # pinned deps
├── runtime.txt / Procfile   # Railway config
├── .env / .env.example      # secrets (gitignored)
├── data/
│   └── fetcher.py           # Multi-exchange fallback (ดู Data Layer)
├── analysis/
│   ├── indicators.py        # EMA, RSI, MACD, BB, ATR + เรียก advanced (รับ params)
│   ├── indicators_advanced.py  # 4 indicators จาก TradingView (ดู Indicators)
│   ├── signals.py           # Signal engine + confluence scoring (รับ params)
│   ├── trade_stats.py       # วิเคราะห์ demo trades ที่เก็บจริง (forward validation, ดู Trade Stats)
│   └── reconcile.py         # กระทบยอด trade_log กับ fill จริงจาก Binance API (ดู Reconcile)
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
  + section **วิเคราะห์เชิงลึก** (จาก `analysis/trade_stats.py`): PF / expectancy / MaxDD / Sharpe, ตารางแยกตาม **ชุด params ที่ live ตอนนั้น**, breakdown ทิศทาง/confluence/ชั่วโมง, ประเมิน BE-trailing (ดู Trade Stats)

---

## Phase 2 — Backtest / Optimizer / Executor

**Backtest (backtest/engine.py):**
- `simulate(df, params, fee, start, end, slippage)` — loop candle [start,end), reuse `generate_signal()` 100%, จำลอง SL/TP hit, fee 0.04%×2
- **Slippage model (เพิ่ม 26 ก.ค. — สำคัญมาก):** ทุกไม้จริงเป็น market order (เข้า = market, SL/TP = STOP/TAKE_PROFIT_MARKET ที่ trigger แล้วกลายเป็น market) → ราคาที่ได้แย่กว่าที่ตั้งใจเสมอ. `slippage` = ส่วนต่างต่อข้าง (fraction; CLI รับเป็น **bps**)
  - ขาเข้า: fill แย่กว่า close ของแท่ง signal แล้ว**ตั้ง SL/TP จาก entry จริง** (ระยะเดิม R:R คงที่ — เหมือน executor) → ต้นทุนของ slippage ขาเข้าไม่ได้กินกำไรตรงๆ แต่ทำให้ **TP ไกลขึ้น = โอกาสถึงน้อยลง**
  - ขาออก: ระดับ SL/TP ใช้ตัดสินว่า trigger ไหม แต่ราคาที่ได้จริงแย่กว่าระดับนั้น
  - `slippage=0` = พฤติกรรมเดิมเป๊ะ (ผลเก่าทั้งหมดยังเทียบกันได้) · sweep: `py -3.12 -m backtest.engine --sweep`
  - test: `py -3.12 test_backtest_slippage.py` (7 เคส, offline)
- `run_backtest(df, params, df_htf)` — add_indicators + simulate ทั้ง df
- indicators ทุกตัวเป็น **causal** (ใช้แค่อดีต) → ไม่มี lookahead bias
- entry = close ของ candle ที่เกิด signal, exit เช็คจาก candle ถัดไป (high/low แตะ SL/TP; โดนทั้งคู่ = SL ก่อน conservative)

**Optimizer (backtest/optimizer.py):**
- Optuna maximize Sharpe — search: `atr_multiplier, risk_reward, confluence_min, st_factor, timeframe`
- **Train/test split 70/30:** add_indicators บน full df ครั้งเดียว → simulate train [warmup,k) + test [k,n) (test ได้ indicator ที่ warm จาก train — กันปัญหา warmup กิน test set)
- **`--slippage <bps>`** (เพิ่ม 26 ก.ค.) — ส่งต่อเข้า simulate ทั้ง train/test. **ตอน optimize รอบหน้าต้องตั้ง ≥5** ไม่งั้นจะได้ params ที่กำไรเฉพาะในโลกที่ execution สมบูรณ์แบบ (ดูตารางเทียบช่วงจริง)
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
- `trading/live_demo.py` — state machine: ถือไม้→update MFE/MAE ทุก loop, position หาย (**ยืนยันแล้ว**)→`record_closed_trade()` + `cancel_open_orders()`, ว่าง+signal→execute. **query position พลาด → ข้ามรอบไปเลย ไม่แตะไม้ที่ถืออยู่** (`PositionQueryError`)
- **Trade stats (MFE/MAE):** เปิดไม้ → `open_trade.json` track high/low ระหว่างถือ; ปิด → บันทึก outcome ลง `trade_log.json`: win/loss, exit price/reason (SL/TP), pnl%, **MFE%** (ไปได้เปรียบสุด), **MAE%** (ไปเสียเปรียบสุด), duration. ดู + Export CSV ใน dashboard Demo Trades

**Trade Stats — forward validation (analysis/trade_stats.py):**
- อ่านผลเทรด**จริง**ที่บันทึกไว้ (`trade_log.json` ตาม `DATA_DIR` หรือ CSV ที่ export จาก dashboard) → คำนวณ metrics ด้วย `backtest.metrics.compute_metrics` **ตัวเดียวกับ backtest** → เทียบ demo vs backtest ได้ตรงๆ ว่า edge มาจริงไหม
- **ไม่ต้องต่อ network/exchange** — วิเคราะห์จากไฟล์อย่างเดียว (ใช้ตอน exchange API เข้าไม่ถึงได้)
- `to_metric_trades()` แปลงหน่วย: demo เก็บ `mfe_pct/mae_pct` เป็น % ของ **ราคา entry** แต่ backtest ใช้ % ของ **ระยะถึง TP/SL** → `(pct × entry) / distance`
- **แยกตาม param era อัตโนมัติ:** `param_eras()` ดึง git log ของ `strategy/active_params.json` + `git show` แต่ละ commit → รู้ว่าไม้แต่ละไม้รัน params ชุดไหน (ไม้เก่าที่รัน params คนละชุดจะไม่ถูกเอามาตัดสิน strategy ปัจจุบัน). ใช้ commit date เป็นขอบเขต — Railway redeploy ตามหลัง push ไม่กี่นาที
- breakdown: ทิศทาง / exit reason / confluence / ชั่วโมงที่เข้า · `exit_management_whatif()` ประเมินว่า BE-trailing จะช่วยไหม (**ขอบบน** — MFE/MAE ไม่บอกลำดับก่อนหลัง ต้อง confirm ด้วย backtest engine)
- `data_quality()` จับ record ที่ **exit == entry** = ตอนบันทึกดึงราคาปิดจาก testnet ไม่ได้ (502) แล้ว fallback เป็น entry → ไม้นั้นถูกนับเป็น "แพ้ค่า fee" ทั้งที่ผลจริงไม่ทราบ, และกรอง record ของ DRY_RUN (ไม่มี `pnl_pct`) ออกจากสถิติ
- `exit_integrity()` เช็คว่า exit price "เป็นไปได้" ไหม (ไม้ต้องจบที่ SL หรือ TP เท่านั้น) + จับ exit price ซ้ำข้ามไม้ → ดู Known Issues "exit price ที่บันทึกเชื่อไม่ได้"
- test: `py -3.12 test_trade_stats.py` (12 เคส ตัวเลขคำนวณมือ, offline)

**📉 ผล forward validation รอบแรก (CSV 7 มิ.ย.–26 ก.ค. 2026, 93 ไม้):**
- **ที่บันทึกไว้: -17.85%, winrate 30%, PF 0.34, MaxDD 17.9%, แพ้ติดกัน 11 ไม้** — สวนทาง backtest (+9% PF1.48) อย่างสิ้นเชิง
- ยุค params ปัจจุบัน (macd_only+skip_high_vol ตั้งแต่ 16 มิ.ย.) = 68 ไม้ **-12.8% PF 0.36** → filter ที่ backtest ว่าดี ไม่ช่วยในการเทรดจริง
- ⚠️ **แต่ตัวเลขนี้ใช้ตัดสิน strategy ไม่ได้** เพราะ **50% ของไม้ (46/93) มี exit price ที่ไม่ตรงทั้ง SL และ TP** (ดู Known Issues) — และในกลุ่มนี้ **19 ไม้เคยวิ่งถึง ~2R แล้วถูกตัดทิ้ง** (MFE เฉลี่ย 1.58R) = บั๊กตัดไม้กำไรทิ้งอย่างเป็นระบบ (ไม้ชนะใช้เวลานานกว่า → โดนตัดง่ายกว่าไม้แพ้ที่ชน SL เร็ว)
- ขอบเขตผลจริง: ถ้าไม้น่าสงสัยจบที่ SL หมด = -102R / ถ้าจบที่ TP หมด = +35R → **กว้างเกินกว่าจะสรุปอะไรได้**
- ไม้ที่ exit สะอาด 47 ไม้เป็น SL 42 : TP 5 — ดูแย่ แต่ **มี selection bias** (ไม้กำไรถูกตัดออกไปอยู่กลุ่มน่าสงสัยหมด) จึงใช้แทนภาพรวมไม่ได้เช่นกัน
- ค่า fee ไม่ใช่ตัวปัญหาหลัก: median 0.22R ต่อไม้ (มีแค่ 4/93 ไม้ที่ fee > 0.5R)
- 👉 **ลำดับที่ต้องทำ: แก้บั๊ก exit/position ก่อน → เก็บข้อมูลใหม่ → ค่อยตัดสิน strategy. อย่า optimize params บนข้อมูลชุดนี้**

**Reconcile — เทียบ log กับของจริง (analysis/reconcile.py):**
- ดึง fill จริงจาก Binance API (`fetch_my_trades` + paginate) → ประกอบเป็นไม้เองด้วยการไล่ position สะสม (พอกลับมา 0 = ไม้ปิด) → **ไม่พึ่ง state ฝั่งเราซึ่งเป็นตัวที่เคยพัง**
- **การดึงต้อง paginate 2 ชั้น** (แก้ 26 ก.ค. — รอบแรกดึงได้แค่ 14 fill/3 ไม้ จากของจริง 647 fill/123 ไม้): ชั้นนอกไล่ทีละ **หน้าต่าง 7 วัน** (Binance บังคับ ถ้าส่ง `startTime` เฉยๆ ได้แค่ 7 วันแรกแล้วเงียบ) · ชั้นในเลื่อน cursor ถ้าหน้าเต็ม 1000 · **dedupe ด้วย `trade_id` เท่านั้น** — order เดียวแตกเป็นหลาย fill ที่ ราคา/ขนาด/เวลา เท่ากันเป๊ะได้ ถ้า dedupe ด้วย key ประกอบจะทิ้ง fill จริง → position ไม่มีวันกลับเป็น 0 → ไม้ทั้งหมดถูกเหมารวมเป็นก้อนเดียว
- ใช้ `realizedPnl` + `commission` ที่ Binance ส่งมากับแต่ละ fill = **ground truth** ที่บั๊กฝั่งเราแตะไม่ได้
- จับคู่กับ `trade_log.json` ด้วยเวลาเปิดไม้ (±10 นาที) แล้วรายงาน: exit price ไม่ตรงกี่ไม้, PnL ไม่ตรงกี่ไม้, **ไม้ผี** (log มีแต่ exchange ไม่มี = บั๊ก query position), **ไม้ที่ bot พลาดไม่บันทึก**, และ PnL รวมสองฝั่งต่างกันกี่จุด
- `--dump fills.json` เก็บ fill ดิบไว้วิเคราะห์ offline ทีหลัง · `--file fills.json` อ่านจากไฟล์ ไม่ต้องต่อ network (ใช้ตอน environment บล็อก exchange API)
- test: `py -3.12 test_reconcile.py` (16 เคส, offline — รวม fake exchange ที่จำลองข้อจำกัด 7 วัน + fill ซ้ำของ order เดียว)

**📊 ผล reconcile รอบแรก (26 ก.ค. 2026 — ground truth จาก Binance, 1 มิ.ย.–26 ก.ค., 123 ไม้ปิด):**

| | ที่ bot บันทึก (93 ไม้) | **ของจริงจาก exchange (123 ไม้)** |
|---|---|---|
| ผลรวม | -17.85% | **-24.27% (-2,139.78 USDT)** |
| winrate | 30% | **35%** |
| PF | 0.34 | **0.40** |

- ❌ **สมมติฐานเดิมผิด** — เคยคาดว่า "ของจริงน่าจะดีกว่า log เพราะบั๊กตัดไม้กำไรทิ้ง" แต่**ของจริงแย่กว่า** (log ตกไป 30 ไม้ที่ bot ไม่ได้บันทึกเลย ซึ่งส่วนใหญ่เป็นไม้ขาดทุน)
- 💸 **ค่าธรรมเนียมคือครึ่งหนึ่งของการขาดทุน:** gross -1,040 USDT · fee **-1,099 USDT** → net -2,140. fee = 0.08% ของ notional ต่อไม้ (เข้า+ออก) ≈ **8.94 USDT/ไม้** เทียบกับไม้แพ้เฉลี่ย -44.84 → **fee ≈ 0.2R ต่อไม้**
- 🐛 **15 ไม้ "เปิดแล้วปิดทันทีใน <2 นาที" = -639 USDT (winrate 0%)** — คือ path "ตั้ง SL/TP ไม่ได้ (-2021) → ปิด position กันเปลือย" ของ executor. **หยุดหลัง 22 มิ.ย.** (ตรงกับตอนแก้ให้ recompute SL/TP จาก entry จริง) → ยืนยันว่าแก้ถูกจุด
- 📉 **เทียบต่อไม้กับ backtest (ตัวเลขสำคัญสุด):**

| ชุดข้อมูล | gross/ไม้ | fee/ไม้ | net/ไม้ |
|---|---|---|---|
| backtest (+9%, 74 ไม้) | ~+0.20% | -0.08% | **+0.12%** |
| จริง ทั้งหมด 123 ไม้ | -0.076% | -0.08% | -0.156% |
| จริง ตัดไม้บั๊ก 108 ไม้ | -0.043% | -0.08% | -0.123% |
| จริง เฉพาะ ก.ค. 40 ไม้ | **-0.020%** | -0.08% | -0.100% |

  → **แม้ตัดบั๊กออกหมด gross ก็ยังติดลบ** (backtest คาด +0.20%, จริง -0.02%) = edge ที่เห็นใน backtest **ไม่ปรากฏในการเทรดจริง** ส่วนต่างนี้คือ slippage ของ market order + สภาพตลาดจริง ซึ่ง backtest ไม่ได้จำลอง
  → edge ที่ backtest อ้าง (+0.12%/ไม้) บางกว่า fee (0.08%) แค่ 1.5 เท่า → **ความคลาดเคลื่อนนิดเดียวก็พลิกเป็นขาดทุน**
- 🔑 **fee ต่อ R ขึ้นกับระยะ SL:** `fee_R ≈ 0.0008 / (ระยะ SL เป็นสัดส่วนของราคา)` — SL แคบ (15m ATR) → size ใหญ่ → fee กิน R เยอะ. อยากลด fee ต่อ R ต้อง **SL กว้างขึ้น / TF สูงขึ้น / เทรดถี่น้อยลง** (เอาไปเป็นทิศทางตอน optimize รอบหน้า)
- ⚠️ **ยังทำไม่ครบ:** เทียบ exit price รายไม้ (log vs จริง) ยังทำไม่ได้ เพราะ `trade_log.json` บนเครื่อง local มีแค่ 2 record เก่า — ต้อง export CSV/JSON จาก Railway มาก่อนแล้วรัน `--log <path>`

**🔬 backtest บนช่วงเวลาเดียวกับที่ bot รันจริง (1 มิ.ย.–26 ก.ค., active params, 26 ก.ค.):**

| slippage/ข้าง | ไม้ | return | winrate | PF | Sharpe | gross/ไม้ | net/ไม้ |
|---|---|---|---|---|---|---|---|
| 0 bps | 89 | +12.61% | 48.3% | 1.58 | 1.85 | +0.216% | +0.136% |
| 2 bps | 93 | +8.36% | 46.2% | 1.35 | 1.24 | +0.169% | +0.089% |
| **5 bps** | 99 | **-0.00%** | 41.4% | 1.01 | 0.03 | +0.082% | +0.002% |
| **10 bps** | **107** | **-15.37%** | 30.8% | 0.60 | -2.41 | **-0.074%** | **-0.154%** |
| 20 bps | 115 | -33.40% | 21.7% | 0.31 | -6.07 | -0.271% | -0.351% |
| **ของจริง (ตัดไม้บั๊ก)** | **108** | — | 40.0% | 0.49 | — | **-0.043%** | **-0.123%** |
| ของจริง (ทั้งหมด) | 123 | -24.27% | 35.0% | 0.40 | — | -0.076% | -0.156% |

- 🎯 **backtest ที่ slippage 10 bps/ข้าง ≈ ของจริงเกือบทุกช่อง** (จำนวนไม้ 107 vs 108, gross/ไม้ -0.074% vs -0.076%, net -0.154% vs -0.156%) — ต่างกันแค่ winrate. ที่ 0 bps backtest บอก **+12.61%** ทั้งที่ช่วงเดียวกัน bot ขาดทุนจริง
- ❗ **จุดคุ้มทุนอยู่ที่ ~5 bps/ข้าง** — edge บางมาก ต้นทุน execution เกินนี้นิดเดียวก็ติดลบ
- 📏 **แต่ entry slippage ที่วัดได้จริง ≈ 0** (เทียบ fill กับ close ของแท่ง 15m: median -0.06 bps, ก.ค. -1.4 bps, ดีเลย์ median 38s) → **ต้นทุน 10 bps ไม่ได้อยู่ที่ขาเข้า** เหลือ 2 คำอธิบาย: (ก) ขาออก — SL/TP เป็น stop-market ที่ trigger ตอนราคาวิ่งแรง จึง fill แย่กว่าระดับที่ตั้ง (backtest เดิมสมมติได้เป๊ะ) (ข) ของจริงเทรดถี่กว่า backtest 20% (108 vs 89 ไม้) = เข้าไม้ที่ backtest ไม่นับ. **ยังพิสูจน์ไม่ได้ว่าเป็นข้อไหน** — ต้องมี SL/TP รายไม้จาก `trade_log.json` (Railway) มาเทียบกับ exit fill ถึงจะแยกได้
- ⚠️ **10 bps คือค่าที่ fit ให้ตรงผลรวม ไม่ใช่ค่าที่วัดได้** — ใช้เป็น "ต้นทุนรวมที่ backtest มองไม่เห็น" ได้ แต่อย่าอ้างว่าเป็น slippage ล้วน

**Self-learning scope:** ตอนนี้ = optimize params ของ strategy ปัจจุบัน (ยังไม่ใช่ RL discover strategy ใหม่)

**Promote (manual approve):** optimizer หา candidate → ดูใน dashboard → กด Apply → executor ใช้ params ใหม่ (ต้อง approve เสมอ ไม่ auto)

---

## Deployment (Railway)

- **Platform:** Railway (cloud, 24/7)
- **Region:** Southeast Asia (สำคัญ — ย้ายมาจาก US เพื่อแก้ geo-block ของ Binance/Bybit)
- **Entry:** `start.py` = supervisor รัน 2 process แยกกัน — `trading.live_demo` (thread) + dashboard (main) **ตัวไหนตายก็ปลุกใหม่เอง ไม่ลากอีกตัวลง** (ดู Known Issues "dashboard segfault"). live_demo = signal + Telegram alert + auto-execute (DRY_RUN guard). *(main.py = legacy signal-only, เก็บไว้)*
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
py -3.12 -m backtest.engine --limit 5000 --slippage 10     # คิดต้นทุน execution 10 bps/ข้าง
py -3.12 -m backtest.engine --limit 5000 --sweep           # เทียบหลายค่า → ดูว่า edge ทนได้แค่ไหน

# optimizer (หา strategy ดีสุด — ใช้เวลาหลายนาที)
py -3.12 -m backtest.optimizer --trials 100 --limit 2000

# live demo (DRY_RUN=true = ทดสอบ ไม่ยิง order จริง)
py -3.12 -m trading.live_demo

# วิเคราะห์สถิติ demo trades ที่เก็บจริง (offline — ไม่ต้องต่อ exchange)
py -3.12 -m analysis.trade_stats                      # อ่าน trade_log.json ตาม DATA_DIR
py -3.12 -m analysis.trade_stats --file demo_trades.csv   # CSV ที่ export จาก dashboard
py -3.12 test_trade_stats.py                          # unit test

# test lifecycle ของไม้ + state machine (offline ทั้งคู่ ไม่ต้องต่อ testnet)
py -3.12 test_executor_exit.py
py -3.12 test_live_demo_loop.py

# กระทบยอด log ที่ bot บันทึก กับ fill จริงจาก Binance (ต้องมี testnet API key)
py -3.12 -m analysis.reconcile --days 60
py -3.12 -m analysis.reconcile --days 60 --dump fills.json   # เก็บ raw ไว้ส่งต่อ/วิเคราะห์ offline
py -3.12 -m analysis.reconcile --file fills.json             # อ่านจากไฟล์ ไม่ต้องต่อ network
py -3.12 test_reconcile.py
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
- ✅ **exit price ที่บันทึกเชื่อไม่ได้ ~50% ของไม้ (แก้แล้ว 26 ก.ค. 2026 — ข้อมูลก่อนหน้านี้ใช้วัดผลไม่ได้):** ไม้ควรจบที่ SL หรือ TP เท่านั้น แต่ข้อมูล 93 ไม้แรกมี 46 ไม้ปิดที่ราคาอื่น, 8 ไม้ exit price **ซ้ำเป๊ะกับไม้อื่น**, และมีไม้บันทึก -3.15R ทั้งที่ SL cap ที่ ~-1.1R. สาเหตุ 2 อย่างเสริมกัน:
  1. **`get_open_position()` คืน `None` ทั้ง "ไม่มี position" และ "query fail"** → live_demo เข้าใจว่าไม้ปิด → `record_closed_trade()` หยิบ fill **ฝั่งตรงข้ามตัวล่าสุด** ซึ่งเป็นของ**ไม้ก่อนหน้า** (→ ราคาซ้ำ) → `clear_open_trade()` → รอบถัดไป position ไม่มี state = orphan → **ปิดทิ้งที่ market** ทั้งที่ไม้ยังดีอยู่
  2. **ไม่ cancel SL/TP ที่ค้างหลังไม้ปิด** → conditional order ของไม้เก่าไปปิดไม้ถัดไปที่ราคาไม่เกี่ยวข้อง
  - **ผลกระทบ:** ตัดไม้กำไรทิ้งอย่างเป็นระบบ (ไม้ชนะใช้เวลานาน → โดนขัดจังหวะง่ายกว่าไม้แพ้ที่ชน SL เร็ว) → สถิติ demo **แย่กว่าความจริง**
  - **ที่แก้:** `get_open_position()` raise `PositionQueryError` แทนคืน None (live_demo เจอ → ข้ามรอบ ไม่แตะไม้) · `_find_exit_fill()` กรอง fill ด้วย `entry_ms` (epoch ms ที่บันทึกตอนเปิดไม้) → หยิบเฉพาะ fill ที่เกิดหลังเปิดไม้ + VWAP ถ้าแตกหลาย fill · หา exit ไม่เจอ → บันทึก `exit_unreliable=True` **ไม่มี `pnl_pct`** (ไม่ปนในสถิติ) แทนการเดาราคา · `cancel_open_orders()` ทุกครั้งที่ไม้ปิด · `exit_reason` ใหม่มีค่า **`other`** = ปิดกลางทาง (ไม่ใช่ SL/TP) + field `exit_off_r` บอกว่าห่างจาก SL/TP กี่ R
  - ตรวจได้ด้วย `py -3.12 -m analysis.trade_stats` (section "ความน่าเชื่อของ exit price") หรือ dashboard → Demo Trades
  - 📌 **ข้อมูลใหม่หลังแก้ต้องเก็บใหม่ทั้งหมด** — อย่าเอาไม้ก่อน 26 ก.ค. มารวมวัดผล
- **`fetch_my_trades` ของ Binance futures ได้ข้อมูลไม่ครบแบบเงียบๆ (แก้แล้ว 26 ก.ค. 2026):** `/fapi/v1/userTrades` จำกัดช่วง `startTime`→`endTime` **ไม่เกิน 7 วัน** — ส่ง `since` ย้อนหลัง 60 วันไปเฉยๆ จะได้แค่ 7 วันแรก **ไม่ error ไม่เตือน** (ครั้งแรกได้ 14 fill จากของจริง 647). และห้าม dedupe fill ด้วย (orderId, เวลา, ราคา, ขนาด) — order เดียวแตกเป็นหลาย fill ที่ค่าเหล่านี้เท่ากันเป๊ะได้ ต้องใช้ **trade id** ไม่งั้น fill จริงหาย → ไล่ position แล้วไม่กลับเป็น 0 → 647 fill ประกอบได้แค่ 7 ไม้แทนที่จะเป็น 123. ทั้งสองจุดแก้ใน `fetch_fills()` แล้ว (`FETCH_WINDOW_MS` + dedupe ด้วย `trade_id`)
- ✅ **dashboard segfault ลากบอทตายไปด้วย → เว็บ 502 (แก้แล้ว 27 ก.ค. 2026):** streamlit process ตายด้วย `Fatal Python error: Segmentation fault` ที่ `pandas/core/arrays/string_arrow.py _from_sequence` ตอน `fetch_ohlcv()` สร้าง DataFrame (แค่ชื่อคอลัมน์ก็พอ) — **pandas 3 ใช้ string dtype ที่ backend เป็น pyarrow เป็น default** และ `pyarrow` ไม่ได้ถูก pin (streamlit ลากมาเอง) → build ใหม่ได้เวอร์ชันที่ไม่เข้ากัน. ที่แย่กว่านั้น: `start.py` เดิมพอ dashboard ตาย process แม่จบตาม → **bot หยุดเทรดทั้งที่ถือไม้อยู่** (เกิดจริง 27 ก.ค. ตอนถือ SHORT) แล้ว container วน crash-loop = 502
  - **ที่แก้ 3 ชั้น:** (1) `config.py` ตั้ง `pd.options.mode.string_storage = "python"` → โค้ดเราไม่แตะ pyarrow อีก · (2) pin `pyarrow==24.0.0` ใน requirements.txt · (3) `start.py` เป็น supervisor: bot กับ dashboard แยก process ใครตายปลุกใหม่เอง (+ `--server.fileWatcherType none` ประหยัดแรม)
  - **ผลข้างเคียงตอนบอทตายคาไม้:** position โดน SL/TP ปิดไปเองแต่ conditional order อีกข้างค้างบน exchange → พอบอทกลับมา branch `open_trade and not has_pos` จะ `record_closed_trade()` + `cancel_open_orders()` ล้างให้เอง (ต้องมี `open_trade.json` บน volume)
  - **ถ้ายัง segfault อีก** (streamlit แปลง DataFrame เป็น Arrow เองตอน render — คนละ path กับที่แก้): ไล่ตามลำดับ (1) downgrade `pyarrow` (2) downgrade `pandas` เป็น 2.3.x. ระหว่างนั้น bot ไม่หยุดเทรดแล้วเพราะ supervisor แยก process ให้
- **Railway auto-deploy:** ถ้า deploy ค้าง commit เก่า → เช็ค Auto Deploy ON + branch ที่ผูก, trigger redeploy manual
- **ดึง trade_log ออกจาก Railway:** filesystem ของ Railway เข้าจากข้างนอกตรงๆ ไม่ได้ → ใช้ dashboard → Demo Trades → **Export CSV** (หรือ Railway CLI: `railway run cat /data/trade_log.json > demo.json`) แล้ววิเคราะห์ด้วย `py -3.12 -m analysis.trade_stats --file demo.csv`
- **Claude Code (web) เข้า exchange API / Railway URL ไม่ได้:** environment มี egress proxy ที่บล็อก host นอก allowlist → `curl`/ccxt ได้ **403 CONNECT tunnel failed** (ไม่ใช่ geo-block ของ exchange, และ retry ไม่ช่วย). ผลคือรัน backtest/optimizer ที่ต้อง fetch OHLCV ไม่ได้ใน session นั้น → ใช้ `analysis/trade_stats.py` (offline) วิเคราะห์จากไฟล์แทน, หรือแก้ network policy ของ environment ให้ผ่าน `api.binance.com` ฯลฯ

---

## Roadmap — งานที่ต้องทำต่อ

**เสร็จแล้ว:**
- ✅ Backtest engine + metrics + Optuna optimizer (train/test split)
- ✅ Executor บน Binance testnet + live_demo deploy บน Railway (เทรดจริงมา 7 สัปดาห์)
- ✅ Dashboard: Backtest / Optimizer / Demo Trades + วิเคราะห์เชิงลึก
- ✅ trade_stats (forward validation) + reconcile (เทียบกับ fill จริง)
- ✅ แก้บั๊ก exit price / phantom close / order ค้าง (26 ก.ค.)
- ✅ reconcile กับ fill จริง + slippage model ใน backtest (26 ก.ค.) → รู้แล้วว่า backtest เดิมมองโลกสวยเกินจริงเพราะสมมติว่าได้ราคาเป๊ะ

**ทำต่อ — เรียงตามลำดับ ห้ามข้าม:**
1. ⏳ **merge branch แก้บั๊กเข้า branch ที่ Railway ผูกไว้ → redeploy** — ถ้าไม่ทำ production ยังรันโค้ดที่มีบั๊กอยู่ ข้อมูลใหม่ก็เชื่อไม่ได้เหมือนเดิม
2. ✅ **reconcile กับ fill จริง (ทำแล้ว 26 ก.ค.)** — ของจริง **-24.27% / 123 ไม้** (แย่กว่า log ที่ -17.85%) ดูตาราง "ผล reconcile รอบแรก" ด้านบน. เหลือส่วนเดียว: export `trade_log.json` จาก Railway มาเทียบ exit price รายไม้ (`--log <path>`)
3. ⏳ **เก็บข้อมูลใหม่ ≥50 ไม้** ด้วย execution ที่แก้แล้ว — ระหว่างนี้เช็คว่าไม่มี `exit_reason: "other"` โผล่ถี่ๆ, ไม่มี record `exit_unreliable` และ**ไม่มีไม้ที่ปิดภายใน <2 นาที**
4. ⏳ **re-validate:** `py -3.12 -m analysis.trade_stats` + `reconcile` เทียบกับ backtest → ตัดสินว่า strategy มี edge จริงไหม. **เกณฑ์ที่ต้องผ่าน: gross/ไม้ > +0.08%** (ไม่งั้นค่า fee กินหมด) — ข้อมูลชุดเก่าอยู่ที่ -0.02%
   - เทียบกับ backtest ต้องใช้ **`--slippage 10`** (ค่าที่ทำให้ backtest ตรงกับผลจริงของช่วง มิ.ย.–ก.ค.) ไม่ใช่ 0
   - มี trade_log จาก Railway เมื่อไหร่ → เทียบ exit fill กับ SL/TP รายไม้ เพื่อแยกว่า 10 bps มาจากขาออกหรือมาจาก "เทรดถี่กว่า backtest"
5. ⏳ **ค่อย optimize params** (`py -3.12 -m backtest.optimizer --trials 100 --slippage 10`) — **ห้ามทำก่อนข้อ 3–4** และ**ห้ามใช้ slippage 0** (จะได้ params ที่กำไรเฉพาะในโลกที่ execution สมบูรณ์แบบ). ทิศทาง: ดัน **fee+slippage ต่อ R ให้ต่ำลง** (SL กว้างขึ้น / TF สูงขึ้น / เทรดถี่น้อยลง)
6. 🔮 อนาคต: walk-forward optimization, slippage model, RL discover strategy ใหม่

**⚠️ งานที่ต้องรันบนเครื่อง local เท่านั้น** (Claude Code บน web ทำไม่ได้ — egress proxy บล็อก host นอก allowlist):
- `analysis.reconcile` (ต่อ Binance API)
- `backtest.engine` / `backtest.optimizer` / dashboard หน้า Live Signal (ต้อง fetch OHLCV)
- ที่รันได้ทุกที่ (offline ล้วน): `analysis.trade_stats`, test ทั้ง 4 ไฟล์

---

## เริ่มงานต่อบนเครื่อง local

```powershell
# 1. ดึง branch ที่มีงานล่าสุด
git fetch origin claude/trading-strategy-stats-wq7k9k
git checkout claude/trading-strategy-stats-wq7k9k

# 2. ตั้งค่า (ครั้งแรกเท่านั้น)
py -3.12 -m pip install -r requirements.txt
copy .env.example .env        # แล้วใส่ TELEGRAM_* + BINANCE_TESTNET_* ให้ครบ

# 3. เช็คว่าทุกอย่างพร้อม (offline ทั้งหมด ควรผ่าน 38/38)
py -3.12 test_trade_stats.py
py -3.12 test_executor_exit.py
py -3.12 test_live_demo_loop.py
py -3.12 test_reconcile.py

# 4. งานแรกที่ควรทำ (ดู Roadmap ข้อ 2)
py -3.12 -m analysis.reconcile --days 60
```

**ไฟล์ข้อมูลที่ไม่ได้อยู่ใน git** (gitignored — ต้องดึงจาก Railway เอง): `trade_log.json`, `open_trade.json`
→ ดึงผ่าน dashboard → Demo Trades → Export CSV (ดู Known Issues "ดึง trade_log ออกจาก Railway")
