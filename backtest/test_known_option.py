from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from alpaca.common.enums import Sort
from alpaca.data.requests import OptionBarsRequest, OptionTradesRequest
from alpaca.data.timeframe import TimeFrame

from alpaca_client.client import get_option_data_client

START = datetime(2024, 6, 3)
END = datetime(2024, 6, 10)

KNOWN_CONTRACTS = [
    "AAPL240620C00150000",
    "AAPL241220P00150000",
    "AAPL241220C00300000",
]

ADDITIONAL_CONTRACTS = [
    "AAPL240620C00160000",
    "AAPL240620P00150000",
    "MSFT240620C00250000",
    "MSFT240620P00250000",
]


def _fetch_contract_rows(symbol: str):
    client = get_option_data_client()

    bars_request = OptionBarsRequest(
        symbol_or_symbols=[symbol],
        timeframe=TimeFrame.Day,
        start=START,
        end=END,
        sort=Sort.ASC,
    )
    trades_request = OptionTradesRequest(
        symbol_or_symbols=[symbol],
        start=START,
        end=END,
        sort=Sort.ASC,
    )

    bars_df = client.get_option_bars(bars_request).df
    trades_df = client.get_option_trades(trades_request).df

    return bars_df, trades_df


def _print_contract_summary(symbol: str) -> None:
    bars_df, trades_df = _fetch_contract_rows(symbol)

    if bars_df.empty:
        bar_rows = 0
        total_volume = 0
        total_trade_count = 0
        first_timestamp = "-"
        last_timestamp = "-"
        first_close = "-"
        last_close = "-"
    else:
        bars = bars_df.reset_index()
        bar_rows = len(bars)
        total_volume = int(bars["volume"].sum())
        total_trade_count = int(bars["trade_count"].sum())
        first_timestamp = str(bars["timestamp"].min())
        last_timestamp = str(bars["timestamp"].max())
        first_close = float(bars["close"].iloc[0])
        last_close = float(bars["close"].iloc[-1])

    if trades_df.empty:
        trade_rows = 0
    else:
        trade_rows = len(trades_df)

    print(f"contract: {symbol}")
    print(f"historical bar rows: {bar_rows}")
    print(f"historical trade rows: {trade_rows}")
    print(f"first timestamp: {first_timestamp}")
    print(f"last timestamp: {last_timestamp}")
    print(f"first close: {first_close}")
    print(f"last close: {last_close}")
    print(f"total volume: {total_volume}")
    print(f"total trade_count: {total_trade_count}")
    print()


if __name__ == "__main__":
    print("Testing direct historical option-data access for known OCC contracts")
    print(f"Date window: {START.isoformat()} -> {END.isoformat()}")
    print("=" * 80)

    for contract in KNOWN_CONTRACTS:
        try:
            _print_contract_summary(contract)
        except Exception as exc:  # pragma: no cover - diagnostic script only
            print(f"contract: {contract}")
            print(f"historical bar rows: 0")
            print(f"historical trade rows: 0")
            print(f"first timestamp: ERROR")
            print(f"last timestamp: ERROR")
            print(f"first close: ERROR")
            print(f"last close: ERROR")
            print(f"total volume: 0")
            print(f"total trade_count: 0")
            print(f"error: {type(exc).__name__}: {exc}")
            print()

    print("Additional known contracts from the existing codebase")
    print("-" * 80)
    for contract in ADDITIONAL_CONTRACTS:
        try:
            _print_contract_summary(contract)
        except Exception as exc:  # pragma: no cover - diagnostic script only
            print(f"contract: {contract}")
            print(f"historical bar rows: 0")
            print(f"historical trade rows: 0")
            print(f"first timestamp: ERROR")
            print(f"last timestamp: ERROR")
            print(f"first close: ERROR")
            print(f"last close: ERROR")
            print(f"total volume: 0")
            print(f"total trade_count: 0")
            print(f"error: {type(exc).__name__}: {exc}")
            print()
