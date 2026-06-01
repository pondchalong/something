"""
Optuna optimizer — "เรียนรู้เอง" หา parameter combo ที่ดีสุด

- Search space: atr_multiplier, risk_reward, confluence_min, st_factor, timeframe
- Objective: Sharpe-like score บน train set (penalize ถ้าเทรดน้อยเกิน — กัน overfit)
- Train/test split 70/30 (ตามเวลา): คำนวณ indicator บน full df ครั้งเดียว แล้ว
  backtest train portion [warmup, k) และ test portion [k, n) — test ได้ indicator
  ที่ warm จาก train แล้ว (ไม่เสีย candle ไปกับ warmup ซ้ำ)
- Best params → save เป็น candidate (รอ manual approve ใน dashboard)
"""
import argparse
import optuna
from analysis.indicators import add_indicators
from data.fetcher import fetch_ohlcv, fetch_htf_ohlcv
from strategy.params import StrategyParams
from backtest.engine import simulate, FEE, WARMUP
from backtest.results import save_candidate

TIMEFRAMES = ["15m", "30m", "1h"]
TRAIN_RATIO = 0.7
MIN_TRADES = 10   # train เทรดน้อยกว่านี้ = penalize (sample เชื่อไม่ได้)


def _prepare(symbol: str, limit: int) -> dict:
    """pre-fetch raw OHLCV ทุก timeframe (fetch ครั้งเดียว ไม่ fetch ซ้ำใน loop)"""
    cache = {}
    for tf in TIMEFRAMES:
        print(f"  fetching {tf} ...")
        df = fetch_ohlcv(symbol=symbol, timeframe=tf, limit=limit)
        df_htf = fetch_htf_ohlcv(symbol=symbol, timeframe=tf, limit=limit)
        cache[tf] = (df, df_htf)
    return cache


def _split_backtest(full_df, df_htf, params):
    """add_indicators บน full df → simulate train + test portion"""
    df_ind = add_indicators(full_df, df_htf, params)
    n = len(df_ind)
    k = int(n * TRAIN_RATIO)
    train_res = simulate(df_ind, params, FEE, WARMUP, k)
    test_res = simulate(df_ind, params, FEE, max(k, WARMUP), n)
    return train_res, test_res


def optimize(symbol: str = "BTC/USDT", limit: int = 1500, n_trials: int = 30) -> dict:
    print(f"Preparing data ({symbol}) ...")
    cache = _prepare(symbol, limit)

    def objective(trial):
        tf = trial.suggest_categorical("timeframe", TIMEFRAMES)
        params = StrategyParams(
            atr_multiplier=trial.suggest_float("atr_multiplier", 1.0, 3.0, step=0.1),
            risk_reward=trial.suggest_float("risk_reward", 1.5, 3.5, step=0.1),
            confluence_min=trial.suggest_int("confluence_min", 2, 5),
            st_factor=trial.suggest_float("st_factor", 2.0, 5.0, step=0.5),
            timeframe=tf,
        )
        full_df, df_htf = cache[tf]
        train_res, _ = _split_backtest(full_df, df_htf, params)
        if train_res.metrics["num_trades"] < MIN_TRADES:
            return -5.0
        return train_res.metrics["sharpe"]

    study = optuna.create_study(direction="maximize")
    study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

    # Best params → eval บน train + test (indicator warm จาก full df)
    best = StrategyParams.from_dict(study.best_params)
    full_df, df_htf = cache[best.timeframe]
    train_res, test_res = _split_backtest(full_df, df_htf, best)

    save_candidate(best.to_dict(), train_res.metrics, test_res.metrics)

    print("\n" + "=" * 60)
    print(f"Best params: {best.to_dict()}")
    print(f"TRAIN: {train_res.summary()}")
    print(f"TEST:  {test_res.summary()}")

    # Overfit check (ASCII only — Windows console cp874 encode emoji ไม่ได้)
    tr_sharpe = train_res.metrics["sharpe"]
    te_sharpe = test_res.metrics["sharpe"]
    if tr_sharpe > 0 and te_sharpe < tr_sharpe * 0.5:
        print("[WARN] Test Sharpe ตกเกินครึ่งจาก Train -> อาจ overfit, ใช้ด้วยความระวัง")
    elif te_sharpe > 0:
        print("[OK] Test ยัง positive -> params robust พอควร")
    else:
        print("[WARN] Test ไม่กำไร -> params อาจ overfit หรือ market เปลี่ยน")
    print("=" * 60)

    return {"params": best.to_dict(), "train": train_res.metrics, "test": test_res.metrics}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTC/USDT")
    ap.add_argument("--limit", type=int, default=1500)
    ap.add_argument("--trials", type=int, default=30)
    args = ap.parse_args()
    optimize(args.symbol, args.limit, args.trials)


if __name__ == "__main__":
    main()
