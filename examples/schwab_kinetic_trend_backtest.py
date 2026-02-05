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
Example backtest for the Kinetic Trend strategy.

This demonstrates:
- Setting up a backtest engine
- Loading historical bar data
- Running the Kinetic Trend strategy
- Analyzing results

Usage:
    python schwab_kinetic_trend_backtest.py

"""

from datetime import datetime
from decimal import Decimal

import pandas as pd

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.engine import BacktestEngineConfig
from nautilus_trader.config import LoggingConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AccountType
from nautilus_trader.model.enums import OmsType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.identifiers import Venue
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity

# Import the strategy (add examples to path for local import)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
from strategies.kinetic_trend import KineticTrendConfig
from strategies.kinetic_trend import KineticTrendStrategy


def create_instrument() -> Equity:
    """Create an AAPL equity instrument for testing."""
    return Equity(
        instrument_id=InstrumentId(Symbol("AAPL"), Venue("SCHWAB")),
        raw_symbol=Symbol("AAPL"),
        currency=USD,
        price_precision=2,
        price_increment=Price.from_str("0.01"),
        lot_size=Quantity.from_int(1),
        ts_event=0,
        ts_init=0,
    )


def create_sample_bars(instrument_id: InstrumentId, bar_type: BarType) -> list[Bar]:
    """
    Create sample bar data for backtesting.

    In production, you would load this from Schwab historical data
    or a data provider like Databento.

    This creates a simulated uptrend with a breakout pattern.

    """
    bars = []

    # Base parameters
    base_price = 150.0
    base_time = int(datetime(2024, 1, 2, 9, 30).timestamp() * 1_000_000_000)
    bar_duration_ns = 15 * 60 * 1_000_000_000  # 15 minutes in nanoseconds

    # Generate 100 bars with an uptrend pattern
    for i in range(100):
        # Simulate gradual uptrend with some volatility
        trend = i * 0.1  # Gradual increase
        volatility = (i % 7 - 3) * 0.3  # Oscillation

        open_price = base_price + trend + volatility
        high_price = open_price + abs(volatility) + 0.5
        low_price = open_price - abs(volatility) - 0.3
        close_price = open_price + volatility * 0.5

        # Ensure OHLC validity
        high_price = max(high_price, open_price, close_price)
        low_price = min(low_price, open_price, close_price)

        ts_event = base_time + (i * bar_duration_ns)

        bar = Bar(
            bar_type=bar_type,
            open=Price.from_str(f"{open_price:.2f}"),
            high=Price.from_str(f"{high_price:.2f}"),
            low=Price.from_str(f"{low_price:.2f}"),
            close=Price.from_str(f"{close_price:.2f}"),
            volume=Quantity.from_int(100000 + i * 1000),
            ts_event=ts_event,
            ts_init=ts_event,
        )
        bars.append(bar)

    return bars


def run_backtest():
    """Run the Kinetic Trend strategy backtest."""
    print("=" * 60)
    print("Kinetic Trend Strategy Backtest")
    print("=" * 60)

    # Create engine configuration
    config = BacktestEngineConfig(
        logging=LoggingConfig(log_level="INFO"),
    )

    # Create backtest engine
    engine = BacktestEngine(config=config)

    # Add venue
    engine.add_venue(
        venue=Venue("SCHWAB"),
        oms_type=OmsType.NETTING,
        account_type=AccountType.CASH,
        base_currency=USD,
        starting_balances=[Money(100_000, USD)],
    )

    # Create and add instrument
    instrument = create_instrument()
    engine.add_instrument(instrument)

    # Create bar type
    bar_type = BarType.from_str("AAPL.SCHWAB-15-MINUTE-LAST-EXTERNAL")

    # Generate and add bar data
    bars = create_sample_bars(instrument.id, bar_type)
    engine.add_data(bars)

    print(f"Loaded {len(bars)} bars")

    # Create strategy configuration
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

    # Create and add strategy
    strategy = KineticTrendStrategy(config=strategy_config)
    engine.add_strategy(strategy)

    print("\nRunning backtest...")
    print("-" * 40)

    # Run the backtest
    engine.run()

    # Print results
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)

    # Get statistics
    stats = engine.trader.generate_account_report(Venue("SCHWAB"))
    print("\nAccount Report:")
    for key, value in stats.items():
        print(f"  {key}: {value}")

    # Order fills report
    fills = engine.trader.generate_order_fills_report()
    print(f"\nTotal fills: {len(fills)}")
    if len(fills) > 0:
        print("\nOrder Fills:")
        print(fills.to_string())

    # Positions report
    positions = engine.trader.generate_positions_report()
    print(f"\nTotal positions: {len(positions)}")
    if len(positions) > 0:
        print("\nPositions:")
        print(positions.to_string())

    # Cleanup
    engine.dispose()

    print("\n" + "=" * 60)
    print("Backtest complete")
    print("=" * 60)


if __name__ == "__main__":
    run_backtest()
