# -------------------------------------------------------------------------------------------------
#  Performance Metrics for Backtest Analysis
# -------------------------------------------------------------------------------------------------

"""
Calculate performance metrics from backtest results.

Metrics include: Sharpe ratio, total return, max drawdown, win rate, trade statistics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd


@dataclass
class BacktestMetrics:
    """
    Container for backtest performance metrics.

    Attributes
    ----------
    sharpe_ratio : float
        Annualized Sharpe ratio (assuming risk-free rate = 0).
    total_return : float
        Total return as decimal (0.10 = 10%).
    max_drawdown : float
        Maximum drawdown as decimal (0.15 = 15% drawdown).
    num_trades : int
        Total number of completed trades.
    win_rate : float
        Percentage of winning trades (0.60 = 60% win rate).
    avg_win : float
        Average winning trade return.
    avg_loss : float
        Average losing trade return.
    profit_factor : float
        Gross profit / gross loss.
    final_equity : float
        Final account equity.
    initial_equity : float
        Starting account equity.

    """

    sharpe_ratio: float
    total_return: float
    max_drawdown: float
    num_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    final_equity: float
    initial_equity: float

    def __str__(self) -> str:
        """Format metrics as string."""
        return (
            f"Sharpe: {self.sharpe_ratio:.2f} | "
            f"Return: {self.total_return * 100:.1f}% | "
            f"MaxDD: {self.max_drawdown * 100:.1f}% | "
            f"Trades: {self.num_trades} | "
            f"WinRate: {self.win_rate * 100:.1f}%"
        )


def calculate_metrics(
    equity_curve: list[float] | np.ndarray,
    trades: list[dict] | None = None,
    initial_equity: float | None = None,
    periods_per_year: int = 252,
) -> BacktestMetrics:
    """
    Calculate backtest performance metrics.

    Parameters
    ----------
    equity_curve : list[float] or np.ndarray
        Time series of account equity values.
    trades : list[dict], optional
        List of trade dictionaries with 'pnl' or 'return' keys.
    initial_equity : float, optional
        Starting equity. If None, uses first value of equity_curve.
    periods_per_year : int, default 252
        Number of trading periods per year (252 for daily, 78 for 5min bars).

    Returns
    -------
    BacktestMetrics
        Calculated performance metrics.

    """
    equity = np.array(equity_curve, dtype=float)

    if len(equity) < 2:
        return BacktestMetrics(
            sharpe_ratio=0.0,
            total_return=0.0,
            max_drawdown=0.0,
            num_trades=0,
            win_rate=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            profit_factor=0.0,
            final_equity=equity[-1] if len(equity) > 0 else 0.0,
            initial_equity=initial_equity or (equity[0] if len(equity) > 0 else 0.0),
        )

    # Initial and final equity
    start_equity = initial_equity if initial_equity is not None else equity[0]
    end_equity = equity[-1]

    # Total return
    total_return = (end_equity - start_equity) / start_equity if start_equity > 0 else 0.0

    # Calculate returns
    returns = np.diff(equity) / equity[:-1]
    returns = np.nan_to_num(returns, nan=0.0, posinf=0.0, neginf=0.0)

    # Sharpe ratio (annualized)
    if len(returns) > 1 and np.std(returns) > 0:
        sharpe_ratio = (np.mean(returns) / np.std(returns)) * np.sqrt(periods_per_year)
    else:
        sharpe_ratio = 0.0

    # Maximum drawdown
    max_drawdown = calculate_max_drawdown(equity)

    # Trade statistics
    if trades:
        trade_returns = []
        for t in trades:
            if "return" in t:
                trade_returns.append(t["return"])
            elif "pnl" in t and "entry_value" in t:
                trade_returns.append(t["pnl"] / t["entry_value"])

        num_trades = len(trade_returns)
        if num_trades > 0:
            wins = [r for r in trade_returns if r > 0]
            losses = [r for r in trade_returns if r <= 0]

            win_rate = len(wins) / num_trades if num_trades > 0 else 0.0
            avg_win = np.mean(wins) if wins else 0.0
            avg_loss = np.mean(losses) if losses else 0.0

            gross_profit = sum(wins) if wins else 0.0
            gross_loss = abs(sum(losses)) if losses else 0.0
            profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        else:
            win_rate = 0.0
            avg_win = 0.0
            avg_loss = 0.0
            profit_factor = 0.0
    else:
        # Estimate trades from equity curve sign changes
        num_trades = estimate_trades_from_equity(equity)
        win_rate = 0.0  # Cannot calculate without trade details
        avg_win = 0.0
        avg_loss = 0.0
        profit_factor = 0.0

    return BacktestMetrics(
        sharpe_ratio=sharpe_ratio,
        total_return=total_return,
        max_drawdown=max_drawdown,
        num_trades=num_trades,
        win_rate=win_rate,
        avg_win=avg_win,
        avg_loss=avg_loss,
        profit_factor=profit_factor,
        final_equity=end_equity,
        initial_equity=start_equity,
    )


def calculate_max_drawdown(equity: np.ndarray) -> float:
    """
    Calculate maximum drawdown from equity curve.

    Parameters
    ----------
    equity : np.ndarray
        Time series of account equity values.

    Returns
    -------
    float
        Maximum drawdown as a positive decimal (0.15 = 15% drawdown).

    """
    if len(equity) < 2:
        return 0.0

    # Running maximum
    running_max = np.maximum.accumulate(equity)

    # Drawdown at each point
    drawdowns = (running_max - equity) / running_max
    drawdowns = np.nan_to_num(drawdowns, nan=0.0, posinf=0.0, neginf=0.0)

    return float(np.max(drawdowns))


def estimate_trades_from_equity(equity: np.ndarray) -> int:
    """
    Estimate number of trades from equity curve movements.

    Counts significant direction changes as trade events.

    Parameters
    ----------
    equity : np.ndarray
        Time series of account equity values.

    Returns
    -------
    int
        Estimated number of trades.

    """
    if len(equity) < 3:
        return 0

    # Calculate returns
    returns = np.diff(equity)

    # Count sign changes (rough estimate of trade entries/exits)
    sign_changes = np.sum(np.abs(np.diff(np.sign(returns))) > 0)

    # Divide by 2 (entry + exit = 1 trade)
    return max(0, sign_changes // 2)


def calculate_sortino_ratio(
    returns: np.ndarray,
    periods_per_year: int = 252,
    target_return: float = 0.0,
) -> float:
    """
    Calculate Sortino ratio (downside risk-adjusted return).

    Parameters
    ----------
    returns : np.ndarray
        Period returns.
    periods_per_year : int, default 252
        Number of trading periods per year.
    target_return : float, default 0.0
        Target return threshold.

    Returns
    -------
    float
        Annualized Sortino ratio.

    """
    excess_returns = returns - target_return
    downside_returns = returns[returns < target_return]

    if len(downside_returns) == 0:
        return float("inf") if np.mean(excess_returns) > 0 else 0.0

    downside_std = np.std(downside_returns)
    if downside_std == 0:
        return float("inf") if np.mean(excess_returns) > 0 else 0.0

    return (np.mean(excess_returns) / downside_std) * np.sqrt(periods_per_year)


def calculate_calmar_ratio(
    total_return: float,
    max_drawdown: float,
    years: float = 1.0,
) -> float:
    """
    Calculate Calmar ratio (return / max drawdown).

    Parameters
    ----------
    total_return : float
        Total return as decimal.
    max_drawdown : float
        Maximum drawdown as positive decimal.
    years : float, default 1.0
        Number of years for annualization.

    Returns
    -------
    float
        Calmar ratio.

    """
    if max_drawdown == 0:
        return float("inf") if total_return > 0 else 0.0

    annualized_return = total_return / years if years > 0 else total_return
    return annualized_return / max_drawdown


def format_metrics_table(metrics_dict: dict[str, BacktestMetrics]) -> str:
    """
    Format multiple metrics as a comparison table.

    Parameters
    ----------
    metrics_dict : dict[str, BacktestMetrics]
        Dictionary mapping timeframe/config name to metrics.

    Returns
    -------
    str
        Formatted table string.

    """
    header = "| Timeframe | Sharpe | Return | MaxDD | Trades | WinRate |"
    separator = "|-----------|--------|--------|-------|--------|---------|"

    rows = [header, separator]
    for name, m in sorted(metrics_dict.items(), key=lambda x: -x[1].sharpe_ratio):
        row = (
            f"| {name:9} | {m.sharpe_ratio:6.2f} | "
            f"{m.total_return * 100:5.1f}% | {m.max_drawdown * 100:4.1f}% | "
            f"{m.num_trades:6} | {m.win_rate * 100:6.1f}% |"
        )
        rows.append(row)

    return "\n".join(rows)
