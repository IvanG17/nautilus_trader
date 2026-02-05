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

"""Common constants and utilities for the Schwab adapter."""

from __future__ import annotations

from nautilus_trader.model.identifiers import Venue


# Schwab venue identifier
SCHWAB_VENUE = Venue("SCHWAB")

# Schwab WebSocket stream field mappings for CHART_EQUITY service
# Reference: https://developer.schwab.com/products/trader-api--individual/details/documentation/Market%20Data%20Production
CHART_EQUITY_FIELDS = {
    "key": "symbol",
    "1": "chart_time",  # Milliseconds since epoch
    "2": "open",
    "3": "high",
    "4": "low",
    "5": "close",
    "6": "volume",
    "7": "sequence",
    "8": "chart_day",
}

# Timeframe mappings (Schwab uses specific period strings)
SCHWAB_TIMEFRAMES = {
    "1min": {"periodType": "day", "period": 1, "frequencyType": "minute", "frequency": 1},
    "5min": {"periodType": "day", "period": 1, "frequencyType": "minute", "frequency": 5},
    "10min": {"periodType": "day", "period": 1, "frequencyType": "minute", "frequency": 10},
    "15min": {"periodType": "day", "period": 1, "frequencyType": "minute", "frequency": 15},
    "30min": {"periodType": "day", "period": 1, "frequencyType": "minute", "frequency": 30},
    "1hour": {"periodType": "day", "period": 10, "frequencyType": "minute", "frequency": 30},
    "1day": {"periodType": "year", "period": 1, "frequencyType": "daily", "frequency": 1},
    "1week": {"periodType": "year", "period": 5, "frequencyType": "weekly", "frequency": 1},
}


def schwab_symbol_to_nautilus(symbol: str) -> str:
    """
    Convert a Schwab symbol to Nautilus format.

    Parameters
    ----------
    symbol : str
        The Schwab symbol (e.g., "AAPL", "MSFT").

    Returns
    -------
    str
        The Nautilus instrument ID string (e.g., "AAPL.SCHWAB").

    """
    return f"{symbol}.{SCHWAB_VENUE.value}"


def nautilus_to_schwab_symbol(instrument_id: str) -> str:
    """
    Convert a Nautilus instrument ID to Schwab symbol.

    Parameters
    ----------
    instrument_id : str
        The Nautilus instrument ID (e.g., "AAPL.SCHWAB").

    Returns
    -------
    str
        The Schwab symbol (e.g., "AAPL").

    """
    return instrument_id.split(".")[0]
