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
    BREAKEVEN_ARM_PCT,
    BREAKEVEN_BUFFER_PCT,
    CREDIT_SPREAD_STOP_LOSS_PCT,
    CREDIT_SPREAD_TAKE_PROFIT_PCT,
    DAILY_FLATTEN_HOUR_ET,
    DAILY_FLATTEN_MINUTE_ET,
    DEBIT_SPREAD_TAKE_PROFIT_PCT,
    FLATTEN_AFTER_HOUR_ET,
    FLATTEN_AFTER_MINUTE_ET,
    FLATTEN_DATE,
    LONG_TAKE_PROFIT_PCT,
    MAX_HOLD_MINUTES,
    MIN_EXIT_DTE,
    STOP_LOSS_PCT,
    TIME_DECAY_STAGE1_MINUTES,
    TIME_DECAY_STAGE1_TARGET_PCT,
    TIME_DECAY_STAGE2_MINUTES,
    TIME_DECAY_STAGE2_TARGET_PCT,
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


def minutes_held(opened_at: str | datetime | None, now: Optional[datetime] = None) -> float:
    """Elapsed minutes a position has been open."""
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
    return elapsed / 60.0


def days_held(opened_at: str | datetime | None, now: Optional[datetime] = None) -> float:
    """Fractional days a position has been open (based on elapsed seconds / 86400)."""
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


def in_daily_flatten_window(now: Optional[datetime] = None) -> bool:
    """True once the daily 15:50 ET hard flatten deadline has passed."""
    reference = market_now(now)
    deadline = time(DAILY_FLATTEN_HOUR_ET, DAILY_FLATTEN_MINUTE_ET)
    return reference.timetz().replace(tzinfo=None) >= deadline


def in_flatten_window(now: Optional[datetime] = None) -> bool:
    """True once the pre-valuation final flatten deadline or daily 15:50 ET deadline has passed."""
    reference = market_now(now)
    if reference.date() > FLATTEN_DATE:
        return True
    if reference.date() == FLATTEN_DATE:
        deadline = time(FLATTEN_AFTER_HOUR_ET, FLATTEN_AFTER_MINUTE_ET)
        return reference.timetz().replace(tzinfo=None) >= deadline
    return in_daily_flatten_window(now)


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
    is_credit: bool = False,
    is_spread: bool = False,
) -> ExitDecision:
    """Decide whether an open position should be closed.

    For Credit Spreads:
      - pnl_pct = % of credit captured (+50% is target)
    For Debit Spreads / Longs:
      - pnl_pct = % ROI on net debit paid (+35% target)
    """

    # 1. Pre-valuation or daily EOD flatten window
    if in_flatten_window(now):
        return ExitDecision(
            should_close=True,
            reason="FLATTEN_WINDOW",
            urgency=URGENCY_IMMEDIATE,
            detail="daily or pre-valuation flatten deadline reached (15:50 ET)",
        )

    # 2. Expiry. Exit if at or below minimum DTE threshold (if MIN_EXIT_DTE < 0, only trigger if already expired)
    if dte is not None:
        if MIN_EXIT_DTE < 0:
            if dte < 0:
                return ExitDecision(
                    should_close=True,
                    reason="EXPIRY",
                    urgency=URGENCY_IMMEDIATE,
                    detail=f"DTE {dte} < 0 (expired)",
                )
        elif dte <= MIN_EXIT_DTE:
            return ExitDecision(
                should_close=True,
                reason="EXPIRY",
                urgency=URGENCY_IMMEDIATE,
                detail=f"DTE {dte} <= {MIN_EXIT_DTE}",
            )

    # 3. Stop loss
    if is_credit:
        if pnl_pct <= CREDIT_SPREAD_STOP_LOSS_PCT:
            return ExitDecision(
                should_close=True,
                reason="STOP_LOSS_CREDIT",
                urgency=URGENCY_IMMEDIATE,
                detail=f"Credit spread loss {pnl_pct:+.1%} <= {CREDIT_SPREAD_STOP_LOSS_PCT:+.0%}",
            )
    else:
        if pnl_pct <= STOP_LOSS_PCT:
            return ExitDecision(
                should_close=True,
                reason="STOP_LOSS",
                urgency=URGENCY_IMMEDIATE,
                detail=f"P&L {pnl_pct:+.1%} <= {STOP_LOSS_PCT:+.0%}",
            )

    # 4. Trailing stop (arms once position reaches TRAIL_ARM_PCT)
    peak = update_peak(peak_pnl_pct, pnl_pct)
    if peak >= TRAIL_ARM_PCT:
        # Dynamic ratcheted floor: tighter giveback at higher peaks
        if peak >= 0.35:
            floor = peak * 0.85
        elif peak >= 0.25:
            floor = peak * (1.0 - TRAIL_GIVEBACK_PCT)
        else:
            # Between TRAIL_ARM_PCT (e.g. 0.18) and 0.25: give back at most 6% absolute
            floor = max(BREAKEVEN_BUFFER_PCT, peak - 0.06)

        if pnl_pct <= floor:
            return ExitDecision(
                should_close=True,
                reason="TRAILING_STOP",
                urgency=URGENCY_NORMAL,
                detail=f"faded to {pnl_pct:+.1%} from peak {peak:+.1%} (floor {floor:+.1%})",
            )

    # 5. Breakeven Shield (protects gains of +12% or more from turning into losses)
    if peak >= BREAKEVEN_ARM_PCT:
        if pnl_pct <= BREAKEVEN_BUFFER_PCT:
            return ExitDecision(
                should_close=True,
                reason="BREAKEVEN_STOP",
                urgency=URGENCY_NORMAL,
                detail=f"protected gain: faded to {pnl_pct:+.1%} after peak {peak:+.1%} (floor {BREAKEVEN_BUFFER_PCT:+.1%})",
            )

    # 6. Take profit & Dynamic Time-Decayed Targets
    mins = minutes_held(opened_at, now)
    if is_credit:
        if pnl_pct >= CREDIT_SPREAD_TAKE_PROFIT_PCT:
            return ExitDecision(
                should_close=True,
                reason="TAKE_PROFIT_CREDIT",
                urgency=URGENCY_NORMAL,
                detail=f"Credit capture {pnl_pct:+.1%} >= {CREDIT_SPREAD_TAKE_PROFIT_PCT:+.0%}",
            )
    else:
        target_pct = DEBIT_SPREAD_TAKE_PROFIT_PCT if is_spread else LONG_TAKE_PROFIT_PCT
        if pnl_pct >= target_pct:
            return ExitDecision(
                should_close=True,
                reason="TAKE_PROFIT",
                urgency=URGENCY_NORMAL,
                detail=f"P&L {pnl_pct:+.1%} >= {target_pct:+.0%}",
            )

        # Dynamic time-decayed targets: take profits early before theta accelerates
        if mins >= TIME_DECAY_STAGE2_MINUTES and pnl_pct >= TIME_DECAY_STAGE2_TARGET_PCT:
            return ExitDecision(
                should_close=True,
                reason="TIME_DECAY_PROFIT",
                urgency=URGENCY_NORMAL,
                detail=f"banked {pnl_pct:+.1%} >= {TIME_DECAY_STAGE2_TARGET_PCT:+.0%} target after {mins:.0f}m hold",
            )
        if mins >= TIME_DECAY_STAGE1_MINUTES and pnl_pct >= TIME_DECAY_STAGE1_TARGET_PCT:
            return ExitDecision(
                should_close=True,
                reason="TIME_DECAY_PROFIT",
                urgency=URGENCY_NORMAL,
                detail=f"banked {pnl_pct:+.1%} >= {TIME_DECAY_STAGE1_TARGET_PCT:+.0%} target after {mins:.0f}m hold",
            )

    # 7. Time stop (max hold window to eliminate theta burn)
    if mins >= MAX_HOLD_MINUTES:
        return ExitDecision(
            should_close=True,
            reason="TIME_STOP",
            urgency=URGENCY_NORMAL,
            detail=f"held {mins:.0f}m >= {MAX_HOLD_MINUTES:.0f}m",
        )

    return HOLD


def realisable_pnl_pct(
    entry_price: float,
    bid: float,
    fallback_pnl_pct: Optional[float] = None,
    is_credit: bool = False,
) -> Optional[float]:
    """Calculate realisable P&L percentage based on executable prices.

    - For Long/Debit: (current_bid - entry_ask) / entry_ask
    - For Credit Spreads: (entry_credit - current_cost_to_close) / entry_credit
    """
    if entry_price > 0:
        if is_credit:
            # bid here represents current cost to buy back / close
            return (entry_price - bid) / entry_price
        if bid > 0:
            return (bid - entry_price) / entry_price
    return fallback_pnl_pct


def dte_from_occ_symbol(option_symbol: str, now: Optional[datetime] = None) -> Optional[int]:
    """Days to expiry parsed from the OCC symbol."""
    from strategy.option_selector import parse_occ_symbol

    try:
        expiration, _, _ = parse_occ_symbol(option_symbol)
    except ValueError:
        return None
    return (expiration - market_now(now).date()).days


def sort_closes_immediate_first(decisions: list[tuple[str, ExitDecision]]) -> list[tuple[str, ExitDecision]]:
    """Order closes so urgent ones are submitted first."""
    return sorted(decisions, key=lambda item: (0 if item[1].is_immediate else 1, item[0]))


__all__ = [
    "ExitDecision",
    "HOLD",
    "URGENCY_IMMEDIATE",
    "URGENCY_NORMAL",
    "days_held",
    "minutes_held",
    "dte_from_occ_symbol",
    "evaluate_exit",
    "in_daily_flatten_window",
    "in_flatten_window",
    "market_now",
    "realisable_pnl_pct",
    "sort_closes_immediate_first",
    "update_peak",
]

