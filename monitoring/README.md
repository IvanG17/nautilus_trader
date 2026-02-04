# SuperStrat Trading Monitor

Loki + Grafana monitoring stack for the SuperStrat trading bot.

## Quick Start

```bash
cd /Users/iguan/Projects/nautilus_trader/monitoring
docker-compose up -d
```

## Access

- **Grafana Dashboard**: http://localhost:3000
  - Username: `admin`
  - Password: `trading123`

- **Loki API**: http://localhost:3100

## Dashboard Features

1. **Trading Signals Panel** - Shows all BUY/SELL/PYRAMID signals
2. **Live Analysis** - Real-time price, ATR, trend updates
3. **Warnings & Errors** - Stream disconnects, errors
4. **Signal Counters** - BUY/SELL counts over 24h
5. **Full Log Stream** - All bot output

## Setting Up Alerts

1. Go to Grafana → Alerting → Alert Rules
2. Create new alert with query:
   ```
   {job="superstrat"} |~ ">>> SIGNAL"
   ```
3. Add contact point (Discord, Slack, Email, etc.)

### Discord Alert Setup

1. Go to Alerting → Contact Points → New
2. Select "Discord"
3. Paste your Discord webhook URL
4. Test & Save

## Log Location

The bot logs to: `/tmp/strat_output.log`

Promtail watches this file and sends to Loki.

## Commands

```bash
# Start stack
docker-compose up -d

# View logs
docker-compose logs -f

# Stop stack
docker-compose down

# Restart
docker-compose restart
```

## Useful LogQL Queries (in Grafana Explore)

```logql
# All signals
{job="superstrat"} |~ ">>> SIGNAL"

# Only BUY signals
{job="superstrat"} |~ ">>> SIGNAL: BUY"

# Analysis with price extraction
{job="superstrat"} |~ "ANALYSIS" | regexp "Price: (?P<price>[\\d.]+)"

# Errors only
{job="superstrat"} |~ "ERROR"

# Stream health issues
{job="superstrat"} |~ "reconnect|stale|disconnect"
```
