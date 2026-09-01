from enum import Enum


class TradeDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


def determine_direction(
    stock,
    *,
    intraday_return: float | None = None,
    change_pct: float | None = None,
    spy_intraday_return: float | None = None,
) -> TradeDirection:
    """
    Determine directional bias with both multi-day trend and real-time intraday momentum confirmation.

    Requires a majority of the three multi-day directional signals to agree,
    AND confirms that real-time intraday price action does not contradict the trade.
    """

    bullish_signals = sum([
        stock.return_5d > 0,
        stock.return_20d > 0,
        stock.relative_strength_spy > 0,
    ])

    bearish_signals = sum([
        stock.return_5d < 0,
        stock.return_20d < 0,
        stock.relative_strength_spy < 0,
    ])

    base_direction = TradeDirection.NEUTRAL
    if bullish_signals >= 2:
        base_direction = TradeDirection.BULLISH
    elif bearish_signals >= 2:
        base_direction = TradeDirection.BEARISH

    if base_direction == TradeDirection.NEUTRAL:
        return TradeDirection.NEUTRAL

    # Intraday momentum confirmation guards:
    # 1. Bullish trades require the stock NOT to be actively selling off intraday.
    if base_direction == TradeDirection.BULLISH:
        if intraday_return is not None and intraday_return < -0.004:
            return TradeDirection.NEUTRAL
        if change_pct is not None and change_pct < -0.008:
            return TradeDirection.NEUTRAL
        if spy_intraday_return is not None and spy_intraday_return < -0.010:
            return TradeDirection.NEUTRAL

    # 2. Bearish trades require the stock NOT to be actively rallying intraday.
    if base_direction == TradeDirection.BEARISH:
        if intraday_return is not None and intraday_return > 0.004:
            return TradeDirection.NEUTRAL
        if change_pct is not None and change_pct > 0.008:
            return TradeDirection.NEUTRAL
        if spy_intraday_return is not None and spy_intraday_return > 0.010:
            return TradeDirection.NEUTRAL

    return base_direction