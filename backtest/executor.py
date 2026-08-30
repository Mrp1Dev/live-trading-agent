from __future__ import annotations

from datetime import datetime

from backtest.models import TradeProposal, TradeRecord
from backtest.portfolio import SimulatedOptionPortfolio


class BacktestExecutor:
    """Pure simulated execution layer for the backtest engine.

    The backtest engine is responsible for passing in prices and timestamps.
    This executor never fetches market data, never calls Alpaca, and never
    submits real orders. It only validates a trade proposal and forwards the
    execution into the simulated portfolio.
    """

    def __init__(
        self,
        starting_cash: float = 100_000.0,
        portfolio: SimulatedOptionPortfolio | None = None,
        **portfolio_kwargs,
    ) -> None:
        self.portfolio = portfolio or SimulatedOptionPortfolio(starting_cash=starting_cash, **portfolio_kwargs)

    def execute_entry(
        self,
        proposal: TradeProposal,
        price: float,
        timestamp: datetime,
    ) -> dict[str, object]:
        """Validate an execution request and send it to the simulated portfolio.

        The price must be supplied by the backtest engine. The executor does not
        fetch prices; it only validates and records the trade into the simulated
        portfolio state.
        """
        if price <= 0:
            return {
                "accepted": False,
                "reason": "invalid price",
                "option_symbol": proposal.option_symbol,
                "requested_price": float(price),
                "timestamp": timestamp,
            }

        if proposal.quantity <= 0:
            return {
                "accepted": False,
                "reason": "invalid quantity",
                "option_symbol": proposal.option_symbol,
                "requested_quantity": int(proposal.quantity),
                "timestamp": timestamp,
            }

        payload = proposal.model_dump()
        payload["entry_price"] = float(price)
        payload["estimated_exposure"] = float(price) * 100 * int(proposal.quantity)
        trade = TradeProposal(**payload)

        result = self.portfolio.open_position(trade, timestamp)
        return {
            "accepted": bool(result.get("accepted", False)),
            "option_symbol": trade.option_symbol,
            "quantity": int(trade.quantity),
            "execution_price": float(price),
            "timestamp": timestamp,
            "cash_after": float(self.portfolio.cash),
            "result": result,
            "reason": result.get("reason") if not result.get("accepted", False) else None,
        }

    def execute_exit(
        self,
        option_symbol: str,
        price: float,
        timestamp: datetime,
        reason: str,
    ) -> TradeRecord:
        """Close an open simulated position and return the resulting TradeRecord."""
        if price <= 0:
            raise ValueError("exit price must be > 0")

        return self.portfolio.close_position(option_symbol, float(price), timestamp, reason)


__all__ = ["BacktestExecutor"]
