"""คำนวณ performance metrics จาก trade list"""
import numpy as np
from dataclasses import dataclass, field


@dataclass
class BacktestResult:
    trades: list = field(default_factory=list)      # list of trade dict
    metrics: dict = field(default_factory=dict)
    equity_curve: list = field(default_factory=lambda: [1.0])
    params: dict = field(default_factory=dict)

    def summary(self) -> str:
        m = self.metrics
        return (
            f"Trades: {m.get('num_trades', 0)} | "
            f"Win: {m.get('winrate', 0):.1f}% | "
            f"Return: {m.get('total_return', 0)*100:+.1f}% | "
            f"MaxDD: {m.get('max_drawdown', 0)*100:.1f}% | "
            f"PF: {m.get('profit_factor', 0):.2f} | "
            f"Sharpe: {m.get('sharpe', 0):.2f} | "
            f"Expect: {m.get('expectancy', 0)*100:+.2f}% | "
            f"Calmar: {m.get('calmar_ratio', 0):.2f}"
        )


def _max_drawdown(equity: list) -> float:
    """max peak-to-trough drop (0.2 = ลง 20%)"""
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        peak = max(peak, v)
        dd = (peak - v) / peak if peak > 0 else 0.0
        max_dd = max(max_dd, dd)
    return max_dd


_EMPTY_METRICS = {
    "num_trades": 0, "winrate": 0.0, "total_return": 0.0,
    "max_drawdown": 0.0, "profit_factor": 0.0, "sharpe": 0.0,
    "avg_win": 0.0, "avg_loss": 0.0,
    "expectancy": 0.0, "calmar_ratio": 0.0, "max_consecutive_losses": 0,
    "avg_mfe_loss": 0.0, "avg_mae_win": 0.0,
    "near_tp_miss_pct": 0.0, "near_sl_scare_pct": 0.0,
}


def compute_metrics(trades: list, params: dict) -> BacktestResult:
    if not trades:
        return BacktestResult(
            trades=[], equity_curve=[1.0], params=params,
            metrics=dict(_EMPTY_METRICS),
        )

    returns = [t["pnl_pct"] for t in trades]
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r <= 0]

    # Equity curve (compound, full position)
    equity = [1.0]
    for r in returns:
        equity.append(equity[-1] * (1 + r))

    total_return = equity[-1] - 1.0
    winrate = len(wins) / len(returns) * 100
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")
    max_dd = _max_drawdown(equity)

    # Sharpe-like score (per-trade, ไม่ annualize) — ใช้เปรียบเทียบ strategy
    arr = np.array(returns)
    sharpe = (arr.mean() / arr.std() * np.sqrt(len(arr))) if arr.std() > 0 else 0.0

    avg_win_v = float(np.mean(wins)) if wins else 0.0
    avg_loss_v = float(np.mean(losses)) if losses else 0.0

    # Expectancy: กำไรคาดหวังต่อ 1 เทรด (edge บวก = ยิ่งเทรดยิ่งกำไร)
    expectancy = round((winrate / 100) * avg_win_v + (1 - winrate / 100) * avg_loss_v, 4)

    # Calmar: return เทียบ max drawdown (สูง = กำไรคุ้มความเจ็บปวด)
    calmar = round(total_return / max_dd, 3) if max_dd > 0 else 0.0

    # Max consecutive losses
    streak = max_streak = 0
    for r in returns:
        streak = streak + 1 if r <= 0 else 0
        max_streak = max(max_streak, streak)

    # MFE/MAE aggregates
    loss_mfe = [t["mfe_pct_of_tp"] for t in trades if t["result"] == "loss" and "mfe_pct_of_tp" in t]
    win_mae  = [t["mae_pct_of_sl"] for t in trades if t["result"] == "win"  and "mae_pct_of_sl" in t]

    avg_mfe_loss      = round(float(np.mean(loss_mfe)), 3) if loss_mfe else 0.0
    avg_mae_win       = round(float(np.mean(win_mae)),  3) if win_mae  else 0.0
    near_tp_miss_pct  = round(sum(1 for v in loss_mfe if v >= 0.7) / len(loss_mfe) * 100, 1) if loss_mfe else 0.0
    near_sl_scare_pct = round(sum(1 for v in win_mae  if v >= 0.7) / len(win_mae)  * 100, 1) if win_mae  else 0.0

    metrics = {
        "num_trades": len(trades),
        "winrate": round(winrate, 2),
        "total_return": round(total_return, 4),
        "max_drawdown": round(max_dd, 4),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999.0,
        "sharpe": round(float(sharpe), 3),
        "avg_win": round(avg_win_v, 4),
        "avg_loss": round(avg_loss_v, 4),
        "expectancy": expectancy,
        "calmar_ratio": calmar,
        "max_consecutive_losses": max_streak,
        "avg_mfe_loss": avg_mfe_loss,
        "avg_mae_win": avg_mae_win,
        "near_tp_miss_pct": near_tp_miss_pct,
        "near_sl_scare_pct": near_sl_scare_pct,
    }
    return BacktestResult(trades=trades, metrics=metrics, equity_curve=equity, params=params)
