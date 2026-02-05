#!/usr/bin/env python3
# -------------------------------------------------------------------------------------------------
#  Multi-Ticker, Multi-Strategy Live Trading
#
#  Reads configuration from config/strategies.yaml and runs multiple strategy instances
#  across multiple tickers with equal cash allocation per ticker.
# -------------------------------------------------------------------------------------------------

"""
Multi-Ticker, Multi-Strategy Live Trading Script

Usage:
    cd /Users/iguan/Projects/nautilus_trader
    source .venv/bin/activate

    # Set credentials (or use .env file)
    export SCHWAB_APP_KEY="your_app_key"
    export SCHWAB_APP_SECRET="your_app_secret"

    # Run with default config
    python examples/schwab_multi_strat_live.py

    # Or specify custom config path
    CONFIG_PATH=config/my_strategies.yaml python examples/schwab_multi_strat_live.py

Configuration:
    See config/strategies.yaml for the YAML configuration format.
    Supports 1-3 strategies with up to 10 tickers each.
    Cash is equally divided among all tickers.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

# Setup path for local imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "schwab_adapter"))
sys.path.insert(0, str(PROJECT_ROOT / "examples"))

from nautilus_trader.config import InstrumentProviderConfig, LoggingConfig
from nautilus_trader.live.config import LiveDataEngineConfig, TradingNodeConfig
from nautilus_trader.live.node import TradingNode

from nautilus_schwab import SchwabDataClientConfig, SchwabLiveDataClientFactory
from strategies.flexible_entry import FlexibleEntryConfig, FlexibleEntryStrategy
from strategies.kinetic_trend import KineticTrendConfig, KineticTrendStrategy
from strategies.super_strat import SuperStratConfig, SuperStratStrategy


# Strategy class registry
STRATEGY_CLASSES = {
    "SuperStratStrategy": SuperStratStrategy,
    "KineticTrendStrategy": KineticTrendStrategy,
    "FlexibleEntryStrategy": FlexibleEntryStrategy,
}

CONFIG_CLASSES = {
    "SuperStratConfig": SuperStratConfig,
    "KineticTrendConfig": KineticTrendConfig,
    "FlexibleEntryConfig": FlexibleEntryConfig,
}

# Timeframe to bar type suffix mapping
TIMEFRAME_MAP = {
    "5min": "5-MINUTE",
    "15min": "15-MINUTE",
    "30min": "30-MINUTE",
    "1day": "1-DAY",
}


def load_config(config_path: str = "config/strategies.yaml") -> dict:
    """
    Load strategy configuration from YAML file.

    Parameters
    ----------
    config_path : str
        Path to the YAML configuration file.

    Returns
    -------
    dict
        Parsed configuration dictionary.

    """
    config_file = Path(config_path)
    if not config_file.exists():
        # Try relative to project root
        config_file = PROJECT_ROOT / config_path

    if not config_file.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_file) as f:
        return yaml.safe_load(f)


def build_strategies(config: dict) -> tuple[list, set[str]]:
    """
    Build strategy instances from configuration.

    Parameters
    ----------
    config : dict
        Parsed YAML configuration.

    Returns
    -------
    tuple[list, set[str]]
        Tuple of (strategy instances, set of instrument IDs).

    """
    strategies = []
    all_instrument_ids = set()

    total_cash = config["global"]["total_cash"]

    # Count total tickers across all strategies
    total_tickers = sum(len(s["tickers"]) for s in config["strategies"])
    if total_tickers == 0:
        raise ValueError("No tickers configured in any strategy")

    cash_per_ticker = total_cash / total_tickers

    print(f"Total cash: ${total_cash:,.0f}")
    print(f"Total tickers: {total_tickers}")
    print(f"Cash per ticker: ${cash_per_ticker:,.0f}")
    print()

    for strat_def in config["strategies"]:
        strat_name = strat_def["name"]
        strat_class_name = strat_def["class"]
        config_class_name = strat_def["config_class"]
        default_timeframe = strat_def["timeframe"]

        # Get strategy and config classes
        if strat_class_name not in STRATEGY_CLASSES:
            raise ValueError(f"Unknown strategy class: {strat_class_name}")
        if config_class_name not in CONFIG_CLASSES:
            raise ValueError(f"Unknown config class: {config_class_name}")

        strat_class = STRATEGY_CLASSES[strat_class_name]
        config_class = CONFIG_CLASSES[config_class_name]

        print(f"Strategy: {strat_name} ({strat_class_name})")
        print(f"  Default timeframe: {default_timeframe}")

        for ticker_def in strat_def["tickers"]:
            symbol = ticker_def["symbol"]
            params = ticker_def.get("params", {})

            # Resolve per-ticker timeframe (falls back to strategy default)
            ticker_timeframe = ticker_def.get("timeframe", default_timeframe)
            bar_type_suffix = TIMEFRAME_MAP.get(ticker_timeframe)
            if bar_type_suffix is None:
                raise ValueError(
                    f"Unknown timeframe '{ticker_timeframe}' for {symbol}. "
                    f"Valid: {list(TIMEFRAME_MAP.keys())}"
                )

            instrument_id = f"{symbol}.SCHWAB"
            bar_type = f"{instrument_id}-{bar_type_suffix}-LAST-EXTERNAL"

            all_instrument_ids.add(instrument_id)

            # Build strategy config
            # Add signal_mode_cash for strategies that support it
            config_kwargs = {
                "strategy_id": f"{strat_name}-{symbol}",
                "instrument_id": instrument_id,
                "bar_type": bar_type,
                **params,
            }

            # Add signal_mode_cash if the config class supports it
            if hasattr(config_class, "__dataclass_fields__") or hasattr(config_class, "model_fields"):
                # Check if signal_mode_cash is a valid field
                try:
                    # Try to get the field names
                    if hasattr(config_class, "__annotations__"):
                        if "signal_mode_cash" in config_class.__annotations__:
                            config_kwargs["signal_mode_cash"] = cash_per_ticker
                except Exception:
                    pass

            strat_config = config_class(**config_kwargs)
            strategy = strat_class(config=strat_config)
            strategies.append(strategy)

            print(f"  - {symbol}: {instrument_id} [{ticker_timeframe}]")

        print()

    return strategies, all_instrument_ids


def main():
    """Run multi-strategy live trading."""
    # Load configuration
    config_path = os.environ.get("CONFIG_PATH", "config/strategies.yaml")

    print("=" * 70)
    print("  Multi-Ticker, Multi-Strategy Live Trading (Signal Mode)")
    print("=" * 70)
    print(f"  Config: {config_path}")
    print(f"  Mode: SIGNAL ONLY (no execution)")
    print("=" * 70)
    print()

    try:
        config = load_config(config_path)
    except FileNotFoundError as e:
        print(f"ERROR: {e}")
        sys.exit(1)

    # Build strategies
    strategies, all_instrument_ids = build_strategies(config)

    print(f"Loaded {len(strategies)} strategy instances")
    print(f"Instruments: {', '.join(sorted(all_instrument_ids))}")
    print()

    # Validate Schwab credentials
    schwab_app_key = os.environ.get("SCHWAB_APP_KEY")
    schwab_app_secret = os.environ.get("SCHWAB_APP_SECRET")

    if not schwab_app_key or not schwab_app_secret:
        print("ERROR: Set SCHWAB_APP_KEY and SCHWAB_APP_SECRET environment variables")
        sys.exit(1)

    schwab_callback_url = os.environ.get("SCHWAB_CALLBACK_URL", "https://127.0.0.1")
    schwab_tokens_db = os.environ.get("SCHWAB_TOKENS_DB", str(Path.home() / ".schwabdev" / "tokens.db"))

    # Configure Schwab data client
    schwab_config = SchwabDataClientConfig(
        app_key=schwab_app_key,
        app_secret=schwab_app_secret,
        callback_url=schwab_callback_url,
        tokens_db=schwab_tokens_db,
        use_websocket=True,
        warmup_bars=100,
        instrument_provider=InstrumentProviderConfig(
            load_all=False,
            load_ids=frozenset(all_instrument_ids),
        ),
    )

    # Configure trading node
    node_config = TradingNodeConfig(
        trader_id="MULTI-STRAT-001",
        logging=LoggingConfig(log_level="INFO"),
        data_clients={"SCHWAB": schwab_config},
        data_engine=LiveDataEngineConfig(
            time_bars_build_with_no_updates=True,
            time_bars_timestamp_on_close=True,
        ),
    )

    # Build and run
    print("Building trading node...")
    node = TradingNode(config=node_config)
    node.add_data_client_factory("SCHWAB", SchwabLiveDataClientFactory)
    node.build()

    print("Adding strategies...")
    for strategy in strategies:
        node.trader.add_strategy(strategy)
        print(f"  Added: {strategy.id}")

    print()
    print("=" * 70)
    print("  STARTING LIVE TRADING")
    print("  Press Ctrl+C to stop")
    print("=" * 70)
    print()

    try:
        node.run()
    except KeyboardInterrupt:
        print("\nShutdown requested...")
    finally:
        node.dispose()
        print("Trading node disposed.")


if __name__ == "__main__":
    main()
