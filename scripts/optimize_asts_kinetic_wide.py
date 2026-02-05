#!/usr/bin/env python3
"""
ASTS Kinetic Trend - Wide Search then Narrow

Phase 1: Wide search to find promising regions
Phase 2: Narrow search around best params
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# Fix path
filtered_path = []
for p in sys.path:
    if p == str(PROJECT_ROOT) or p == str(PROJECT_ROOT / "nautilus_trader"):
        continue
    filtered_path.append(p)
filtered_path.insert(0, str(PROJECT_ROOT / "examples"))
filtered_path.insert(0, str(PROJECT_ROOT / "schwab_adapter"))
filtered_path.insert(0, str(PROJECT_ROOT / "optimization"))
sys.path = filtered_path

import pandas as pd
from data_fetcher import SchwabDataFetcher
from optimizer import NautilusOptimizer
from strategies.kinetic_trend import KineticTrendConfig, KineticTrendStrategy

SYMBOL = "ASTS"
INITIAL_CASH = 100_000
OUTPUT_DIR = PROJECT_ROOT / "results" / "asts_kinetic_wide"

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    fetcher = SchwabDataFetcher(cache_dir=PROJECT_ROOT / "data" / "cache")
    optimizer = NautilusOptimizer(initial_cash=INITIAL_CASH, log_level="ERROR")

    # Fetch 5min data (best performer)
    bars = fetcher.fetch_bars(SYMBOL, "5min", use_cache=True)
    print(f"Loaded {len(bars)} bars\n")

    # =========================================================================
    # PHASE 1: WIDE SEARCH
    # =========================================================================
    print("=" * 60)
    print("  PHASE 1: WIDE SEARCH")
    print("=" * 60)

    wide_param_space = {
        "bb_period": ("int", 5, 50),              # Very wide: 5-50
        "bb_std": ("float", 1.0, 4.0),            # Wide: 1-4 std
        "atr_period": ("int", 7, 21),             # ATR period
        "atr_stop_multiplier": ("float", 1.0, 4.0),  # Stop loss ATR mult
        "max_pyramid_levels": ("int", 1, 7),      # 1-7 levels
        "pyramid_threshold": ("float", 0.005, 0.10),  # 0.5%-10%
        "position_size_pct": ("float", 0.05, 0.50),   # 5%-50%
        "profit_target_pct": ("float", 0.03, 0.30),   # 3%-30%
    }

    print("\nWide ranges:")
    for k, v in wide_param_space.items():
        print(f"  {k}: {v[1]} - {v[2]}")
    print()

    wide_result = optimizer.optimize(
        strategy_class=KineticTrendStrategy,
        strategy_config_class=KineticTrendConfig,
        bars=bars,
        param_space=wide_param_space,
        symbol=SYMBOL,
        n_trials=75,
        n_splits=3,
        show_progress=True,
    )

    print(f"\n  Wide Search Best Sharpe: {wide_result.best_sharpe:.3f}")
    print(f"  Wide Search Best Params: {wide_result.best_params}")

    # =========================================================================
    # PHASE 2: NARROW SEARCH around best params
    # =========================================================================
    print("\n" + "=" * 60)
    print("  PHASE 2: NARROW SEARCH (around best params)")
    print("=" * 60)

    # Build narrow ranges: ±30% around best values
    best = wide_result.best_params

    def narrow_int(val, pct=0.3, min_val=1, max_val=100):
        delta = max(1, int(val * pct))
        return ("int", max(min_val, val - delta), min(max_val, val + delta))

    def narrow_float(val, pct=0.3, min_val=0.01, max_val=1.0):
        delta = val * pct
        return ("float", max(min_val, val - delta), min(max_val, val + delta))

    narrow_param_space = {
        "bb_period": narrow_int(best["bb_period"], 0.3, 3, 60),
        "bb_std": narrow_float(best["bb_std"], 0.3, 0.5, 5.0),
        "max_pyramid_levels": narrow_int(best["max_pyramid_levels"], 0.5, 1, 10),
        "pyramid_threshold": narrow_float(best["pyramid_threshold"], 0.3, 0.001, 0.15),
        "position_size_pct": narrow_float(best["position_size_pct"], 0.3, 0.05, 0.60),
        "profit_target_pct": narrow_float(best["profit_target_pct"], 0.3, 0.02, 0.40),
    }

    print("\nNarrow ranges (±30% around best):")
    for k, v in narrow_param_space.items():
        print(f"  {k}: {v[1]:.4f} - {v[2]:.4f}" if isinstance(v[1], float) else f"  {k}: {v[1]} - {v[2]}")
    print()

    narrow_result = optimizer.optimize(
        strategy_class=KineticTrendStrategy,
        strategy_config_class=KineticTrendConfig,
        bars=bars,
        param_space=narrow_param_space,
        symbol=SYMBOL,
        n_trials=75,
        n_splits=3,
        show_progress=True,
    )

    # =========================================================================
    # RESULTS
    # =========================================================================
    print("\n" + "=" * 60)
    print("  FINAL RESULTS")
    print("=" * 60)

    print(f"\n  Wide Search:   Sharpe = {wide_result.best_sharpe:.3f}")
    print(f"  Narrow Search: Sharpe = {narrow_result.best_sharpe:.3f}")

    # Use the better one
    if narrow_result.best_sharpe >= wide_result.best_sharpe:
        final_result = narrow_result
        print("\n  Using NARROW search results (better)")
    else:
        final_result = wide_result
        print("\n  Using WIDE search results (better)")

    print(f"\n  BEST SHARPE: {final_result.best_sharpe:.3f}")
    print("\n  BEST PARAMS:")
    for k, v in final_result.best_params.items():
        if isinstance(v, float):
            print(f"    {k}: {v:.4f}")
        else:
            print(f"    {k}: {v}")

    print("\n  WALK-FORWARD VALIDATION:")
    total_return = 0
    total_trades = 0
    for fold in final_result.fold_results:
        print(f"    Fold {fold['fold']}: Return={fold['return']*100:.2f}%, Trades={fold['trades']}")
        total_return += fold['return']
        total_trades += fold['trades']

    avg_return = total_return / len(final_result.fold_results) if final_result.fold_results else 0
    avg_trades = total_trades / len(final_result.fold_results) if final_result.fold_results else 0

    print(f"\n  AVERAGE RETURN PER FOLD: {avg_return*100:.2f}%")
    print(f"  AVERAGE TRADES PER FOLD: {avg_trades:.1f}")

    # Save results
    results = {
        "wide_search": {
            "best_sharpe": wide_result.best_sharpe,
            "best_params": wide_result.best_params,
            "fold_results": wide_result.fold_results,
        },
        "narrow_search": {
            "best_sharpe": narrow_result.best_sharpe,
            "best_params": narrow_result.best_params,
            "fold_results": narrow_result.fold_results,
        },
        "final": {
            "best_sharpe": final_result.best_sharpe,
            "best_params": final_result.best_params,
            "fold_results": final_result.fold_results,
            "avg_return_per_fold": avg_return,
            "avg_trades_per_fold": avg_trades,
        }
    }

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    with open(OUTPUT_DIR / f"results_{timestamp}.json", "w") as f:
        json.dump(results, f, indent=2, default=float)

    print(f"\n  Results saved to: {OUTPUT_DIR}")


if __name__ == "__main__":
    main()
