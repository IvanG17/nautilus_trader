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
import json
import logging
import threading
import time
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable

from nautilus_schwab.common import SCHWAB_TIMEFRAMES
from nautilus_schwab.common import SCHWAB_VENUE
from nautilus_schwab.common import nautilus_to_schwab_symbol
from nautilus_trader.common.component import Logger
from nautilus_trader.data.messages import RequestBars
from nautilus_trader.data.messages import SubscribeBars
from nautilus_trader.data.messages import UnsubscribeBars
from nautilus_trader.live.data_client import LiveMarketDataClient
from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarSpecification
from nautilus_trader.model.data import BarType
from nautilus_trader.model.enums import BarAggregation
from nautilus_trader.model.identifiers import ClientId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


if TYPE_CHECKING:
    import schwabdev

    from nautilus_schwab.config import SchwabDataClientConfig
    from nautilus_schwab.providers import SchwabInstrumentProvider
    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock
    from nautilus_trader.common.component import MessageBus


logger = logging.getLogger(__name__)


# -------------------------------------------------------------------------------------------------
#  SchwabStreamManager - Threading-based WebSocket streaming
# -------------------------------------------------------------------------------------------------


class SchwabStreamManager:
    """
    Manages schwabdev WebSocket stream in a dedicated thread.

    This class handles the schwabdev streaming connection in a separate daemon thread
    to avoid asyncio/threading conflicts. Data is bridged back to Nautilus's asyncio
    context using `loop.call_soon_threadsafe()`.

    Features:
    - Dedicated daemon thread for schwabdev streaming
    - Thread-safe subscription management
    - Exponential backoff reconnection (5s → 60s max)
    - Health monitoring with automatic reconnection
    - Graceful shutdown
    """

    def __init__(
        self,
        client: schwabdev.Client,
        loop: asyncio.AbstractEventLoop,
        on_bar_callback: Callable[[Bar], None],
        log: Logger,
        clock: LiveClock,
        config: SchwabDataClientConfig,
    ) -> None:
        """
        Initialize the stream manager.

        Parameters
        ----------
        client : schwabdev.Client
            The Schwab API client.
        loop : asyncio.AbstractEventLoop
            The Nautilus event loop for thread-safe callbacks.
        on_bar_callback : Callable[[Bar], None]
            Callback to invoke when a bar is received (in asyncio context).
        log : Logger
            The Nautilus logger.
        clock : LiveClock
            The Nautilus clock for timestamps.
        config : SchwabDataClientConfig
            The client configuration.

        """
        self._client = client
        self._loop = loop
        self._on_bar_callback = on_bar_callback
        self._log = log
        self._clock = clock
        self._config = config

        # Thread management
        self._stream: Any | None = None  # schwabdev.Stream instance
        self._stream_thread: threading.Thread | None = None
        self._running = threading.Event()
        self._connected = threading.Event()
        self._force_reconnect = threading.Event()

        # Subscriptions (thread-safe)
        self._subscribed_symbols: set[str] = set()
        self._subscribed_bar_types: dict[str, set[BarType]] = {}
        self._lock = threading.Lock()

        # Health monitoring
        self._last_message_time: float = 0.0
        self._health_check_task: asyncio.Task | None = None
        self._reconnect_count = 0
        self._stream_start_time: float = 0.0
        self._initial_data_received: bool = False

        # Circuit breaker state
        self._circuit_breaker_failures: int = 0
        self._circuit_open_until: float = 0.0

        # Dropped bar counter
        self._bars_dropped_missing_fields: int = 0

        # Backpressure monitoring
        self._pending_callbacks: int = 0

        # Configuration
        self._health_check_interval = config.stream_health_check_interval
        self._max_stale_time = config.stream_max_stale_time
        self._max_reconnect_delay = config.stream_max_reconnect_delay
        self._circuit_breaker_threshold = config.stream_circuit_breaker_threshold
        self._circuit_breaker_reset_time = config.stream_circuit_breaker_reset_time
        self._initial_data_timeout = config.stream_initial_data_timeout

    def start(self) -> None:
        """Start the stream thread and health check task."""
        if self._running.is_set():
            self._log.warning("SchwabStreamManager already running")
            return

        self._running.set()

        # Start stream thread
        self._stream_thread = threading.Thread(
            target=self._run_stream,
            daemon=True,
            name="schwab-stream",
        )
        self._stream_thread.start()

        # Start health check task in asyncio context
        self._health_check_task = asyncio.create_task(
            self._run_health_check(),
            name="schwab_health_check",
        )

        self._log.info("SchwabStreamManager started")

    def stop(self) -> None:
        """Stop the stream thread and health check task gracefully."""
        if not self._running.is_set():
            return

        self._log.info("Stopping SchwabStreamManager...")
        self._running.clear()
        self._connected.clear()

        # Cancel health check task
        if self._health_check_task:
            self._health_check_task.cancel()
            self._health_check_task = None

        # Stop the stream (in a timeout-protected way)
        if self._stream:
            try:
                self._stream.stop()
            except Exception as e:
                self._log.warning(f"Error stopping stream: {e}")
            self._stream = None

        # Wait for thread to finish
        if self._stream_thread and self._stream_thread.is_alive():
            self._stream_thread.join(timeout=5.0)
            if self._stream_thread.is_alive():
                self._log.warning("Stream thread did not stop within timeout")

        self._log.info(
            f"SchwabStreamManager stopped: reconnects={self._reconnect_count}"
        )

    def subscribe(self, symbol: str, bar_type: BarType) -> None:
        """
        Subscribe to bar data for a symbol.

        Parameters
        ----------
        symbol : str
            The Schwab symbol (e.g., "AAPL").
        bar_type : BarType
            The Nautilus bar type.

        """
        with self._lock:
            is_new_symbol = symbol not in self._subscribed_symbols
            self._subscribed_symbols.add(symbol)

            if symbol not in self._subscribed_bar_types:
                self._subscribed_bar_types[symbol] = set()
            self._subscribed_bar_types[symbol].add(bar_type)

        # Send subscription if stream is connected and this is a new symbol
        if is_new_symbol and self._connected.is_set() and self._stream:
            self._send_subscription(symbol)

        self._log.info(f"Subscribed to {symbol} for bar type {bar_type}")

    def unsubscribe(self, symbol: str, bar_type: BarType) -> None:
        """
        Unsubscribe from bar data for a symbol.

        Parameters
        ----------
        symbol : str
            The Schwab symbol.
        bar_type : BarType
            The Nautilus bar type.

        """
        with self._lock:
            if symbol in self._subscribed_bar_types:
                self._subscribed_bar_types[symbol].discard(bar_type)

                # If no more bar types for this symbol, fully unsubscribe
                if not self._subscribed_bar_types[symbol]:
                    del self._subscribed_bar_types[symbol]
                    self._subscribed_symbols.discard(symbol)

                    # Send unsubscription
                    if self._connected.is_set() and self._stream:
                        self._send_unsubscription(symbol)

        self._log.info(f"Unsubscribed from {symbol} for bar type {bar_type}")

    def force_reconnect(self) -> None:
        """Signal the stream thread to reconnect."""
        self._log.info("Force reconnect requested")
        self._force_reconnect.set()

    def is_connected(self) -> bool:
        """Check if the stream is connected."""
        return self._connected.is_set()

    # -------------------------------------------------------------------------
    # Stream thread methods (run in dedicated thread)
    # -------------------------------------------------------------------------

    def _run_stream(self) -> None:
        """Run in dedicated thread - handles schwabdev streaming with reconnection."""
        import schwabdev

        while self._running.is_set():
            # Check circuit breaker
            current_time = time.time()
            if current_time < self._circuit_open_until:
                wait_time = self._circuit_open_until - current_time
                self._log.warning(
                    f"Circuit breaker open, waiting {wait_time:.0f}s before retry"
                )
                time.sleep(min(wait_time, 1.0))  # Check periodically for shutdown
                continue

            try:
                self._log.info("Starting schwabdev stream...")

                # Explicit cleanup of old stream before creating new one
                old_stream = self._stream
                if old_stream:
                    try:
                        old_stream.stop()
                    except Exception:
                        pass
                    self._stream = None

                # Create new stream instance
                self._stream = schwabdev.Stream(self._client)

                # Start streaming (let schwabdev handle its own ping/keepalive)
                self._stream.start(self._handle_stream_message)

                self._connected.set()
                self._reconnect_count = 0
                self._circuit_breaker_failures = 0  # Reset on successful connect
                self._stream_start_time = time.time()
                self._initial_data_received = False
                self._log.info("schwabdev stream connected")

                # Subscribe to current symbols (copy under lock, send outside)
                with self._lock:
                    symbols_to_subscribe = list(self._subscribed_symbols)
                for symbol in symbols_to_subscribe:
                    self._send_subscription(symbol)

                # Wait for stop or reconnect signal (responsive to shutdown)
                while self._running.is_set():
                    if self._force_reconnect.wait(timeout=0.1):
                        break

                self._force_reconnect.clear()

            except Exception as e:
                self._log.error(f"Stream error: {e}")
                self._connected.clear()

                if not self._running.is_set():
                    break

                # Update circuit breaker
                self._circuit_breaker_failures += 1
                if self._circuit_breaker_failures >= self._circuit_breaker_threshold:
                    self._circuit_open_until = time.time() + self._circuit_breaker_reset_time
                    self._log.warning(
                        f"Circuit breaker opened after {self._circuit_breaker_failures} failures, "
                        f"pausing for {self._circuit_breaker_reset_time}s"
                    )

                # Exponential backoff: 5, 10, 20, 40, 60 (capped)
                delay = min(5 * (2**self._reconnect_count), self._max_reconnect_delay)
                self._reconnect_count += 1
                self._log.info(
                    f"Reconnecting in {delay}s (attempt {self._reconnect_count})"
                )
                time.sleep(delay)

            finally:
                self._connected.clear()
                if self._stream:
                    try:
                        self._stream.stop()
                    except Exception:
                        pass
                    self._stream = None

    def _send_subscription(self, symbol: str) -> None:
        """Send subscription request to schwabdev stream."""
        stream = self._stream  # Capture reference atomically
        if not stream:
            return

        try:
            stream.send(
                stream.chart_equity(
                    symbol,
                    "0,1,2,3,4,5,6,7,8",  # All fields
                )
            )
            self._log.debug(f"Sent subscription for {symbol}")
        except AttributeError:
            self._log.warning(f"Stream unavailable for {symbol}")
        except Exception as e:
            self._log.error(f"Failed to send subscription for {symbol}: {e}")

    def _send_unsubscription(self, symbol: str) -> None:
        """Send unsubscription request to schwabdev stream."""
        stream = self._stream  # Capture reference atomically
        if not stream:
            return

        try:
            stream.send(
                stream.chart_equity(
                    symbol,
                    "0,1,2,3,4,5,6,7,8",
                    command="UNSUBS",
                )
            )
            self._log.debug(f"Sent unsubscription for {symbol}")
        except AttributeError:
            self._log.warning(f"Stream unavailable for {symbol}")
        except Exception as e:
            self._log.error(f"Failed to send unsubscription for {symbol}: {e}")

    def _handle_stream_message(self, message: dict[str, Any] | str) -> None:
        """
        Handle message from schwabdev (runs in stream thread).

        Parameters
        ----------
        message : dict[str, Any] | str
            The raw message from schwabdev stream.

        """
        try:
            # Parse JSON string if needed
            msg: dict[str, Any]
            if isinstance(message, str):
                msg = json.loads(message)
            else:
                msg = message

            # Only track DATA messages for health check (not heartbeats)
            if "data" in msg:
                self._last_message_time = time.time()
                self._initial_data_received = True
                self._log.debug(f"DATA message received: {len(msg.get('data', []))} items")

                for item in msg.get("data", []):
                    if item.get("service") == "CHART_EQUITY":
                        self._process_chart_equity(item)

            elif "notify" in msg:
                # Heartbeat/notification - don't update last_message_time
                pass

        except Exception as e:
            self._log.error(f"Error handling stream message: {e}")

    def _process_chart_equity(self, data: dict) -> None:
        """
        Process CHART_EQUITY data and schedule bar callback in asyncio context.

        Parameters
        ----------
        data : dict
            The CHART_EQUITY service data.

        """
        for item in data.get("content", []):
            symbol = item.get("key")
            if not symbol:
                continue

            with self._lock:
                if symbol not in self._subscribed_symbols:
                    continue
                bar_types = set(self._subscribed_bar_types.get(symbol, set()))

            if not bar_types:
                continue

            # Extract bar data
            chart_time_ms = item.get("7")  # milliseconds since epoch
            open_price = item.get("2")
            high_price = item.get("3")
            low_price = item.get("4")
            close_price = item.get("5")
            volume = item.get("6", 0)

            if not all([chart_time_ms, open_price, high_price, low_price, close_price]):
                self._bars_dropped_missing_fields += 1
                # Log periodically to avoid spam
                if self._bars_dropped_missing_fields % 100 == 1:
                    self._log.warning(
                        f"Dropped {self._bars_dropped_missing_fields} bars due to missing fields. "
                        f"Latest {symbol}: ts={chart_time_ms}, O={open_price}, H={high_price}, "
                        f"L={low_price}, C={close_price}"
                    )
                continue

            # Convert timestamp
            ts_event = int(chart_time_ms * 1_000_000)  # ms to ns
            ts_init = self._clock.timestamp_ns()

            # Create bars for all subscribed bar types
            for bar_type in bar_types:
                try:
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

                    self._log.debug(f"Created bar: {bar}")

                    # Thread-safe callback to asyncio context
                    self._pending_callbacks += 1
                    self._loop.call_soon_threadsafe(
                        self._on_bar_callback_wrapper,
                        bar,
                    )
                except Exception as e:
                    self._log.error(f"Error creating bar for {symbol}: {e}")

    def _on_bar_callback_wrapper(self, bar: Bar) -> None:
        """Wrapper to track pending callbacks for backpressure monitoring."""
        self._pending_callbacks -= 1
        self._on_bar_callback(bar)

    # -------------------------------------------------------------------------
    # Health check (runs in asyncio context)
    # -------------------------------------------------------------------------

    async def _run_health_check(self) -> None:
        """Monitor stream health and trigger reconnection if needed."""
        self._log.info(
            f"Stream health check started (interval={self._health_check_interval}s, "
            f"max_stale={self._max_stale_time}s)"
        )

        initial_timeout_warned = False

        while True:
            try:
                await asyncio.sleep(self._health_check_interval)

                current_time = time.time()

                # Check for initial data timeout
                if (
                    self._stream_start_time > 0
                    and not self._initial_data_received
                    and not initial_timeout_warned
                ):
                    elapsed_since_start = current_time - self._stream_start_time
                    if elapsed_since_start > self._initial_data_timeout:
                        self._log.warning(
                            f"No initial data received after {elapsed_since_start:.0f}s"
                        )
                        initial_timeout_warned = True
                        # Force reconnect if waiting too long for initial data
                        if elapsed_since_start > self._max_stale_time:
                            self._log.warning("Forcing reconnect due to no initial data")
                            self.force_reconnect()
                            initial_timeout_warned = False

                if self._last_message_time == 0:
                    continue  # No messages yet

                elapsed = current_time - self._last_message_time

                # Check backpressure
                if self._pending_callbacks > 1000:
                    self._log.warning(
                        f"Backpressure detected: {self._pending_callbacks} pending callbacks"
                    )

                if elapsed > self._max_stale_time:
                    self._log.warning(
                        f"No stream data for {elapsed:.0f}s, forcing reconnect"
                    )
                    self.force_reconnect()
                    initial_timeout_warned = False  # Reset for new connection
                elif elapsed > 60:
                    self._log.warning(
                        f"Stream data stale: {elapsed:.0f}s since last message"
                    )

            except asyncio.CancelledError:
                self._log.info("Health check task cancelled")
                break
            except Exception as e:
                self._log.error(f"Health check error: {e}")


class SchwabDataClient(LiveMarketDataClient):
    """
    Provides a data client for streaming market data from Schwab.

    This client supports:
    - Real-time bar data via WebSocket streaming (using threading-based SchwabStreamManager)
    - Historical bar data via REST API
    - Instrument definitions

    The streaming is handled by `SchwabStreamManager` which runs schwabdev's WebSocket
    in a dedicated daemon thread to avoid asyncio/threading conflicts. Data is bridged
    back to Nautilus's asyncio context using `loop.call_soon_threadsafe()`.

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
            client_id=ClientId(str(SCHWAB_VENUE)),
            venue=SCHWAB_VENUE,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=instrument_provider,
        )

        self._client = client
        self._config = config
        self._stream_manager: SchwabStreamManager | None = None
        self._subscribed_bars: dict[str, set[BarType]] = {}  # symbol -> bar_types (for tracking)
        # Note: self._log is inherited from parent Component class

    async def _connect(self) -> None:
        """Connect to Schwab and start the WebSocket stream."""
        self._log.info("Connecting to Schwab...")

        if self._config.use_websocket:
            self._stream_manager = SchwabStreamManager(
                client=self._client,
                loop=self._loop,
                on_bar_callback=self._on_bar_received,
                log=self._log,
                clock=self._clock,
                config=self._config,
            )
            self._stream_manager.start()

        self._log.info("Connected to Schwab")

    async def _disconnect(self) -> None:
        """Disconnect from Schwab."""
        self._log.info("Disconnecting from Schwab...")

        if self._stream_manager:
            self._stream_manager.stop()
            self._stream_manager = None

        self._log.info("Disconnected from Schwab")

    def _on_bar_received(self, bar: Bar) -> None:
        """
        Handle bar received from stream manager (called via call_soon_threadsafe).

        Parameters
        ----------
        bar : Bar
            The bar received from the stream.

        """
        self._log.info(f"Bar received: {bar}")
        self._handle_data(bar)

    # -- SUBSCRIPTIONS ----------------------------------------------------------------------------

    async def _subscribe_bars(self, command: SubscribeBars) -> None:
        """
        Subscribe to bar data for an instrument with optional historical warm-up.

        When warmup_bars is configured, historical bars are fetched via REST API
        and pushed to the strategy before live WebSocket streaming starts.

        Parameters
        ----------
        command : SubscribeBars
            The subscription command.

        """
        bar_type = command.bar_type
        instrument_id = bar_type.instrument_id
        symbol = nautilus_to_schwab_symbol(str(instrument_id))

        # Load instrument if not in cache
        if not self._cache.instrument(instrument_id):
            self._log.info(f"Loading instrument {instrument_id}...")
            try:
                await self._instrument_provider.load_async(instrument_id)
                # Get instrument from provider's internal storage
                instruments = list(self._instrument_provider.get_all().values())
                instrument = next(
                    (i for i in instruments if i.id == instrument_id), None
                )
                if instrument:
                    self._cache.add_instrument(instrument)
                    self._log.info(f"Loaded instrument: {instrument_id}")
                else:
                    self._log.warning(f"Could not load instrument: {instrument_id}")
            except Exception as e:
                self._log.error(f"Failed to load instrument {instrument_id}: {e}")

        # Track subscription locally
        if symbol not in self._subscribed_bars:
            self._subscribed_bars[symbol] = set()
        self._subscribed_bars[symbol].add(bar_type)

        # Fetch historical warm-up bars BEFORE starting stream
        if self._config.warmup_bars > 0:
            warmup_bars = await self._fetch_warmup_bars(symbol, bar_type)
            if warmup_bars:
                # Push historical bars to strategy
                for bar in warmup_bars:
                    self._handle_data(bar)
                self._log.info(
                    f"Pushed {len(warmup_bars)} warm-up bars for {symbol}"
                )

        # Subscribe via stream manager (live data)
        if self._stream_manager and self._config.use_websocket:
            self._stream_manager.subscribe(symbol, bar_type)

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

        # Remove from local tracking
        if symbol in self._subscribed_bars:
            self._subscribed_bars[symbol].discard(bar_type)
            if not self._subscribed_bars[symbol]:
                del self._subscribed_bars[symbol]

        # Unsubscribe via stream manager
        if self._stream_manager:
            self._stream_manager.unsubscribe(symbol, bar_type)

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

            # Request historical data from Schwab with retry
            data = await self._request_price_history_with_retry(symbol, tf_params)

            if data is None or "candles" not in data:
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

    async def _request_price_history_with_retry(
        self,
        symbol: str,
        tf_params: dict,
    ) -> dict | None:
        """
        Request price history with retry logic for transient errors.

        Parameters
        ----------
        symbol : str
            The symbol to request.
        tf_params : dict
            The timeframe parameters.

        Returns
        -------
        dict | None
            The response data or None if all retries failed.

        """
        max_retries = self._config.http_max_retries
        retry_delay = self._config.http_retry_delay

        for attempt in range(max_retries):
            try:
                response = self._client.price_history(
                    symbol,
                    periodType=tf_params["periodType"],
                    period=tf_params["period"],
                    frequencyType=tf_params["frequencyType"],
                    frequency=tf_params["frequency"],
                )

                # Check for rate limiting (429) or server errors (5xx)
                if hasattr(response, "status_code"):
                    if response.status_code == 429:
                        # Rate limited - check Retry-After header
                        retry_after = response.headers.get("Retry-After", retry_delay * 2)
                        self._log.warning(
                            f"Rate limited for {symbol}, waiting {retry_after}s"
                        )
                        await asyncio.sleep(float(retry_after))
                        continue
                    elif response.status_code >= 500:
                        self._log.warning(
                            f"Server error {response.status_code} for {symbol}, "
                            f"retry {attempt + 1}/{max_retries}"
                        )
                        await asyncio.sleep(retry_delay * (2 ** attempt))
                        continue

                return response.json()

            except Exception as e:
                self._log.warning(
                    f"Request failed for {symbol}: {e}, retry {attempt + 1}/{max_retries}"
                )
                if attempt < max_retries - 1:
                    await asyncio.sleep(retry_delay * (2 ** attempt))

        self._log.error(f"All retries exhausted for {symbol}")
        return None

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

    # -- WARM-UP METHODS --------------------------------------------------------------------------

    def _get_bar_minutes(self, spec: BarSpecification) -> int:
        """
        Get the number of minutes per bar for a specification.

        Parameters
        ----------
        spec : BarSpecification
            The bar specification.

        Returns
        -------
        int
            Number of minutes per bar.

        """
        if spec.aggregation == BarAggregation.MINUTE:
            return spec.step
        elif spec.aggregation == BarAggregation.HOUR:
            return spec.step * 60
        elif spec.aggregation == BarAggregation.DAY:
            return spec.step * 60 * 24
        return 1

    def _get_warmup_timeframe_params(
        self,
        timeframe_key: str,
        warmup_minutes: int,
    ) -> dict[str, str | int]:
        """
        Calculate Schwab API parameters for warm-up period.

        Adjusts period based on requested warm-up duration.

        Parameters
        ----------
        timeframe_key : str
            The timeframe key (e.g., "1min", "5min").
        warmup_minutes : int
            Total minutes of data needed.

        Returns
        -------
        dict[str, str | int]
            Parameters for Schwab price_history API call.

        """
        base_params: dict[str, str | int] = dict(SCHWAB_TIMEFRAMES[timeframe_key])  # type: ignore[arg-type]

        # For minute-based timeframes, adjust period based on warmup needs
        if base_params["frequencyType"] == "minute":
            # Calculate how many days of data we need
            # ~6.5 market hours per day = 390 minutes
            frequency = int(base_params["frequency"])  # type: ignore[arg-type]
            bars_per_day = 390 / frequency
            bars_needed = warmup_minutes / frequency
            days_needed = max(1, int(bars_needed / bars_per_day) + 1)

            base_params["periodType"] = "day"
            base_params["period"] = min(days_needed, 10)  # Schwab limit

        return base_params

    async def _fetch_warmup_bars(
        self,
        symbol: str,
        bar_type: BarType,
    ) -> list[Bar]:
        """
        Fetch historical bars for warm-up.

        Uses the config's warmup_bars setting to load historical data
        before starting the live WebSocket stream.

        Parameters
        ----------
        symbol : str
            The Schwab symbol (e.g., "AAPL").
        bar_type : BarType
            The Nautilus bar type.

        Returns
        -------
        list[Bar]
            List of historical bars, limited to warmup_bars count.

        """
        if self._config.warmup_bars <= 0:
            return []  # Warm-up disabled

        spec = bar_type.spec
        timeframe_key = self._bar_spec_to_timeframe_key(spec)

        if timeframe_key not in SCHWAB_TIMEFRAMES:
            self._log.warning(f"Unsupported timeframe for warmup: {spec}")
            return []

        # Calculate how many minutes of data we need based on bars requested
        bar_minutes = self._get_bar_minutes(spec)
        warmup_minutes = self._config.warmup_bars * bar_minutes
        tf_params = self._get_warmup_timeframe_params(timeframe_key, warmup_minutes)

        try:
            # Use retry logic for warmup requests
            data = await self._request_price_history_with_retry(symbol, tf_params)
            if data is None or "candles" not in data:
                self._log.warning(f"No candle data in warmup response for {symbol}")
                return []

            # Convert to Nautilus bars
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

            # Limit to requested number of bars (take most recent)
            if len(bars) > self._config.warmup_bars:
                bars = bars[-self._config.warmup_bars:]

            self._log.info(f"Loaded {len(bars)} warm-up bars for {symbol}")
            return bars

        except Exception as e:
            self._log.error(f"Failed to fetch warm-up bars for {symbol}: {e}")
            return []
