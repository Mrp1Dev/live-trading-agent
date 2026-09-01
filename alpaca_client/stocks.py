from datetime import datetime, timedelta
from typing import Dict, List, Optional, Union
import pandas as pd

from alpaca.data.enums import DataFeed
from alpaca.data.models import Bar, Snapshot
from alpaca.data.requests import (
    StockBarsRequest,
    StockLatestBarRequest,
    StockSnapshotRequest,
)
from alpaca.data.timeframe import TimeFrame

from .client import get_stock_data_client
from config import STOCK_SCANNER_TOP_N
from strategy.scanner import ScannedStock, print_scan_results, scan_stock_bars
from strategy.universe import BENCHMARK_SYMBOL, UNIVERSE


def get_stock_bars(
    symbols: Union[str, List[str]],
    start: Union[datetime, str],
    end: Optional[Union[datetime, str]] = None,
    timeframe: TimeFrame = TimeFrame.Day,
    feed: DataFeed = DataFeed.IEX,
    limit: Optional[int] = None,
) -> pd.DataFrame:
    """Fetch historical OHLCV bars for one or more symbols.

    Returns a pandas DataFrame. For multiple symbols, the DataFrame
    has a MultiIndex of (symbol, timestamp).
    """
    client = get_stock_data_client()
    symbol_list = [symbols] if isinstance(symbols, str) else list(symbols)

    request = StockBarsRequest(
        symbol_or_symbols=symbol_list,
        timeframe=timeframe,
        start=start,
        end=end,
        limit=limit,
        feed=feed,
    )

    bar_set = client.get_stock_bars(request)
    return bar_set.df


def get_historical_daily_bars(
    symbols: Union[str, List[str]],
    days: int = 90,
    feed: DataFeed = DataFeed.IEX,
) -> pd.DataFrame:
    """Fetch daily bars for the past N calendar days (with weekend/holiday buffer).

    Returns a MultiIndex DataFrame indexed by (symbol, timestamp).
    """
    calendar_buffer_days = max(int(days * 1.6), days + 20)
    start_time = datetime.now() - timedelta(days=calendar_buffer_days)

    return get_stock_bars(
        symbols=symbols,
        start=start_time,
        timeframe=TimeFrame.Day,
        feed=feed,
    )


def get_stock_snapshots(
    symbols: Union[str, List[str]],
    feed: DataFeed = DataFeed.IEX,
) -> Dict[str, Snapshot]:
    """Fetch latest snapshot (quotes, trades, daily bars) for symbols."""
    client = get_stock_data_client()
    symbol_list = [symbols] if isinstance(symbols, str) else list(symbols)

    request = StockSnapshotRequest(
        symbol_or_symbols=symbol_list,
        feed=feed,
    )

    return client.get_stock_snapshot(request)


def get_latest_underlying_prices(
    symbols: Union[str, List[str]],
    feed: DataFeed = DataFeed.IEX,
) -> Dict[str, float]:
    """Return the freshest usable underlying price for each symbol.

    Preference order:
        latest trade
        quote midpoint
        latest daily close

    Raises if no usable price exists.
    """
    metrics = get_latest_market_metrics(symbols=symbols, feed=feed)
    return {sym: m["price"] for sym, m in metrics.items()}


def get_latest_market_metrics(
    symbols: Union[str, List[str]],
    feed: DataFeed = DataFeed.IEX,
) -> Dict[str, Dict[str, float]]:
    """Return fresh price, today's open, and intraday return metrics for each symbol."""
    snapshots = get_stock_snapshots(
        symbols=symbols,
        feed=feed,
    )
    symbol_list = [symbols] if isinstance(symbols, str) else list(symbols)
    metrics: Dict[str, Dict[str, float]] = {}

    for symbol in symbol_list:
        snapshot = snapshots.get(symbol)
        if snapshot is None:
            continue

        price: Optional[float] = None
        if snapshot.latest_trade is not None:
            trade_price = float(snapshot.latest_trade.price or 0)
            if trade_price > 0:
                price = trade_price

        if price is None and snapshot.latest_quote is not None:
            bid = float(snapshot.latest_quote.bid_price or 0)
            ask = float(snapshot.latest_quote.ask_price or 0)
            if bid > 0 and ask > 0 and ask >= bid:
                price = (bid + ask) / 2.0

        if price is None and snapshot.daily_bar is not None:
            daily_close = float(snapshot.daily_bar.close or 0)
            if daily_close > 0:
                price = daily_close

        if price is None or price <= 0:
            continue

        daily_bar = getattr(snapshot, "daily_bar", None)
        today_open = float(getattr(daily_bar, "open", 0.0) or 0.0) if daily_bar else 0.0

        prev_bar = getattr(snapshot, "previous_daily_bar", None) or getattr(snapshot, "prev_daily_bar", None)
        prev_close = float(getattr(prev_bar, "close", 0.0) or 0.0) if prev_bar else 0.0

        intraday_return = (price - today_open) / today_open if today_open > 0 else 0.0
        change_pct = (price - prev_close) / prev_close if prev_close > 0 else intraday_return

        metrics[symbol] = {
            "price": price,
            "today_open": today_open,
            "prev_close": prev_close,
            "intraday_return": intraday_return,
            "change_pct": change_pct,
        }

    return metrics


def get_latest_stock_bars(
    symbols: Union[str, List[str]],
    feed: DataFeed = DataFeed.IEX,
) -> Dict[str, Bar]:
    """Fetch the latest bar for one or more symbols."""
    client = get_stock_data_client()
    symbol_list = [symbols] if isinstance(symbols, str) else list(symbols)

    request = StockLatestBarRequest(
        symbol_or_symbols=symbol_list,
        feed=feed,
    )

    return client.get_stock_latest_bar(request)


def scan_stocks(
    universe: Optional[List[str]] = None,
    top_n: int = STOCK_SCANNER_TOP_N,
    benchmark_symbol: str = BENCHMARK_SYMBOL,
) -> List[ScannedStock]:
    """Fetch historical daily bars from Alpaca and scan the universe for top candidates."""
    target_universe = list(universe) if universe else list(UNIVERSE)
    all_symbols = list(set(target_universe + [benchmark_symbol]))

    df = get_historical_daily_bars(symbols=all_symbols, days=90)
    return scan_stock_bars(
        bars_df=df,
        universe=target_universe,
        benchmark_symbol=benchmark_symbol,
        top_n=top_n,
    )


def print_top_scanned_stocks(
    universe: Optional[List[str]] = None,
    top_n: int = STOCK_SCANNER_TOP_N,
) -> List[ScannedStock]:
    """Scan the universe and print the formatted top picks table."""
    stocks = scan_stocks(universe=universe, top_n=top_n)
    print_scan_results(stocks, title=f"Top Stock Scanner Picks (Top {len(stocks)})")
    return stocks


if __name__ == "__main__":
    print_top_scanned_stocks(top_n=STOCK_SCANNER_TOP_N)
