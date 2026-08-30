from __future__ import annotations

from datetime import datetime
from typing import Any

from strategy.option_selector import parse_occ_symbol
from backtest.models import PortfolioState, TradeProposal, TradeRecord


class SimulatedOptionPortfolio:
    """Pure simulated option portfolio used by the backtest engine.

    This portfolio never reaches out to Alpaca or any other live market data source.
    All prices and mark-to-market values are supplied by the backtest engine.
    """

    def __init__(
        self,
        starting_cash: float = 100_000.0,
        commission: float = 0.0,
        slippage: float = 0.0,
        commission_per_contract: float = 0.0,
        slippage_pct: float = 0.0,
        option_contract_multiplier: int = 100,
    ) -> None:
        self.starting_cash = float(starting_cash)
        self.cash = float(starting_cash)
        self.commission = float(commission)
        self.slippage = float(slippage)
        self.commission_per_contract = float(commission_per_contract)
        self.slippage_pct = float(slippage_pct)
        self.option_contract_multiplier = int(option_contract_multiplier)

        self.positions: dict[str, dict[str, Any]] = {}
        self.realized_pnl = 0.0
        self.trade_history: list[TradeRecord] = []
        self.portfolio_history: list[PortfolioState] = []

    def _transaction_costs(self, price: float, quantity: int) -> float:
        gross_notional = price * self.option_contract_multiplier * quantity
        commission_cost = self.commission + (self.commission_per_contract * quantity)
        slippage_cost = gross_notional * self.slippage_pct
        return commission_cost + slippage_cost

    def _option_type_from_symbol(self, option_symbol: str) -> str:
        try:
            _, option_type, _ = parse_occ_symbol(option_symbol)
            return option_type.upper()
        except ValueError as exc:
            raise ValueError(f"Unable to parse option symbol for expiration validation: {option_symbol}") from exc

    def _expiration_from_symbol(self, option_symbol: str):
        try:
            expiration_date, _, _ = parse_occ_symbol(option_symbol)
            return expiration_date
        except ValueError as exc:
            raise ValueError(f"Unable to parse option expiration: {option_symbol}") from exc

    def _position_to_record(self, position: dict[str, Any]) -> TradeRecord:
        return TradeRecord(
            entry_time=position["entry_time"],
            exit_time=position.get("exit_time"),
            underlying=position["underlying_symbol"],
            option_symbol=position["option_symbol"],
            option_type=position["option_type"],
            quantity=position["quantity"],
            entry_price=position["entry_price"],
            exit_price=position.get("exit_price"),
            pnl=position.get("pnl"),
            pnl_pct=position.get("pnl_pct"),
            exit_reason=position.get("exit_reason"),
            trade_score=position.get("trade_score"),
        )

    def open_position(self, trade: TradeProposal, timestamp: datetime) -> dict[str, Any]:
        """Open a simulated long-option position.

        Returns a result dict describing whether the order was accepted.
        """
        if trade.quantity <= 0:
            return {"accepted": False, "reason": "quantity must be > 0"}
        if trade.entry_price <= 0:
            return {"accepted": False, "reason": "entry_price must be > 0"}
        if trade.option_symbol in self.positions:
            return {"accepted": False, "reason": f"position already open for {trade.option_symbol}"}

        option_type = self._option_type_from_symbol(trade.option_symbol)
        if option_type not in {"CALL", "PUT"}:
            return {"accepted": False, "reason": f"unsupported option type for {trade.option_symbol}"}

        entry_cost = trade.entry_price * self.option_contract_multiplier * trade.quantity
        total_cost = entry_cost + self._transaction_costs(trade.entry_price, trade.quantity)

        if total_cost > self.cash:
            return {
                "accepted": False,
                "reason": "insufficient cash",
                "required": total_cost,
                "available": self.cash,
            }

        expiration_date = self._expiration_from_symbol(trade.option_symbol)
        if expiration_date < timestamp.date():
            return {"accepted": False, "reason": "position expiration is already passed before entry"}

        self.cash -= total_cost
        position = {
            "option_symbol": trade.option_symbol,
            "underlying_symbol": trade.symbol,
            "option_type": option_type,
            "quantity": int(trade.quantity),
            "entry_price": float(trade.entry_price),
            "entry_time": timestamp,
            "expiration_date": expiration_date,
            "cost_basis": total_cost,
            "trade_score": float(trade.trade_score),
        }
        self.positions[trade.option_symbol] = position

        record = self._position_to_record(position)
        self.trade_history.append(record)

        return {
            "accepted": True,
            "option_symbol": trade.option_symbol,
            "cost": total_cost,
            "cash_after": self.cash,
            "position": position,
        }

    def close_position(
        self,
        option_symbol: str,
        exit_price: float,
        timestamp: datetime,
        reason: str,
    ) -> TradeRecord:
        """Close a long option position and return a TradeRecord."""
        if option_symbol not in self.positions:
            raise KeyError(f"No open position for {option_symbol}")
        if exit_price <= 0:
            raise ValueError("exit_price must be > 0")

        position = self.positions[option_symbol]
        expiration_date = position["expiration_date"]
        if timestamp.date() > expiration_date:
            raise ValueError(
                f"Position {option_symbol} expired on {expiration_date.isoformat()} before close; backtest does not auto-exercise options."
            )

        quantity = int(position["quantity"])
        entry_price = float(position["entry_price"])
        proceeds = exit_price * self.option_contract_multiplier * quantity
        pnl = (exit_price - entry_price) * self.option_contract_multiplier * quantity
        pnl_pct = 0.0 if entry_price == 0 else (pnl / (entry_price * self.option_contract_multiplier * quantity))
        exit_cost = self._transaction_costs(exit_price, quantity)
        net_proceeds = proceeds - exit_cost
        self.cash += net_proceeds

        self.realized_pnl += pnl

        trade_record = TradeRecord(
            entry_time=position["entry_time"],
            exit_time=timestamp,
            underlying=position["underlying_symbol"],
            option_symbol=option_symbol,
            option_type=position["option_type"],
            quantity=quantity,
            entry_price=entry_price,
            exit_price=float(exit_price),
            pnl=float(pnl),
            pnl_pct=float(pnl_pct),
            exit_reason=reason,
            trade_score=position.get("trade_score"),
        )

        self.trade_history.append(trade_record)
        del self.positions[option_symbol]
        return trade_record

    def mark_to_market(
        self,
        prices: dict[str, float],
        timestamp: datetime,
    ) -> dict[str, Any]:
        """Mark open positions to market using supplied backtest prices.

        The portfolio never fetches prices itself; the engine must pass them in.
        """
        if not isinstance(prices, dict):
            raise TypeError("prices must be a dict keyed by option_symbol")

        market_values: list[float] = []
        for option_symbol, position in list(self.positions.items()):
            if timestamp.date() > position["expiration_date"]:
                raise ValueError(
                    f"Position {option_symbol} reached expiration on {position['expiration_date']} and was not closed; backtest does not auto-exercise."
                )
            if option_symbol not in prices:
                raise KeyError(f"No price supplied for open option {option_symbol}")

            current_price = float(prices[option_symbol])
            if current_price <= 0:
                raise ValueError(f"Price for {option_symbol} must be > 0")

            market_value = current_price * self.option_contract_multiplier * position["quantity"]
            market_values.append(market_value)

        total_market_value = sum(market_values)
        equity = self.cash + total_market_value
        unrealized_pnl = sum(
            (
                float(prices.get(position["option_symbol"], 0.0)) * self.option_contract_multiplier * position["quantity"]
                - float(position["cost_basis"])
            )
            for position in self.positions.values()
        )

        state = PortfolioState(
            timestamp=timestamp,
            cash=self.cash,
            equity=equity,
            positions=[self._position_to_record(position) for position in self.positions.values()],
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized_pnl,
        )
        self.portfolio_history.append(state)

        return {
            "timestamp": timestamp,
            "cash": self.cash,
            "equity": equity,
            "market_value": total_market_value,
            "unrealized_pnl": unrealized_pnl,
            "state": state,
        }

    def get_state(self, timestamp: datetime) -> PortfolioState:
        """Return a snapshot of the portfolio state at a supplied timestamp."""
        equity = self.cash + sum(
            float(position["entry_price"]) * self.option_contract_multiplier * int(position["quantity"])
            for position in self.positions.values()
        )
        # The backtest engine is responsible for mark-to-market; this state reflects current
        # open positions at the supplied timestamp using the most recent known prices from the engine.
        state = PortfolioState(
            timestamp=timestamp,
            cash=self.cash,
            equity=equity,
            positions=[self._position_to_record(position) for position in self.positions.values()],
            realized_pnl=self.realized_pnl,
            unrealized_pnl=0.0,
        )
        return state


__all__ = ["SimulatedOptionPortfolio"]
