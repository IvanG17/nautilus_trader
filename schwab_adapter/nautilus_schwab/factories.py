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

"""Factory functions for Schwab adapter components."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from nautilus_schwab.config import SchwabDataClientConfig
from nautilus_schwab.data import SchwabDataClient
from nautilus_schwab.providers import SchwabInstrumentProvider
from nautilus_trader.live.factories import LiveDataClientFactory


if TYPE_CHECKING:
    import schwabdev

    from nautilus_trader.cache.cache import Cache
    from nautilus_trader.common.component import LiveClock
    from nautilus_trader.common.component import MessageBus


logger = logging.getLogger(__name__)

# Module-level caches for singleton patterns
SCHWAB_CLIENTS: dict[tuple, "schwabdev.Client"] = {}
SCHWAB_INSTRUMENT_PROVIDERS: dict[tuple, SchwabInstrumentProvider] = {}


def get_cached_schwab_client(
    app_key: str,
    app_secret: str,
    callback_url: str,
    tokens_db: str,
) -> "schwabdev.Client":
    """
    Get or create a cached Schwab API client.

    Parameters
    ----------
    app_key : str
        The Schwab API application key.
    app_secret : str
        The Schwab API application secret.
    callback_url : str
        The OAuth2 callback URL.
    tokens_db : str
        The path to the SQLite database for OAuth2 tokens.

    Returns
    -------
    schwabdev.Client
        The Schwab API client.

    """
    global SCHWAB_CLIENTS

    client_key = (app_key, callback_url, tokens_db)

    if client_key not in SCHWAB_CLIENTS:
        import schwabdev
        from pathlib import Path

        # Expand ~ in path
        tokens_path = str(Path(tokens_db).expanduser())

        client = schwabdev.Client(
            app_key=app_key,
            app_secret=app_secret,
            callback_url=callback_url,
            tokens_db=tokens_path,
        )

        SCHWAB_CLIENTS[client_key] = client
        logger.info("Created new Schwab client")

    return SCHWAB_CLIENTS[client_key]


def get_cached_schwab_instrument_provider(
    client: "schwabdev.Client",
    clock: "LiveClock",
    config: "SchwabDataClientConfig",
) -> SchwabInstrumentProvider:
    """
    Get or create a cached Schwab instrument provider.

    Parameters
    ----------
    client : schwabdev.Client
        The Schwab API client.
    clock : LiveClock
        The clock for timestamping.
    config : SchwabDataClientConfig
        The client configuration.

    Returns
    -------
    SchwabInstrumentProvider
        The instrument provider.

    """
    global SCHWAB_INSTRUMENT_PROVIDERS

    # Create cache key based on config
    provider_key = (id(client), hash(str(config.instrument_provider)))

    if provider_key not in SCHWAB_INSTRUMENT_PROVIDERS:
        provider = SchwabInstrumentProvider(
            client=client,
            clock=clock,
            config=config.instrument_provider,
        )
        SCHWAB_INSTRUMENT_PROVIDERS[provider_key] = provider
        logger.info("Created new Schwab instrument provider")

    return SCHWAB_INSTRUMENT_PROVIDERS[provider_key]


class SchwabLiveDataClientFactory(LiveDataClientFactory):
    """
    Factory for creating Schwab live data clients.

    This factory handles the creation of:
    - Schwab API client (with OAuth2 authentication)
    - Instrument provider
    - Data client with WebSocket streaming

    """

    @staticmethod
    def create(
        loop: asyncio.AbstractEventLoop,
        name: str,
        config: SchwabDataClientConfig,
        msgbus: "MessageBus",
        cache: "Cache",
        clock: "LiveClock",
    ) -> SchwabDataClient:
        """
        Create a new Schwab data client.

        Parameters
        ----------
        loop : asyncio.AbstractEventLoop
            The event loop for the client.
        name : str
            The custom client name.
        config : SchwabDataClientConfig
            The client configuration.
        msgbus : MessageBus
            The message bus for the client.
        cache : Cache
            The cache for the client.
        clock : LiveClock
            The clock for the client.

        Returns
        -------
        SchwabDataClient
            The configured Schwab data client.

        """
        # Get or create Schwab API client
        client = get_cached_schwab_client(
            app_key=config.app_key,
            app_secret=config.app_secret,
            callback_url=config.callback_url,
            tokens_db=config.tokens_db,
        )

        # Get or create instrument provider
        provider = get_cached_schwab_instrument_provider(
            client=client,
            clock=clock,
            config=config,
        )

        # Create and return data client
        data_client = SchwabDataClient(
            loop=loop,
            client=client,
            msgbus=msgbus,
            cache=cache,
            clock=clock,
            instrument_provider=provider,
            config=config,
        )

        return data_client
