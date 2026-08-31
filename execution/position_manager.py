"""Open-position management: mark, decide, close.

Exits run BEFORE entries in every cycle. They free risk budget, and a position
the exit engine wants gone should not be competing for capital with a new idea in
the same cycle.

Every broker call is individually wrapped. One symbol throwing must not prevent
the remaining positions from being closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from strategy import state as position_state
from strategy.exits import (
    ExitDecision,
    dte_from_occ_symbol,
    evaluate_exit,
    market_now,
    realisable_pnl_pct,
    sort_closes_immediate_first,
)
from .errors import BrokerError

EXIT_LIMIT_TOLERANCE = 0.02   # cross slightly THROUGH the bid: exits must fill


@dataclass
class ManagedPosition:
    option_symbol: str
    stock_symbol: str
    contracts: int
    entry_price: float
    bid: float
    ask: float
    pnl_pct: Optional[float]
    peak_pnl_pct: float
    dte: Optional[int]
    decision: ExitDecision


@dataclass
class PositionReport:
    positions: list[ManagedPosition]
    closed: list[tuple[str, str]]          # (symbol, reason)
    failed: list[tuple[str, str]]          # (symbol, error)
    quote_gaps: list[str]
    adopted: list[str]
    pruned: list[str]

    @property
    def open_count(self) -> int:
        return len(self.positions)

    @property
    def held_count(self) -> int:
        return sum(1 for p in self.positions if not p.decision.should_close)


def _is_option_position(position: Any) -> bool:
    """Alpaca's asset_class enum spelling varies across alpaca-py versions."""
    return "option" in str(getattr(position, "asset_class", "")).lower()


def _underlying_of(option_symbol: str) -> str:
    # OCC suffix is 6 (date) + 1 (type) + 8 (strike) = 15 characters.
    return option_symbol[:-15] if len(option_symbol) > 15 else option_symbol


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def manage_open_positions(
    broker: Any,
    *,
    now: Optional[datetime] = None,
    dry_run: bool = False,
    state_path: str | None = None,
    verbose: bool = True,
) -> PositionReport:
    """Mark every open option position, evaluate exits, and submit the closes."""

    reference = market_now(now)
    state = position_state.load_state(state_path)

    try:
        raw_positions = broker.get_positions()
    except BrokerError as exc:
        if verbose:
            print(f"Could not fetch positions: {exc}")
        return PositionReport([], [], [("<fetch>", str(exc))], [], [], [])

    option_positions = [p for p in raw_positions if _is_option_position(p)]
    open_symbols = [str(getattr(p, "symbol", "")) for p in option_positions]

    pruned = position_state.reconcile(state, open_symbols)

    managed: list[ManagedPosition] = []
    quote_gaps: list[str] = []
    adopted: list[str] = []

    for position in option_positions:
        symbol = str(getattr(position, "symbol", ""))
        if not symbol:
            continue

        contracts = abs(int(_safe_float(getattr(position, "qty", 0))))
        entry_price = _safe_float(getattr(position, "avg_entry_price", 0.0))
        broker_pnl_pct = _safe_float(getattr(position, "unrealized_plpc", 0.0))

        if symbol not in state:
            position_state.adopt_untracked(
                state,
                option_symbol=symbol,
                stock_symbol=_underlying_of(symbol),
                entry_price=entry_price,
                contracts=contracts,
                now=reference,
            )
            adopted.append(symbol)

        # A live BID is required: we sell into it, and the midpoint lies.
        bid = 0.0
        ask = 0.0
        try:
            quote = broker.get_option_quote(symbol)
            bid = _safe_float(getattr(quote, "bid", 0.0))
            ask = _safe_float(getattr(quote, "ask", 0.0))
        except BrokerError as exc:
            quote_gaps.append(symbol)
            if verbose:
                print(f"  {symbol}: no live quote ({exc}); falling back to broker mark")

        pnl_pct = realisable_pnl_pct(entry_price, bid, fallback_pnl_pct=broker_pnl_pct)

        peak = state[symbol].peak_pnl_pct
        if pnl_pct is not None:
            peak = position_state.update_peak(state, symbol, pnl_pct)

        dte = dte_from_occ_symbol(symbol, reference)

        decision = evaluate_exit(
            pnl_pct=pnl_pct if pnl_pct is not None else 0.0,
            peak_pnl_pct=peak,
            dte=dte if dte is not None else 99,
            opened_at=state[symbol].opened_at,
            now=reference,
        )

        managed.append(
            ManagedPosition(
                option_symbol=symbol,
                stock_symbol=state[symbol].stock_symbol or _underlying_of(symbol),
                contracts=contracts,
                entry_price=entry_price,
                bid=bid,
                ask=ask,
                pnl_pct=pnl_pct,
                peak_pnl_pct=peak,
                dte=dte,
                decision=decision,
            )
        )

    if verbose:
        _print_position_report(managed, reference)

    closes = sort_closes_immediate_first(
        [(p.option_symbol, p.decision) for p in managed if p.decision.should_close]
    )
    by_symbol = {p.option_symbol: p for p in managed}

    closed: list[tuple[str, str]] = []
    failed: list[tuple[str, str]] = []

    for symbol, decision in closes:
        position = by_symbol[symbol]

        if dry_run:
            closed.append((symbol, f"DRY_RUN {decision.reason}"))
            if verbose:
                print(f"  [dry-run] would close {symbol} x{position.contracts} - {decision.reason}")
            continue

        try:
            if position.bid > 0:
                # Marketable limit placed slightly through the bid. Exits are the
                # half of the round trip you cannot afford to miss.
                limit_price = max(0.01, round(position.bid * (1.0 - EXIT_LIMIT_TOLERANCE), 2))
                broker.place_option_order(
                    symbol=symbol,
                    qty=position.contracts,
                    side="sell",
                    position_intent="sell_to_close",
                    order_type="limit",
                    time_in_force="day",
                    limit_price=limit_price,
                    client_order_id=_exit_client_order_id(symbol, reference),
                )
            else:
                # No usable bid; fall back to the broker's own close endpoint
                # rather than guessing a price.
                broker.close_position(symbol, qty=position.contracts)

            position_state.forget(state, symbol)
            closed.append((symbol, decision.reason))
            if verbose:
                print(f"  CLOSING {symbol} x{position.contracts} - {decision.reason}: {decision.detail}")
        except (BrokerError, ValueError) as exc:
            failed.append((symbol, str(exc)))
            if verbose:
                print(f"  FAILED to close {symbol}: {exc}")

    position_state.save_state(state, state_path)

    return PositionReport(
        positions=managed,
        closed=closed,
        failed=failed,
        quote_gaps=quote_gaps,
        adopted=adopted,
        pruned=pruned,
    )


def entry_window_status(broker: Any, now: Optional[datetime] = None) -> tuple[bool, str]:
    """Whether it is safe to OPEN new risk right now.

    Spreads are widest in the first and last minutes of the session, which is
    exactly when a marketable limit costs the most. Exits are never blocked by
    this - only entries.

    Uses the broker clock so market holidays are handled by Alpaca rather than a
    hand-maintained calendar. Falls back to a plain time check if the clock call
    fails, so a transient API error cannot silently open the window.
    """
    from config import NO_TRADE_MINUTES_AFTER_OPEN, NO_TRADE_MINUTES_BEFORE_CLOSE

    reference = market_now(now)

    try:
        clock = broker.trading_client.get_clock()
    except Exception as exc:  # noqa: BLE001 - any clock failure is non-fatal
        if reference.weekday() >= 5:
            return False, f"weekend (clock unavailable: {exc})"
        minutes = reference.hour * 60 + reference.minute
        if not (570 + NO_TRADE_MINUTES_AFTER_OPEN <= minutes <= 960 - NO_TRADE_MINUTES_BEFORE_CLOSE):
            return False, f"outside the entry window (clock unavailable: {exc})"
        return True, "entry window open (assumed; broker clock unavailable)"

    if not getattr(clock, "is_open", False):
        return False, "market is closed"

    clock_now = getattr(clock, "timestamp", None) or reference
    next_close = getattr(clock, "next_close", None)

    if clock_now.tzinfo is None:
        clock_now = clock_now.replace(tzinfo=reference.tzinfo)

    minutes_of_day = market_now(clock_now).hour * 60 + market_now(clock_now).minute
    if minutes_of_day < 570 + NO_TRADE_MINUTES_AFTER_OPEN:
        return False, f"within {NO_TRADE_MINUTES_AFTER_OPEN} min of the open - spreads are widest"

    if next_close is not None:
        if next_close.tzinfo is None:
            next_close = next_close.replace(tzinfo=reference.tzinfo)
        minutes_left = (next_close - clock_now).total_seconds() / 60.0
        if minutes_left <= NO_TRADE_MINUTES_BEFORE_CLOSE:
            return False, f"within {NO_TRADE_MINUTES_BEFORE_CLOSE} min of the close"

    return True, "entry window open"


def verify_fills(
    broker: Any,
    report: Any,
    *,
    state_path: str | None = None,
    verbose: bool = True,
) -> list[tuple[str, str, int]]:
    """Re-check submitted entry orders and record only what actually filled.

    Without this the agent believes it holds positions it never got. A resting
    DAY limit that has not filled is stale information: it was priced against a
    quote we may no longer accept, so it is cancelled rather than left to fill
    later at a price the model never approved.

    Returns [(symbol, status, filled_qty)].
    """
    outcomes: list[tuple[str, str, int]] = []
    state = position_state.load_state(state_path)
    changed = False

    for result in getattr(report, "results", []):
        order_id = getattr(result, "order_id", None)
        symbol = getattr(getattr(result, "intent", None), "option_symbol", "")
        if not order_id or not symbol:
            continue

        try:
            order = broker.get_order_by_id(order_id)
        except BrokerError as exc:
            outcomes.append((symbol, f"UNKNOWN ({exc})", 0))
            continue

        status = str(getattr(order, "status", "")).lower()
        filled_qty = int(_safe_float(getattr(order, "filled_qty", 0)))
        fill_price = _safe_float(getattr(order, "filled_avg_price", 0.0))

        if filled_qty > 0:
            entry = state.get(symbol)
            if entry is not None:
                entry.contracts = filled_qty
                if fill_price > 0:
                    entry.entry_price = fill_price
                changed = True
            outcomes.append((symbol, "FILLED", filled_qty))
            if verbose:
                print(f"  {symbol}: filled {filled_qty} @ ${fill_price:.2f}")
            continue

        # Nothing filled. Drop the optimistic state entry and cancel the order.
        if symbol in state:
            position_state.forget(state, symbol)
            changed = True

        if "cancel" not in status and "expired" not in status and "reject" not in status:
            try:
                broker.cancel_order(order_id)
                outcomes.append((symbol, "UNFILLED_CANCELLED", 0))
                if verbose:
                    print(f"  {symbol}: no fill ({status}) - order cancelled, state cleared")
            except BrokerError as exc:
                outcomes.append((symbol, f"UNFILLED_CANCEL_FAILED ({exc})", 0))
                if verbose:
                    print(f"  {symbol}: no fill and cancel failed: {exc}")
        else:
            outcomes.append((symbol, status.upper(), 0))
            if verbose:
                print(f"  {symbol}: {status} - state cleared")

    if changed:
        position_state.save_state(state, state_path)

    return outcomes


def _exit_client_order_id(symbol: str, now: datetime) -> str:
    return f"exit-{symbol}-{now.strftime('%Y%m%dT%H%M%S')}"


def _print_position_report(positions: list[ManagedPosition], now: datetime) -> None:
    print()
    print("=" * 118)
    print(f" OPEN POSITIONS - {now.strftime('%Y-%m-%d %H:%M:%S %Z')}")
    print("=" * 118)

    if not positions:
        print(" No open option positions.")
        print("=" * 118)
        return

    print(
        f"{'Contract':<24}{'Qty':>5}{'Entry':>9}{'Bid':>9}{'P&L':>10}"
        f"{'Peak':>10}{'DTE':>5}  {'Action':<16}{'Why'}"
    )
    print("-" * 118)

    for p in positions:
        pnl_str = f"{p.pnl_pct:+.1%}" if p.pnl_pct is not None else "N/A"
        action = p.decision.reason if p.decision.should_close else "HOLD"
        print(
            f"{p.option_symbol:<24}{p.contracts:>5}{p.entry_price:>9.2f}{p.bid:>9.2f}"
            f"{pnl_str:>10}{p.peak_pnl_pct:>+10.1%}"
            f"{(p.dte if p.dte is not None else -1):>5}  {action:<16}{p.decision.detail}"
        )

    print("=" * 118)


__all__ = [
    "ManagedPosition",
    "PositionReport",
    "entry_window_status",
    "manage_open_positions",
    "verify_fills",
]
