from alpaca_client.client import get_trading_client
from alpaca_client.options import print_best_candidates, inspect_options, get_option_chain
from alpaca_client.stocks import (
    print_top_scanned_stocks,
    get_latest_underlying_prices,
)

from strategy.option_selector import select_directional_options
from strategy.llm import analyze_trade
from strategy.trade_scorer import score_trade, ScoredTrade, validate_trade
from strategy.portfolio import build_portfolio, print_portfolio_plan
from risk.risk import assess_portfolio, print_risk_report
from strategy.direction import TradeDirection, determine_direction

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
    top_candidates = print_top_scanned_stocks(top_n=15)

    # 2. Inspect directional option candidates for the top stocks
    if top_candidates:
        latest_prices = get_latest_underlying_prices(
            [stock.symbol for stock in top_candidates]
        )
        print("\nAnalyzing option opportunities across top 15 stocks...")

        all_trade_candidates = []

        for stock in top_candidates:
            underlying_price = latest_prices.get(stock.symbol)
            if underlying_price is None:
                print(
                    f"Skipping {stock.symbol}: "
                    "no fresh underlying price available."
                )
                continue

            if underlying_price <= 0:
                print(
                    f"Skipping {stock.symbol}: "
                    f"invalid underlying price {underlying_price}."
                )
                continue

            # Get option chain
            chain = get_option_chain(stock.symbol)

            direction = determine_direction(stock)

            if direction == TradeDirection.NEUTRAL:
                continue

            options = select_directional_options(
                chain=chain,
                underlying_price=underlying_price,
                direction=direction.value.lower(),
            )
            for option in options[:5]:
                decision = analyze_trade(option, stock)

                if decision.decision == "WATCH":
                    continue

                scored_trade = score_trade(
                    stock=stock,
                    option=option,
                    decision=decision,
                    underlying_price=underlying_price,
                )

                is_valid, reason = validate_trade(scored_trade)

                if not is_valid:
                    print(
                        f"Skipping {scored_trade.option_symbol}: {reason}"
                    )
                    continue

                all_trade_candidates.append(scored_trade)
    
        print("\n" + "=" * 120)
        print(" TOP TRADE CANDIDATES")
        print("=" * 120)

        all_trade_candidates.sort(
            key=lambda x: x.trade_score,
            reverse=True,
        )

        for i, trade in enumerate(all_trade_candidates[:20], start=1):
            print(
                f"#{i:<3} "
                f"{trade.stock_symbol:<6} "
                f"{trade.direction:<8} "
                f"TradeScore={trade.trade_score:>5.1f} "
                f"StockScore={trade.stock_score:>5.1f} "
                f"OptScore={trade.option_score:>5.1f} "
                f"LLM={trade.llm_confidence:.2f} "
                f"Option={trade.option_symbol}"
            )

        portfolio = build_portfolio(
            trades=all_trade_candidates,
            account_equity=float(account.equity),
        )

        print_portfolio_plan(
            positions=portfolio,
            account_equity=float(account.equity),
        )

        risk_report = assess_portfolio(
            positions=portfolio,
            account_equity=float(account.equity),
        )

        print_risk_report(
            report=risk_report,
            account_equity=float(account.equity),
        )

if __name__ == "__main__":
    main()