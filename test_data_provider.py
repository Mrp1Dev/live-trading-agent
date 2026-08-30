from datetime import datetime

from backtest.data_provider import (
    get_stock_bars,
    get_stock_trades,
    get_stock_quotes,
    get_option_bars,
    get_option_trades,
)


START = datetime(2024, 6, 3)
END = datetime(2024, 6, 10)


print("\n========== STOCK BARS ==========")

bars = get_stock_bars(
    "AAPL",
    START,
    END,
)

print(bars.head())
print(bars.tail())


print("\n========== STOCK TRADES ==========")

trades = get_stock_trades(
    "AAPL",
    START,
    END,
)

print(trades.head())
print("\n========== STOCK QUOTES ==========")
quotes = get_stock_quotes(
    "AAPL",
    START,
    END,
)
print(quotes.head())
contracts = [
    "AAPL241220C00300000",
    "AAPL241220P00150000",
]
print("\n========== OPTION BARS ==========")
option_bars = get_option_bars(
    contracts,
    START,
    END,
)
print(option_bars.head())
print("\n========== OPTION TRADES ==========")
option_trades = get_option_trades(
    contracts,
    START,
    END,
)
print(option_trades.head())