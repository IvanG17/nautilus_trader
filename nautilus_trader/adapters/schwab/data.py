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

"""Live data client for Schwab adapter."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from datetime import timezone
from typing import TYPE_CHECKING


from nautilus_trader.adapters.schwab.common import SCHWAB_VENUE
from nautilus_trader.adapters.schwab.common import SCHWAB_TIMEFRAMES
from nautilus_trader.adapters.schwab.common import nautilus_to_schwab_symbol
from nautilus_trader.data.messages import RequestBars
from nautilus_trader.data.messages import SubscribeBars
from nautilus_trader.data.messages import UnsubscribeBars
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarSpecification
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import AggregationSource
from nautilus_trader.model.enums import BarAggregation
from nautilus_trader.model.enums import PriceType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


if TYPE_CHECKING:
    import schwabdev

    from nautilus_trader.adapters.schwab.config import SchwabDataClientConfig
    from nautilus_trader.adapters.schwab.providers import SchwabInstrumentProvider
    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock
    from nautilus_trader.common.component import MessageBus


logger = logging.getLogger(__name__)


class SchwabDataClient(LiveMarketDataClient):
    """
    Provides a data client for streaming market data from Schwab.

    This client supports:
    - Real-time bar data via WebSocket streaming
    - Historical bar data via REST API
    - Instrument definitions

    Parameters
    ----------
    loop : asyncio.AbstractEventLoop
        The event loop for the client.
    client : schwabdev.Client
        The Schwab API client.
    msgbus : MessageBus
        The message bus for the client.
    cache : Cache
        The cache for the client.
    clock : LiveClock
        The clock for the client.
    instrument_provider : SchwabInstrumentProvider
        The instrument provider.
    config : SchwabDataClientConfig
        The client configuration.

    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        client: schwabdev.Client,
        msgbus: MessageBus,
        cache: Cache,
        clock: LiveClock,
        instrument_provider: SchwabInstrumentProvider,
        config: SchwabDataClientConfig,
    ) -> None:
        """Initialize the Schwab data client."""
        super().__init__(
            loop=loop,
            client_id=str(SCHWAB_VENUE),
            venue=SCHWAB_VENUE,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
        )

        self._client = client
        self._config = config
        self._stream = None
        self._stream_task: asyncio.Task | None = None
        self._subscribed_bars: dict[str, set[BarType]] = {}  # symbol -> bar_types
        self._log = logger

    async def _connect(self) -> None:
        """Connect to Schwab and start the WebSocket stream."""
        self._log.info("Connecting to Schwab...")

        if self._config.use_websocket:
            await self._start_stream()

        self._log.info("Connected to Schwab")

    async def _disconnect(self) -> None:
        """Disconnect from Schwab."""
        self._log.info("Disconnecting from Schwab...")

        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
            self._stream_task = None

        if self._stream:
            self._stream = None

        self._log.info("Disconnected from Schwab")

    async def _start_stream(self) -> None:
        """Start the WebSocket streaming connection."""
        try:
            import schwabdev

            self._stream = schwabdev.Stream(self._client)

            # Start stream in background task
            self._stream_task = asyncio.create_task(
                self._run_stream(),
                name="schwab_stream",
            )

            self._log.info("WebSocket stream started")

        except Exception as e:
            self._log.error(f"Failed to start WebSocket stream: {e}")
            raise

    async def _run_stream(self) -> None:
        """Run the WebSocket stream message handler."""
        try:
            # Start the stream with our message handler
            self._stream.start(self._handle_stream_message)

            # Keep running until cancelled
            while True:
                await asyncio.sleep(1)

        except asyncio.CancelledError:
            self._log.info("Stream task cancelled")
        except Exception as e:
            self._log.error(f"Stream error: {e}")

    def _handle_stream_message(self, message: dict) -> None:
        """
        Handle incoming WebSocket stream message.

        Parameters
        ----------
        message : dict
            The raw message from Schwab WebSocket.

        """
        try:
            # Handle different message types
            if "data" in message:
                for item in message.get("data", []):
                    service = item.get("service")

                    if service == "CHART_EQUITY":
                        self._handle_chart_equity(item)
                    elif service == "QUOTE":
                        self._handle_quote(item)

            elif "notify" in message:
                # Heartbeat or notification
                pass

        except Exception as e:
            self._log.error(f"Error handling stream message: {e}")

    def _handle_chart_equity(self, data: dict) -> None:
        """
        Handle CHART_EQUITY (bar) data from stream.

        Schwab CHART_EQUITY format:
        {
            "service": "CHART_EQUITY",
            "timestamp": 1234567890123,
            "command": "SUBS",
            "content": [{
                "key": "AAPL",
                "1": 1234567890123,  # chart_time (ms)
                "2": 150.00,         # open
                "3": 150.50,         # high
                "4": 149.50,         # low
                "5": 150.25,         # close
                "6": 10000,          # volume
                "7": 12345,          # sequence
                "8": 18888,          # chart_day
            }]
        }

        """
        content = data.get("content", [])

        for item in content:
            symbol = item.get("key")
            if not symbol:
                continue

            # Extract bar data
            chart_time_ms = item.get("1")  # milliseconds since epoch
            open_price = item.get("2")
            high_price = item.get("3")
            low_price = item.get("4")
            close_price = item.get("5")
            volume = item.get("6", 0)

            if not all([chart_time_ms, open_price, high_price, low_price, close_price]):
                continue

            # Convert timestamp
            ts_event = int(chart_time_ms * 1_000_000)  # Convert ms to ns
            ts_init = self._clock.timestamp_ns()

            # Create bars for all subscribed bar types for this symbol
            bar_types = self._subscribed_bars.get(symbol, set())

            for bar_type in bar_types:
                bar = Bar(
                    bar_type=bar_type,
                    open=Price.from_str(str(open_price)),
                    high=Price.from_str(str(high_price)),
                    low=Price.from_str(str(low_price)),
                    close=Price.from_str(str(close_price)),
                    volume=Quantity.from_int(int(volume)),
                    ts_event=ts_event,
                    ts_init=ts_init,
                )

                self._handle_data(bar)

    def _handle_quote(self, data: dict) -> None:
        """Handle QUOTE data from stream (for future use)."""
        # Quote handling can be added later for QuoteTick support
        pass

    # -- SUBSCRIPTIONS ----------------------------------------------------------------------------

    async def _subscribe_bars(self, command: SubscribeBars) -> None:
        """
        Subscribe to bar data for an instrument.

        Parameters
        ----------
        command : SubscribeBars
            The subscription command.

        """
        bar_type = command.bar_type
        instrument_id = bar_type.instrument_id
        symbol = nautilus_to_schwab_symbol(str(instrument_id))

        # Track subscription
        if symbol not in self._subscribed_bars:
            self._subscribed_bars[symbol] = set()
        self._subscribed_bars[symbol].add(bar_type)

        # Subscribe via WebSocket if connected
        if self._stream and self._config.use_websocket:
            try:
                # Schwab streams 1-minute bars, we'll aggregate locally if needed
                # Subscribe to CHART_EQUITY service
                self._stream.send(
                    self._stream.chart_equity(
                        symbol,
                        "0,1,2,3,4,5,6,7,8",  # All fields
                    )
                )
                self._log.info(f"Subscribed to bars for {symbol}")

            except Exception as e:
                self._log.error(f"Failed to subscribe to bars for {symbol}: {e}")

    async def _unsubscribe_bars(self, command: UnsubscribeBars) -> None:
        """
        Unsubscribe from bar data.

        Parameters
        ----------
        command : UnsubscribeBars
            The unsubscription command.

        """
        bar_type = command.bar_type
        instrument_id = bar_type.instrument_id
        symbol = nautilus_to_schwab_symbol(str(instrument_id))

        # Remove from tracked subscriptions
        if symbol in self._subscribed_bars:
            self._subscribed_bars[symbol].discard(bar_type)
            if not self._subscribed_bars[symbol]:
                del self._subscribed_bars[symbol]

                # Unsubscribe from stream
                if self._stream:
                    try:
                        self._stream.send(
                            self._stream.chart_equity(
                                symbol,
                                "0,1,2,3,4,5,6,7,8",
                                command="UNSUBS",
                            )
                        )
                        self._log.info(f"Unsubscribed from bars for {symbol}")
                    except Exception as e:
                        self._log.error(f"Failed to unsubscribe from bars for {symbol}: {e}")

    # -- REQUESTS ---------------------------------------------------------------------------------

    async def _request_bars(self, request: RequestBars) -> None:
        """
        Request historical bars.

        Parameters
        ----------
        request : RequestBars
            The bar request.

        """
        bar_type = request.bar_type
        instrument_id = bar_type.instrument_id
        symbol = nautilus_to_schwab_symbol(str(instrument_id))

        try:
            # Get timeframe parameters
            spec = bar_type.spec
            timeframe_key = self._bar_spec_to_timeframe_key(spec)

            if timeframe_key not in SCHWAB_TIMEFRAMES:
                self._log.warning(f"Unsupported timeframe: {spec}")
                return

            tf_params = SCHWAB_TIMEFRAMES[timeframe_key]

            # Request historical data from Schwab
            response = self._client.price_history(
                symbol,
                periodType=tf_params["periodType"],
                period=tf_params["period"],
                frequencyType=tf_params["frequencyType"],
                frequency=tf_params["frequency"],
            )

            data = response.json()

            if "candles" not in data:
                self._log.warning(f"No candle data in response for {symbol}")
                return

            # Convert candles to Nautilus bars
            bars = []
            for candle in data["candles"]:
                ts_event = int(candle["datetime"] * 1_000_000)  # ms to ns
                ts_init = self._clock.timestamp_ns()

                bar = Bar(
                    bar_type=bar_type,
                    open=Price.from_str(str(candle["open"])),
                    high=Price.from_str(str(candle["high"])),
                    low=Price.from_str(str(candle["low"])),
                    close=Price.from_str(str(candle["close"])),
                    volume=Quantity.from_int(int(candle["volume"])),
                    ts_event=ts_event,
                    ts_init=ts_init,
                )
                bars.append(bar)

            # Handle the response
            self._handle_bars(
                bar_type=bar_type,
                bars=bars,
                partial=None,
                request_id=request.id,
                ts_init=self._clock.timestamp_ns(),
            )

            self._log.info(f"Received {len(bars)} historical bars for {symbol}")

        except Exception as e:
            self._log.error(f"Failed to request bars for {symbol}: {e}")

    def _bar_spec_to_timeframe_key(self, spec: BarSpecification) -> str:
        """
        Convert a BarSpecification to a Schwab timeframe key.

        Parameters
        ----------
        spec : BarSpecification
            The bar specification.

        Returns
        -------
        str
            The timeframe key for SCHWAB_TIMEFRAMES lookup.

        """
        step = spec.step
        aggregation = spec.aggregation

        if aggregation == BarAggregation.MINUTE:
            if step == 1:
                return "1min"
            elif step == 5:
                return "5min"
            elif step == 10:
                return "10min"
            elif step == 15:
                return "15min"
            elif step == 30:
                return "30min"
            elif step == 60:
                return "1hour"
        elif aggregation == BarAggregation.HOUR:
            return "1hour"
        elif aggregation == BarAggregation.DAY:
            return "1day"
        elif aggregation == BarAggregation.WEEK:
            return "1week"

        return "1min"  # Default fallback
