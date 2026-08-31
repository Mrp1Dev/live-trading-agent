from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv

from alpaca.common.exceptions import APIError
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionLatestQuoteRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    OrderSide,
    OrderType,
    PositionIntent,
    QueryOrderStatus,
    TimeInForce,
)
from alpaca.trading.requests import (
    ClosePositionRequest,
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    ReplaceOrderRequest,
)

from alpaca_client.client import get_option_data_client, get_trading_client
from .errors import BrokerError, SafetyViolation
from .models import LiveOptionQuote

load_dotenv()


class AlpacaBroker:
    """Direct Alpaca API broker implementation for paper trading."""

    def __init__(
        self,
        *,
        trading_client: TradingClient | None = None,
        option_data_client: OptionHistoricalDataClient | None = None,
        paper: bool = True,
    ) -> None:
        paper_env = os.getenv("ALPACA_PAPER_TRADE", "true").strip().lower()
        if paper_env != "true" or not paper:
            raise SafetyViolation("Refusing to initialize broker: ALPACA_PAPER_TRADE must be true. Live trading is strictly forbidden.")

        self.trading_client = trading_client or get_trading_client(paper=True)
        self.option_data_client = option_data_client or get_option_data_client()

        # Deterministic paper safety guard: confirm client endpoint is paper.
        self._verify_paper_environment()

    def _verify_paper_environment(self) -> None:
        """Enforce paper trading endpoint safety check."""
        base_url = str(getattr(self.trading_client, "_base_url", "")).lower()
        is_paper = getattr(self.trading_client, "_paper", None)
        if is_paper is False or ("api.alpaca.markets" in base_url and "paper" not in base_url):
            raise SafetyViolation(
                f"Live API endpoint detected ({base_url}). Execution is restricted to the Alpaca paper-trading environment only."
            )

    def get_account(self) -> Any:
        """Fetch and return account details."""
        try:
            return self.trading_client.get_account()
        except APIError as exc:
            raise BrokerError(f"Alpaca API error fetching account: {exc}") from exc
        except Exception as exc:
            raise BrokerError(f"Failed to fetch Alpaca account: {exc}") from exc

    def get_positions(self) -> list[Any]:
        """Fetch all current open positions."""
        try:
            positions = self.trading_client.get_all_positions()
            return list(positions) if positions else []
        except APIError as exc:
            raise BrokerError(f"Alpaca API error fetching positions: {exc}") from exc
        except Exception as exc:
            raise BrokerError(f"Failed to fetch Alpaca positions: {exc}") from exc

    def get_open_orders(self) -> list[Any]:
        """Fetch all active open orders."""
        try:
            orders = self.trading_client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
            return list(orders) if orders else []
        except APIError as exc:
            raise BrokerError(f"Alpaca API error fetching open orders: {exc}") from exc
        except Exception as exc:
            raise BrokerError(f"Failed to fetch Alpaca open orders: {exc}") from exc

    def get_order_by_client_id(self, client_order_id: str) -> Any | None:
        """Fetch an order by its unique client_order_id, returning None if not found."""
        try:
            return self.trading_client.get_order_by_client_id(client_order_id)
        except APIError as exc:
            code = None
            try:
                code = exc.code
            except Exception:
                pass
            status_code = getattr(exc, "status_code", None)
            err_str = str(exc).lower()
            if status_code == 404 or code == 40410000 or "not found" in err_str or "404" in err_str:
                return None
            raise BrokerError(f"Alpaca API error fetching order by client_order_id '{client_order_id}': {exc}") from exc
        except Exception as exc:
            raise BrokerError(f"Failed to fetch order by client_order_id '{client_order_id}': {exc}") from exc

    def get_order_by_id(self, order_id: str) -> Any:
        """Fetch an order by its broker order ID."""
        try:
            return self.trading_client.get_order_by_id(order_id)
        except APIError as exc:
            raise BrokerError(f"Alpaca API error fetching order '{order_id}': {exc}") from exc
        except Exception as exc:
            raise BrokerError(f"Failed to fetch order '{order_id}': {exc}") from exc

    def get_option_contract(self, symbol: str) -> Any:
        """Fetch option contract details for symbol."""
        try:
            return self.trading_client.get_option_contract(symbol)
        except APIError as exc:
            raise BrokerError(f"Alpaca API error fetching contract '{symbol}': {exc}") from exc
        except Exception as exc:
            raise BrokerError(f"Failed to fetch option contract '{symbol}': {exc}") from exc

    def get_option_quote(self, symbol: str) -> LiveOptionQuote:
        """Fetch live bid/ask quote for option contract symbol."""
        try:
            req = OptionLatestQuoteRequest(symbol_or_symbols=symbol)
            quotes = self.option_data_client.get_option_latest_quote(req)
            raw_quote = quotes.get(symbol) if isinstance(quotes, dict) else quotes
            if not raw_quote:
                raise BrokerError(f"No live quote returned by Alpaca for {symbol}")

            bid = getattr(raw_quote, "bid_price", None)
            ask = getattr(raw_quote, "ask_price", None)
            ts = getattr(raw_quote, "timestamp", None)

            if bid is None and isinstance(raw_quote, dict):
                bid = raw_quote.get("bid_price", raw_quote.get("bid", raw_quote.get("b", 0.0)))
                ask = raw_quote.get("ask_price", raw_quote.get("ask", raw_quote.get("a", 0.0)))
                ts = raw_quote.get("timestamp") or raw_quote.get("t")

            return LiveOptionQuote(
                symbol=symbol,
                bid=float(bid or 0.0),
                ask=float(ask or 0.0),
                timestamp=ts if isinstance(ts, datetime) else None,
                source="alpaca-api",
            )
        except APIError as exc:
            raise BrokerError(f"Alpaca API error fetching quote for '{symbol}': {exc}") from exc
        except Exception as exc:
            if isinstance(exc, BrokerError):
                raise
            raise BrokerError(f"Failed to fetch quote for '{symbol}': {exc}") from exc

    def place_option_order(
        self,
        *,
        symbol: str,
        qty: int,
        side: str,
        position_intent: str,
        order_type: str,
        time_in_force: str,
        limit_price: float | None,
        client_order_id: str,
    ) -> Any:
        """Place an option order via the Alpaca Trading API."""
        self._verify_paper_environment()

        try:
            side_norm = side.strip().lower()
            side_enum = OrderSide.BUY if side_norm == "buy" else OrderSide.SELL

            tif_norm = time_in_force.strip().lower()
            tif_map = {
                "day": TimeInForce.DAY,
                "gtc": TimeInForce.GTC,
                "ioc": TimeInForce.IOC,
                "fok": TimeInForce.FOK,
                "opg": TimeInForce.OPG,
                "cls": TimeInForce.CLS,
            }
            tif_enum = tif_map.get(tif_norm, TimeInForce.DAY)

            intent_norm = position_intent.strip().lower()
            intent_map = {
                "buy_to_open": PositionIntent.BUY_TO_OPEN,
                "buy_to_close": PositionIntent.BUY_TO_CLOSE,
                "sell_to_open": PositionIntent.SELL_TO_OPEN,
                "sell_to_close": PositionIntent.SELL_TO_CLOSE,
            }
            intent_enum = intent_map.get(intent_norm, PositionIntent.BUY_TO_OPEN)

            order_type_norm = order_type.strip().lower()

            if order_type_norm == "limit":
                if limit_price is None or limit_price <= 0:
                    raise ValueError(f"Limit price must be positive for limit orders (got {limit_price})")
                order_req = LimitOrderRequest(
                    symbol=symbol,
                    qty=float(qty),
                    side=side_enum,
                    time_in_force=tif_enum,
                    position_intent=intent_enum,
                    limit_price=round(float(limit_price), 2),
                    client_order_id=client_order_id,
                )
            elif order_type_norm == "market":
                order_req = MarketOrderRequest(
                    symbol=symbol,
                    qty=float(qty),
                    side=side_enum,
                    time_in_force=tif_enum,
                    position_intent=intent_enum,
                    client_order_id=client_order_id,
                )
            else:
                raise ValueError(f"Unsupported option order type: {order_type}")

            return self.trading_client.submit_order(order_req)
        except APIError as exc:
            raise BrokerError(f"Alpaca API error placing order: {exc}") from exc
        except Exception as exc:
            if isinstance(exc, (BrokerError, SafetyViolation, ValueError)):
                raise
            raise BrokerError(f"Failed to submit option order: {exc}") from exc

    def cancel_order(self, order_id: str) -> Any:
        """Cancel an open order by ID."""
        try:
            return self.trading_client.cancel_order_by_id(order_id)
        except APIError as exc:
            raise BrokerError(f"Alpaca API error canceling order '{order_id}': {exc}") from exc
        except Exception as exc:
            raise BrokerError(f"Failed to cancel order '{order_id}': {exc}") from exc

    def replace_order(
        self,
        order_id: str,
        *,
        qty: int | None = None,
        limit_price: float | None = None,
    ) -> Any:
        """Replace an existing open order with updated quantity or limit price."""
        try:
            req = ReplaceOrderRequest(
                qty=qty,
                limit_price=round(float(limit_price), 2) if limit_price is not None else None,
            )
            return self.trading_client.replace_order_by_id(order_id, req)
        except APIError as exc:
            raise BrokerError(f"Alpaca API error replacing order '{order_id}': {exc}") from exc
        except Exception as exc:
            raise BrokerError(f"Failed to replace order '{order_id}': {exc}") from exc

    def close_position(self, symbol: str, qty: int | None = None) -> Any:
        """Close an open position."""
        try:
            req = ClosePositionRequest(qty=str(qty) if qty is not None else None)
            return self.trading_client.close_position(symbol, req)
        except APIError as exc:
            raise BrokerError(f"Alpaca API error closing position '{symbol}': {exc}") from exc
        except Exception as exc:
            raise BrokerError(f"Failed to close position '{symbol}': {exc}") from exc
