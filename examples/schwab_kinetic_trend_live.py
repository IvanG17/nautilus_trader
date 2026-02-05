#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Copyright (C) 2015-2026 Nautech Systems Pty Ltd. All rights reserved.
#  https://nautechsystems.io
#
#  Licensed under the GNU Lesser General Public License Version 3.0 (the "License");
#  You may not use this file except in compliance with the License.
#  You may obtain a copy of the License at https://www.gnu.org/licenses/lgpl-3.0.en.html
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS,
#  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#  See the License for the specific language governing permissions and
#  limitations under the License.
# -------------------------------------------------------------------------------------------------

"""
Example live trading setup for the Kinetic Trend strategy with Schwab.

This demonstrates:
- Configuring the Schwab data adapter
- Setting up a TradingNode for live trading
- Running the Kinetic Trend strategy with real-time data

IMPORTANT: This is for PAPER TRADING demonstration only.
Do NOT use with real money without thorough testing.

Prerequisites:
- Schwab API credentials (app_key, app_secret)
- Valid OAuth2 tokens (run initial auth flow first)
- schwabdev package installed

Usage:
    # Set environment variables first:
    export SCHWAB_APP_KEY="your_app_key"
    export SCHWAB_APP_SECRET="your_app_secret"

    python schwab_kinetic_trend_live.py

"""

import os
import sys
from pathlib import Path

# Load environment variables from .env file
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent.parent / ".env")

from nautilus_schwab import SCHWAB_VENUE
from nautilus_schwab import SchwabDataClientConfig
from nautilus_schwab import SchwabLiveDataClientFactory
from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveDataEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.live.node import TradingNode
from nautilus_trader.model.identifiers import InstrumentId

# Import the strategy (add examples to path for local import)
sys.path.insert(0, str(Path(__file__).parent))
from strategies.kinetic_trend import KineticTrendConfig
from strategies.kinetic_trend import KineticTrendStrategy


def get_schwab_credentials() -> tuple[str, str]:
    """Get Schwab API credentials from environment variables."""
    app_key = os.environ.get("SCHWAB_APP_KEY")
    app_secret = os.environ.get("SCHWAB_APP_SECRET")

    if not app_key or not app_secret:
        print("ERROR: Schwab credentials not found in environment variables.")
        print("Please set SCHWAB_APP_KEY and SCHWAB_APP_SECRET")
        sys.exit(1)

    return app_key, app_secret


def main():
    """Run the live trading node."""
    print("=" * 60)
    print("Kinetic Trend Strategy - Live Trading (Schwab)")
    print("=" * 60)
    print("\nWARNING: This connects to LIVE market data.")
    print("Ensure you're using paper trading or have proper risk controls.\n")

    # Get credentials
    app_key, app_secret = get_schwab_credentials()

    # Configure Schwab data client
    # tokens_db defaults to ~/.schwabdev/tokens.db (copied from Gordan)
    schwab_config = SchwabDataClientConfig(
        app_key=app_key,
        app_secret=app_secret,
        callback_url="https://127.0.0.1",
        use_websocket=True,
        instrument_provider=InstrumentProviderConfig(
            load_all=False,
        ),
    )

    # Configure the trading node
    node_config = TradingNodeConfig(
        trader_id="KINETIC-001",
        logging=LoggingConfig(
            log_level="INFO",
            log_level_file="DEBUG",
        ),
        data_engine=LiveDataEngineConfig(
            time_bars_build_with_no_updates=True,
            time_bars_timestamp_on_close=True,
        ),
        data_clients={
            "SCHWAB": schwab_config,
        },
        # Note: For actual trading, you would also configure:
        # exec_clients={
        #     "SCHWAB": SchwabExecClientConfig(...),
        # },
    )

    # Create the trading node
    node = TradingNode(config=node_config)

    # Register the Schwab data client factory
    node.add_data_client_factory("SCHWAB", SchwabLiveDataClientFactory)

    # Build the node
    node.build()

    # Create strategy configuration
    # Trading AAPL on 15-minute bars
    strategy_config = KineticTrendConfig(
        instrument_id="AAPL.SCHWAB",
        bar_type="AAPL.SCHWAB-15-MINUTE-LAST-EXTERNAL",
        bb_period=20,
        bb_std=2.0,
        max_pyramid_levels=3,
        pyramid_threshold=0.02,
        position_size_pct=0.20,
        profit_target_pct=0.10,
    )

    # Create and add the strategy
    strategy = KineticTrendStrategy(config=strategy_config)
    node.trader.add_strategy(strategy)

    print("\nStarting trading node...")
    print(f"  Strategy: Kinetic Trend")
    print(f"  Instrument: AAPL.SCHWAB")
    print(f"  Bar type: 15-MINUTE")
    print(f"  BB Period: {strategy_config.bb_period}")
    print(f"  BB Std: {strategy_config.bb_std}")
    print(f"  Max Pyramids: {strategy_config.max_pyramid_levels}")
    print(f"  Position Size: {strategy_config.position_size_pct * 100}%")
    print(f"  Profit Target: {strategy_config.profit_target_pct * 100}%")
    print("\nPress Ctrl+C to stop.\n")

    try:
        # Run the trading node
        node.run()

    except KeyboardInterrupt:
        print("\nShutdown requested...")

    finally:
        # Cleanup
        node.dispose()
        print("Trading node stopped.")


if __name__ == "__main__":
    main()
