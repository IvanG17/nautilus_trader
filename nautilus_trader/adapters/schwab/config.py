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

"""Configuration for Schwab adapter components."""

from __future__ import annotations

from nautilus_trader.config import InstrumentProviderConfig
from nautilus_trader.config import LiveDataClientConfig


class SchwabInstrumentProviderConfig(InstrumentProviderConfig, frozen=True):
    """
    Configuration for ``SchwabInstrumentProvider`` instances.

    Parameters
    ----------
    load_all : bool, default False
        If True, will load all available instruments on startup.
    load_ids : frozenset[str], optional
        A frozenset of instrument ID strings to load on startup.
    account_hash : str, optional
        The Schwab account hash for fetching account-specific instruments.
        If None, will use the first available account.

    """

    account_hash: str | None = None


class SchwabDataClientConfig(LiveDataClientConfig, frozen=True, kw_only=True):
    """
    Configuration for ``SchwabDataClient`` instances.

    Parameters
    ----------
    app_key : str
        The Schwab API application key (client ID).
    app_secret : str
        The Schwab API application secret.
    callback_url : str, default "https://127.0.0.1"
        The OAuth2 callback URL registered with Schwab.
    tokens_db : str, default "~/.schwabdev/tokens.db"
        The path to the SQLite database for OAuth2 tokens.
    use_websocket : bool, default True
        If True, use WebSocket streaming for real-time data.
        If False, use polling (less efficient).
    instrument_provider : SchwabInstrumentProviderConfig, optional
        Configuration for the instrument provider.

    """

    app_key: str
    app_secret: str
    callback_url: str = "https://127.0.0.1"
    tokens_db: str = "~/.schwabdev/tokens.db"
    use_websocket: bool = True
    instrument_provider: SchwabInstrumentProviderConfig = SchwabInstrumentProviderConfig()

    def __repr__(self) -> str:
        """Return a string representation masking sensitive fields."""
        return (
            f"SchwabDataClientConfig("
            f"app_key={self._mask_key(self.app_key)}, "
            f"app_secret=********, "
            f"callback_url='{self.callback_url}', "
            f"tokens_db='{self.tokens_db}', "
            f"use_websocket={self.use_websocket})"
        )

    @staticmethod
    def _mask_key(value: str | None) -> str:
        """Mask sensitive string values."""
        if value is None:
            return "None"
        if len(value) <= 4:
            return "*" * len(value)
        return value[:2] + "*" * (len(value) - 4) + value[-2:]
