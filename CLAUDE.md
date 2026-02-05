# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Customized NautilusTrader fork: high-performance algorithmic trading platform (Rust core + Python API) extended with a Schwab data adapter, three trading strategies, Bayesian optimization, and production deployment infrastructure.

## Build & Development

```bash
make install          # Install deps + build (release)
make build-debug      # Build debug mode (faster, for development)
make pytest           # Run all Python tests (parallel)
make cargo-test       # Run all Rust tests
make check-code       # Clippy + ruff linting
make format           # Rust nightly + ruff formatting
make pre-commit       # Pre-commit hooks
make clean            # Clean build artifacts

# Single test
uv run --active --no-sync pytest tests/unit_tests/path/to/test.py::TestClass::test_name -v

# Single Rust crate
make cargo-test-crate-nautilus-model

# Schwab utilities
make schwab-auth      # OAuth authentication
make schwab-status    # Check token status
make schwab-live      # Run SuperStrat live
```

## Architecture

### Core NautilusTrader

Hybrid Rust/Cython/Python:
- **Rust core** (`crates/`): `nautilus-model`, `nautilus-core`, `nautilus-backtest`, `nautilus-live`, `nautilus-execution`, `nautilus-data`
- **Cython layer**: PyO3 bindings
- **Python API** (`nautilus_trader/`): Strategies, backtesting, live trading

### Custom Components (This Fork)

#### Schwab Adapter — Two Implementations

| Location | Purpose |
|---|---|
| `schwab_adapter/nautilus_schwab/` | **Primary** — full-featured with threaded WebSocket, circuit breaker, health monitoring, warmup |
| `nautilus_trader/adapters/schwab/` | **Simplified** — basic asyncio WebSocket, fewer features |

Primary adapter files (`schwab_adapter/nautilus_schwab/`):
- `config.py` — `SchwabDataClientConfig` with OAuth2 credentials, WebSocket settings, health monitoring params
- `data.py` — `SchwabDataClient` + `SchwabStreamManager` (daemon thread with `call_soon_threadsafe()` bridge to asyncio)
- `factories.py` — Factory for creating clients + instrument providers (singleton caching)
- `providers.py` — `SchwabInstrumentProvider` for loading US equity instruments from Schwab API
- Data-only: no order execution

#### Trading Strategies (`examples/strategies/`)

| Strategy | File | Description |
|---|---|---|
| SuperStrat | `super_strat.py` | Donchian breakout + Supertrend/VSA + pyramiding (+50% on +1.5 ATR profit) |
| FlexibleEntry | `flexible_entry.py` | Supertrend trend following + VSA climax detection + ATR trailing stops |
| KineticTrend | `kinetic_trend.py` | Bollinger Bands mean reversion + pyramiding (enter upper band breakout, exit below lower) |

#### Optimization Framework (`optimization/`)

- `optimizer.py` — Bayesian optimization (Optuna TPE sampler) with walk-forward validation
- `data_fetcher.py` — Schwab REST API data fetching with parquet caching
- `metrics.py` — Sharpe ratio, total return, max drawdown, win rate, profit factor

### Data Flow

**Backtest**: `scripts/optimize_*.py` → `SchwabDataFetcher` → `NautilusOptimizer` → walk-forward validation → `results/` JSON

**Live (signal mode)**: `TradingNode` → `SchwabDataClient` (WebSocket thread) → `call_soon_threadsafe()` → `Strategy.on_bar()` → signal logged (no execution)

## Running Strategies

```bash
# Live signals - single ticker
export SCHWAB_APP_KEY="your_key"
export SCHWAB_APP_SECRET="your_secret"
TIMEFRAME=1day python examples/schwab_superstrat_live.py

# Live signals - multi-ticker, multi-strategy (reads config/strategies.yaml)
python examples/schwab_multi_strat_live.py

# Custom config file
CONFIG_PATH=config/my_strategies.yaml python examples/schwab_multi_strat_live.py

# Backtest optimization
python scripts/optimize_asts_superstrat.py
```

### Multi-Strategy Configuration (`config/strategies.yaml`)

Supports 1-3 strategies with up to 10 tickers each. Cash divided equally among all tickers.

```yaml
global:
  total_cash: 100000
  allocation: equal_per_ticker

strategies:
  - name: SuperStrat
    class: SuperStratStrategy
    config_class: SuperStratConfig
    timeframe: 1day          # default for all tickers in this strategy
    tickers:
      - symbol: ASTS
        timeframe: 5min      # optional per-ticker override
        params:
          atr_period: 13
          # ... per-ticker parameters
      - symbol: AAPL
        # inherits 1day from strategy default
        params:
          atr_period: 14
```

**Valid timeframes**: `5min`, `15min`, `30min`, `1day`

**Strategy/config class registry** (in `schwab_multi_strat_live.py`):
- `SuperStratStrategy` / `SuperStratConfig`
- `KineticTrendStrategy` / `KineticTrendConfig`
- `FlexibleEntryStrategy` / `FlexibleEntryConfig`

## Project File Map

```
schwab_adapter/nautilus_schwab/   # Primary Schwab adapter (threaded WebSocket)
nautilus_trader/adapters/schwab/  # Simplified Schwab adapter (asyncio)
examples/strategies/              # Strategy implementations
examples/schwab_*_live.py         # Live trading runners
optimization/                     # Bayesian optimization framework
scripts/optimize_*.py             # Optimization runners per strategy
scripts/schwab_auth.py            # OAuth2 authentication
scripts/schwab_status.py          # Token status check
config/strategies.yaml            # Multi-strategy YAML config
results/                          # Optimization output (JSON, CSV, summaries)
tests/custom/                     # Custom strategy tests
deploy/                           # Digital Ocean production deployment (Docker, systemd)
monitoring/                       # Local Grafana + Loki log monitoring stack
.github/workflows/superstrat-*    # CI (ruff, mypy, tests) + CD (deploy to DO)
run_kinetic_trend.py              # Production KineticTrend wrapper
```

## Environment

```bash
SCHWAB_APP_KEY=your_app_key       # Required
SCHWAB_APP_SECRET=your_app_secret # Required
SCHWAB_CALLBACK_URL=https://127.0.0.1  # Optional (default shown)
SCHWAB_TOKENS_DB=~/.schwabdev/tokens.db # Optional (default shown)
CONFIG_PATH=config/strategies.yaml      # Optional (default shown)
TIMEFRAME=1day                          # For single-ticker live scripts
```

## Testing

```bash
make pytest                        # All Python tests
make cargo-test                    # All Rust tests
make cargo-test-crate-nautilus-core # Single Rust crate

# Custom strategy tests
uv run --active --no-sync pytest tests/custom/ -v
```

## Key Constraints

- **Schwab intraday data limit**: 10 days max for 1min-30min bars
- **No order execution**: Schwab adapter is data-only (signal mode)
- **Backtest simulation**: No slippage, fees, or liquidity modeling
- **Threading**: WebSocket runs in dedicated daemon thread due to schwabdev blocking I/O
- **OAuth2 tokens**: Refresh tokens valid 7 days, access tokens 30 minutes
