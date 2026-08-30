from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from backtest.data_provider import get_option_bars, get_option_trades, get_stock_bars, get_stock_quotes, get_stock_trades
from backtest.models import BacktestSnapshot, PortfolioState
from backtest.option_universe import get_historical_option_universe

_HISTORICAL_NEWS_DATA: list[dict[str, Any]] = []


def set_historical_news_data(news_items: list[dict[str, Any]]) -> None:
    """Inject local historical news records for point-in-time snapshot assembly."""
    global _HISTORICAL_NEWS_DATA
    _HISTORICAL_NEWS_DATA = list(news_items)


def _normalize_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if hasattr(value, "to_pydatetime"):
        return value.to_pydatetime()
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    raise TypeError(f"Unsupported timestamp value: {value!r}")


def _completed_period_bounds(timestamp: datetime) -> tuple[datetime, datetime]:
    """Use only completed periods before the decision timestamp.

    For daily bars/trades, the decision for day D cannot consume the same day's
    close unless the strategy explicitly operates after the close. This helper is
    intentionally conservative: it excludes same-day bar data and keeps only
    previously completed periods.
    """
    decision_day = timestamp.date()
    previous_day = decision_day - timedelta(days=1)
    start = datetime.combine(previous_day, datetime.min.time())
    end = datetime.combine(decision_day, datetime.min.time()) - timedelta(microseconds=1)
    return start, end


def _coerce_to_record_list(frame_or_records: Any) -> list[dict[str, Any]]:
    if frame_or_records is None:
        return []

    if hasattr(frame_or_records, "to_dict"):
        try:
            return frame_or_records.to_dict("records")
        except Exception:
            pass

    if isinstance(frame_or_records, list):
        return [record for record in frame_or_records if isinstance(record, dict)]

    if isinstance(frame_or_records, dict):
        return [frame_or_records]

    return []


def _extract_timestamp(obj: dict[str, Any]) -> datetime | None:
    for key in ("timestamp", "t", "time", "created_at", "published_at"):
        if key in obj and obj[key] is not None:
            return _normalize_timestamp(obj[key])
    return None


def _assert_no_future_timestamps(rows: list[dict[str, Any]], decision_time: datetime, label: str) -> None:
    future = []
    for row in rows:
        ts = _extract_timestamp(row)
        if ts is not None and ts > decision_time:
            future.append(ts)
    if future:
        raise ValueError(f"{label} contains timestamps after the decision time: {future[:5]}")


def assert_no_future_stock_data(stock_data: list[dict[str, Any]] | Any, decision_time: datetime) -> None:
    rows = _coerce_to_record_list(stock_data)
    _assert_no_future_timestamps(rows, decision_time, "stock data")


def assert_no_future_option_data(option_data: list[dict[str, Any]] | Any, decision_time: datetime) -> None:
    rows = _coerce_to_record_list(option_data)
    _assert_no_future_timestamps(rows, decision_time, "option data")


def assert_no_future_news(news_data: list[dict[str, Any]] | Any, decision_time: datetime) -> None:
    rows = _coerce_to_record_list(news_data)
    _assert_no_future_timestamps(rows, decision_time, "news data")


def get_point_in_time_stock_data(timestamp: datetime, symbols: list[str]) -> list[dict[str, Any]]:
    """Assemble historical stock bars/trades/quotes available at or before timestamp."""
    if not symbols:
        return []

    start, end = _completed_period_bounds(timestamp)
    records: list[dict[str, Any]] = []

    for symbol in symbols:
        bar_df = get_stock_bars(symbol, start, end)
        trade_df = get_stock_trades(symbol, start, end)
        quote_df = get_stock_quotes(symbol, start, end)

        for frame in (bar_df, trade_df, quote_df):
            records.extend(_coerce_to_record_list(frame))

    assert_no_future_stock_data(records, timestamp)
    return records


def get_point_in_time_options(timestamp: datetime, symbols: list[str]) -> dict[str, Any]:
    """Assemble historical option universe and market-data evidence available at or before timestamp."""
    if not symbols:
        return {"universe": [], "bars": [], "trades": []}

    universe_by_symbol: dict[str, list[Any]] = {}
    bars_rows: list[dict[str, Any]] = []
    trades_rows: list[dict[str, Any]] = []

    start, end = _completed_period_bounds(timestamp)

    for symbol in symbols:
        universe = get_historical_option_universe(symbol, timestamp)
        universe_by_symbol[symbol] = universe

        if not universe:
            continue

        option_symbols = [contract.symbol for contract in universe]
        if option_symbols:
            bars_df = get_option_bars(option_symbols, start, end)
            trades_df = get_option_trades(option_symbols, start, end)
            bars_rows.extend(_coerce_to_record_list(bars_df))
            trades_rows.extend(_coerce_to_record_list(trades_df))

    assert_no_future_option_data(bars_rows + trades_rows, timestamp)
    return {
        "universe": [contract for contracts in universe_by_symbol.values() for contract in contracts],
        "bars": bars_rows,
        "trades": trades_rows,
    }


def get_point_in_time_news(
    timestamp: datetime,
    symbols: list[str],
    historical_news: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Return historical news items that were already published at or before timestamp.

    This deliberately does NOT call the live news API. The caller may inject a local
    historical dataset via the `historical_news` argument or through the module-level
    loader used by the backtest pipeline.
    """
    dataset = list(historical_news) if historical_news is not None else list(_HISTORICAL_NEWS_DATA)
    rows: list[dict[str, Any]] = []

    for item in dataset:
        if not isinstance(item, dict):
            continue
        article_symbols = item.get("symbols", [])
        item_time = _extract_timestamp(item)
        if item_time is None:
            continue
        if item_time > timestamp:
            continue
        if not symbols:
            rows.append(item)
            continue
        if not article_symbols:
            continue
        if any(symbol in symbols for symbol in article_symbols):
            rows.append(item)

    assert_no_future_news(rows, timestamp)
    return rows


def get_backtest_snapshot(
    timestamp: datetime,
    symbols: list[str],
    portfolio_state: PortfolioState | dict[str, Any],
) -> BacktestSnapshot:
    """Create a strict point-in-time snapshot containing only data available before the decision timestamp."""
    if not isinstance(timestamp, datetime):
        raise TypeError("timestamp must be a datetime")

    normalized_symbols = [str(symbol).upper() for symbol in symbols]
    if not normalized_symbols:
        raise ValueError("symbols must not be empty")

    stock_data = get_point_in_time_stock_data(timestamp, normalized_symbols)
    option_snapshot = get_point_in_time_options(timestamp, normalized_symbols)
    news = get_point_in_time_news(timestamp, normalized_symbols)

    if isinstance(portfolio_state, dict):
        portfolio = PortfolioState.model_validate(portfolio_state)
    else:
        portfolio = portfolio_state

    snapshot = BacktestSnapshot(
        timestamp=timestamp,
        stock_data=stock_data,
        news=news,
        option_universe=option_snapshot["universe"],
        portfolio_state=portfolio,
    )

    assert_no_future_stock_data(snapshot.stock_data, snapshot.timestamp)
    assert_no_future_option_data(option_snapshot["bars"] + option_snapshot["trades"], snapshot.timestamp)
    assert_no_future_news(snapshot.news, snapshot.timestamp)
    return snapshot


__all__ = [
    "assert_no_future_news",
    "assert_no_future_option_data",
    "assert_no_future_stock_data",
    "get_backtest_snapshot",
    "get_point_in_time_news",
    "get_point_in_time_options",
    "get_point_in_time_stock_data",
    "set_historical_news_data",
]
