from enum import Enum


class TradeDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


def determine_direction(stock) -> TradeDirection:
    """
    Determine directional bias from the stock-level signals.

    Requires a majority of the three directional signals to agree.
    Mixed signals are explicitly NEUTRAL rather than forced bearish.
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

    if bullish_signals >= 2:
        return TradeDirection.BULLISH

    if bearish_signals >= 2:
        return TradeDirection.BEARISH

    return TradeDirection.NEUTRAL