from alpaca_client.client import get_trading_client
from alpaca_client.options import get_option_chain
from alpaca_client.stocks import (
    print_top_scanned_stocks,
    get_latest_underlying_prices,
)

from strategy.option_selector import select_directional_options
from strategy.direction import TradeDirection, determine_direction
from strategy.research_agent import (
    research_stocks,
    research_text_by_symbol,
    print_research_reports,
)
from strategy.llm_ranker import rank_stocks
from strategy.option_ranker import rank_option_pool
from config import STOCK_SCANNER_TOP_N, LLM_STOCK_TOP_K

def main() -> None:
    trading_client = get_trading_client()
    account = trading_client.get_account()

    print("=== Connected to Alpaca (Paper Trading) ===")
    print(f"Account ID:     {account.id}")
    print(f"Equity:         ${float(account.equity):,.2f}")
    print(f"Buying power:   ${float(account.buying_power):,.2f}")
    print(f"Options level:  {account.options_trading_level} (approved: {account.options_approved_level})")

    # 1. Scan 50-stock universe and display top 15 ranked candidates
    print("\nScanning 50-stock universe...")
    top_candidates = print_top_scanned_stocks(
        top_n=STOCK_SCANNER_TOP_N
    )

    if top_candidates:
        # Research all scanner-selected stocks.
        research_reports = research_stocks(top_candidates)
        print_research_reports(research_reports)
        research = research_text_by_symbol(research_reports)

        # LLM ranks stocks comparatively.
        ranked_symbols = rank_stocks(
            stocks=top_candidates,
            research=research,
            debug=True,
        )

        # Restore ScannedStock objects in LLM order.
        stocks_by_symbol = {
            stock.symbol: stock
            for stock in top_candidates
        }

        ranked_candidates = [
            stocks_by_symbol[symbol]
            for symbol in ranked_symbols
        ]

        print("\n" + "=" * 120)
        print(" LLM STOCK RANKING")
        print("=" * 120)

        for llm_rank, symbol in enumerate(ranked_symbols, start=1):
            stock = stocks_by_symbol[symbol]
            scanner_rank = stock.rank if stock.rank is not None else "-"
            print(
                f"#{llm_rank:<2} "
                f"{symbol:<6} "
                f"Scanner=#{scanner_rank:<3} "
                f"ScannerScore={stock.score:>5.1f}"
            )

        # Only send LLM top-K into option selection.
        selected_stocks = ranked_candidates[:LLM_STOCK_TOP_K]

        print("\nLLM-RANKED STOCKS")
        for rank, stock in enumerate(selected_stocks, start=1):
            print(
                f"#{rank:<2} {stock.symbol:<6} "
                f"Scanner=#{stock.rank:<2} "
                f"ScannerScore={stock.score:.1f}"
            )

        latest_prices = get_latest_underlying_prices(
            [stock.symbol for stock in selected_stocks]
        )

        all_options = []
        stocks_by_symbol = {}
        directions_by_symbol = {}

        for stock in selected_stocks:
            underlying_price = latest_prices.get(stock.symbol)

            if underlying_price is None or underlying_price <= 0:
                print(f"Skipping {stock.symbol}: no valid live price.")
                continue

            direction = determine_direction(stock)

            if direction == TradeDirection.NEUTRAL:
                continue

            chain = get_option_chain(stock.symbol)

            options = select_directional_options(
                chain=chain,
                underlying_price=underlying_price,
                direction=direction.value.lower(),
            )

            if not options:
                continue

            stocks_by_symbol[stock.symbol] = stock
            directions_by_symbol[stock.symbol] = direction.value
            all_options.extend(options)

        if not all_options:
            print("No option candidates survived deterministic filtering.")
            return

        ranked_option_symbols = rank_option_pool(
            options=all_options,
            stocks=stocks_by_symbol,
            directions=directions_by_symbol,
            research=research,
            debug=True,
        )

        TOP_OPTIONS = 5

        ranked_option_symbols = ranked_option_symbols[:TOP_OPTIONS]

        options_by_symbol = {
            option.symbol: option
            for option in all_options
        }

        selected_options = [
            options_by_symbol[symbol]
            for symbol in ranked_option_symbols
        ]

        print("\n" + "=" * 120)
        print(" TOP RANKED OPTIONS (GLOBAL)")
        print("=" * 120)

        for rank, option in enumerate(selected_options, start=1):
            print(
                f"#{rank:<2} "
                f"{option.symbol:<24} "
                f"Type={option.option_type.upper():<4} "
                f"Strike=${option.strike:<7.2f} "
                f"DTE={option.dte:<3} "
                f"Mid=${option.mid:<6.2f} "
                f"Spread={option.spread_pct:<6.1%} "
                f"Delta={option.delta if option.delta is not None else 0.0:+.3f} "
                f"Score={option.score:>5.1f}"
            )


if __name__ == "__main__":
    main()