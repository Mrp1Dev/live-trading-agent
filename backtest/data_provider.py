from datetime import datetime

from alpaca_client.client import (
    get_stock_data_client,
    get_option_data_client,
)

from alpaca.data.requests import (
    StockBarsRequest,
    StockTradesRequest,
    StockQuotesRequest,
    OptionBarsRequest,
    OptionTradesRequest,
)

from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed
from alpaca.common.enums import Sort


def get_stock_bars(
    symbol: str,
    start: datetime,
    end: datetime,
):
    client = get_stock_data_client()

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        feed=DataFeed.IEX,
        sort=Sort.ASC,
    )

    return client.get_stock_bars(request).df


def get_stock_trades(
    symbol: str,
    start: datetime,
    end: datetime,
):
    client = get_stock_data_client()

    request = StockTradesRequest(
        symbol_or_symbols=symbol,
        start=start,
        end=end,
        feed=DataFeed.IEX,
        sort=Sort.ASC,
    )

    return client.get_stock_trades(request).df


def get_stock_quotes(
    symbol: str,
    start: datetime,
    end: datetime,
):
    client = get_stock_data_client()

    request = StockQuotesRequest(
        symbol_or_symbols=symbol,
        start=start,
        end=end,
        feed=DataFeed.IEX,
        sort=Sort.ASC,
    )

    return client.get_stock_quotes(request).df


def get_option_bars(
    symbols,
    start: datetime,
    end: datetime,
):
    client = get_option_data_client()

    request = OptionBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        sort=Sort.ASC,
    )

    return client.get_option_bars(request).df


def get_option_trades(
    symbols,
    start: datetime,
    end: datetime,
):
    client = get_option_data_client()

    request = OptionTradesRequest(
        symbol_or_symbols=symbols,
        start=start,
        end=end,
        sort=Sort.ASC,
    )

    return client.get_option_trades(request).df