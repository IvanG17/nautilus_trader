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
Provides an API integration for Charles Schwab.

This adapter enables:
- Real-time market data streaming via WebSocket
- Historical bar data via REST API
- Instrument definitions

Authentication requires Schwab API credentials:
- App Key (client ID)
- App Secret
- OAuth2 callback URL

See: https://developer.schwab.com/

"""

from nautilus_schwab.common import SCHWAB_VENUE
from nautilus_schwab.config import SchwabDataClientConfig
from nautilus_schwab.config import SchwabInstrumentProviderConfig
from nautilus_schwab.data import SchwabDataClient
from nautilus_schwab.factories import SchwabLiveDataClientFactory
from nautilus_schwab.factories import get_cached_schwab_client
from nautilus_schwab.factories import get_cached_schwab_instrument_provider
from nautilus_schwab.providers import SchwabInstrumentProvider


__all__ = [
    "SCHWAB_VENUE",
    "SchwabDataClient",
    "SchwabDataClientConfig",
    "SchwabInstrumentProvider",
    "SchwabInstrumentProviderConfig",
    "SchwabLiveDataClientFactory",
    "get_cached_schwab_client",
    "get_cached_schwab_instrument_provider",
]
