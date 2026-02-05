# -------------------------------------------------------------------------------------------------
#  ASTS Optimization Pipeline for Nautilus Trader
# -------------------------------------------------------------------------------------------------

"""
Optimization module for parameter tuning using Optuna and walk-forward validation.

Components:
- data_fetcher: Fetch historical bars from Schwab API
- optimizer: Optuna-based parameter optimization with walk-forward
- metrics: Performance calculation (Sharpe, returns, drawdown)
"""

from .data_fetcher import SchwabDataFetcher
from .metrics import calculate_metrics
from .optimizer import NautilusOptimizer


__all__ = [
    "NautilusOptimizer",
    "SchwabDataFetcher",
    "calculate_metrics",
]
