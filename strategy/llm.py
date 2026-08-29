from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from strategy.option_selector import OptionCandidate
from strategy.scanner import ScannedStock
from strategy.direction import TradeDirection, determine_direction

@dataclass
class LLMDecision:
    """Represents a trading decision and confidence evaluation for an option contract."""

    decision: str  # "BULLISH", "BEARISH" etc.
    confidence: float  # 0.0 to 1.0
    thesis: str
    invalidation: str


def analyze_trade(candidate, stock):
    # TEMPORARY PLACEHOLDER FOR FUTURE FEATHERLESS MODEL

    expected_direction = determine_direction(stock)

    if expected_direction == TradeDirection.NEUTRAL:
        return LLMDecision(
            decision="WATCH",
            confidence=0.0,
            thesis=(
                f"{stock.symbol} has mixed directional signals; "
                "no clear bullish or bearish bias."
            ),
            invalidation="No directional edge is currently established.",
        )

    if (
        expected_direction == TradeDirection.BULLISH
        and candidate.option_type != "call"
    ):
        decision = "WATCH"

    elif (
        expected_direction == TradeDirection.BEARISH
        and candidate.option_type != "put"
    ):
        decision = "WATCH"

    else:
        decision = expected_direction.value
    confidence = min(
        0.95,
        max(
            0.50,
            0.50 + abs(stock.score - 50.0) / 100.0,
        ),
    )

    if decision == "BULLISH":
        thesis = (
            f"{stock.symbol} shows positive medium-term momentum, "
            "positive relative strength, and a favorable trend."
        )
        invalidation = (
            "Momentum turns negative and relative strength "
            "versus SPY deteriorates."
        )

    elif decision == "BEARISH":
        thesis = (
            f"{stock.symbol} shows weak momentum or relative strength, "
            "creating downside risk."
        )
        invalidation = (
            "Momentum reverses higher and relative strength "
            "versus SPY improves."
        )

    else:
        thesis = (
            f"The stock thesis is {expected_direction.lower()}, "
            f"but the option direction does not match."
        )
        invalidation = "No trade until stock and option direction agree."

        # Don't allow WATCH to compete with real trades.
        confidence = 0.0

    return LLMDecision(
        decision=decision,
        confidence=confidence,
        thesis=thesis,
        invalidation=invalidation,
    )