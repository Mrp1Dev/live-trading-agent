import os

from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.data.historical import NewsClient, StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient

load_dotenv()


def _get_credentials() -> tuple[str, str]:
    """Retrieve API key and secret from environment variables.
    
    Supports both ALPACA_API_KEY / ALPACA_SECRET_KEY and
    standard APCA_API_KEY_ID / APCA_API_SECRET_KEY.
    """
    api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")

    if not api_key or not secret_key:
        raise RuntimeError(
            "Missing Alpaca API credentials. Please set ALPACA_API_KEY and ALPACA_SECRET_KEY (or APCA_API_KEY_ID and APCA_API_SECRET_KEY) in .env"
        )
    return api_key, secret_key


def get_trading_client(paper: bool = True) -> TradingClient:
    """Return a TradingClient instance for the Trading API.
    
    Defaults to paper=True for development safety.
    """
    api_key, secret_key = _get_credentials()
    return TradingClient(
        api_key=api_key,
        secret_key=secret_key,
        paper=paper,
    )


def get_stock_data_client() -> StockHistoricalDataClient:
    """Return a StockHistoricalDataClient for historical equity data."""
    api_key, secret_key = _get_credentials()
    return StockHistoricalDataClient(
        api_key=api_key,
        secret_key=secret_key,
    )


def get_option_data_client() -> OptionHistoricalDataClient:
    """Return an OptionHistoricalDataClient for historical options data."""
    api_key, secret_key = _get_credentials()
    return OptionHistoricalDataClient(
        api_key=api_key,
        secret_key=secret_key,
    )
def get_news_client() -> NewsClient:
    """Return a NewsClient for accessing news data."""
    api_key, secret_key = _get_credentials()
    return NewsClient(
        api_key=api_key,
        secret_key=secret_key
    )