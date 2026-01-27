#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  SuperStrat Live Trading (Signal Mode)
#
#  Streams live ASTS data from Schwab and generates trading signals.
#  Execution must be done manually or via webhook (Schwab execution not yet implemented).
# -------------------------------------------------------------------------------------------------

"""
SuperStrat Live Trading Script

Usage:
    cd /Users/iguan/Projects/nautilus_trader
    source .venv/bin/activate

    # Set credentials (or use .env file)
    export SCHWAB_APP_KEY="your_app_key"
    export SCHWAB_APP_SECRET="your_app_secret"

    # Run
    python examples/schwab_superstrat_live.py

Timeframe Options (set TIMEFRAME env var):
    - 5min  (recommended - best backtest results)
    - 15min
    - 30min
    - 1day
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Setup path for local imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "schwab_adapter"))
sys.path.insert(0, str(PROJECT_ROOT / "examples"))

from nautilus_trader.config import LoggingConfig
from nautilus_trader.live.config import TradingNodeConfig
from nautilus_trader.live.config import LiveDataEngineConfig
from nautilus_trader.live.node import TradingNode

from nautilus_schwab import SchwabDataClientConfig
from nautilus_schwab import SchwabLiveDataClientFactory
from nautilus_trader.config import InstrumentProviderConfig

from strategies.super_strat import SuperStratConfig, SuperStratStrategy


# =============================================================================
# CONFIGURATION
# =============================================================================

SYMBOL = os.environ.get("SYMBOL", "ASTS")
TIMEFRAME = os.environ.get("TIMEFRAME", "5min")  # Best backtest results

# Schwab credentials
SCHWAB_APP_KEY = os.environ.get("SCHWAB_APP_KEY")
SCHWAB_APP_SECRET = os.environ.get("SCHWAB_APP_SECRET")
SCHWAB_CALLBACK_URL = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1")
SCHWAB_TOKENS_DB = os.environ.get("SCHWAB_TOKENS_DB", str(Path.home() / ".schwabdev" / "tokens.db"))

# Optimal parameters from optimization (5min timeframe)
OPTIMAL_PARAMS_5MIN = {
    "atr_period": 27,
    "atr_multiplier": 3.54,
    "vsa_window": 46,
    "vsa_volume_factor": 1.79,
    "trailing_stop_atr_mult": 1.89,
    "position_size_pct": 0.43,
    "donchian_period": 33,
    "pyramid_atr_threshold": 1.45,
    "pyramid_size_pct": 0.50,
}

# Optimal parameters for 1day timeframe
OPTIMAL_PARAMS_1DAY = {
    "atr_period": 13,
    "atr_multiplier": 1.74,
    "vsa_window": 10,
    "vsa_volume_factor": 1.16,
    "trailing_stop_atr_mult": 1.74,
    "position_size_pct": 0.19,
    "donchian_period": 48,
    "pyramid_atr_threshold": 0.62,
    "pyramid_size_pct": 0.78,
}

# Timeframe to bar type mapping
TIMEFRAME_MAP = {
    "5min": "5-MINUTE",
    "15min": "15-MINUTE",
    "30min": "30-MINUTE",
    "1day": "1-DAY",
}


def get_optimal_params(timeframe: str) -> dict:
    """Get optimal parameters for the specified timeframe."""
    if timeframe == "5min":
        return OPTIMAL_PARAMS_5MIN
    elif timeframe == "1day":
        return OPTIMAL_PARAMS_1DAY
    else:
        # Use 5min params as default
        print(f"WARNING: No optimized params for {timeframe}, using 5min params")
        return OPTIMAL_PARAMS_5MIN


def main():
    """Run SuperStrat live trading."""
    # Validate credentials
    if not SCHWAB_APP_KEY or not SCHWAB_APP_SECRET:
        print("ERROR: Set SCHWAB_APP_KEY and SCHWAB_APP_SECRET environment variables")
        sys.exit(1)

    print("=" * 70)
    print("  SuperStrat Live Trading (Signal Mode)")
    print("=" * 70)
    print(f"  Symbol: {SYMBOL}")
    print(f"  Timeframe: {TIMEFRAME}")
    print(f"  Mode: SIGNAL ONLY (no execution)")
    print("=" * 70)
    print()

    # Get optimal parameters
    params = get_optimal_params(TIMEFRAME)
    print("Using optimized parameters:")
    for k, v in params.items():
        print(f"  {k}: {v}")
    print()

    # Build bar type string
    bar_type_suffix = TIMEFRAME_MAP.get(TIMEFRAME, "5-MINUTE")
    instrument_id = f"{SYMBOL}.SCHWAB"
    bar_type = f"{instrument_id}-{bar_type_suffix}-LAST-EXTERNAL"

    # Configure Schwab data client
    schwab_config = SchwabDataClientConfig(
        app_key=SCHWAB_APP_KEY,
        app_secret=SCHWAB_APP_SECRET,
        callback_url=SCHWAB_CALLBACK_URL,
        tokens_db=SCHWAB_TOKENS_DB,
        use_websocket=True,
        instrument_provider=InstrumentProviderConfig(
            load_all=False,
        ),
    )

    # Configure trading node
    node_config = TradingNodeConfig(
        trader_id=f"SUPERSTRAT-{SYMBOL}-001",
        logging=LoggingConfig(log_level="INFO"),
        data_clients={"SCHWAB": schwab_config},
        data_engine=LiveDataEngineConfig(
            time_bars_build_with_no_updates=True,
            time_bars_timestamp_on_close=True,
        ),
        # No exec_clients - signal mode only
    )

    # Configure strategy
    strategy_config = SuperStratConfig(
        strategy_id=f"SUPERSTRAT-{SYMBOL}",
        instrument_id=instrument_id,
        bar_type=bar_type,
        **params,
    )

    # Build and run
    print("Building trading node...")
    node = TradingNode(config=node_config)
    node.add_data_client_factory("SCHWAB", SchwabLiveDataClientFactory)
    node.build()

    print("Adding SuperStrat strategy...")
    strategy = SuperStratStrategy(config=strategy_config)
    node.trader.add_strategy(strategy)

    print()
    print("=" * 70)
    print("  STARTING LIVE TRADING")
    print("  Press Ctrl+C to stop")
    print("=" * 70)
    print()

    try:
        node.run()
    except KeyboardInterrupt:
        print("\nShutdown requested...")
    finally:
        node.dispose()
        print("Trading node disposed.")


if __name__ == "__main__":
    main()
