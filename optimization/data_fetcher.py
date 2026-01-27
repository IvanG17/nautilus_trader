# -------------------------------------------------------------------------------------------------
#  Data Fetcher for Schwab API
# -------------------------------------------------------------------------------------------------

"""
Fetch historical bars from Schwab API and convert to Nautilus Bar objects.

Supports multiple timeframes with automatic caching to parquet.
"""

from __future__ import annotations

import os
from datetime import datetime
from datetime import timezone
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd
import schwabdev

from nautilus_trader.model.data import Bar
from nautilus_trader.model.data import BarType
from nautilus_trader.model.identifiers import InstrumentId
from nautilus_trader.model.objects import Price
from nautilus_trader.model.objects import Quantity


# Schwab API timeframe parameters
SCHWAB_TIMEFRAMES = {
    "1min": {"periodType": "day", "period": 10, "frequencyType": "minute", "frequency": 1},
    "5min": {"periodType": "day", "period": 10, "frequencyType": "minute", "frequency": 5},
    "15min": {"periodType": "day", "period": 10, "frequencyType": "minute", "frequency": 15},
    "30min": {"periodType": "day", "period": 10, "frequencyType": "minute", "frequency": 30},
    "1day": {"periodType": "year", "period": 1, "frequencyType": "daily", "frequency": 1},
}

# Extended timeframes for longer history
EXTENDED_TIMEFRAMES = {
    "5min": {"periodType": "day", "period": 10, "frequencyType": "minute", "frequency": 5},
    "15min": {"periodType": "day", "period": 10, "frequencyType": "minute", "frequency": 15},
    "30min": {"periodType": "day", "period": 10, "frequencyType": "minute", "frequency": 30},
    "1hour": {"periodType": "month", "period": 6, "frequencyType": "minute", "frequency": 30},
    "1day": {"periodType": "year", "period": 2, "frequencyType": "daily", "frequency": 1},
}


class SchwabDataFetcher:
    """
    Fetch historical bars from Schwab API.

    Provides:
    - Multi-timeframe data fetching
    - Automatic conversion to Nautilus Bar objects
    - Parquet caching for fast reruns

    Parameters
    ----------
    app_key : str, optional
        Schwab API app key. Defaults to SCHWAB_APP_KEY env var.
    app_secret : str, optional
        Schwab API app secret. Defaults to SCHWAB_APP_SECRET env var.
    cache_dir : Path, optional
        Directory for parquet cache files.

    """

    def __init__(
        self,
        app_key: str | None = None,
        app_secret: str | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        """Initialize the Schwab data fetcher."""
        self._app_key = app_key or os.environ.get("SCHWAB_APP_KEY")
        self._app_secret = app_secret or os.environ.get("SCHWAB_APP_SECRET")

        if not self._app_key or not self._app_secret:
            raise ValueError(
                "Schwab credentials required. Set SCHWAB_APP_KEY and SCHWAB_APP_SECRET "
                "environment variables or pass them directly."
            )

        self._client = schwabdev.Client(self._app_key, self._app_secret)
        self._cache_dir = cache_dir or Path("data/cache")
        self._cache_dir.mkdir(parents=True, exist_ok=True)

    def fetch_bars(
        self,
        symbol: str,
        timeframe: str,
        use_cache: bool = True,
        extended: bool = True,
    ) -> list[Bar]:
        """
        Fetch historical bars for a symbol and timeframe.

        Parameters
        ----------
        symbol : str
            The ticker symbol (e.g., "ASTS").
        timeframe : str
            The bar timeframe: "5min", "15min", "30min", "1hour", "1day".
        use_cache : bool, default True
            Whether to use cached data if available.
        extended : bool, default True
            Whether to use extended timeframe parameters for more history.

        Returns
        -------
        list[Bar]
            List of Nautilus Bar objects.

        """
        # Check cache first
        cache_file = self._cache_dir / f"{symbol}_{timeframe}.parquet"

        if use_cache and cache_file.exists():
            print(f"Loading {symbol} {timeframe} from cache...")
            return self._load_from_cache(cache_file, symbol, timeframe)

        # Fetch from Schwab API
        print(f"Fetching {symbol} {timeframe} from Schwab API...")
        df = self._fetch_from_api(symbol, timeframe, extended)

        if df.empty:
            print(f"  WARNING: No data returned for {symbol} {timeframe}")
            return []

        # Save to cache
        df.to_parquet(cache_file)
        print(f"  Cached to {cache_file}")

        # Convert to bars
        return self._df_to_bars(df, symbol, timeframe)

    def _fetch_from_api(
        self,
        symbol: str,
        timeframe: str,
        extended: bool = True,
    ) -> pd.DataFrame:
        """
        Fetch bar data from Schwab API.

        Returns DataFrame with columns: timestamp, open, high, low, close, volume
        """
        # Select timeframe parameters
        if extended:
            params = EXTENDED_TIMEFRAMES.get(timeframe, SCHWAB_TIMEFRAMES.get(timeframe))
        else:
            params = SCHWAB_TIMEFRAMES.get(timeframe)

        if params is None:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        try:
            response = self._client.price_history(
                symbol,
                periodType=params["periodType"],
                period=params["period"],
                frequencyType=params["frequencyType"],
                frequency=params["frequency"],
            )

            data = response.json()

            if "candles" not in data:
                print(f"  WARNING: No candles in response: {data.get('error', 'unknown error')}")
                return pd.DataFrame()

            candles = data["candles"]
            print(f"  Received {len(candles)} bars")

            if not candles:
                return pd.DataFrame()

            # Convert to DataFrame
            df = pd.DataFrame(candles)
            df["timestamp"] = pd.to_datetime(df["datetime"], unit="ms", utc=True)
            df = df[["timestamp", "open", "high", "low", "close", "volume"]]

            # Print date range
            print(f"  Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")

            return df

        except Exception as e:
            print(f"  ERROR fetching from Schwab: {e}")
            return pd.DataFrame()

    def _load_from_cache(
        self,
        cache_file: Path,
        symbol: str,
        timeframe: str,
    ) -> list[Bar]:
        """Load bars from parquet cache file."""
        df = pd.read_parquet(cache_file)
        print(f"  Loaded {len(df)} bars from cache")
        print(f"  Date range: {df['timestamp'].min()} to {df['timestamp'].max()}")
        return self._df_to_bars(df, symbol, timeframe)

    def _df_to_bars(
        self,
        df: pd.DataFrame,
        symbol: str,
        timeframe: str,
    ) -> list[Bar]:
        """
        Convert DataFrame to Nautilus Bar objects.

        Parameters
        ----------
        df : pd.DataFrame
            DataFrame with timestamp, open, high, low, close, volume columns.
        symbol : str
            The ticker symbol.
        timeframe : str
            The bar timeframe string.

        Returns
        -------
        list[Bar]
            List of Nautilus Bar objects.

        """
        # Create bar type string
        bar_type_str = self._timeframe_to_bar_type_str(symbol, timeframe)
        bar_type = BarType.from_str(bar_type_str)

        bars = []
        for _, row in df.iterrows():
            # Convert timestamp to nanoseconds
            ts = row["timestamp"]
            if isinstance(ts, pd.Timestamp):
                ts_ns = int(ts.value)  # pandas Timestamp.value is nanoseconds
            else:
                ts_ns = int(pd.Timestamp(ts).value)

            bar = Bar(
                bar_type=bar_type,
                open=Price.from_str(f"{row['open']:.2f}"),
                high=Price.from_str(f"{row['high']:.2f}"),
                low=Price.from_str(f"{row['low']:.2f}"),
                close=Price.from_str(f"{row['close']:.2f}"),
                volume=Quantity.from_int(int(row["volume"])),
                ts_event=ts_ns,
                ts_init=ts_ns,
            )
            bars.append(bar)

        return bars

    def _timeframe_to_bar_type_str(self, symbol: str, timeframe: str) -> str:
        """
        Convert timeframe string to Nautilus BarType string.

        Examples:
            "5min" -> "ASTS.SCHWAB-5-MINUTE-LAST-EXTERNAL"
            "1day" -> "ASTS.SCHWAB-1-DAY-LAST-EXTERNAL"
        """
        instrument_id = f"{symbol}.SCHWAB"

        if timeframe == "1min":
            return f"{instrument_id}-1-MINUTE-LAST-EXTERNAL"
        elif timeframe == "5min":
            return f"{instrument_id}-5-MINUTE-LAST-EXTERNAL"
        elif timeframe == "15min":
            return f"{instrument_id}-15-MINUTE-LAST-EXTERNAL"
        elif timeframe == "30min":
            return f"{instrument_id}-30-MINUTE-LAST-EXTERNAL"
        elif timeframe == "1hour":
            return f"{instrument_id}-60-MINUTE-LAST-EXTERNAL"
        elif timeframe == "1day":
            return f"{instrument_id}-1-DAY-LAST-EXTERNAL"
        else:
            raise ValueError(f"Unknown timeframe: {timeframe}")

    def fetch_multi_timeframe(
        self,
        symbol: str,
        timeframes: list[str] | None = None,
        use_cache: bool = True,
    ) -> dict[str, list[Bar]]:
        """
        Fetch bars for multiple timeframes.

        Parameters
        ----------
        symbol : str
            The ticker symbol.
        timeframes : list[str], optional
            List of timeframes. Defaults to ["5min", "15min", "30min", "1day"].
        use_cache : bool, default True
            Whether to use cached data.

        Returns
        -------
        dict[str, list[Bar]]
            Dictionary mapping timeframe to list of bars.

        """
        if timeframes is None:
            timeframes = ["5min", "15min", "30min", "1day"]

        results = {}
        for tf in timeframes:
            bars = self.fetch_bars(symbol, tf, use_cache=use_cache)
            results[tf] = bars
            print()

        return results

    def clear_cache(self, symbol: str | None = None) -> None:
        """
        Clear cached data files.

        Parameters
        ----------
        symbol : str, optional
            If provided, only clear cache for this symbol.
            Otherwise, clear all cached data.

        """
        if symbol:
            for f in self._cache_dir.glob(f"{symbol}_*.parquet"):
                f.unlink()
                print(f"Deleted {f}")
        else:
            for f in self._cache_dir.glob("*.parquet"):
                f.unlink()
                print(f"Deleted {f}")
