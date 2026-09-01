from __future__ import annotations

from .models import TradeCandidate


def build_trade_candidates(
    ranked_options,
    *,
    stocks_by_symbol,
    directions_by_symbol,
    stock_rank_by_symbol,
    option_rank_by_symbol,
) -> list[TradeCandidate]:
    result: list[TradeCandidate] = []
    for option in ranked_options:
        stock_symbol = getattr(option, "underlying_symbol", None)
        if not stock_symbol or stock_symbol not in stocks_by_symbol:
            matches = [
                symbol
                for symbol in stocks_by_symbol
                if option.symbol.upper().startswith(symbol.upper()) or f"{symbol.upper()}_" in option.symbol.upper()
            ]
            if not matches:
                continue
            stock_symbol = max(matches, key=len)
        stock = stocks_by_symbol[stock_symbol]
        direction = directions_by_symbol[stock_symbol]
        result.append(
            TradeCandidate.from_ranked_option(
                option=option,
                stock=stock,
                direction=direction,
                stock_llm_rank=stock_rank_by_symbol.get(stock_symbol, 0),
                option_llm_rank=option_rank_by_symbol.get(option.symbol, 0),
            )
        )
    return result
