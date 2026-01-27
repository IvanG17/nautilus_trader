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
Flexible Entry Strategy using Supertrend and Volume Spread Analysis (VSA).

Entry Logic:
- Supertrend indicates uptrend (direction = +1)
- No selling climax detected (avoid exhaustion)
- Optional: buying climax for momentum confirmation

Exit Logic:
- Supertrend reverses to downtrend
- OR ATR trailing stop is hit

This is a port of the Gordan trading suite's FlexibleEntryStrategy.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pandas as pd

from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class FlexibleEntryConfig(StrategyConfig, frozen=True):
    """
    Configuration for the Flexible Entry strategy.

    Parameters
    ----------
    instrument_id : str
        The instrument ID to trade (e.g., "ASTS.SCHWAB").
    bar_type : str
        The bar type to use (e.g., "ASTS.SCHWAB-15-MINUTE-LAST-EXTERNAL").
    atr_period : int, default 14
        Period for ATR calculation.
    atr_multiplier : float, default 3.0
        Multiplier for Supertrend bands.
    vsa_window : int, default 20
        Window for VSA average calculations.
    vsa_volume_factor : float, default 1.5
        Multiplier for high volume detection.
    trailing_stop_atr_mult : float, default 2.0
        ATR multiplier for trailing stop distance.
    position_size_pct : float, default 0.25
        Position size as percentage of available cash (25%).

    """

    instrument_id: str
    bar_type: str
    atr_period: int = 14
    atr_multiplier: float = 3.0
    vsa_window: int = 20
    vsa_volume_factor: float = 1.5
    trailing_stop_atr_mult: float = 2.0
    position_size_pct: float = 0.25


class FlexibleEntryStrategy(Strategy):
    """
    Flexible Entry strategy using Supertrend and VSA.

    This strategy:
    1. Enters long when Supertrend shows uptrend and no selling climax
    2. Uses ATR-based trailing stops
    3. Exits on Supertrend reversal or trailing stop hit

    Parameters
    ----------
    config : FlexibleEntryConfig
        The strategy configuration.

    """

    def __init__(self, config: FlexibleEntryConfig) -> None:
        """Initialize the Flexible Entry strategy."""
        super().__init__(config)

        # Configuration
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        # Parameters
        self.atr_period = config.atr_period
        self.atr_multiplier = config.atr_multiplier
        self.vsa_window = config.vsa_window
        self.vsa_volume_factor = config.vsa_volume_factor
        self.trailing_stop_atr_mult = config.trailing_stop_atr_mult
        self.position_size_pct = config.position_size_pct

        # Minimum bars needed for indicators
        self._min_bars = max(self.atr_period, self.vsa_window) + 10

        # State tracking
        self._bars: list[Bar] = []
        self._highest_price: float = 0.0
        self._entry_price: float = 0.0

        # Instrument reference (set on start)
        self._instrument: Instrument | None = None

    def on_start(self) -> None:
        """Called when the strategy starts."""
        self.log.info("Starting Flexible Entry strategy")

        # Get instrument from cache
        self._instrument = self.cache.instrument(self.instrument_id)

        if self._instrument is None:
            self.log.error(f"Instrument not found: {self.instrument_id}")
            return

        # Subscribe to bar data
        self.subscribe_bars(self.bar_type)

        self.log.info(
            f"Initialized: ATR({self.atr_period}, x{self.atr_multiplier}), "
            f"VSA({self.vsa_window}), TrailingStop({self.trailing_stop_atr_mult}x ATR)"
        )

    def on_bar(self, bar: Bar) -> None:
        """
        Handle new bar data.

        Parameters
        ----------
        bar : Bar
            The new bar.

        """
        # Store bar for indicator calculations
        self._bars.append(bar)

        # Keep only needed bars
        if len(self._bars) > self._min_bars + 50:
            self._bars = self._bars[-(self._min_bars + 50):]

        # Check we have enough data
        if len(self._bars) < self._min_bars:
            return

        # Build DataFrame for indicators
        df = self._bars_to_dataframe()

        # Calculate indicators
        atr = self._calculate_atr(df)
        supertrend_line, direction = self._calculate_supertrend(df, atr)
        vsa = self._calculate_vsa(df)

        # Current values
        price = float(bar.close)
        current_atr = float(atr.iloc[-1]) if len(atr) > 0 else price * 0.02
        is_uptrend = int(direction.iloc[-1]) == 1 if len(direction) > 0 else False
        is_selling_climax = bool(vsa["selling_climax"].iloc[-1]) if len(vsa["selling_climax"]) > 0 else False

        # Get current position
        position = self.portfolio.net_position(self.instrument_id)

        if position == 0:
            self._check_entry(bar, price, current_atr, is_uptrend, is_selling_climax)
        else:
            self._check_exit(bar, price, current_atr, is_uptrend, position)

    def _bars_to_dataframe(self) -> pd.DataFrame:
        """Convert stored bars to DataFrame."""
        data = {
            "open": [float(b.open) for b in self._bars],
            "high": [float(b.high) for b in self._bars],
            "low": [float(b.low) for b in self._bars],
            "close": [float(b.close) for b in self._bars],
            "volume": [float(b.volume) for b in self._bars],
        }
        return pd.DataFrame(data)

    def _calculate_atr(self, df: pd.DataFrame) -> pd.Series:
        """Calculate ATR using EMA."""
        high = df["high"]
        low = df["low"]
        close = df["close"]

        tr1 = high - low
        tr2 = (high - close.shift(1)).abs()
        tr3 = (low - close.shift(1)).abs()

        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = true_range.ewm(span=self.atr_period, adjust=False).mean()

        return atr

    def _calculate_supertrend(
        self,
        df: pd.DataFrame,
        atr: pd.Series,
    ) -> tuple[pd.Series, pd.Series]:
        """Calculate Supertrend indicator."""
        hl2 = (df["high"] + df["low"]) / 2

        upper_band = hl2 + (self.atr_multiplier * atr)
        lower_band = hl2 - (self.atr_multiplier * atr)

        supertrend = pd.Series(index=df.index, dtype=float)
        direction = pd.Series(index=df.index, dtype=int)

        close = df["close"]

        for i in range(len(df)):
            if i == 0:
                supertrend.iloc[i] = upper_band.iloc[i]
                direction.iloc[i] = -1
                continue

            prev_supertrend = supertrend.iloc[i - 1]
            prev_direction = direction.iloc[i - 1]
            curr_close = close.iloc[i]
            curr_upper = upper_band.iloc[i]
            curr_lower = lower_band.iloc[i]

            if prev_direction == 1:
                # Was in uptrend
                if curr_close < prev_supertrend:
                    # Trend reversal to downtrend
                    supertrend.iloc[i] = curr_upper
                    direction.iloc[i] = -1
                else:
                    # Continue uptrend
                    supertrend.iloc[i] = max(curr_lower, prev_supertrend)
                    direction.iloc[i] = 1
            else:
                # Was in downtrend
                if curr_close > prev_supertrend:
                    # Trend reversal to uptrend
                    supertrend.iloc[i] = curr_lower
                    direction.iloc[i] = 1
                else:
                    # Continue downtrend
                    supertrend.iloc[i] = min(curr_upper, prev_supertrend)
                    direction.iloc[i] = -1

        return supertrend, direction

    def _calculate_vsa(self, df: pd.DataFrame) -> dict:
        """Calculate Volume Spread Analysis indicators."""
        spread = df["high"] - df["low"]
        avg_spread = spread.rolling(window=self.vsa_window).mean()
        avg_volume = df["volume"].rolling(window=self.vsa_window).mean()

        is_high_volume = df["volume"] > (avg_volume * self.vsa_volume_factor)
        is_wide_spread = spread > (avg_spread * 1.3)  # 30% wider than average

        # Close position within bar (0 = low, 1 = high)
        close_position = (df["close"] - df["low"]) / spread.replace(0, np.nan)
        close_position = close_position.fillna(0.5)

        # Buying climax: high volume, wide spread, close near high
        buying_climax = is_high_volume & is_wide_spread & (close_position > 0.7)

        # Selling climax: high volume, wide spread, close near low
        selling_climax = is_high_volume & is_wide_spread & (close_position < 0.3)

        return {
            "buying_climax": buying_climax,
            "selling_climax": selling_climax,
        }

    def _check_entry(
        self,
        bar: Bar,
        price: float,
        current_atr: float,
        is_uptrend: bool,
        is_selling_climax: bool,
    ) -> None:
        """
        Check for entry signal.

        Entry conditions:
        1. Supertrend shows uptrend
        2. No selling climax (avoid exhaustion)
        """
        if not is_uptrend:
            return

        if is_selling_climax:
            return

        # Calculate position size
        quantity = self._calculate_position_size(price)

        if quantity < 1:
            self.log.warning("Insufficient funds for entry")
            return

        # Submit market buy order
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.BUY,
            quantity=Quantity.from_int(quantity),
            time_in_force=TimeInForce.IOC,
        )

        self.submit_order(order)

        # Track entry state
        self._entry_price = price
        self._highest_price = price

        self.log.info(
            f"Entry order submitted: {quantity} shares @ ~${price:.2f}, "
            f"ATR: ${current_atr:.2f}"
        )

    def _check_exit(
        self,
        bar: Bar,
        price: float,
        current_atr: float,
        is_uptrend: bool,
        position: float,
    ) -> None:
        """
        Check for exit signal.

        Exit conditions:
        1. Supertrend reverses to downtrend
        2. Price hits trailing stop (highest - ATR * multiplier)
        """
        # Update highest price for trailing stop
        if price > self._highest_price:
            self._highest_price = price

        # Calculate trailing stop
        trailing_stop = self._highest_price - (self.trailing_stop_atr_mult * current_atr)

        # Check exit conditions
        should_exit = False
        exit_reason = ""

        if not is_uptrend:
            should_exit = True
            exit_reason = "supertrend_reversal"
        elif price <= trailing_stop:
            should_exit = True
            exit_reason = "trailing_stop"

        if not should_exit:
            return

        # Calculate P&L
        if self._entry_price > 0:
            pnl_pct = (price - self._entry_price) / self._entry_price
        else:
            pnl_pct = 0.0

        # Submit market sell order
        order = self.order_factory.market(
            instrument_id=self.instrument_id,
            order_side=OrderSide.SELL,
            quantity=Quantity.from_int(int(abs(position))),
            time_in_force=TimeInForce.IOC,
        )

        self.submit_order(order)

        self.log.info(
            f"Exit ({exit_reason}): {int(position)} shares @ ~${price:.2f}, "
            f"P&L: {pnl_pct * 100:+.1f}%"
        )

        # Reset state
        self._entry_price = 0.0
        self._highest_price = 0.0

    def _calculate_position_size(self, price: float) -> int:
        """
        Calculate position size based on available cash.

        Parameters
        ----------
        price : float
            The current price.

        Returns
        -------
        int
            The number of shares to buy.

        """
        if self._instrument is None:
            return 0

        # Get available cash
        account = self.portfolio.account(self._instrument.id.venue)
        if account is None:
            return 0

        # Get balance in quote currency (USD for equities)
        balance = account.balance_total(self._instrument.quote_currency)
        if balance is None:
            return 0

        cash = float(balance.as_double())

        # Calculate position value
        position_value = cash * self.position_size_pct

        # Calculate shares
        if price > 0:
            shares = int(position_value / price)
        else:
            shares = 0

        return max(0, shares)

    def on_order_filled(self, event: OrderFilled) -> None:
        """
        Handle order fill event.

        Parameters
        ----------
        event : OrderFilled
            The order filled event.

        """
        self.log.info(
            f"Order filled: {event.order_side.name} {event.last_qty} @ {event.last_px}"
        )

    def on_stop(self) -> None:
        """Called when the strategy stops."""
        self.log.info("Stopping Flexible Entry strategy")

        # Close any open positions
        self.close_all_positions(self.instrument_id)

        self.log.info("Flexible Entry strategy stopped")

    def on_reset(self) -> None:
        """Called when the strategy is reset."""
        # Reset state
        self._bars = []
        self._highest_price = 0.0
        self._entry_price = 0.0
