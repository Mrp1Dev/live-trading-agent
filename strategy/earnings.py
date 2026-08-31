"""Deterministic earnings-event veto.

Over a four-day window holding short-dated long premium, one earnings print can
cost more than every other improvement combined - and it costs you even when the
direction is right, because implied vol collapses the moment the event passes.

This is deliberately NOT an LLM judgment. In a live run the stock ranker put a
name at #1 whose own dossier said it reported earnings the next evening: the
model read "catalyst" where a long-premium strategy should read "binary event".
A model asked to weigh a catalyst will sometimes weigh it positively. A veto in
plain Python cannot.

The input is the research dossier text, which is model-written. That makes this a
CONSERVATIVE filter, not a calendar: ambiguous future-earnings language vetoes.
Refusing a good trade costs an opportunity; taking an earnings gamble on a 4-DTE
call costs the demo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional

# Language that implies an earnings event is STILL AHEAD.
_FUTURE_PATTERNS = (
    r"\bwill report\b",
    r"\bis (?:set|scheduled|expected) to report\b",
    r"\breports?\s+(?:its\s+)?(?:fiscal\s+)?q[1-4]\b",
    r"\breports?\s+(?:its\s+)?(?:fiscal\s+)?(?:first|second|third|fourth)[- ]quarter\b",
    r"\breports?\s+earnings\b",
    r"\bearnings?\s+(?:are\s+)?(?:due|scheduled|upcoming|expected)\b",
    r"\bupcoming\s+earnings\b",
    r"\bahead of\s+(?:its\s+)?(?:q[1-4]\s+)?earnings\b",
    r"\bahead of\s+(?:the\s+)?earnings call\b",
    r"\bearnings call\b",
    r"\bbefore the (?:opening )?bell\b",
    r"\bafter the (?:closing )?bell\b",
    r"\bafter the close on\b",
    r"\bpre[- ]earnings\b",
    r"\bimplied earnings mover\b",
    r"\bearnings volatility\b",
    r"\bexpects? eps of\b",
    r"\banalysts? expect\s+eps\b",
)

# Language that implies the event has ALREADY HAPPENED.
_PAST_PATTERNS = (
    r"\bbeat\b",
    r"\bmissed\b",
    r"\bposted\b",
    r"\breported\s+(?:q[1-4]|first|second|third|fourth)\b",
    r"\bfollowing\s+q[1-4]\s+results\b",
    r"\bpost[- ]earnings\b",
    r"\bafter\s+(?:its\s+)?q[1-4]\s+(?:results|earnings|report)\b",
    r"\bq[1-4]\s+(?:results|beat|miss)\b",
    r"\blast\s+quarter\b",
)

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
    "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}

_MONTH_DAY = re.compile(
    r"\b(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{1,2})\b",
    re.IGNORECASE,
)
_NUMERIC_DATE = re.compile(r"\b(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?\b")


@dataclass(frozen=True)
class EarningsRisk:
    has_risk: bool
    reason: str = ""
    event_date: Optional[date] = None
    confidence: str = "none"      # none | inferred | dated

    def __bool__(self) -> bool:
        return self.has_risk


def _matches(patterns: tuple[str, ...], text: str) -> list[str]:
    return [p for p in patterns if re.search(p, text, re.IGNORECASE)]


def extract_candidate_dates(text: str, as_of: date, lookahead_days: int = 400) -> list[date]:
    """Pull plausible near-future dates out of free text.

    Year is inferred: dossiers say "Tuesday, Sept. 1", not "2026-09-01". A month
    that has already passed this year is read as next year, so a December run
    reading "Jan. 5" resolves forward rather than ten months backwards.
    """
    found: list[date] = []
    horizon = as_of + timedelta(days=lookahead_days)

    for match in _MONTH_DAY.finditer(text):
        month = _MONTHS[match.group(1).lower()]
        day = int(match.group(2))
        for year in (as_of.year, as_of.year + 1):
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if as_of <= candidate <= horizon:
                found.append(candidate)
                break

    for match in _NUMERIC_DATE.finditer(text):
        month, day = int(match.group(1)), int(match.group(2))
        raw_year = match.group(3)
        years = [int(raw_year) + (2000 if int(raw_year) < 100 else 0)] if raw_year else [as_of.year, as_of.year + 1]
        for year in years:
            try:
                candidate = date(year, month, day)
            except ValueError:
                continue
            if as_of <= candidate <= horizon:
                found.append(candidate)
                break

    return sorted(set(found))


def detect_earnings_risk(
    symbol: str,
    research_text: str,
    *,
    as_of: Optional[date] = None,
    horizon_days: int = 14,
) -> EarningsRisk:
    """Decide whether `symbol` has an earnings event inside the trading horizon.

    `horizon_days` should cover the longest contract life being considered.
    """
    if not research_text or not research_text.strip():
        return EarningsRisk(has_risk=False)

    reference = as_of or datetime.now().date()
    text = research_text.strip()

    future_hits = _matches(_FUTURE_PATTERNS, text)
    if not future_hits:
        return EarningsRisk(has_risk=False)

    past_hits = _matches(_PAST_PATTERNS, text)
    # Extraction is deliberately WIDE and the horizon check narrow. If a far-out
    # earnings date is not even extracted, the code cannot tell "reports in
    # November" from "reports, date unknown" and falls through to the
    # conservative undated branch, vetoing a name that is perfectly tradeable.
    dates = extract_candidate_dates(text, reference)
    in_window = [d for d in dates if reference <= d <= reference + timedelta(days=horizon_days)]

    if in_window:
        event = in_window[0]
        return EarningsRisk(
            has_risk=True,
            reason=f"earnings event on {event.isoformat()} falls within the {horizon_days}-day trading horizon",
            event_date=event,
            confidence="dated",
        )

    # Dated, but comfortably beyond anything we would hold.
    if dates:
        return EarningsRisk(has_risk=False)

    # Future-earnings language with no parseable date. If the dossier is
    # dominated by past-tense reporting, treat it as already-happened.
    if len(past_hits) >= len(future_hits):
        return EarningsRisk(has_risk=False)

    return EarningsRisk(
        has_risk=True,
        reason="undated forward-looking earnings language in the research dossier",
        confidence="inferred",
    )


def filter_earnings_risk(
    symbols: list[str],
    research: dict[str, str],
    *,
    as_of: Optional[date] = None,
    horizon_days: int = 14,
) -> tuple[list[str], dict[str, EarningsRisk]]:
    """Split symbols into (safe, vetoed).

    Applied BEFORE the LLM ranker so a binary event never reaches a model that
    might score it as a catalyst - and so no tokens are spent ranking a name that
    cannot be traded.
    """
    safe: list[str] = []
    vetoed: dict[str, EarningsRisk] = {}

    for symbol in symbols:
        risk = detect_earnings_risk(
            symbol,
            research.get(symbol, ""),
            as_of=as_of,
            horizon_days=horizon_days,
        )
        if risk.has_risk:
            vetoed[symbol] = risk
        else:
            safe.append(symbol)

    return safe, vetoed


def iv_term_structure_warning(
    front_iv: Optional[float],
    back_iv: Optional[float],
    threshold: float = 1.30,
) -> Optional[str]:
    """Flag an inverted IV term structure, which can signal a pending event.

    Reported as a WARNING, never a veto. At 1-14 DTE the front expiry carries a
    structural vol premium from weekend decay alone: on a live chain every one of
    four names showed front IV above back IV, including one that had already
    reported. The signal is too noisy at these tenors to trade off.
    """
    if not front_iv or not back_iv or back_iv <= 0:
        return None
    ratio = front_iv / back_iv
    if ratio >= threshold:
        return f"front/back IV {ratio:.2f} - possible pending event"
    return None


__all__ = [
    "EarningsRisk",
    "detect_earnings_risk",
    "extract_candidate_dates",
    "filter_earnings_risk",
    "iv_term_structure_warning",
]
