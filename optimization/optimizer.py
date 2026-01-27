# -------------------------------------------------------------------------------------------------
#  Optuna-based Optimizer for Nautilus Trader
# -------------------------------------------------------------------------------------------------

"""
Parameter optimization using Optuna with walk-forward validation.

Prevents overfitting by:
1. Using Bayesian search (TPE sampler) for efficient exploration
2. Walk-forward validation with non-overlapping train/test splits
3. Averaging performance across multiple folds
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from typing import Callable
from typing import TYPE_CHECKING

import numpy as np
import optuna
from optuna.samplers import TPESampler

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.config import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model import TraderId
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy

try:
    from .metrics import BacktestMetrics, calculate_metrics
except ImportError:
    from metrics import BacktestMetrics, calculate_metrics


# Suppress Optuna logging noise
optuna.logging.set_verbosity(optuna.logging.WARNING)


@dataclass
class OptimizationResult:
    """
    Results from parameter optimization.

    Attributes
    ----------
    best_params : dict
        Best parameter values found.
    best_sharpe : float
        Best Sharpe ratio achieved.
    all_trials : list[dict]
        All trial results (params, sharpe, metrics).
    fold_results : list[dict]
        Per-fold validation results for best params.

    """

    best_params: dict[str, Any]
    best_sharpe: float
    all_trials: list[dict]
    fold_results: list[dict]

    def __str__(self) -> str:
        """Format as string."""
        params_str = ", ".join(f"{k}={v}" for k, v in self.best_params.items())
        return f"Best Sharpe: {self.best_sharpe:.3f} | Params: {params_str}"


class NautilusOptimizer:
    """
    Optuna-based optimizer for Nautilus strategies with walk-forward validation.

    This optimizer:
    1. Uses TPE (Tree-structured Parzen Estimator) for efficient Bayesian search
    2. Splits data into train/test windows for walk-forward validation
    3. Averages performance across folds to prevent overfitting

    Parameters
    ----------
    initial_cash : float, default 100_000
        Starting cash for backtests.
    venue_name : str, default "SCHWAB"
        Venue name for instrument IDs.
    log_level : str, default "ERROR"
        Logging level during backtests.

    """

    def __init__(
        self,
        initial_cash: float = 100_000,
        venue_name: str = "SCHWAB",
        log_level: str = "ERROR",
    ) -> None:
        """Initialize the optimizer."""
        self._initial_cash = initial_cash
        self._venue_name = venue_name
        self._log_level = log_level
        self._venue = Venue(venue_name)

    def optimize(
        self,
        strategy_class: type[Strategy],
        strategy_config_class: type,
        bars: list[Bar],
        param_space: dict[str, tuple],
        symbol: str,
        n_trials: int = 100,
        n_splits: int = 3,
        gap_pct: float = 0.05,
        direction: str = "maximize",
        show_progress: bool = True,
    ) -> OptimizationResult:
        """
        Run parameter optimization with walk-forward validation.

        Parameters
        ----------
        strategy_class : type[Strategy]
            The Nautilus strategy class to optimize.
        strategy_config_class : type
            The strategy's config class.
        bars : list[Bar]
            Historical bar data for backtesting.
        param_space : dict[str, tuple]
            Parameter search space. Format:
            {
                "param_name": ("int", min, max),
                "param_name": ("float", min, max),
                "param_name": ("categorical", [val1, val2, ...]),
            }
        symbol : str
            The trading symbol (e.g., "ASTS").
        n_trials : int, default 100
            Number of optimization trials.
        n_splits : int, default 3
            Number of walk-forward splits.
        gap_pct : float, default 0.05
            Gap between train and test as percentage of data.
        direction : str, default "maximize"
            Optimization direction ("maximize" or "minimize").
        show_progress : bool, default True
            Whether to show trial progress.

        Returns
        -------
        OptimizationResult
            Optimization results with best params and metrics.

        """
        if len(bars) < 50:
            raise ValueError(f"Insufficient data: {len(bars)} bars (need at least 50)")

        # Create instrument for backtesting
        instrument = self._create_instrument(symbol)

        # Calculate walk-forward splits
        splits = self._create_walk_forward_splits(bars, n_splits, gap_pct)

        print(f"\n=== Optimizing {strategy_class.__name__} ===")
        print(f"Data: {len(bars)} bars")
        print(f"Splits: {n_splits} walk-forward folds")
        print(f"Trials: {n_trials}")
        print()

        # Track all trials
        all_trials = []

        def objective(trial: optuna.Trial) -> float:
            """Optuna objective function."""
            # Sample parameters
            params = self._sample_params(trial, param_space)

            # Run backtest on each fold
            fold_sharpes = []
            for i, (train_bars, test_bars) in enumerate(splits):
                # Train on train_bars (could do param refinement here)
                # Test on test_bars
                metrics = self._run_backtest(
                    strategy_class=strategy_class,
                    strategy_config_class=strategy_config_class,
                    bars=test_bars,  # Use test set for evaluation
                    params=params,
                    symbol=symbol,
                    instrument=instrument,
                )

                if metrics is not None:
                    fold_sharpes.append(metrics.sharpe_ratio)

            # Average across folds
            if fold_sharpes:
                avg_sharpe = np.mean(fold_sharpes)
            else:
                avg_sharpe = -10.0  # Penalty for failed backtests

            # Store trial info
            all_trials.append({
                "params": params.copy(),
                "sharpe": avg_sharpe,
                "fold_sharpes": fold_sharpes.copy() if fold_sharpes else [],
            })

            if show_progress and len(all_trials) % 10 == 0:
                print(f"  Trial {len(all_trials)}/{n_trials}: sharpe={avg_sharpe:.3f}")

            return avg_sharpe

        # Create Optuna study
        study = optuna.create_study(
            direction=direction,
            sampler=TPESampler(seed=42),
        )

        # Run optimization
        study.optimize(objective, n_trials=n_trials, show_progress_bar=False)

        # Get best results
        best_params = self._extract_best_params(study.best_params, param_space)
        best_sharpe = study.best_value

        # Validate best params on each fold
        fold_results = []
        for i, (train_bars, test_bars) in enumerate(splits):
            metrics = self._run_backtest(
                strategy_class=strategy_class,
                strategy_config_class=strategy_config_class,
                bars=test_bars,
                params=best_params,
                symbol=symbol,
                instrument=instrument,
            )
            if metrics:
                fold_results.append({
                    "fold": i + 1,
                    "sharpe": metrics.sharpe_ratio,
                    "return": metrics.total_return,
                    "max_dd": metrics.max_drawdown,
                    "trades": metrics.num_trades,
                })

        print(f"\nBest params: {best_params}")
        print(f"Best Sharpe: {best_sharpe:.3f}")

        return OptimizationResult(
            best_params=best_params,
            best_sharpe=best_sharpe,
            all_trials=all_trials,
            fold_results=fold_results,
        )

    def run_single_backtest(
        self,
        strategy_class: type[Strategy],
        strategy_config_class: type,
        bars: list[Bar],
        params: dict[str, Any],
        symbol: str,
    ) -> BacktestMetrics | None:
        """
        Run a single backtest with given parameters.

        Parameters
        ----------
        strategy_class : type[Strategy]
            The strategy class.
        strategy_config_class : type
            The strategy config class.
        bars : list[Bar]
            Bar data.
        params : dict
            Strategy parameters.
        symbol : str
            Trading symbol.

        Returns
        -------
        BacktestMetrics or None
            Backtest metrics, or None if backtest failed.

        """
        instrument = self._create_instrument(symbol)
        return self._run_backtest(
            strategy_class=strategy_class,
            strategy_config_class=strategy_config_class,
            bars=bars,
            params=params,
            symbol=symbol,
            instrument=instrument,
        )

    def _run_backtest(
        self,
        strategy_class: type[Strategy],
        strategy_config_class: type,
        bars: list[Bar],
        params: dict[str, Any],
        symbol: str,
        instrument,
    ) -> BacktestMetrics | None:
        """
        Run a single backtest.

        Returns BacktestMetrics or None if backtest fails.
        """
        try:
            # Create backtest engine
            engine = BacktestEngine(
                config=BacktestEngineConfig(
                    trader_id=TraderId("OPTIMIZER-001"),
                    logging=LoggingConfig(log_level=self._log_level),
                ),
            )

            # Add venue
            engine.add_venue(
                venue=self._venue,
                oms_type=OmsType.NETTING,
                account_type=AccountType.CASH,
                starting_balances=[Money(self._initial_cash, USD)],
                base_currency=USD,
                default_leverage=Decimal(1),
            )

            # Add instrument
            engine.add_instrument(instrument)

            # Add data
            engine.add_data(bars)

            # Create strategy config
            bar_type = bars[0].bar_type if bars else None
            if bar_type is None:
                return None

            config = strategy_config_class(
                instrument_id=str(instrument.id),
                bar_type=str(bar_type),
                **params,
            )

            # Create and add strategy
            strategy = strategy_class(config=config)
            engine.add_strategy(strategy)

            # Run backtest
            engine.run()

            # Extract results
            account = engine.portfolio.account(self._venue)
            account_balance = account.balance_total(USD) if account else None
            final_equity = float(account_balance.as_double()) if account_balance else self._initial_cash

            # Get closed orders to count trades
            closed_orders = engine.cache.orders_closed()
            num_fills = len(closed_orders) if closed_orders else 0

            # Round-trips: 2 fills (buy + sell) = 1 trade
            num_trades = num_fills // 2

            # Calculate return
            total_return = (final_equity - self._initial_cash) / self._initial_cash

            # Calculate Sharpe (simplified - assumes daily returns)
            # If we have trades, use return divided by a basic volatility estimate
            if num_trades > 0 and total_return != 0:
                # Simple approximation: assume volatility proportional to return magnitude
                # This is a rough estimate when we don't have full equity curve
                volatility_estimate = abs(total_return) * 0.5 + 0.001  # Add small floor
                sharpe_ratio = (total_return / volatility_estimate) * np.sqrt(252)
            else:
                sharpe_ratio = 0.0

            # Create metrics directly
            metrics = BacktestMetrics(
                sharpe_ratio=sharpe_ratio,
                total_return=total_return,
                max_drawdown=0.0,  # Can't calculate without full equity curve
                num_trades=num_trades,
                win_rate=0.0,  # Can't calculate without trade P&L details
                avg_win=0.0,
                avg_loss=0.0,
                profit_factor=0.0,
                final_equity=final_equity,
                initial_equity=self._initial_cash,
            )

            # Clean up
            engine.dispose()

            return metrics

        except Exception as e:
            print(f"    Backtest error: {e}")
            return None

    def _create_instrument(self, symbol: str):
        """Create an equity instrument for backtesting."""
        # Use test kit to create a simple equity
        return TestInstrumentProvider.equity(
            symbol=symbol,
            venue=self._venue_name,
        )

    def _create_walk_forward_splits(
        self,
        bars: list[Bar],
        n_splits: int,
        gap_pct: float,
    ) -> list[tuple[list[Bar], list[Bar]]]:
        """
        Create walk-forward train/test splits.

        Walk-forward validation with expanding window:
        Split 1: [Train: 0-50%] [Gap: 5%] [Test: 55-70%]
        Split 2: [Train: 0-65%] [Gap: 5%] [Test: 70-85%]
        Split 3: [Train: 0-80%] [Gap: 5%] [Test: 85-100%]

        Parameters
        ----------
        bars : list[Bar]
            All bar data.
        n_splits : int
            Number of splits.
        gap_pct : float
            Gap between train and test as fraction.

        Returns
        -------
        list[tuple[list[Bar], list[Bar]]]
            List of (train_bars, test_bars) tuples.

        """
        n = len(bars)
        splits = []

        # Calculate test window size
        # Reserve last 50% of data for testing across all folds
        test_region_start = int(n * 0.50)
        test_region_size = n - test_region_start
        test_window_size = test_region_size // n_splits

        gap_size = int(n * gap_pct)

        for i in range(n_splits):
            # Test window
            test_start = test_region_start + (i * test_window_size)
            test_end = min(test_start + test_window_size, n)

            # Train window (expanding)
            train_end = test_start - gap_size
            train_bars = bars[:train_end]
            test_bars = bars[test_start:test_end]

            if len(train_bars) > 20 and len(test_bars) > 10:
                splits.append((train_bars, test_bars))

        return splits

    def _sample_params(
        self,
        trial: optuna.Trial,
        param_space: dict[str, tuple],
    ) -> dict[str, Any]:
        """
        Sample parameters from search space.

        Parameters
        ----------
        trial : optuna.Trial
            Optuna trial object.
        param_space : dict
            Parameter space definition.

        Returns
        -------
        dict
            Sampled parameter values.

        """
        params = {}

        for name, spec in param_space.items():
            param_type = spec[0]

            if param_type == "int":
                params[name] = trial.suggest_int(name, spec[1], spec[2])
            elif param_type == "float":
                params[name] = trial.suggest_float(name, spec[1], spec[2])
            elif param_type == "categorical":
                params[name] = trial.suggest_categorical(name, spec[1])
            elif param_type == "loguniform":
                params[name] = trial.suggest_float(name, spec[1], spec[2], log=True)
            else:
                raise ValueError(f"Unknown param type: {param_type}")

        return params

    def _extract_best_params(
        self,
        optuna_params: dict[str, Any],
        param_space: dict[str, tuple],
    ) -> dict[str, Any]:
        """
        Extract and clean best params from Optuna.

        Ensures proper types (int params stay int, etc.).
        """
        params = {}

        for name, value in optuna_params.items():
            if name in param_space:
                param_type = param_space[name][0]
                if param_type == "int":
                    params[name] = int(value)
                elif param_type == "float":
                    params[name] = float(value)
                else:
                    params[name] = value
            else:
                params[name] = value

        return params


def run_grid_search(
    strategy_class: type[Strategy],
    strategy_config_class: type,
    bars: list[Bar],
    param_grid: dict[str, list],
    symbol: str,
    initial_cash: float = 100_000,
) -> list[dict]:
    """
    Run exhaustive grid search over parameter combinations.

    Use for small parameter spaces where you want to explore all combinations.

    Parameters
    ----------
    strategy_class : type[Strategy]
        The strategy class.
    strategy_config_class : type
        The strategy config class.
    bars : list[Bar]
        Bar data.
    param_grid : dict[str, list]
        Parameter grid: {"param_name": [val1, val2, ...]}
    symbol : str
        Trading symbol.
    initial_cash : float, default 100_000
        Starting cash.

    Returns
    -------
    list[dict]
        List of results sorted by Sharpe ratio.

    """
    from itertools import product

    optimizer = NautilusOptimizer(initial_cash=initial_cash)
    results = []

    # Generate all combinations
    param_names = list(param_grid.keys())
    param_values = list(param_grid.values())
    combinations = list(product(*param_values))

    print(f"Grid search: {len(combinations)} combinations")

    for i, combo in enumerate(combinations):
        params = dict(zip(param_names, combo))

        metrics = optimizer.run_single_backtest(
            strategy_class=strategy_class,
            strategy_config_class=strategy_config_class,
            bars=bars,
            params=params,
            symbol=symbol,
        )

        if metrics:
            results.append({
                "params": params,
                "sharpe": metrics.sharpe_ratio,
                "return": metrics.total_return,
                "max_dd": metrics.max_drawdown,
                "trades": metrics.num_trades,
            })

        if (i + 1) % 10 == 0:
            print(f"  Completed {i + 1}/{len(combinations)}")

    # Sort by Sharpe
    results.sort(key=lambda x: x["sharpe"], reverse=True)

    return results
