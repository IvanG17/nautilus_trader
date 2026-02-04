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
SuperStrat Strategy - Aggressive Flexible Entry with Donchian Breakouts and Pyramiding.

Based on Flexible Entry (Supertrend + VSA) with two aggressive enhancements:
1. Donchian Breakout Entry - Catch breakouts without waiting for Supertrend
2. Pyramiding - Scale into winning trades for larger gains

Entry Logic (OR conditions):
- Supertrend indicates uptrend (direction = +1) AND no selling climax
- OR Price breaks above 20-period high AND no selling climax

Exit Logic:
- Supertrend reverses to downtrend
- OR ATR trailing stop is hit
- OR Break-even stop hit (when pyramid is active)

Pyramiding Logic:
- When trade is +1.5 ATR in profit, add 50% of original position
- Move stop to break-even on original position
- Maximum 1 pyramid add per trade
"""

from __future__ import annotations

import math

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


class SuperStratConfig(StrategyConfig, frozen=True):
    """
    Configuration for the SuperStrat strategy.

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
    donchian_period : int, default 20
        Period for Donchian channel (N-period high lookback).
    pyramid_atr_threshold : float, default 1.5
        ATR profit threshold to trigger pyramid add.
    pyramid_size_pct : float, default 0.5
        Pyramid add size as percentage of original position (50%).
    signal_mode_cash : float, default 0.0
        Simulated cash balance for signal-only mode (no execution client).
        Set to 0 to use real account balance. When > 0, enables signal mode
        with this cash balance for position sizing.

    """

    instrument_id: str
    bar_type: str
    atr_period: int = 14
    atr_multiplier: float = 3.0
    vsa_window: int = 20
    vsa_volume_factor: float = 1.5
    trailing_stop_atr_mult: float = 2.0
    position_size_pct: float = 0.25
    donchian_period: int = 20
    pyramid_atr_threshold: float = 1.5
    pyramid_size_pct: float = 0.5
    signal_mode_cash: float = 0.0


class SuperStratStrategy(Strategy):
    """
    SuperStrat strategy using Supertrend, VSA, Donchian breakouts, and pyramiding.

    This strategy:
    1. Enters long when Supertrend shows uptrend and no selling climax
       OR when price breaks above Donchian high and no selling climax
    2. Uses ATR-based trailing stops
    3. Pyramids into winning trades (adds 50% at +1.5 ATR profit)
    4. Moves stop to break-even when pyramid is active
    5. Exits on Supertrend reversal, trailing stop, or break-even stop

    Parameters
    ----------
    config : SuperStratConfig
        The strategy configuration.

    """

    def __init__(self, config: SuperStratConfig) -> None:
        """Initialize the SuperStrat strategy."""
        super().__init__(config)

        # Configuration
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        # Base parameters (from Flexible Entry)
        self.atr_period = config.atr_period
        self.atr_multiplier = config.atr_multiplier
        self.vsa_window = config.vsa_window
        self.vsa_volume_factor = config.vsa_volume_factor
        self.trailing_stop_atr_mult = config.trailing_stop_atr_mult
        self.position_size_pct = config.position_size_pct

        # Donchian breakout parameters
        self.donchian_period = config.donchian_period

        # Pyramiding parameters
        self.pyramid_atr_threshold = config.pyramid_atr_threshold
        self.pyramid_size_pct = config.pyramid_size_pct

        # Signal-only mode (simulated cash for position sizing)
        self.signal_mode_cash = config.signal_mode_cash
        self._simulated_cash = config.signal_mode_cash  # Track remaining cash in signal mode
        self._simulated_position = 0  # Track simulated position in signal mode
        self._warmup_complete = False  # Flag to skip trading during warmup

        # Minimum bars needed for indicators
        self._min_bars = max(self.atr_period, self.vsa_window, self.donchian_period) + 10

        # State tracking
        self._bars: list[Bar] = []
        self._highest_price: float = 0.0
        self._entry_price: float = 0.0

        # Pyramid state tracking
        self._pyramid_added: bool = False
        self._original_entry_price: float = 0.0
        self._original_quantity: int = 0
        self._breakeven_stop: float = 0.0

        # Average cost basis tracking for pyramids
        self._total_cost_basis: float = 0.0
        self._total_quantity: int = 0
        self._avg_entry_price: float = 0.0

        # Instrument reference (set on start)
        self._instrument: Instrument | None = None

    def _is_valid_value(self, value) -> bool:
        """Check if value is valid (not None, NaN, or Inf)."""
        if value is None:
            return False
        try:
            fval = float(value)
            return not (math.isnan(fval) or math.isinf(fval))
        except (TypeError, ValueError):
            return False

    def on_start(self) -> None:
        """Called when the strategy starts."""
        self.log.info("Starting SuperStrat strategy")

        # Subscribe to bar data first - this triggers instrument loading
        self.subscribe_bars(self.bar_type)

        self.log.info(
            f"Subscribed to {self.bar_type}, waiting for instrument load. "
            f"ATR({self.atr_period}, x{self.atr_multiplier}), "
            f"VSA({self.vsa_window}), Donchian({self.donchian_period}), "
            f"TrailingStop({self.trailing_stop_atr_mult}x ATR), "
            f"Pyramid(+{self.pyramid_atr_threshold}ATR, {self.pyramid_size_pct*100:.0f}%)"
        )

    def on_bar(self, bar: Bar) -> None:
        """
        Handle new bar data.

        Parameters
        ----------
        bar : Bar
            The new bar.

        """
        # Get instrument from cache on first bar (after subscription loads it)
        if self._instrument is None:
            self._instrument = self.cache.instrument(self.instrument_id)
            if self._instrument is None:
                self.log.warning(f"Instrument still not available: {self.instrument_id}")
                return
            self.log.info(f"Instrument loaded: {self._instrument.id}")

        # Store bar for indicator calculations
        self._bars.append(bar)

        # Keep only needed bars
        if len(self._bars) > self._min_bars + 50:
            self._bars = self._bars[-(self._min_bars + 50):]

        # Log progress every 5 bars
        if len(self._bars) % 5 == 0 or len(self._bars) == self._min_bars:
            self.log.info(
                f"Bar {len(self._bars)}/{self._min_bars} | "
                f"Close: {float(bar.close):.2f} | "
                f"Volume: {int(bar.volume)}"
            )

        # Check we have enough data
        if len(self._bars) < self._min_bars:
            return

        # In signal mode, skip trading during warmup (historical bars)
        # Detect warmup complete when bar timestamp is within 10 minutes of current time
        if self.signal_mode_cash > 0 and not self._warmup_complete:
            bar_time_ns = bar.ts_event
            current_time_ns = self.clock.timestamp_ns()
            time_diff_sec = abs(current_time_ns - bar_time_ns) / 1_000_000_000

            if time_diff_sec < 600:  # Within 10 minutes = live bar
                self._warmup_complete = True
                self.log.info(
                    f"WARMUP COMPLETE - Starting live trading. "
                    f"Cash: ${self._simulated_cash:,.0f}"
                )
            else:
                # Still in warmup - calculate indicators but don't trade
                return

        # Build DataFrame for indicators
        df = self._bars_to_dataframe()

        # Calculate indicators
        atr = self._calculate_atr(df)
        supertrend_line, direction = self._calculate_supertrend(df, atr)
        vsa = self._calculate_vsa(df)
        donchian_high = self._calculate_donchian_high(df)

        # Current values
        price = float(bar.close)
        current_atr = float(atr.iloc[-1]) if len(atr) > 0 and self._is_valid_value(atr.iloc[-1]) else price * 0.02
        is_uptrend = int(direction.iloc[-1]) == 1 if len(direction) > 0 and self._is_valid_value(direction.iloc[-1]) else False
        is_selling_climax = bool(vsa["selling_climax"].iloc[-1]) if len(vsa["selling_climax"]) > 0 else False
        # Donchian high is already shifted, use iloc[-1] with NaN check
        current_donchian_high = 0.0
        if len(donchian_high) > 0 and self._is_valid_value(donchian_high.iloc[-1]):
            current_donchian_high = float(donchian_high.iloc[-1])

        # Get current position (use simulated position in signal mode)
        if self.signal_mode_cash > 0:
            position = self._simulated_position
        else:
            position = self.portfolio.net_position(self.instrument_id)

        # Log analysis every bar once we have enough data
        trend_str = "UP" if is_uptrend else "DOWN"
        climax_str = "CLIMAX" if is_selling_climax else "normal"
        self.log.info(
            f"ANALYSIS | Price: {price:.2f} | ATR: {current_atr:.3f} | "
            f"Trend: {trend_str} | VSA: {climax_str} | "
            f"Donchian: {current_donchian_high:.2f} | Pos: {position}"
        )

        if position == 0:
            self._check_entry(bar, price, current_atr, is_uptrend, is_selling_climax, current_donchian_high)
        else:
            # Check for pyramid opportunity first
            self._check_pyramid(price, current_atr)
            # Then check for exit
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

    def _calculate_donchian_high(self, df: pd.DataFrame) -> pd.Series:
        """Calculate rolling N-period high (Donchian upper channel), shifted to exclude current bar."""
        return df["high"].rolling(window=self.donchian_period).max().shift(1)

    def _check_entry(
        self,
        bar: Bar,
        price: float,
        current_atr: float,
        is_uptrend: bool,
        is_selling_climax: bool,
        donchian_high: float,
    ) -> None:
        """
        Check for entry signal.

        Entry conditions (OR logic):
        1. Supertrend shows uptrend AND no selling climax
        2. OR Price breaks above Donchian high AND no selling climax
        """
        # Always respect selling climax filter
        if is_selling_climax:
            return

        # Entry condition 1: Supertrend uptrend
        supertrend_entry = is_uptrend

        # Entry condition 2: Donchian breakout (price > previous N-period high)
        donchian_entry = donchian_high > 0 and price > donchian_high

        # Check if either condition is met
        if not (supertrend_entry or donchian_entry):
            return

        # Determine entry reason for logging
        if supertrend_entry and donchian_entry:
            entry_reason = "supertrend+donchian"
        elif donchian_entry:
            entry_reason = "donchian_breakout"
        else:
            entry_reason = "supertrend_uptrend"

        # Calculate position size
        quantity = self._calculate_position_size(price)

        if quantity < 1:
            self.log.warning("Insufficient funds for entry")
            return

        # In signal mode, track simulated position; otherwise submit real order
        if self.signal_mode_cash > 0:
            # Signal mode: verify we can afford the position
            actual_cost = quantity * price
            if actual_cost > self._simulated_cash:
                quantity = int(self._simulated_cash / price)
                if quantity < 1:
                    self.log.warning("Insufficient simulated cash for entry")
                    return
                actual_cost = quantity * price

            # Signal mode: simulate the trade
            self._simulated_position = quantity
            self._simulated_cash -= actual_cost
            self.log.info(
                f">>> SIGNAL: BUY {quantity} shares @ ${price:.2f} "
                f"(Reason: {entry_reason}, ATR: ${current_atr:.2f})"
            )
        else:
            # Real mode: submit market order
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=Quantity.from_int(quantity),
                time_in_force=TimeInForce.IOC,
            )
            self.submit_order(order)
            self.log.info(
                f"Entry ({entry_reason}): {quantity} shares @ ~${price:.2f}, "
                f"ATR: ${current_atr:.2f}"
            )

        # Track entry state
        self._entry_price = price
        self._highest_price = price

        # Initialize pyramid state
        self._original_entry_price = price
        self._original_quantity = quantity
        self._pyramid_added = False
        self._breakeven_stop = 0.0

        # Initialize cost basis tracking
        self._total_cost_basis = quantity * price
        self._total_quantity = quantity
        self._avg_entry_price = price

    def _check_pyramid(self, price: float, current_atr: float) -> None:
        """
        Check if conditions met for pyramid add.

        Pyramid conditions:
        - Only one pyramid per trade
        - Profit >= pyramid_atr_threshold * current_atr
        - Add pyramid_size_pct of original position size
        - Move stop to break-even
        """
        if self._pyramid_added:
            return  # Only one pyramid per trade

        if self._original_entry_price <= 0:
            return

        # Check if profit >= threshold ATR
        profit = price - self._original_entry_price
        threshold = self.pyramid_atr_threshold * current_atr

        if profit < threshold:
            return

        # Calculate add size (percentage of original)
        add_quantity = int(self._original_quantity * self.pyramid_size_pct)
        if add_quantity < 1:
            return

        profit_pct = (price - self._original_entry_price) / self._original_entry_price * 100

        # In signal mode, track simulated position; otherwise submit real order
        if self.signal_mode_cash > 0:
            # Signal mode: simulate the pyramid
            self._simulated_position += add_quantity
            self._simulated_cash -= add_quantity * price
            self.log.info(
                f">>> SIGNAL: PYRAMID BUY +{add_quantity} shares @ ${price:.2f} "
                f"(+{profit_pct:.1f}%, +{profit/current_atr:.1f}ATR)"
            )
        else:
            # Real mode: submit market order
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=Quantity.from_int(add_quantity),
                time_in_force=TimeInForce.IOC,
            )
            self.submit_order(order)
            self.log.info(
                f"Pyramid ADD: +{add_quantity} shares @ ~${price:.2f} "
                f"(+{profit_pct:.1f}%, +{profit/current_atr:.1f}ATR), "
                f"BE stop @ ${self._breakeven_stop:.2f}"
            )

        # Update cost basis tracking
        self._total_cost_basis += add_quantity * price
        self._total_quantity += add_quantity
        self._avg_entry_price = self._total_cost_basis / self._total_quantity

        # Move original stop to break-even (use average entry for fairness)
        self._breakeven_stop = self._avg_entry_price
        self._pyramid_added = True

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
        3. Price hits break-even stop (when pyramid is active)
        """
        # Update highest price for trailing stop
        if price > self._highest_price:
            self._highest_price = price

        # Calculate trailing stop with floor (never below 50% of entry, never zero/negative)
        raw_stop = self._highest_price - (self.trailing_stop_atr_mult * current_atr)
        min_stop = self._avg_entry_price * 0.50 if self._avg_entry_price > 0 else 0.01
        trailing_stop = max(raw_stop, min_stop, 0.01)

        # Check exit conditions
        should_exit = False
        exit_reason = ""

        # Check break-even stop first (if pyramid is active, use avg entry)
        if self._breakeven_stop > 0 and price <= self._breakeven_stop:
            should_exit = True
            exit_reason = "breakeven_stop"
        elif not is_uptrend:
            should_exit = True
            exit_reason = "supertrend_reversal"
        elif price <= trailing_stop:
            should_exit = True
            exit_reason = "trailing_stop"

        if not should_exit:
            return

        # Calculate P&L using average entry price for accuracy
        entry_for_pnl = self._avg_entry_price if self._avg_entry_price > 0 else self._entry_price
        if entry_for_pnl > 0:
            pnl_pct = (price - entry_for_pnl) / entry_for_pnl
            pnl_dollars = (price - entry_for_pnl) * int(abs(position))
        else:
            pnl_pct = 0.0
            pnl_dollars = 0.0

        pyramid_info = " (pyramided)" if self._pyramid_added else ""

        # In signal mode, track simulated position; otherwise submit real order
        if self.signal_mode_cash > 0:
            # Signal mode: simulate the exit
            self._simulated_cash += int(abs(position)) * price
            self._simulated_position = 0
            self.log.info(
                f">>> SIGNAL: SELL {int(position)} shares @ ${price:.2f} "
                f"(Reason: {exit_reason}{pyramid_info}, P&L: {pnl_pct * 100:+.1f}% / ${pnl_dollars:+.2f})"
            )
        else:
            # Real mode: submit market order
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=Quantity.from_int(int(abs(position))),
                time_in_force=TimeInForce.IOC,
            )
            self.submit_order(order)
            self.log.info(
                f"Exit ({exit_reason}){pyramid_info}: {int(position)} shares @ ~${price:.2f}, "
                f"P&L: {pnl_pct * 100:+.1f}%"
            )

        # Reset state
        self._entry_price = 0.0
        self._highest_price = 0.0
        self._original_entry_price = 0.0
        self._original_quantity = 0
        self._pyramid_added = False
        self._breakeven_stop = 0.0
        self._total_cost_basis = 0.0
        self._total_quantity = 0
        self._avg_entry_price = 0.0

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

        cash = 0.0

        # Check for signal mode (simulated cash)
        if self.signal_mode_cash > 0:
            cash = self._simulated_cash
        else:
            # Get available cash from real account
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

        # Final validation: ensure total cost doesn't exceed available cash
        if shares > 0:
            total_cost = shares * price
            if total_cost > cash:
                shares = int(cash / price)
                self.log.warning(f"Adjusted position to {shares} due to cash constraint")

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
        self.log.info("Stopping SuperStrat strategy")

        # Close any open positions
        self.close_all_positions(self.instrument_id)

        self.log.info("SuperStrat strategy stopped")

    def on_reset(self) -> None:
        """Called when the strategy is reset."""
        # Reset state
        self._bars = []
        self._highest_price = 0.0
        self._entry_price = 0.0

        # Reset pyramid state
        self._pyramid_added = False
        self._original_entry_price = 0.0
        self._original_quantity = 0
        self._breakeven_stop = 0.0

        # Reset cost basis tracking
        self._total_cost_basis = 0.0
        self._total_quantity = 0
        self._avg_entry_price = 0.0
