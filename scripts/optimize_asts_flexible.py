#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  ASTS Flexible Entry Optimization Script
# -------------------------------------------------------------------------------------------------

"""
ASTS Flexible Entry Optimization

Finds optimal parameters for the Flexible Entry strategy (Supertrend + VSA)
across multiple timeframes using Bayesian optimization with walk-forward validation.

Usage:
    cd /Users/iguan/Projects/nautilus_trader
    source .venv/bin/activate
    python scripts/optimize_asts_flexible.py

Output:
    results/asts_flexible_optimization/
    ├── optimization_results.json
    ├── best_params_by_timeframe.csv
    └── summary.txt
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Determine project root
PROJECT_ROOT = Path(__file__).parent.parent

# Load environment variables first (before any path manipulation)
from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# Filter sys.path to avoid local nautilus_trader source conflicting with installed package
filtered_path = []
for p in sys.path:
    if p == str(PROJECT_ROOT) or p == str(PROJECT_ROOT / "nautilus_trader"):
        continue
    filtered_path.append(p)

# Add our custom directories
filtered_path.insert(0, str(PROJECT_ROOT / "examples"))
filtered_path.insert(0, str(PROJECT_ROOT / "schwab_adapter"))
filtered_path.insert(0, str(PROJECT_ROOT / "optimization"))
sys.path = filtered_path

# Now imports will use installed nautilus_trader package
import pandas as pd

# Import from optimization package
from data_fetcher import SchwabDataFetcher
from optimizer import NautilusOptimizer
from metrics import format_metrics_table, BacktestMetrics

# Import strategy
from strategies.flexible_entry import FlexibleEntryConfig, FlexibleEntryStrategy


# =============================================================================
# CONFIGURATION
# =============================================================================

SYMBOL = "ASTS"
TIMEFRAMES = ["5min", "15min", "30min", "1day"]
N_TRIALS = 50  # Reduce for faster runs, increase for better results
N_SPLITS = 3   # Walk-forward folds
INITIAL_CASH = 100_000

# Flexible Entry parameter search space
PARAM_SPACE = {
    "atr_period": ("int", 7, 21),                  # ATR calculation period
    "atr_multiplier": ("float", 2.0, 4.0),         # Supertrend band multiplier
    "vsa_window": ("int", 10, 30),                 # VSA rolling window
    "vsa_volume_factor": ("float", 1.2, 2.0),      # High volume threshold
    "trailing_stop_atr_mult": ("float", 1.5, 3.0), # Trailing stop ATR multiplier
    "position_size_pct": ("float", 0.15, 0.35),    # Position size %
}

# Output directory
OUTPUT_DIR = PROJECT_ROOT / "results" / "asts_flexible_optimization"


# =============================================================================
# MAIN
# =============================================================================

def main():
    """Run ASTS Flexible Entry optimization."""
    print("=" * 70)
    print("  ASTS Flexible Entry Optimization")
    print("  (Supertrend + VSA Strategy)")
    print("=" * 70)
    print()

    # Create output directory
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    # Initialize data fetcher
    try:
        fetcher = SchwabDataFetcher(
            cache_dir=PROJECT_ROOT / "data" / "cache"
        )
    except ValueError as e:
        print(f"ERROR: {e}")
        print("\nMake sure SCHWAB_APP_KEY and SCHWAB_APP_SECRET are set in .env")
        sys.exit(1)

    # Initialize optimizer
    optimizer = NautilusOptimizer(
        initial_cash=INITIAL_CASH,
        venue_name="SCHWAB",
        log_level="ERROR",
    )

    # Results storage
    all_results = {}
    best_by_timeframe = {}

    # Fetch and optimize for each timeframe
    for timeframe in TIMEFRAMES:
        print(f"\n{'=' * 50}")
        print(f"  Timeframe: {timeframe}")
        print(f"{'=' * 50}")

        # Fetch data
        bars = fetcher.fetch_bars(SYMBOL, timeframe, use_cache=True)

        if len(bars) < 50:
            print(f"  SKIPPING: Only {len(bars)} bars (need at least 50)")
            continue

        print(f"  Bars: {len(bars)}")
        print()

        # Run optimization
        try:
            result = optimizer.optimize(
                strategy_class=FlexibleEntryStrategy,
                strategy_config_class=FlexibleEntryConfig,
                bars=bars,
                param_space=PARAM_SPACE,
                symbol=SYMBOL,
                n_trials=N_TRIALS,
                n_splits=N_SPLITS,
                show_progress=True,
            )

            all_results[timeframe] = {
                "best_params": result.best_params,
                "best_sharpe": result.best_sharpe,
                "fold_results": result.fold_results,
                "n_trials": N_TRIALS,
                "n_bars": len(bars),
            }

            best_by_timeframe[timeframe] = result

        except Exception as e:
            print(f"  ERROR: {e}")
            import traceback
            traceback.print_exc()
            continue

    # Print summary
    print("\n" + "=" * 70)
    print("  OPTIMIZATION RESULTS")
    print("=" * 70)

    if not best_by_timeframe:
        print("\nNo successful optimizations. Check data and credentials.")
        sys.exit(1)

    # Create comparison table
    print("\n=== Results by Timeframe ===\n")
    print(f"| {'Timeframe':^9} | {'Sharpe':^8} | {'Best Params':^50} |")
    print(f"|{'-' * 11}|{'-' * 10}|{'-' * 52}|")

    best_timeframe = None
    best_sharpe = -float("inf")

    for tf, result in sorted(best_by_timeframe.items(), key=lambda x: -x[1].best_sharpe):
        params_str = ", ".join(f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}"
                              for k, v in list(result.best_params.items())[:4])
        print(f"| {tf:^9} | {result.best_sharpe:^8.3f} | {params_str:^50} |")

        if result.best_sharpe > best_sharpe:
            best_sharpe = result.best_sharpe
            best_timeframe = tf

    # Print recommendation
    print()
    print("=" * 70)
    print(f"  RECOMMENDATION: {best_timeframe} timeframe")
    print("=" * 70)

    if best_timeframe:
        best_result = best_by_timeframe[best_timeframe]
        print(f"\nBest Sharpe Ratio: {best_result.best_sharpe:.3f}")
        print("\nOptimal Parameters:")
        for param, value in best_result.best_params.items():
            if isinstance(value, float):
                print(f"  {param}: {value:.4f}")
            else:
                print(f"  {param}: {value}")

        # Print fold results
        print("\nWalk-Forward Validation:")
        for fold in best_result.fold_results:
            print(f"  Fold {fold['fold']}: Sharpe={fold['sharpe']:.3f}, "
                  f"Return={fold['return']*100:.1f}%, Trades={fold['trades']}")

    # Save results
    save_results(all_results, best_by_timeframe, best_timeframe)

    print(f"\nResults saved to: {OUTPUT_DIR}")


def save_results(
    all_results: dict,
    best_by_timeframe: dict,
    best_timeframe: str | None,
) -> None:
    """Save optimization results to files."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # Save JSON results
    json_path = OUTPUT_DIR / f"optimization_results_{timestamp}.json"

    # Convert numpy types to Python types for JSON
    def convert_types(obj):
        import numpy as np
        if isinstance(obj, np.integer):
            return int(obj)
        elif isinstance(obj, np.floating):
            return float(obj)
        elif isinstance(obj, dict):
            return {k: convert_types(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [convert_types(v) for v in obj]
        return obj

    with open(json_path, "w") as f:
        json.dump(convert_types(all_results), f, indent=2)
    print(f"  Saved: {json_path}")

    # Save CSV summary
    csv_path = OUTPUT_DIR / f"best_params_{timestamp}.csv"
    rows = []
    for tf, data in all_results.items():
        row = {"timeframe": tf, "sharpe": data["best_sharpe"], "n_bars": data["n_bars"]}
        row.update(data["best_params"])
        rows.append(row)

    df = pd.DataFrame(rows)
    df = df.sort_values("sharpe", ascending=False)
    df.to_csv(csv_path, index=False)
    print(f"  Saved: {csv_path}")

    # Save text summary
    txt_path = OUTPUT_DIR / f"summary_{timestamp}.txt"
    with open(txt_path, "w") as f:
        f.write("ASTS Flexible Entry Optimization Summary\n")
        f.write("=" * 50 + "\n")
        f.write(f"Date: {datetime.now().isoformat()}\n")
        f.write(f"Symbol: {SYMBOL}\n")
        f.write(f"Strategy: FlexibleEntry (Supertrend + VSA)\n")
        f.write(f"Trials per timeframe: {N_TRIALS}\n")
        f.write(f"Walk-forward folds: {N_SPLITS}\n\n")

        if best_timeframe:
            f.write(f"RECOMMENDED: {best_timeframe}\n\n")
            best = all_results[best_timeframe]
            f.write("Best Parameters:\n")
            for param, value in best["best_params"].items():
                f.write(f"  {param}: {value}\n")
            f.write(f"\nBest Sharpe: {best['best_sharpe']:.3f}\n")

    print(f"  Saved: {txt_path}")

    # Save symlink to latest
    latest_json = OUTPUT_DIR / "optimization_results_latest.json"
    if latest_json.exists():
        latest_json.unlink()
    latest_json.symlink_to(json_path.name)


if __name__ == "__main__":
    main()
