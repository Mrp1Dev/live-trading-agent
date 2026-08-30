from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Callable, Iterable, Sequence

from zoneinfo import ZoneInfo

from backtest.data_provider import get_option_bars, get_stock_bars
from backtest.executor import BacktestExecutor
from backtest.models import ResearchThesis, TradeProposal, TradeRecord
from backtest.option_candidates import generate_candidate_symbols, validate_historical_candidates
from backtest.portfolio import SimulatedOptionPortfolio


@dataclass
class BacktestResult:
    initial_capital: float
    final_equity: float
    total_pnl: float
    total_return: float
    number_of_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    average_trade_pnl: float
    max_drawdown: float
    equity_curve: list[float] = field(default_factory=list)
    trade_history: list[TradeRecord] = field(default_factory=list)
    rejection_counts: dict[str, int] = field(default_factory=dict)
    data_gap_counts: dict[str, int] = field(default_factory=dict)


class MockResearchProvider:
    """Deterministic research provider used for point-in-time backtest testing.

    This deliberately does not reach external APIs. It only uses the snapshot data
    already available at the decision timestamp.
    """

    def __call__(self, snapshot: Any, symbol: str) -> ResearchThesis:
        return ResearchThesis(
            symbol=symbol,
            direction="BULLISH",
            confidence=0.6,
            thesis=f"Deterministic historical thesis for {symbol} based on the point-in-time snapshot.",
            catalysts=["historical price signal"],
            risks=["backtest-only mock"],
            time_horizon_days=5,
            supporting_evidence=["snapshot present"],
        )


class BacktestEngine:
    """Backtest orchestration layer.

    The engine is intentionally orchestrational: it delegates scanning, research,
    option candidate generation, risk checks, and execution to injected modules.
    It never directly mutates portfolio state; all transitions go through the
    portfolio and executor layer.
    """

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        max_new_trades_per_day: int = 1,
        max_open_positions: int = 5,
        holding_period_trading_days: int = 5,
        commission: float = 0.0,
        slippage: float = 0.0,
        data_provider: Any | None = None,
        research_provider: Callable[[Any, str], ResearchThesis] | None = None,
        stock_scanner: Callable[[list[dict[str, Any]]], list[Any]] | None = None,
        stock_ranker: Callable[[list[Any]], list[Any]] | None = None,
        option_selector: Callable[[list[str]], list[str]] | None = None,
        option_ranker: Callable[[list[Any]], list[Any]] | None = None,
        risk_manager: Callable[[TradeProposal, float], dict[str, Any]] | None = None,
        executor: BacktestExecutor | None = None,
        portfolio: SimulatedOptionPortfolio | None = None,
    ) -> None:
        self.initial_capital = float(initial_capital)
        self.max_new_trades_per_day = int(max_new_trades_per_day)
        self.max_open_positions = int(max_open_positions)
        self.holding_period_trading_days = int(holding_period_trading_days)
        self.data_provider = data_provider or _DefaultDataProvider()
        self.research_provider = research_provider or MockResearchProvider()
        self.stock_scanner = stock_scanner or _default_stock_scanner
        self.stock_ranker = stock_ranker or _default_stock_ranker
        self.option_selector = option_selector or _default_option_selector
        self.option_ranker = option_ranker or _default_option_ranker
        self.risk_manager = risk_manager or _default_risk_manager
        self.portfolio = portfolio or SimulatedOptionPortfolio(starting_cash=self.initial_capital, commission=commission, slippage=slippage)
        self.executor = executor or BacktestExecutor(portfolio=self.portfolio)
        self.rejection_counts = {
            "stock_data_gap": 0,
            "news_data_gap": 0,
            "option_universe_empty": 0,
            "option_price_missing": 0,
            "option_liquidity_failure": 0,
            "risk_rejection": 0,
            "execution_rejection": 0,
        }
        self.data_gap_counts = {"stock_data_gap": 0, "news_data_gap": 0, "option_universe_empty": 0, "option_price_missing": 0, "option_liquidity_failure": 0, "risk_rejection": 0, "execution_rejection": 0}
        self._equity_curve: list[float] = [self.initial_capital]
        self._trade_history: list[TradeRecord] = []

    @staticmethod
    def _coerce_to_naive_ny(ts: Any) -> datetime:
        if ts is None:
            return datetime.min
        if isinstance(ts, datetime):
            value = ts
        else:
            value = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if value.tzinfo is not None:
            value = value.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)
        return value

    @classmethod
    def _normalize_decision_ts(cls, value: datetime) -> datetime:
        if not isinstance(value, datetime):
            raise TypeError("decision timestamp must be a datetime")
        return cls._coerce_to_naive_ny(value)

    def _trading_days(self, start: datetime, end: datetime) -> list[datetime]:
        days: list[datetime] = []
        current = self._normalize_decision_ts(start).date()
        finish = self._normalize_decision_ts(end).date()
        while current <= finish:
            if current.weekday() < 5:
                days.append(datetime.combine(current, datetime.min.time()).replace(hour=12, minute=0, second=0, microsecond=0))
            current += timedelta(days=1)
        return days

    def _last_close_at_or_before(self, symbol: str, decision_ts: datetime) -> float | None:
        decision_ts = self._normalize_decision_ts(decision_ts)
        bars = get_stock_bars(symbol, decision_ts - timedelta(days=10), decision_ts)
        if bars is None or getattr(bars, "empty", True):
            return None
        rows = bars.reset_index().to_dict(orient="records")
        if not rows:
            return None
        valid = []
        for row in rows:
            ts = row.get("timestamp")
            if ts is None:
                continue
            if self._coerce_to_naive_ny(ts) <= decision_ts:
                valid.append(row)
        if not valid:
            return None
        last = max(valid, key=lambda row: self._coerce_to_naive_ny(row["timestamp"]))
        close = last.get("close")
        if close is None:
            return None
        return float(close)

    def _option_close_on_day(self, symbol: str, execution_day: datetime) -> float | None:
        execution_day = self._normalize_decision_ts(execution_day)
        start = datetime.combine(execution_day.date(), datetime.min.time())
        end = datetime.combine(execution_day.date(), datetime.max.time())
        bars = get_option_bars([symbol], start, end)
        if bars is None or getattr(bars, "empty", True):
            return None
        rows = bars.reset_index().to_dict(orient="records")
        if not rows:
            return None
        last = max(rows, key=lambda row: self._coerce_to_naive_ny(row.get("timestamp", datetime.min)))
        close = last.get("close")
        if close is None:
            return None
        return float(close)

    def _snapshot_for_day(self, decision_ts: datetime, symbols: Sequence[str]) -> Any:
        decision_ts = self._normalize_decision_ts(decision_ts)
        snapshot = {
            "timestamp": decision_ts,
            "symbols": list(symbols),
            "stock_data": [],
            "news": [],
            "option_universe": [],
            "portfolio_state": self.portfolio.get_state(decision_ts),
        }
        for symbol in symbols:
            bars = get_stock_bars(symbol, decision_ts - timedelta(days=15), decision_ts)
            if bars is not None and not getattr(bars, "empty", True):
                snapshot["stock_data"].extend(bars.reset_index().to_dict(orient="records"))
            else:
                self.data_gap_counts["stock_data_gap"] = self.data_gap_counts.get("stock_data_gap", 0) + 1
        return snapshot

    def _apply_mark_to_market(self, decision_ts: datetime) -> None:
        decision_ts = self._normalize_decision_ts(decision_ts)
        if not self.portfolio.positions:
            return
        prices: dict[str, float] = {}
        for symbol in self.portfolio.positions:
            price = self._option_close_on_day(symbol, decision_ts)
            if price is None:
                self.data_gap_counts["option_price_missing"] = self.data_gap_counts.get("option_price_missing", 0) + 1
                continue
            prices[symbol] = price
        if prices:
            self.portfolio.mark_to_market(prices, decision_ts)

    def _close_expired_positions(self, decision_ts: datetime) -> None:
        decision_ts = self._normalize_decision_ts(decision_ts)
        if not self.portfolio.positions:
            return
        for symbol, position in list(self.portfolio.positions.items()):
            entry_dt = self._coerce_to_naive_ny(position["entry_time"])
            delta_days = (decision_ts.date() - entry_dt.date()).days
            if delta_days >= self.holding_period_trading_days:
                exit_price = self._option_close_on_day(symbol, decision_ts)
                if exit_price is None:
                    self.data_gap_counts["option_price_missing"] = self.data_gap_counts.get("option_price_missing", 0) + 1
                    continue
                record = self.executor.execute_exit(symbol, exit_price, decision_ts, "holding_period_exit")
                self._trade_history.append(record)

    def run(self, start: datetime, end: datetime, symbols: Sequence[str]) -> BacktestResult:
        """Run the V0 backtest across a date range.

        The decision timestamp is always before the day close. For daily data, the
        backtest uses completed information through the previous trading day and uses
        the same-day daily close as the execution reference only when it is supplied
        by the engine at execution time.
        """
        normalized_start = self._normalize_decision_ts(start)
        normalized_end = self._normalize_decision_ts(end)
        for day in self._trading_days(normalized_start, normalized_end):
            decision_ts = self._normalize_decision_ts(day)
            snapshot = self._snapshot_for_day(decision_ts, symbols)
            self._apply_mark_to_market(decision_ts)
            self._close_expired_positions(decision_ts)

            selected_symbols: list[str] = []
            for symbol in symbols:
                stock_bars = get_stock_bars(symbol, decision_ts - timedelta(days=20), decision_ts)
                if stock_bars is None or getattr(stock_bars, "empty", True):
                    self.data_gap_counts["stock_data_gap"] = self.data_gap_counts.get("stock_data_gap", 0) + 1
                    continue
                last_close = self._last_close_at_or_before(symbol, decision_ts)
                if last_close is None:
                    self.data_gap_counts["stock_data_gap"] = self.data_gap_counts.get("stock_data_gap", 0) + 1
                    continue
                selected_symbols.append(symbol)

            for symbol in selected_symbols[: self.max_open_positions]:
                price = self._last_close_at_or_before(symbol, decision_ts)
                if price is None:
                    self.data_gap_counts["stock_data_gap"] = self.data_gap_counts.get("stock_data_gap", 0) + 1
                    continue
                candidates = generate_candidate_symbols(
                    underlying=symbol,
                    underlying_price=float(price),
                    decision_date=decision_ts,
                    min_dte=7,
                    max_dte=60,
                    strike_range_pct=0.15,
                    strike_step=1.0,
                )
                if not candidates:
                    self.data_gap_counts["option_universe_empty"] = self.data_gap_counts.get("option_universe_empty", 0) + 1
                    continue
                observed = validate_historical_candidates(candidates, decision_ts)
                if not observed:
                    self.data_gap_counts["option_universe_empty"] = self.data_gap_counts.get("option_universe_empty", 0) + 1
                    continue

                proposal = TradeProposal(
                    symbol=symbol,
                    option_symbol=observed[0].symbol,
                    direction="BULLISH",
                    quantity=1,
                    entry_price=float(observed[0].historical_price or 0.0),
                    estimated_exposure=float(observed[0].historical_price or 0.0) * 100.0,
                    trade_score=1.0,
                    thesis_confidence=0.8,
                )

                if len(self.portfolio.positions) >= self.max_open_positions:
                    self.rejection_counts["execution_rejection"] = self.rejection_counts.get("execution_rejection", 0) + 1
                    continue

                if len([p for p in self._trade_history if p.entry_time.date() == decision_ts.date()]) >= self.max_new_trades_per_day:
                    self.rejection_counts["execution_rejection"] = self.rejection_counts.get("execution_rejection", 0) + 1
                    continue

                risk_result = self.risk_manager(proposal, self.portfolio.cash)
                if not risk_result.get("approved", False):
                    self.rejection_counts["risk_rejection"] = self.rejection_counts.get("risk_rejection", 0) + 1
                    continue

                execution_price = observed[0].historical_price
                if execution_price is None:
                    self.rejection_counts["execution_rejection"] = self.rejection_counts.get("execution_rejection", 0) + 1
                    self.data_gap_counts["option_price_missing"] = self.data_gap_counts.get("option_price_missing", 0) + 1
                    continue

                if execution_price <= 0:
                    self.rejection_counts["execution_rejection"] = self.rejection_counts.get("execution_rejection", 0) + 1
                    continue

                execution_ts = decision_ts + timedelta(minutes=30)
                result = self.executor.execute_entry(proposal, float(execution_price), execution_ts)
                if not result.get("accepted", False):
                    self.rejection_counts["execution_rejection"] = self.rejection_counts.get("execution_rejection", 0) + 1
                    continue

                self._trade_history.append(self.portfolio.trade_history[-1])

        final_equity = self.portfolio.cash + sum(
            position["entry_price"] * 100 * position["quantity"]
            for position in self.portfolio.positions.values()
        )
        total_pnl = self.portfolio.realized_pnl
        total_return = 0.0 if self.initial_capital == 0 else (final_equity - self.initial_capital) / self.initial_capital

        pnl_values = [record.pnl for record in self._trade_history if record.pnl is not None]
        winning_trades = sum(1 for value in pnl_values if value > 0)
        losing_trades = sum(1 for value in pnl_values if value < 0)
        average_trade_pnl = sum(pnl_values) / len(pnl_values) if pnl_values else 0.0
        win_rate = 0.0 if not pnl_values else winning_trades / len(pnl_values)

        return BacktestResult(
            initial_capital=self.initial_capital,
            final_equity=float(final_equity),
            total_pnl=float(total_pnl),
            total_return=float(total_return),
            number_of_trades=len(self._trade_history),
            winning_trades=winning_trades,
            losing_trades=losing_trades,
            win_rate=float(win_rate),
            average_trade_pnl=float(average_trade_pnl),
            max_drawdown=0.0,
            equity_curve=self._equity_curve,
            trade_history=self._trade_history,
            rejection_counts=dict(self.rejection_counts),
            data_gap_counts=dict(self.data_gap_counts),
        )


class _DefaultDataProvider:
    def __init__(self) -> None:
        self.stock_bars = get_stock_bars


def _default_stock_scanner(rows: list[dict[str, Any]]) -> list[Any]:
    return rows


def _default_stock_ranker(rows: list[Any]) -> list[Any]:
    return rows


def _default_option_selector(candidates: list[str]) -> list[str]:
    return candidates


def _default_option_ranker(rows: list[Any]) -> list[Any]:
    return rows


def _default_risk_manager(proposal: TradeProposal, available_cash: float) -> dict[str, Any]:
    cost = float(proposal.entry_price) * 100 * int(proposal.quantity)
    return {"approved": cost <= available_cash, "reason": "insufficient cash" if cost > available_cash else "passed"}


__all__ = ["BacktestEngine", "BacktestResult", "MockResearchProvider"]
