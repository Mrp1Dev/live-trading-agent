"""Exit decision rules.

Pure functions: plain numbers in, a decision out. No API calls, no clients, no
hidden state. That makes every "why did it close?" answer auditable and lets the
whole rule set be tested without a broker.

Rule ORDER is load-bearing. The first rule that fires is the reported reason, so
they run most-urgent first:

    1. flatten window   competition valuation approaching        [immediate]
    2. expiry           DTE <= MIN_EXIT_DTE                      [immediate]
    3. stop loss        pnl <= STOP_LOSS_PCT                     [immediate]
    4. trailing stop    faded from peak                          [normal]
    5. take profit      pnl >= TAKE_PROFIT_PCT                   [normal]
    6. time stop        held >= MAX_HOLD_DAYS                    [normal]

Trailing is checked BEFORE the fixed target on purpose: a position that ran to
+150% and fell back to +70% should report as a faded winner, not a target hit.
Those say very different things about whether the strategy is working.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional

from config import (
    FLATTEN_AFTER_HOUR_ET,
    FLATTEN_AFTER_MINUTE_ET,
    FLATTEN_DATE,
    MAX_HOLD_DAYS,
    MIN_EXIT_DTE,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TRAIL_ARM_PCT,
    TRAIL_GIVEBACK_PCT,
)

try:  # pragma: no cover - environment dependent
    from zoneinfo import ZoneInfo

    MARKET_TZ = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - environment dependent
    from datetime import timezone

    MARKET_TZ = timezone(timedelta(hours=-4), name="America/New_York")


URGENCY_IMMEDIATE = "IMMEDIATE"
URGENCY_NORMAL = "NORMAL"


@dataclass(frozen=True)
class ExitDecision:
    should_close: bool
    reason: str
    urgency: str = URGENCY_NORMAL
    detail: str = ""

    @property
    def is_immediate(self) -> bool:
        return self.urgency == URGENCY_IMMEDIATE


HOLD = ExitDecision(should_close=False, reason="HOLD", urgency=URGENCY_NORMAL)


def market_now(now: Optional[datetime] = None) -> datetime:
    """Exchange-local time. Never trust the local system clock for this."""
    if now is None:
        return datetime.now(MARKET_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=MARKET_TZ)
    return now.astimezone(MARKET_TZ)


def days_held(opened_at: str | datetime | None, now: Optional[datetime] = None) -> float:
    """Fractional days a position has been open.

    Returns 0.0 for a missing, unparseable or future timestamp rather than
    raising. A position with no memory should be governed by the other rules,
    not force-closed by a bad string.
    """
    if opened_at is None:
        return 0.0

    if isinstance(opened_at, datetime):
        opened = opened_at
    else:
        text = str(opened_at).strip()
        if not text:
            return 0.0
        try:
            opened = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return 0.0

    reference = market_now(now)
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=reference.tzinfo)

    elapsed = (reference - opened).total_seconds()
    if elapsed <= 0:
        return 0.0
    return elapsed / 86400.0


def in_flatten_window(now: Optional[datetime] = None) -> bool:
    """True once the pre-valuation flatten deadline has passed."""
    reference = market_now(now)
    if reference.date() > FLATTEN_DATE:
        return True
    if reference.date() < FLATTEN_DATE:
        return False
    deadline = time(FLATTEN_AFTER_HOUR_ET, FLATTEN_AFTER_MINUTE_ET)
    return reference.timetz().replace(tzinfo=None) >= deadline


def update_peak(previous_peak_pct: float, current_pnl_pct: float) -> float:
    """High-water mark. Only ever rises."""
    return max(float(previous_peak_pct or 0.0), float(current_pnl_pct))


def evaluate_exit(
    *,
    pnl_pct: float,
    peak_pnl_pct: float,
    dte: int,
    opened_at: str | datetime | None,
    now: Optional[datetime] = None,
) -> ExitDecision:
    """Decide whether an open long option position should be closed.

    `pnl_pct` must be measured against the BID (what you can actually sell at),
    not a midpoint mark. On a wide market that difference is a winner versus a
    scratch.
    """

    # 1. Flatten window - nothing outranks being flat for the valuation.
    if in_flatten_window(now):
        return ExitDecision(
            should_close=True,
            reason="FLATTEN_WINDOW",
            urgency=URGENCY_IMMEDIATE,
            detail="pre-valuation flatten deadline reached",
        )

    # 2. Expiry. Do not carry a long option into its final session.
    if dte is not None and dte <= MIN_EXIT_DTE:
        return ExitDecision(
            should_close=True,
            reason="EXPIRY",
            urgency=URGENCY_IMMEDIATE,
            detail=f"DTE {dte} <= {MIN_EXIT_DTE}",
        )

    # 3. Stop loss.
    if pnl_pct <= STOP_LOSS_PCT:
        return ExitDecision(
            should_close=True,
            reason="STOP_LOSS",
            urgency=URGENCY_IMMEDIATE,
            detail=f"P&L {pnl_pct:+.1%} <= {STOP_LOSS_PCT:+.0%}",
        )

    # 4. Trailing stop, checked BEFORE the fixed target. Arms only once the
    #    position has actually worked, so a trade that never ran is handled by
    #    the stop rather than the trail.
    peak = update_peak(peak_pnl_pct, pnl_pct)
    if peak >= TRAIL_ARM_PCT:
        floor = peak * (1.0 - TRAIL_GIVEBACK_PCT)
        if pnl_pct <= floor:
            return ExitDecision(
                should_close=True,
                reason="TRAILING_STOP",
                urgency=URGENCY_NORMAL,
                detail=f"faded to {pnl_pct:+.1%} from peak {peak:+.1%} (floor {floor:+.1%})",
            )

    # 5. Fixed take profit. Deliberately far out; the trail usually gets there first.
    if pnl_pct >= TAKE_PROFIT_PCT:
        return ExitDecision(
            should_close=True,
            reason="TAKE_PROFIT",
            urgency=URGENCY_NORMAL,
            detail=f"P&L {pnl_pct:+.1%} >= {TAKE_PROFIT_PCT:+.0%}",
        )

    # 6. Time stop. Theta on short-dated premium is unforgiving; if the thesis
    #    has not paid within the planned hold, the trade is over.
    held = days_held(opened_at, now)
    if held >= MAX_HOLD_DAYS:
        return ExitDecision(
            should_close=True,
            reason="TIME_STOP",
            urgency=URGENCY_NORMAL,
            detail=f"held {held:.1f}d >= {MAX_HOLD_DAYS}d",
        )

    return HOLD


def realisable_pnl_pct(
    entry_price: float,
    bid: float,
    fallback_pnl_pct: Optional[float] = None,
) -> Optional[float]:
    """P&L measured against the bid.

    Alpaca's `unrealized_plpc` marks to the midpoint, but a long option is exited
    by hitting the bid. Using the mid systematically overstates every position,
    and on a wide market it is the difference between a winner and a scratch.
    Returns None when neither the bid nor a fallback is usable, so the caller can
    treat it as missing data instead of a zero.
    """
    if bid > 0 and entry_price > 0:
        return (bid - entry_price) / entry_price
    return fallback_pnl_pct


def dte_from_occ_symbol(option_symbol: str, now: Optional[datetime] = None) -> Optional[int]:
    """Days to expiry parsed from the OCC symbol - no extra API call needed."""
    from strategy.option_selector import parse_occ_symbol

    try:
        expiration, _, _ = parse_occ_symbol(option_symbol)
    except ValueError:
        return None
    return (expiration - market_now(now).date()).days


def sort_closes_immediate_first(decisions: list[tuple[str, ExitDecision]]) -> list[tuple[str, ExitDecision]]:
    """Order closes so urgent ones are submitted first if the loop is interrupted."""
    return sorted(decisions, key=lambda item: (0 if item[1].is_immediate else 1, item[0]))


__all__ = [
    "ExitDecision",
    "HOLD",
    "URGENCY_IMMEDIATE",
    "URGENCY_NORMAL",
    "days_held",
    "dte_from_occ_symbol",
    "evaluate_exit",
    "in_flatten_window",
    "market_now",
    "realisable_pnl_pct",
    "sort_closes_immediate_first",
    "update_peak",
]
