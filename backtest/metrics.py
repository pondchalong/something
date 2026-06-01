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
            f"Sharpe: {m.get('sharpe', 0):.2f}"
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


def compute_metrics(trades: list, params: dict) -> BacktestResult:
    if not trades:
        return BacktestResult(
            trades=[], equity_curve=[1.0], params=params,
            metrics={"num_trades": 0, "winrate": 0.0, "total_return": 0.0,
                     "max_drawdown": 0.0, "profit_factor": 0.0, "sharpe": 0.0,
                     "avg_win": 0.0, "avg_loss": 0.0},
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

    # Sharpe-like score (per-trade, ไม่ annualize) — ใช้เปรียบเทียบ strategy
    arr = np.array(returns)
    sharpe = (arr.mean() / arr.std() * np.sqrt(len(arr))) if arr.std() > 0 else 0.0

    metrics = {
        "num_trades": len(trades),
        "winrate": round(winrate, 2),
        "total_return": round(total_return, 4),
        "max_drawdown": round(_max_drawdown(equity), 4),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999.0,
        "sharpe": round(float(sharpe), 3),
        "avg_win": round(float(np.mean(wins)), 4) if wins else 0.0,
        "avg_loss": round(float(np.mean(losses)), 4) if losses else 0.0,
    }
    return BacktestResult(trades=trades, metrics=metrics, equity_curve=equity, params=params)
