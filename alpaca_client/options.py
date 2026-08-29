from typing import Dict

from alpaca.data.historical.option import OptionsSnapshot
from alpaca.data.requests import OptionChainRequest

from .client import get_option_data_client
from strategy.option_selector import (
    select_directional_options,
    print_option_candidates,
)


def get_option_chain(symbol: str) -> Dict[str, OptionsSnapshot]:
    client = get_option_data_client()

    request = OptionChainRequest(
        underlying_symbol=symbol,
    )

    return client.get_option_chain(request)


def print_chain(symbol: str, limit: int = 10) -> None:
    """Print a snapshot summary of option contracts for a symbol."""
    chain = get_option_chain(symbol)

    print(f"\nOption chain for {symbol}")
    print(f"Contracts returned: {len(chain)}")

    if not chain:
        print("No contracts found.")
        return

    for contract_symbol, snapshot in list(chain.items())[:limit]:
        print(f"\n{contract_symbol}")

        if snapshot.latest_quote:
            print(f"  bid: {snapshot.latest_quote.bid_price}")
            print(f"  ask: {snapshot.latest_quote.ask_price}")

        if snapshot.implied_volatility is not None:
            print(f"  IV:    {snapshot.implied_volatility:.4f}")

        if snapshot.greeks:
            print(f"  delta: {snapshot.greeks.delta}")
            print(f"  gamma: {snapshot.greeks.gamma}")
            print(f"  theta: {snapshot.greeks.theta}")
            print(f"  vega:  {snapshot.greeks.vega}")

def print_best_candidates(
    symbol: str,
    underlying_price: float = 100.0,
    direction: str = "bullish",
    limit: int = 20,
) -> None:
    chain = get_option_chain(symbol)

    candidates = select_directional_options(
        chain=chain,
        underlying_price=underlying_price,
        direction=direction,
    )

    print(f"\n{symbol}")
    print(f"Contracts returned: {len(chain)}")
    print(f"Liquid candidates:  {len(candidates)}")

    for candidate in candidates[:limit]:
        print(
            f"{candidate.symbol} "
            f"mid={candidate.mid:.2f} "
            f"spread={candidate.spread_pct:.1%} "
            f"IV={candidate.iv} "
            f"delta={candidate.delta}"
        )


def inspect_options(
    symbol: str,
    underlying_price: float,
    direction: str = "bullish",
) -> None:
    chain = get_option_chain(symbol)

    candidates = select_directional_options(
        chain=chain,
        underlying_price=underlying_price,
        direction=direction,
    )

    print(f"\n{symbol}")
    print(f"Contracts returned: {len(chain)}")
    print(f"Selected candidates: {len(candidates)}")

    print_option_candidates(
        symbol=symbol,
        underlying_price=underlying_price,
        candidates=candidates,
        direction=direction,
    )

if __name__ == "__main__":
    import sys
    ticker = sys.argv[1] if len(sys.argv) > 1 else "SPY"
    print_chain(ticker)