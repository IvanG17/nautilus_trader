#!/usr/bin/env python3
"""
Fake Portfolio Data Generator

Generates realistic test portfolio data for the Grafana portfolio dashboard.
Writes structured JSON log lines that Promtail can parse and send to Loki.

Usage:
    python monitoring/scripts/fake_portfolio_data.py

The script writes to /var/log/trading/portfolio.log by default,
or to ./logs/portfolio.log if the system path is not writable.
"""

import json
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path


# Portfolio configuration
INITIAL_CASH = 100_000.0
HOLDINGS = {
    "ASTS": {"shares": 500, "cost_basis": 42.50},
    "AAPL": {"shares": 100, "cost_basis": 178.25},
    "GOOGL": {"shares": 50, "cost_basis": 142.80},
    "MSFT": {"shares": 75, "cost_basis": 405.60},
    "TSLA": {"shares": 30, "cost_basis": 245.30},
}

# Simulated current prices (will fluctuate)
CURRENT_PRICES = {
    "ASTS": 45.23,
    "AAPL": 182.50,
    "GOOGL": 148.75,
    "MSFT": 415.20,
    "TSLA": 238.90,
}

# Price volatility (daily standard deviation as percentage)
VOLATILITY = {
    "ASTS": 0.03,  # 3% daily volatility
    "AAPL": 0.015,
    "GOOGL": 0.02,
    "MSFT": 0.018,
    "TSLA": 0.04,
}


def get_log_path() -> Path:
    """Determine the best path for writing logs.

    For Docker-based monitoring, write to project's logs/ directory which
    Promtail mounts as /var/log/trading inside the container.
    """
    # Get the project root (parent of monitoring/scripts)
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent.parent
    local_path = project_root / "logs" / "portfolio.log"

    # Create the logs directory if it doesn't exist
    local_path.parent.mkdir(parents=True, exist_ok=True)
    return local_path


def simulate_price_movement(symbol: str, current_price: float) -> float:
    """Simulate realistic price movement based on volatility."""
    vol = VOLATILITY.get(symbol, 0.02)
    # Random walk with slight mean reversion
    change_pct = random.gauss(0, vol / 10)  # Scale down for 30-second intervals
    new_price = current_price * (1 + change_pct)
    return round(new_price, 2)


def calculate_portfolio_metrics(prices: dict[str, float]) -> dict:
    """Calculate portfolio-level metrics."""
    total_value = INITIAL_CASH
    total_cost = 0.0

    for symbol, holding in HOLDINGS.items():
        value = holding["shares"] * prices[symbol]
        cost = holding["shares"] * holding["cost_basis"]
        total_value += value
        total_cost += cost

    # Calculate invested amount (total cost basis)
    invested = sum(h["shares"] * h["cost_basis"] for h in HOLDINGS.values())

    # Daily P&L simulation (random for demo purposes)
    daily_pnl = random.gauss(total_value * 0.001, total_value * 0.005)
    daily_pnl = round(daily_pnl, 2)
    daily_pnl_pct = round((daily_pnl / total_value) * 100, 2)

    return {
        "total_value": round(total_value, 2),
        "cash": round(INITIAL_CASH - invested + random.uniform(-500, 500), 2),
        "daily_pnl": daily_pnl,
        "daily_pnl_pct": daily_pnl_pct,
    }


def generate_snapshot(prices: dict[str, float]) -> dict:
    """Generate a portfolio snapshot log entry."""
    metrics = calculate_portfolio_metrics(prices)
    return {
        "type": "snapshot",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        **metrics,
    }


def generate_holding(symbol: str, prices: dict[str, float]) -> dict:
    """Generate a holding log entry for a single position."""
    holding = HOLDINGS[symbol]
    price = prices[symbol]
    shares = holding["shares"]
    cost_basis = holding["cost_basis"]

    value = round(shares * price, 2)
    cost = shares * cost_basis
    pnl = round(value - cost, 2)

    # Calculate allocation percentage
    total_portfolio = sum(
        HOLDINGS[s]["shares"] * prices[s] for s in HOLDINGS
    ) + INITIAL_CASH
    allocation_pct = round((value / total_portfolio) * 100, 2)

    return {
        "type": "holding",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "shares": shares,
        "price": price,
        "value": value,
        "pnl": pnl,
        "allocation_pct": allocation_pct,
    }


def write_log_line(log_file: Path, data: dict) -> None:
    """Write a JSON log line to the log file."""
    with open(log_file, "a") as f:
        f.write(json.dumps(data) + "\n")


def main():
    """Main loop to generate portfolio data continuously."""
    log_path = get_log_path()
    print(f"Writing portfolio data to: {log_path}")
    print("Press Ctrl+C to stop\n")

    # Initialize prices
    prices = CURRENT_PRICES.copy()

    try:
        while True:
            timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

            # Update prices with simulated movement
            for symbol in prices:
                prices[symbol] = simulate_price_movement(symbol, prices[symbol])

            # Write portfolio snapshot
            snapshot = generate_snapshot(prices)
            write_log_line(log_path, snapshot)
            print(
                f"[{timestamp}] Snapshot: "
                f"Value=${snapshot['total_value']:,.2f}, "
                f"P&L=${snapshot['daily_pnl']:+,.2f} ({snapshot['daily_pnl_pct']:+.2f}%)"
            )

            # Write individual holdings
            for symbol in HOLDINGS:
                holding = generate_holding(symbol, prices)
                write_log_line(log_path, holding)
                print(
                    f"  {symbol}: {holding['shares']} shares @ ${holding['price']:.2f} "
                    f"= ${holding['value']:,.2f} (P&L: ${holding['pnl']:+,.2f})"
                )

            print()

            # Wait 30 seconds before next update
            time.sleep(30)

    except KeyboardInterrupt:
        print("\nStopped portfolio data generation")


if __name__ == "__main__":
    main()
