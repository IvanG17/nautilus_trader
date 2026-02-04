# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is a Loki + Grafana monitoring stack for NautilusTrader trading strategies. It collects logs from trading bots and displays them in real-time dashboards.

## Commands

```bash
# Start the monitoring stack
docker-compose up -d

# Stop and remove containers
docker-compose down

# Stop and clear all data (Loki logs + Grafana settings)
docker-compose down -v

# Restart Grafana to reload dashboard changes
docker-compose restart grafana

# View container logs
docker-compose logs -f

# Generate fake portfolio data for testing
python scripts/fake_portfolio_data.py
```

## Access

- **Grafana**: http://localhost:3000 (admin / trading123)
- **Loki API**: http://localhost:3100

## Architecture

```
Trading Bot → logs/portfolio.log → Promtail → Loki → Grafana Dashboards
                                      ↑
                                 promtail-config.yml
                                 (defines jobs & labels)
```

### Key Components

| File | Purpose |
|------|---------|
| `docker-compose.yml` | Defines Loki, Promtail, Grafana services |
| `promtail-config.yml` | Log scraping config - defines jobs (`superstrat`, `portfolio`) and label extraction |
| `loki-config.yml` | Loki storage and retention settings (30 day retention) |
| `grafana/provisioning/dashboards/*.json` | Dashboard definitions |
| `grafana/provisioning/datasources/loki.yml` | Loki datasource config |

### Log Jobs

**superstrat**: Trading strategy logs from `logs/strat_output.log`
- Labels extracted: `level` (INFO/WARN/ERROR), `trend` (UP/DOWN)

**portfolio**: Portfolio snapshot data from `logs/portfolio.log`
- JSON format with `type` (snapshot/holding) and `symbol` labels
- Snapshot fields: `total_value`, `cash`, `daily_pnl`, `daily_pnl_pct`
- Holding fields: `symbol`, `shares`, `price`, `value`, `pnl`, `allocation_pct`

## Dashboard Development

Dashboards are provisioned from JSON files in `grafana/provisioning/dashboards/`. After editing, restart Grafana:

```bash
docker-compose restart grafana
```

### Loki Query Patterns

For metric values from JSON logs, use `unwrap` with aggregation to avoid duplicate series:

```logql
# Single value (for stat panels) - use max() to deduplicate
max(last_over_time({job="portfolio", type="snapshot"} | json | unwrap total_value [1h]))

# Time series (for graphs)
max(last_over_time({job="portfolio", type="snapshot"} | json | unwrap total_value [1m]))

# Log queries (for tables)
{job="portfolio", type="holding"} | json
```

### Testing Queries

```bash
# Instant query (single value)
curl -s 'http://localhost:3100/loki/api/v1/query' \
  --data-urlencode 'query=max(last_over_time({job="portfolio", type="snapshot"} | json | unwrap total_value [1h]))' \
  | jq '.data.result[0].value[1]'

# Range query (time series)
curl -s 'http://localhost:3100/loki/api/v1/query_range' \
  --data-urlencode 'query={job="portfolio", type="snapshot"}' \
  --data-urlencode 'limit=10' \
  | jq '.data.result[0].values'
```

## Known Issues

- Loki JSON parsing creates separate series for each unique label combination. Always wrap metric queries with `max()` or `sum()` to aggregate into a single series.
- Stat panels need `queryType: "instant"` in their target config to show single values instead of all historical points.
