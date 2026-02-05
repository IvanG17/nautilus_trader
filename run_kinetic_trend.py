#!/usr/bin/env python3
"""
Kinetic Trend Strategy - Production Runner

Run with:
    python run_kinetic_trend.py

For production:
    nohup python run_kinetic_trend.py > logs/kinetic_trend.log 2>&1 &

Stop with:
    Ctrl+C (or kill the process)
"""

import os
import sys
from pathlib import Path

# Load environment variables
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")

from nautilus_schwab import SCHWAB_VENUE
from nautilus_schwab import SchwabDataClientConfig
from nautilus_schwab import SchwabLiveDataClientFactory
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveDataEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import TraderId

# Import strategy
sys.path.insert(0, str(Path(__file__).parent / "examples"))
from strategies.kinetic_trend import KineticTrendConfig
from strategies.kinetic_trend import KineticTrendStrategy


# =============================================================================
# CONFIGURATION
# =============================================================================

TRADER_ID = "KINETIC-001"
INSTRUMENT = "AAPL"  # Change to trade different symbols
BAR_TIMEFRAME = "15-MINUTE"

# Strategy parameters
STRATEGY_PARAMS = {
    "bb_period": 20,
    "bb_std": 2.0,
    "max_pyramid_levels": 3,
    "pyramid_threshold": 0.02,  # 2% move to pyramid
    "position_size_pct": 0.20,  # 20% of cash per position
    "profit_target_pct": 0.10,  # 10% profit target
}


# =============================================================================
# MAIN
# =============================================================================

def main():
    # Get credentials from environment
    app_key = os.environ.get("SCHWAB_APP_KEY")
    app_secret = os.environ.get("SCHWAB_APP_SECRET")

    if not app_key or not app_secret:
        print("ERROR: Set SCHWAB_APP_KEY and SCHWAB_APP_SECRET in .env")
        sys.exit(1)

    # Configure trading node
    config = TradingNodeConfig(
        trader_id=TraderId(TRADER_ID),
        logging=LoggingConfig(
            log_level="INFO",
            log_level_file="DEBUG",
            log_directory="logs",
            log_file_format="{ts_init}.nautilus.log",
        ),
        data_engine=LiveDataEngineConfig(
            time_bars_build_with_no_updates=True,
            time_bars_timestamp_on_close=True,
        ),
        data_clients={
            "SCHWAB": SchwabDataClientConfig(
                app_key=app_key,
                app_secret=app_secret,
                use_websocket=True,
                instrument_provider=InstrumentProviderConfig(load_all=False),
            ),
        },
        timeout_connection=30.0,
        timeout_disconnection=10.0,
        timeout_post_stop=5.0,
    )

    # Create node
    node = TradingNode(config=config)

    # Configure strategy
    instrument_id = f"{INSTRUMENT}.SCHWAB"
    bar_type = f"{INSTRUMENT}.SCHWAB-{BAR_TIMEFRAME}-LAST-EXTERNAL"

    strategy_config = KineticTrendConfig(
        instrument_id=instrument_id,
        bar_type=bar_type,
        **STRATEGY_PARAMS,
    )
    strategy = KineticTrendStrategy(config=strategy_config)

    # Add strategy
    node.trader.add_strategy(strategy)

    # Register Schwab adapter
    node.add_data_client_factory("SCHWAB", SchwabLiveDataClientFactory)
    node.build()

    # Run
    print("=" * 60)
    print(f"  Kinetic Trend Strategy")
    print(f"  Instrument: {instrument_id}")
    print(f"  Bar Type: {bar_type}")
    print("=" * 60)
    print("\nPress Ctrl+C to stop\n")

    try:
        node.run()
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
