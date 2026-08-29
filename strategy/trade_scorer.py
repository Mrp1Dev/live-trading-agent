from dataclasses import dataclass

from strategy.llm import LLMDecision


@dataclass
class ScoredTrade:
    stock_symbol: str
    option_symbol: str
    direction: str

    stock_score: float
    option_score: float
    llm_confidence: float
    trade_score: float

    thesis: str
    invalidation: str

    dte: int
    spread_pct: float

    underlying_price: float

    option_bid: float
    option_ask: float
    option_mid: float

    option_delta: float | None
    option_gamma: float | None
    option_vega: float | None
    option_theta: float | None


def calculate_trade_score(
    stock_score: float,
    option_score: float,
    llm_confidence: float,
    spread_pct: float,
    dte: int,
) -> float:
    """
    Combine stock quality, option quality, LLM confidence,
    liquidity and time-to-expiry into one trade score.
    """

    option_quality = option_score

    # Liquidity penalty.
    if spread_pct <= 0.03:
        liquidity_score = 100.0
    elif spread_pct <= 0.05:
        liquidity_score = 90.0
    elif spread_pct <= 0.08:
        liquidity_score = 75.0
    elif spread_pct <= 0.12:
        liquidity_score = 55.0
    else:
        liquidity_score = 25.0

    # Prefer short duration for this particular competition,
    # without preferring contracts expiring immediately.
    if 2 <= dte <= 7:
        dte_score = 100.0
    elif 1 <= dte <= 10:
        dte_score = 85.0
    elif dte <= 14:
        dte_score = 65.0
    else:
        dte_score = 40.0

    confidence_score = llm_confidence * 100.0

    score = (
        0.30 * stock_score
        + 0.30 * option_quality
        + 0.25 * confidence_score
        + 0.10 * liquidity_score
        + 0.05 * dte_score
    )

    return round(score, 2)


def score_trade(
    stock,
    option,
    decision: LLMDecision,
    underlying_price: float,
) -> ScoredTrade:
    """
    Convert a stock + option + LLM decision into a single scored trade.
    """

    trade_score = calculate_trade_score(
        stock_score=stock.score,
        option_score=option.score,
        llm_confidence=decision.confidence,
        spread_pct=option.spread_pct,
        dte=option.dte,
    )

    return ScoredTrade(
        stock_symbol=stock.symbol,
        option_symbol=option.symbol,
        direction=decision.decision,
        stock_score=stock.score,
        option_score=option.score,
        llm_confidence=decision.confidence,
        trade_score=trade_score,
        thesis=decision.thesis,
        invalidation=decision.invalidation,
        dte=option.dte,
        spread_pct=option.spread_pct,
        underlying_price=underlying_price,
        option_bid=option.bid,
        option_ask=option.ask,
        option_mid=option.mid,
        option_delta=option.delta,
        option_gamma=option.gamma,
        option_vega=option.vega,
        option_theta=option.theta,
    )


def validate_trade(trade: ScoredTrade) -> tuple[bool, str]:
    """
    Validate that a scored trade satisfies the invariants
    required by portfolio construction.
    """

    if trade.direction not in {"BULLISH", "BEARISH"}:
        return False, "Invalid trade direction."

    if not 0.0 <= trade.trade_score <= 100.0:
        return False, "Trade score outside 0-100 range."

    if not 0.0 <= trade.stock_score <= 100.0:
        return False, "Stock score outside 0-100 range."

    if not 0.0 <= trade.option_score <= 100.0:
        return False, "Option score outside 0-100 range."

    if not 0.0 <= trade.llm_confidence <= 1.0:
        return False, "LLM confidence outside 0-1 range."

    if trade.dte < 1:
        return False, "Option expires too soon."

    if trade.option_bid <= 0:
        return False, "Invalid option bid."

    if trade.option_ask <= 0:
        return False, "Invalid option ask."

    if trade.option_ask < trade.option_bid:
        return False, "Ask is below bid."

    if trade.option_mid <= 0:
        return False, "Invalid option midpoint."

    if trade.spread_pct < 0:
        return False, "Negative spread."

    return True, "Valid trade."