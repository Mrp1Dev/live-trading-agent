from __future__ import annotations

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest.data_provider import get_stock_bars
from backtest.option_candidates import generate_candidate_symbols, validate_historical_candidates

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")


def _historical_underlying_price(underlying: str, decision_date: datetime) -> float:
    start = decision_date - timedelta(days=2)
    end = decision_date + timedelta(days=1)
    df = get_stock_bars(underlying, start, end)
    if df.empty:
        raise RuntimeError(f"No historical stock bars available for {underlying} around {decision_date.isoformat()}")

    records = df.reset_index().to_dict(orient="records")
    normalized_decision = decision_date if decision_date.tzinfo is not None else decision_date.replace(tzinfo=timezone.utc)
    before = [
        row for row in records
        if row.get("timestamp") is not None and row["timestamp"] <= normalized_decision
    ]
    if not before:
        raise RuntimeError(f"No stock data at or before {decision_date.isoformat()} for {underlying}")

    last = max(before, key=lambda row: row["timestamp"])
    return float(last["close"])


if __name__ == "__main__":
    underlying = "AAPL"
    decision_date = datetime(2024, 6, 5, 12, 0, 0)
    underlying_price = _historical_underlying_price(underlying, decision_date)

    generated = generate_candidate_symbols(
        underlying=underlying,
        underlying_price=underlying_price,
        decision_date=decision_date,
        min_dte=7,
        max_dte=60,
        strike_range_pct=0.15,
        strike_step=None,
    )

    calls = sum(1 for symbol in generated if symbol[-9] == "C")
    puts = sum(1 for symbol in generated if symbol[-9] == "P")

    observed = validate_historical_candidates(generated, decision_date)
    liquid = [item for item in observed if item.volume is not None and item.trade_count is not None and item.volume >= 50 and item.trade_count >= 2]

    print(f"underlying price: {underlying_price}")
    print(f"generated calls: {calls}")
    print(f"generated puts: {puts}")
    print(f"total candidates: {len(generated)}")
    print(f"candidates with historical bars: {len(observed)}")
    print(f"candidates with historical trades: {sum(1 for item in observed if item.trade_count is not None and item.trade_count > 0)}")
    print(f"liquid candidates: {len(liquid)}")
    print("first 20 final candidates:")
    for item in observed[:20]:
        print(
            f"  {item.symbol} | expiration={item.expiration.isoformat()} | "
            f"strike={item.strike} | type={item.option_type} | dte={item.dte} | "
            f"price={item.historical_price} | volume={item.volume} | trade_count={item.trade_count} | "
            f"vwap={item.vwap} | ts={item.data_timestamp.isoformat()}"
        )

    normalized_decision = decision_date if decision_date.tzinfo is not None else decision_date.replace(tzinfo=timezone.utc)
    for item in observed:
        assert item.expiration > decision_date.date(), f"Expiration must be after decision date: {item.symbol}"
        item_ts = item.data_timestamp if item.data_timestamp.tzinfo is not None else item.data_timestamp.replace(tzinfo=timezone.utc)
        assert item_ts <= normalized_decision, f"Observation must be at or before decision date: {item.symbol}"

    print("validation passed")
