from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest.snapshot import get_backtest_snapshot, get_point_in_time_news, get_point_in_time_options, get_point_in_time_stock_data


if __name__ == "__main__":
    timestamp = datetime(2024, 6, 5, 12, 0, 0)
    symbols = ["AAPL", "MSFT"]

    portfolio_state = {
        "timestamp": timestamp,
        "cash": 100000.0,
        "equity": 100000.0,
        "positions": [],
        "realized_pnl": 0.0,
        "unrealized_pnl": 0.0,
    }

    stock_data = get_point_in_time_stock_data(timestamp, symbols)
    option_snapshot = get_point_in_time_options(timestamp, symbols)
    news = get_point_in_time_news(timestamp, symbols, historical_news=[])
    snapshot = get_backtest_snapshot(timestamp, symbols, portfolio_state)

    print("\n=== snapshot assembly summary ===")
    print(f"decision timestamp: {timestamp.isoformat()}")
    print(f"stock rows assembled: {len(stock_data)}")
    print(f"option universe contracts: {len(option_snapshot['universe'])}")
    print(f"option bar rows assembled: {len(option_snapshot['bars'])}")
    print(f"option trade rows assembled: {len(option_snapshot['trades'])}")
    print(f"historical news rows assembled: {len(news)}")
    print(f"snapshot portfolio positions: {len(snapshot.portfolio_state.positions)}")

    future_stock = any(item.get("timestamp") and item.get("timestamp") > timestamp for item in stock_data if isinstance(item, dict))
    future_option = any(item.get("timestamp") and item.get("timestamp") > timestamp for item in option_snapshot["bars"] + option_snapshot["trades"] if isinstance(item, dict))
    future_news = any(item.get("timestamp") and item.get("timestamp") > timestamp for item in news if isinstance(item, dict))

    print("\nfuture-data check:")
    print(f"  stock data after timestamp: {future_stock}")
    print(f"  option data after timestamp: {future_option}")
    print(f"  news after timestamp: {future_news}")

    if snapshot.stock_data:
        print("\nfirst stock data record:")
        print(snapshot.stock_data[0])

    if snapshot.option_universe:
        print("\nfirst option universe record:")
        print(snapshot.option_universe[0])

    if snapshot.news:
        print("\nfirst news record:")
        print(snapshot.news[0])
