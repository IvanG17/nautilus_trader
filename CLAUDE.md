# Nautilus Trader - Strategy Development & Backtesting

## Overview

This is a customized Nautilus Trader setup for developing and backtesting trading strategies, with a Schwab data adapter for live market data streaming.

| Component | Purpose |
|-----------|---------|
| **Nautilus Trader** | High-performance backtesting engine (Rust core, Python API) |
| **Schwab Adapter** | Live data streaming from Schwab API |
| **Optimization** | Bayesian parameter optimization with walk-forward validation |
| **Strategies** | Custom strategies (SuperStrat, Kinetic Trend, Flexible Entry) |

---

## Quick Start

```bash
cd /Users/iguan/Projects/nautilus_trader
source .venv/bin/activate

# Run backtest optimization
python scripts/optimize_asts_superstrat.py

# Run live (signal mode - no execution)
export SCHWAB_APP_KEY="your_key"
export SCHWAB_APP_SECRET="your_secret"
TIMEFRAME=1day python examples/schwab_superstrat_live.py
```

---

## Directory Structure

```
nautilus_trader/
├── schwab_adapter/              # Custom Schwab integration
│   └── nautilus_schwab/
│       ├── config.py            # SchwabDataClientConfig
│       ├── data.py              # SchwabDataClient (WebSocket streaming)
│       ├── factories.py         # Client factory for TradingNode
│       ├── providers.py         # Instrument provider
│       └── common.py            # Constants, venue ID, timeframes
│
├── examples/
│   ├── strategies/
│   │   ├── super_strat.py       # SuperStrat (Donchian + Pyramiding)
│   │   ├── flexible_entry.py    # Base Flexible Entry strategy
│   │   └── kinetic_trend.py     # Kinetic Trend strategy
│   ├── schwab_superstrat_live.py    # Live trading script
│   └── schwab_kinetic_trend_live.py # Example live setup
│
├── optimization/
│   ├── data_fetcher.py          # Fetch historical bars from Schwab
│   ├── optimizer.py             # NautilusOptimizer (Bayesian + walk-forward)
│   └── metrics.py               # Performance metrics calculation
│
├── scripts/
│   ├── optimize_asts_superstrat.py  # SuperStrat optimization
│   ├── optimize_asts_flexible.py    # Flexible Entry optimization
│   └── optimize_asts_kinetic_wide.py
│
├── results/                     # Optimization results (JSON, CSV)
├── data/cache/                  # Cached historical bars (parquet)
└── .env                         # Schwab API credentials
```

---

## Strategies

### SuperStrat (Primary)

**Location:** `examples/strategies/super_strat.py`

Aggressive strategy based on Flexible Entry with two enhancements:

1. **Donchian Breakout Entry** - Enter on N-period high breakout (don't wait for Supertrend)
2. **Pyramiding** - Add 50% position at +1.5 ATR profit, move stop to break-even

**Entry Logic (OR):**
```
Entry = (Supertrend uptrend AND no selling climax)
     OR (Price > Donchian high AND no selling climax)
```

**Optimal Parameters (ASTS 1day):**
```python
atr_period = 13
atr_multiplier = 1.74
donchian_period = 48
pyramid_atr_threshold = 0.62  # ATR profit to trigger add
pyramid_size_pct = 0.78       # Add 78% of original position
```

**Backtest Results:**
| Timeframe | Sharpe | Return |
|-----------|--------|--------|
| 5min | 28.1 | +5.3% |
| 1day | 27.9 | +16.4% |

### Flexible Entry (Base)

**Location:** `examples/strategies/flexible_entry.py`

- Supertrend trend following
- VSA (Volume Spread Analysis) for climax detection
- ATR trailing stop

### Kinetic Trend

**Location:** `examples/strategies/kinetic_trend.py`

- Bollinger Bands mean reversion
- Pyramiding on trend continuation
- Multiple profit targets

---

## How the Backtest Engine Works

```python
# Simplified core loop
portfolio = Portfolio(cash=100_000)
strategy = SuperStrat(params)

for bar in historical_bars:
    signal = strategy.on_bar(bar)

    if signal == "BUY":
        execute_simulated_buy(portfolio, bar.close)
    elif signal == "SELL":
        execute_simulated_sell(portfolio, bar.close)

    track_equity_curve(portfolio)

calculate_metrics()  # Sharpe, return, drawdown
```

**Key point:** This is simulation on historical data, not real trading.

---

## Live Trading Mode

**Current limitation:** Schwab adapter only streams data - NO order execution.

```
Schwab WebSocket → Nautilus → Strategy.on_bar() → Signal logged
                                                      ↓
                                            Manual execution required
```

**To run live signals:**
```bash
TIMEFRAME=1day python examples/schwab_superstrat_live.py
```

**Event-driven architecture:**
- No cron jobs
- WebSocket streams bars in real-time
- `on_bar()` called when each bar completes
- Process runs continuously until Ctrl+C

---

## Schwab Adapter

### Configuration

```python
from nautilus_schwab import SchwabDataClientConfig, SchwabLiveDataClientFactory

config = SchwabDataClientConfig(
    app_key=os.environ["SCHWAB_APP_KEY"],
    app_secret=os.environ["SCHWAB_APP_SECRET"],
    callback_url="https://127.0.0.1",
    use_websocket=True,
)
```

### Data Limits

| Timeframe | Max Historical Data |
|-----------|---------------------|
| 1min, 5min, 15min, 30min | **10 days** (Schwab API limit) |
| 1day | 20 years |

### Token Storage

OAuth2 tokens stored in: `~/.schwabdev/tokens.db`

---

## Optimization

### Running Optimization

```bash
python scripts/optimize_asts_superstrat.py
```

### Parameter Space (Wide)

```python
PARAM_SPACE = {
    "atr_period": ("int", 5, 30),
    "atr_multiplier": ("float", 1.0, 5.0),
    "vsa_window": ("int", 5, 50),
    "vsa_volume_factor": ("float", 1.0, 3.0),
    "trailing_stop_atr_mult": ("float", 0.5, 4.0),
    "position_size_pct": ("float", 0.10, 0.50),
    "donchian_period": ("int", 5, 50),
    "pyramid_atr_threshold": ("float", 0.5, 3.0),
    "pyramid_size_pct": ("float", 0.2, 1.0),
}
```

### Output

Results saved to `results/asts_superstrat_optimization/`:
- `optimization_results_TIMESTAMP.json` - Full results
- `best_params_TIMESTAMP.csv` - Best params by timeframe
- `summary_TIMESTAMP.txt` - Human-readable summary

---

## Environment Setup

```bash
# Create venv
python -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install nautilus_trader schwabdev python-dotenv pandas

# Set credentials
cp .env.example .env
# Edit .env with your Schwab credentials
```

### Required Environment Variables

```bash
SCHWAB_APP_KEY=your_app_key
SCHWAB_APP_SECRET=your_app_secret
```

---

## Key Files Reference

| File | Purpose |
|------|---------|
| `examples/strategies/super_strat.py` | SuperStrat strategy implementation |
| `examples/schwab_superstrat_live.py` | Live trading runner |
| `scripts/optimize_asts_superstrat.py` | Optimization script |
| `optimization/data_fetcher.py` | Historical data fetching |
| `optimization/optimizer.py` | Bayesian optimization engine |
| `schwab_adapter/nautilus_schwab/data.py` | Schwab WebSocket client |
| `schwab_adapter/nautilus_schwab/factories.py` | Client factory |

---

## Deployment Notes

### Current Status
- Runs locally on Mac
- Not deployed to production server yet

### To Deploy to gordan-prod
1. Copy nautilus_trader to server
2. Set up venv and install dependencies
3. Copy Schwab tokens (`~/.schwabdev/tokens.db`)
4. Create systemd service
5. Monitor logs via SSH

### Integration with Gordan
The Gordan trading bot (`gordan_stocks`) has Alpaca execution. Options:
- Port SuperStrat to Gordan format for automated execution
- Or run Nautilus for signals, execute manually

---

## Caveats

1. **Backtest ≠ Reality** - No slippage, fees, or liquidity simulation
2. **Schwab data limit** - Only 10 days of intraday data
3. **No execution** - Schwab adapter is data-only
4. **Past performance** - Historical returns don't guarantee future results
