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
from backtest.option_universe import (
    _decision_window,
    _extract_symbol_set,
    _fetch_metadata_candidates,
    get_historical_option_universe,
)


def print_universe_summary(label: str, underlying: str, decision_date: datetime) -> None:
    metadata_candidates = _fetch_metadata_candidates(underlying, decision_date, (-30, 120))
    bars_symbols = set()
    trades_symbols = set()

    if metadata_candidates:
        start_dt, end_dt = _decision_window(decision_date)
        client = get_option_data_client()

        bars_request = OptionBarsRequest(
            symbol_or_symbols=[candidate["symbol"] for candidate in metadata_candidates],
            start=start_dt,
            end=end_dt,
            timeframe=TimeFrame.Day,
            sort=Sort.ASC,
        )
        trades_request = OptionTradesRequest(
            symbol_or_symbols=[candidate["symbol"] for candidate in metadata_candidates],
            start=start_dt,
            end=end_dt,
            sort=Sort.ASC,
        )

        bars_df = client.get_option_bars(bars_request).df
        trades_df = client.get_option_trades(trades_request).df
        bars_symbols = _extract_symbol_set(bars_df)
        trades_symbols = _extract_symbol_set(trades_df)

    universe = get_historical_option_universe(underlying, decision_date)
    print(f"\n=== {label} ===")
    print(f"total metadata candidates: {len(metadata_candidates)}")
    print(f"contracts with historical bars: {len(bars_symbols)}")
    print(f"contracts with historical trades: {len(trades_symbols)}")
    print(f"final historical universe: {len(universe)}")
    if universe:
        print("sample historical universe:")
        for contract in universe[:10]:
            print(f"  - {contract.symbol} | {contract.expiration_date} | {contract.option_type} | {contract.strike_price}")


if __name__ == "__main__":
    decision_date = datetime(2024, 6, 5, 12, 0, 0)
    print_universe_summary("AAPL historical universe on 2024-06-05", "AAPL", decision_date)
