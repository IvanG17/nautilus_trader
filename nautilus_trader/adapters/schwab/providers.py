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

"""Instrument provider for Schwab adapter."""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING

from nautilus_trader.adapters.schwab.common import SCHWAB_VENUE
from nautilus_trader.adapters.schwab.common import schwab_symbol_to_nautilus
from nautilus_trader.common.providers import InstrumentProvider
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.identifiers import Symbol
from nautilus_trader.model.instruments import Equity
from nautilus_trader.model.objects import Currency
from nautilus_trader.model.objects import Money
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


if TYPE_CHECKING:
    import schwabdev

    from nautilus_trader.adapters.schwab.config import SchwabInstrumentProviderConfig
    from nautilus_trader.common.component import LiveClock


logger = logging.getLogger(__name__)


class SchwabInstrumentProvider(InstrumentProvider):
    """
    Provides instrument definitions from Schwab API.

    Parameters
    ----------
    client : schwabdev.Client
        The Schwab API client.
    clock : LiveClock
        The clock for timestamping.
    config : SchwabInstrumentProviderConfig
        The instrument provider configuration.

    """

    def __init__(
        self,
        client: schwabdev.Client,
        clock: LiveClock,
        config: SchwabInstrumentProviderConfig,
    ) -> None:
        """Initialize the Schwab instrument provider."""
        super().__init__()

        self._client = client
        self._clock = clock
        self._config = config
        self._log = logger

    async def load_all_async(
        self,
        filters: dict | None = None,
    ) -> None:
        """
        Load all instruments.

        Note: Schwab doesn't support loading all instruments.
        Use load_ids_async or load_async instead.

        """
        self._log.warning("Schwab does not support loading all instruments")

    async def load_ids_async(
        self,
        instrument_ids: list[InstrumentId],
        filters: dict | None = None,
    ) -> None:
        """
        Load instruments by their IDs.

        Parameters
        ----------
        instrument_ids : list[InstrumentId]
            The instrument IDs to load.
        filters : dict, optional
            Additional filters (not used).

        """
        for instrument_id in instrument_ids:
            await self.load_async(instrument_id, filters)

    async def load_async(
        self,
        instrument_id: InstrumentId,
        filters: dict | None = None,
    ) -> None:
        """
        Load a single instrument by ID.

        Parameters
        ----------
        instrument_id : InstrumentId
            The instrument ID to load.
        filters : dict, optional
            Additional filters (not used).

        """
        symbol = instrument_id.symbol.value

        try:
            # Get quote data from Schwab
            response = self._client.quote(symbol)
            data = response.json()

            if symbol not in data:
                self._log.warning(f"No data found for {symbol}")
                return

            quote_data = data[symbol]
            instrument = self._parse_equity(symbol, quote_data)

            if instrument:
                self.add(instrument)
                self._log.info(f"Loaded instrument: {instrument_id}")

        except Exception as e:
            self._log.error(f"Failed to load instrument {instrument_id}: {e}")

    def _parse_equity(self, symbol: str, data: dict) -> Equity | None:
        """
        Parse Schwab quote data into a Nautilus Equity instrument.

        Parameters
        ----------
        symbol : str
            The symbol string.
        data : dict
            The Schwab quote response data.

        Returns
        -------
        Equity or None

        """
        try:
            reference = data.get("reference", {})
            quote = data.get("quote", {})

            # Extract instrument details
            description = reference.get("description", symbol)
            exchange = reference.get("exchange", "XNAS")

            # Determine price precision from current price
            last_price = quote.get("lastPrice", 0)
            if last_price:
                # Most US equities use 2 decimal places
                price_precision = 2
                price_increment = Decimal("0.01")
            else:
                price_precision = 2
                price_increment = Decimal("0.01")

            instrument_id = InstrumentId(
                symbol=Symbol(symbol),
                venue=SCHWAB_VENUE,
            )

            # Create the Equity instrument
            equity = Equity(
                instrument_id=instrument_id,
                raw_symbol=Symbol(symbol),
                currency=Currency.from_str("USD"),
                price_precision=price_precision,
                price_increment=Price.from_str(str(price_increment)),
                lot_size=Quantity.from_int(1),  # US equities typically have lot size of 1
                ts_event=self._clock.timestamp_ns(),
                ts_init=self._clock.timestamp_ns(),
                isin=reference.get("cusip"),  # Use CUSIP as ISIN placeholder
            )

            return equity

        except Exception as e:
            self._log.error(f"Failed to parse equity {symbol}: {e}")
            return None

    def find(self, instrument_id: InstrumentId) -> Equity | None:
        """
        Find an instrument by ID.

        Parameters
        ----------
        instrument_id : InstrumentId
            The instrument ID to find.

        Returns
        -------
        Equity or None

        """
        return self.get(instrument_id)
