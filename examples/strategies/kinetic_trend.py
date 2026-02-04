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
Kinetic Trend Strategy using Bollinger Bands with pyramiding.

Exact port of Gordan trading suite's KineticTrendStrategy.

Entry Logic:
- Price breaks above upper Bollinger Band (BB% crosses from <1.0 to >=1.0)

Exit Logic:
- Price breaks below lower band (BB% < 0)
- Profit target reached

Risk Management:
- ATR-based stop loss on entry (price - 2*ATR)

Pyramiding:
- Adds to winning positions on significant price moves
- Each pyramid level is 50% of normal position size
- Maximum configurable pyramid levels
"""

from __future__ import annotations

import math

from nautilus_trader.config import StrategyConfig
from nautilus_trader.indicators import BollingerBands
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import OrderSide
from nautilus_trader.model.enums import TimeInForce
from nautilus_trader.model.events import OrderFilled
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.instruments import Instrument
from nautilus_trader.model.objects import Quantity
from nautilus_trader.trading.strategy import Strategy


class KineticTrendConfig(StrategyConfig, frozen=True):
    """
    Configuration for the Kinetic Trend strategy.

    Parameters
    ----------
    instrument_id : str
        The instrument ID to trade (e.g., "ASTS.SCHWAB").
    bar_type : str
        The bar type to use (e.g., "ASTS.SCHWAB-15-MINUTE-LAST-EXTERNAL").
    bb_period : int, default 20
        The Bollinger Bands lookback period.
    bb_std : float, default 2.0
        The Bollinger Bands standard deviation multiplier.
    atr_period : int, default 14
        The ATR period for stop loss calculation.
    atr_stop_multiplier : float, default 2.0
        ATR multiplier for stop loss distance.
    max_pyramid_levels : int, default 3
        Maximum number of pyramid additions.
    pyramid_threshold : float, default 0.02
        Price move threshold (2%) to trigger pyramid add.
    position_size_pct : float, default 0.20
        Position size as percentage of available cash (20%).
    profit_target_pct : float, default 0.10
        Profit target percentage (10%) for exits.
    signal_mode_cash : float, default 0.0
        Simulated cash balance for signal-only mode (no execution client).
        Set to 0 to use real account balance. When > 0, enables signal mode
        with this cash balance for position sizing.

    """

    instrument_id: str
    bar_type: str
    bb_period: int = 20
    bb_std: float = 2.0
    atr_period: int = 14
    atr_stop_multiplier: float = 2.0
    max_pyramid_levels: int = 3
    pyramid_threshold: float = 0.02
    position_size_pct: float = 0.20
    profit_target_pct: float = 0.10
    signal_mode_cash: float = 0.0


class KineticTrendStrategy(Strategy):
    """
    Kinetic Trend strategy using Bollinger Bands with pyramiding.

    Exact port of Gordan's KineticTrendStrategy.

    This strategy:
    1. Enters long on Bollinger Band breakouts (BB% > 1.0)
    2. Sets ATR-based stop loss on entry
    3. Adds to positions (pyramids) on continued strength
    4. Exits on lower band breaks (BB% < 0) or profit targets

    Parameters
    ----------
    config : KineticTrendConfig
        The strategy configuration.

    """

    def __init__(self, config: KineticTrendConfig) -> None:
        """Initialize the Kinetic Trend strategy."""
        super().__init__(config)

        # Configuration
        self.instrument_id = InstrumentId.from_str(config.instrument_id)
        self.bar_type = BarType.from_str(config.bar_type)

        # Parameters
        self.bb_period = config.bb_period
        self.bb_std = config.bb_std
        self.atr_period = config.atr_period
        self.atr_stop_multiplier = config.atr_stop_multiplier
        self.max_pyramid_levels = config.max_pyramid_levels
        self.pyramid_threshold = config.pyramid_threshold
        self.position_size_pct = config.position_size_pct
        self.profit_target_pct = config.profit_target_pct

        # Signal-only mode (simulated cash for position sizing)
        self.signal_mode_cash = config.signal_mode_cash
        self._simulated_cash = config.signal_mode_cash
        self._simulated_position = 0
        self._warmup_complete = False

        # Indicators
        self.bollinger = BollingerBands(
            period=config.bb_period,
            k=config.bb_std,
        )

        # Bar storage for ATR calculation
        self._bars: list[Bar] = []
        self._min_bars = max(config.bb_period, config.atr_period) + 5

        # State tracking
        self._pyramid_level: int = 0
        self._last_add_price: float = 0.0
        self._prev_bb_pct: float = 0.5
        self._entry_price: float = 0.0
        self._stop_loss_price: float = 0.0

        # Cost basis tracking for pyramids
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
        self.log.info("Starting Kinetic Trend strategy")

        # Get instrument from cache
        self._instrument = self.cache.instrument(self.instrument_id)

        if self._instrument is None:
            self.log.error(f"Instrument not found: {self.instrument_id}")
            return

        # Register indicator
        self.register_indicator_for_bars(self.bar_type, self.bollinger)

        # Subscribe to bar data
        self.subscribe_bars(self.bar_type)

        self.log.info(
            f"Initialized: BB({self.bb_period}, {self.bb_std}), "
            f"ATR({self.atr_period}), "
            f"Pyramid max {self.max_pyramid_levels}, "
            f"Size {self.position_size_pct * 100}%"
        )

    def on_bar(self, bar: Bar) -> None:
        """
        Handle new bar data.

        Parameters
        ----------
        bar : Bar
            The new bar.

        """
        # Store bar for ATR calculation
        self._bars.append(bar)
        if len(self._bars) > self._min_bars + 50:
            self._bars = self._bars[-(self._min_bars + 50):]

        # Check indicator is initialized
        if not self.bollinger.initialized:
            return

        # Need enough bars for ATR
        if len(self._bars) < self._min_bars:
            return

        # In signal mode, skip trading during warmup (historical bars)
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
                # Still in warmup - update prev_bb_pct but don't trade
                price = float(bar.close)
                upper = float(self.bollinger.upper)
                lower = float(self.bollinger.lower)
                bb_range = upper - lower
                if bb_range > 0:
                    self._prev_bb_pct = (price - lower) / bb_range
                return

        # Calculate BB% (position within bands)
        price = float(bar.close)
        upper = float(self.bollinger.upper)
        lower = float(self.bollinger.lower)

        # BB% = (price - lower) / (upper - lower)
        # BB% > 1.0 = above upper band (breakout)
        # BB% < 0.0 = below lower band
        bb_range = upper - lower
        if bb_range > 0:
            bb_pct = (price - lower) / bb_range
        else:
            bb_pct = 0.5

        # Calculate ATR
        current_atr = self._calculate_atr()

        # Get current position (use simulated position in signal mode)
        if self.signal_mode_cash > 0:
            position = self._simulated_position
        else:
            position = self.portfolio.net_position(self.instrument_id)

        if position == 0:
            self._check_entry(bar, price, bb_pct, current_atr)
        else:
            self._check_exit_or_pyramid(bar, price, bb_pct, current_atr, position)

        # Store for next bar comparison
        self._prev_bb_pct = bb_pct

    def _calculate_atr(self) -> float:
        """
        Calculate ATR using EMA (matches Gordan's calculate_atr_ema).

        Returns
        -------
        float
            Current ATR value.

        """
        if len(self._bars) < self.atr_period + 1:
            # Fallback: 2% of last close
            return float(self._bars[-1].close) * 0.02 if self._bars else 0.0

        # Build arrays for ATR calculation
        highs = [float(b.high) for b in self._bars]
        lows = [float(b.low) for b in self._bars]
        closes = [float(b.close) for b in self._bars]

        # Calculate True Range
        true_ranges = []
        for i in range(1, len(self._bars)):
            tr1 = highs[i] - lows[i]
            tr2 = abs(highs[i] - closes[i - 1])
            tr3 = abs(lows[i] - closes[i - 1])
            true_ranges.append(max(tr1, tr2, tr3))

        if not true_ranges:
            return float(self._bars[-1].close) * 0.02

        # EMA of True Range
        # EMA = price * k + EMA_prev * (1 - k), where k = 2 / (period + 1)
        k = 2 / (self.atr_period + 1)
        atr = true_ranges[0]
        for tr in true_ranges[1:]:
            atr = tr * k + atr * (1 - k)

        return atr

    def _check_entry(self, bar: Bar, price: float, bb_pct: float, current_atr: float) -> None:
        """
        Check for entry signal.

        Entry: Previous BB% < 1.0 and current BB% >= 1.0 (breakout above upper band)

        """
        # Breakout detection
        breakout = self._prev_bb_pct < 1.0 and bb_pct >= 1.0

        if not breakout:
            return

        self.log.info(f"Breakout detected: BB% {self._prev_bb_pct:.2f} -> {bb_pct:.2f}")

        # Calculate position size
        quantity = self._calculate_position_size(price)

        if quantity < 1:
            self.log.warning("Insufficient funds for entry")
            return

        # Calculate ATR-based stop loss with floor (matches Gordan: price - 2*ATR)
        raw_stop = price - (self.atr_stop_multiplier * current_atr)
        min_stop = price * 0.50  # Floor: at least 50% of entry price
        stop_loss = max(raw_stop, min_stop, 0.01)

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

            self._simulated_position = quantity
            self._simulated_cash -= actual_cost
            self.log.info(
                f">>> SIGNAL: BUY {quantity} shares @ ${price:.2f} "
                f"(Stop: ${stop_loss:.2f}, ATR: ${current_atr:.2f})"
            )
        else:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=Quantity.from_int(quantity),
                time_in_force=TimeInForce.IOC,
            )
            self.submit_order(order)
            self.log.info(
                f"Entry order submitted: {quantity} shares @ ~${price:.2f}, "
                f"Stop: ${stop_loss:.2f}, ATR: ${current_atr:.2f}"
            )

        # Track entry state
        self._pyramid_level = 1
        self._last_add_price = price
        self._entry_price = price
        self._stop_loss_price = stop_loss

        # Initialize cost basis tracking
        self._total_cost_basis = quantity * price
        self._total_quantity = quantity
        self._avg_entry_price = price

    def _check_exit_or_pyramid(
        self,
        bar: Bar,
        price: float,
        bb_pct: float,
        current_atr: float,
        position: float,
    ) -> None:
        """
        Check for exit or pyramid add signals.

        Exit conditions:
        1. Price breaks below lower band (BB% < 0)
        2. Profit target reached
        3. Stop loss hit

        Pyramid: Price moved threshold % above last add price

        """
        # Calculate P&L
        if self._entry_price > 0:
            pnl_pct = (price - self._entry_price) / self._entry_price
        else:
            pnl_pct = 0.0

        # Check exit conditions
        should_exit = False
        exit_reason = ""

        # 1. Price breaks below lower band
        if bb_pct < 0:
            should_exit = True
            exit_reason = "lower_band_break"

        # 2. Profit target reached
        if pnl_pct >= self.profit_target_pct:
            should_exit = True
            exit_reason = "profit_target"

        # 3. Stop loss hit
        if self._stop_loss_price > 0 and price <= self._stop_loss_price:
            should_exit = True
            exit_reason = "stop_loss"

        if should_exit:
            self._exit_position(price, exit_reason, pnl_pct)
            return

        # Check for pyramid add
        self._check_pyramid(price, current_atr, position)

    def _check_pyramid(self, price: float, current_atr: float, position: float) -> None:
        """
        Check if conditions are met for pyramid add.

        Add when:
        - Not at max pyramid levels
        - Price moved threshold % above last add price

        """
        if self._pyramid_level >= self.max_pyramid_levels:
            return

        if self._last_add_price <= 0:
            return

        # Check price move since last add
        price_move = (price - self._last_add_price) / self._last_add_price

        if price_move < self.pyramid_threshold:
            return

        # Calculate add size (50% of normal)
        add_quantity = int(self._calculate_position_size(price) * 0.5)

        if add_quantity < 1:
            return

        # In signal mode, track simulated position; otherwise submit real order
        if self.signal_mode_cash > 0:
            # Signal mode: verify we can afford the pyramid
            actual_cost = add_quantity * price
            if actual_cost > self._simulated_cash:
                add_quantity = int(self._simulated_cash / price)
                if add_quantity < 1:
                    return
                actual_cost = add_quantity * price

            self._simulated_position += add_quantity
            self._simulated_cash -= actual_cost
            self.log.info(
                f">>> SIGNAL: PYRAMID BUY +{add_quantity} shares @ ${price:.2f} "
                f"(Level {self._pyramid_level + 1})"
            )
        else:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.BUY,
                quantity=Quantity.from_int(add_quantity),
                time_in_force=TimeInForce.IOC,
            )
            self.submit_order(order)
            self.log.info(
                f"Pyramid add #{self._pyramid_level + 1}: {add_quantity} shares @ ~${price:.2f}"
            )

        # Update cost basis tracking
        self._total_cost_basis += add_quantity * price
        self._total_quantity += add_quantity
        self._avg_entry_price = self._total_cost_basis / self._total_quantity

        # Update tracking
        self._pyramid_level += 1
        self._last_add_price = price

        # Update stop loss based on new ATR (trail up only, with floor)
        raw_stop = price - (self.atr_stop_multiplier * current_atr)
        min_stop = self._avg_entry_price * 0.50 if self._avg_entry_price > 0 else 0.01
        new_stop = max(raw_stop, min_stop, 0.01)
        if new_stop > self._stop_loss_price:
            self._stop_loss_price = new_stop

    def _exit_position(self, price: float, reason: str, pnl_pct: float) -> None:
        """
        Exit the entire position.

        Parameters
        ----------
        price : float
            The current price.
        reason : str
            The exit reason.
        pnl_pct : float
            The P&L percentage.

        """
        # Get position (use simulated in signal mode)
        if self.signal_mode_cash > 0:
            position = self._simulated_position
        else:
            position = self.portfolio.net_position(self.instrument_id)

        if position <= 0:
            return

        # Use avg entry price for accurate P&L calculation
        entry_for_pnl = self._avg_entry_price if self._avg_entry_price > 0 else self._entry_price
        pnl_dollars = (price - entry_for_pnl) * int(abs(position))

        # In signal mode, track simulated position; otherwise submit real order
        if self.signal_mode_cash > 0:
            self._simulated_cash += int(abs(position)) * price
            self._simulated_position = 0
            self.log.info(
                f">>> SIGNAL: SELL {int(position)} shares @ ${price:.2f} "
                f"(Reason: {reason}, P&L: {pnl_pct * 100:+.1f}% / ${pnl_dollars:+.2f})"
            )
        else:
            order = self.order_factory.market(
                instrument_id=self.instrument_id,
                order_side=OrderSide.SELL,
                quantity=Quantity.from_int(int(abs(position))),
                time_in_force=TimeInForce.IOC,
            )
            self.submit_order(order)
            self.log.info(
                f"Exit ({reason}): {int(position)} shares @ ~${price:.2f}, "
                f"P&L: {pnl_pct * 100:+.1f}%"
            )

        # Reset state
        self._pyramid_level = 0
        self._last_add_price = 0.0
        self._entry_price = 0.0
        self._stop_loss_price = 0.0
        self._total_cost_basis = 0.0
        self._total_quantity = 0
        self._avg_entry_price = 0.0

    def _calculate_position_size(self, price: float) -> int:
        """
        Calculate position size based on available cash and risk parameters.

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
        self.log.info("Stopping Kinetic Trend strategy")

        # Close any open positions
        self.close_all_positions(self.instrument_id)

        self.log.info("Kinetic Trend strategy stopped")

    def on_reset(self) -> None:
        """Called when the strategy is reset."""
        # Reset indicator
        self.bollinger.reset()

        # Reset bars
        self._bars = []

        # Reset state
        self._pyramid_level = 0
        self._last_add_price = 0.0
        self._prev_bb_pct = 0.5
        self._entry_price = 0.0
        self._stop_loss_price = 0.0

        # Reset cost basis tracking
        self._total_cost_basis = 0.0
        self._total_quantity = 0
        self._avg_entry_price = 0.0
