from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class PortfolioPosition:
    trade: object
    contracts: int
    max_loss: float
    premium_deployed: float
    risk_weight: float


from config import (
    MAX_TOTAL_RISK_PCT,
    MAX_SINGLE_TRADE_RISK_PCT,
    MAX_UNDERLYING_RISK_PCT,
    MAX_POSITIONS,
    MIN_TRADE_SCORE,
)


def _estimated_entry_price(trade) -> float:
    """
    Estimate the executable entry price for sizing.

    Current strategy only supports buying single-leg options,
    so the conservative reference price is the ask.

    Midpoint remains a useful informational/reference value,
    but should not be used to size a live position.
    """
    if trade.option_ask > 0:
        return trade.option_ask

    return trade.option_mid


def _per_contract_risk(trade) -> float:
    """
    Current system assumes long single-leg options.

    For a long option:
        max loss = premium paid × 100

    Position sizing uses the estimated executable entry price,
    not the midpoint.
    """

    entry_price = _estimated_entry_price(trade)

    if entry_price <= 0:
        return 0.0

    return entry_price * 100.0


def _trade_weight(trade) -> float:
    """
    Convert trade quality into an allocation weight.

    Higher-scoring trades receive disproportionately more
    risk budget, rather than everything being allocated equally.
    """

    # Start with score above our minimum acceptable threshold.
    excess_score = max(
        0.0,
        trade.trade_score - MIN_TRADE_SCORE,
    )

    if excess_score <= 0:
        return 0.0

    # Square the advantage so materially better trades receive
    # noticeably more capital.
    return excess_score ** 2


def select_best_trade_per_stock(
    trades: List[object],
) -> List[object]:
    """
    Only keep the best complete trade for each underlying.

    This prevents the portfolio from accidentally holding:
        MSTR 130C
        MSTR 128C
        MSTR 129C
        ...

    and treating them as independent opportunities.
    """

    best_by_symbol = {}

    for trade in trades:
        existing = best_by_symbol.get(trade.stock_symbol)

        if (
            existing is None
            or trade.trade_score > existing.trade_score
        ):
            best_by_symbol[trade.stock_symbol] = trade

    return sorted(
        best_by_symbol.values(),
        key=lambda t: t.trade_score,
        reverse=True,
    )


def build_portfolio(
    trades: List[object],
    account_equity: float,
    total_risk_pct: float = MAX_TOTAL_RISK_PCT,
    max_trade_risk_pct: float = MAX_SINGLE_TRADE_RISK_PCT,
    max_underlying_risk_pct: float = MAX_UNDERLYING_RISK_PCT,
    max_positions: int = MAX_POSITIONS,
    min_trade_score: float = MIN_TRADE_SCORE,
) -> List[PortfolioPosition]:
    """
    Convert ranked trades into a proposed portfolio.

    Current instrument assumption:
        long calls / long puts.

    The portfolio is allocated by MAXIMUM LOSS, not buying power.

    Example:
        $100,000 account
        total_risk = 12%
        => max planned loss = $12,000

    This is a proposed portfolio. The risk engine gets the
    final veto before anything can be executed.
    """

    if account_equity <= 0:
        raise ValueError("account_equity must be positive")

    if total_risk_pct <= 0:
        raise ValueError("total_risk_pct must be positive")

    if max_trade_risk_pct <= 0:
        raise ValueError("max_trade_risk_pct must be positive")

    if max_underlying_risk_pct <= 0:
        raise ValueError(
            "max_underlying_risk_pct must be positive"
        )

    max_total_risk = account_equity * total_risk_pct
    max_trade_risk = account_equity * max_trade_risk_pct

    # ---------------------------------------------------------
    # 1. Remove weak trades.
    # ---------------------------------------------------------

    eligible = [
        trade
        for trade in trades
        if trade.trade_score >= min_trade_score
    ]

    # ---------------------------------------------------------
    # 2. One trade per underlying.
    # ---------------------------------------------------------

    eligible = select_best_trade_per_stock(eligible)

    # ---------------------------------------------------------
    # 3. Keep only best N underlyings.
    # ---------------------------------------------------------

    eligible = eligible[:max_positions]

    if not eligible:
        return []

    # ---------------------------------------------------------
    # 4. Calculate relative weights.
    # ---------------------------------------------------------

    weighted_trades = []

    for trade in eligible:
        weight = _trade_weight(trade)

        if weight <= 0:
            continue

        per_contract_risk = _per_contract_risk(trade)

        if per_contract_risk <= 0:
            continue

        weighted_trades.append(
            (trade, weight, per_contract_risk)
        )

    if not weighted_trades:
        return []

    weight_sum = sum(
        weight
        for _, weight, _ in weighted_trades
    )

    # ---------------------------------------------------------
    # 5. Convert risk budget into contracts.
    # ---------------------------------------------------------

    positions: List[PortfolioPosition] = []

    for trade, weight, per_contract_risk in weighted_trades:
        normalized_weight = weight / weight_sum

        desired_risk = (
            max_total_risk
            * normalized_weight
        )

        # Hard single-trade cap.
        allocated_risk = min(
            desired_risk,
            max_trade_risk,
        )

        contracts = int(
            allocated_risk // per_contract_risk
        )

        if contracts <= 0:
            continue

        max_loss = contracts * per_contract_risk

        positions.append(
            PortfolioPosition(
                trade=trade,
                contracts=contracts,
                max_loss=max_loss,
                premium_deployed=max_loss,
                risk_weight=normalized_weight,
            )
        )

    # ---------------------------------------------------------
    # 6. Ensure we never exceed total risk because of rounding.
    # ---------------------------------------------------------

    positions.sort(
        key=lambda p: p.trade.trade_score,
        reverse=True,
    )

    final_positions: List[PortfolioPosition] = []
    running_risk = 0.0

    for position in positions:
        remaining_risk = max_total_risk - running_risk

        if remaining_risk <= 0:
            break

        if position.max_loss <= remaining_risk:
            final_positions.append(position)
            running_risk += position.max_loss
            continue

        per_contract_risk = (
            position.max_loss / position.contracts
        )

        allowed_contracts = int(
            remaining_risk // per_contract_risk
        )

        if allowed_contracts <= 0:
            continue

        adjusted_loss = (
            allowed_contracts
            * per_contract_risk
        )

        final_positions.append(
            PortfolioPosition(
                trade=position.trade,
                contracts=allowed_contracts,
                max_loss=adjusted_loss,
                premium_deployed=adjusted_loss,
                risk_weight=position.risk_weight,
            )
        )

        running_risk += adjusted_loss

    return final_positions


def print_portfolio_plan(
    positions: List[PortfolioPosition],
    account_equity: float,
) -> None:
    print("\n" + "=" * 130)
    print(" PORTFOLIO PLAN")
    print("=" * 130)

    if not positions:
        print("No trades passed portfolio construction.")
        print("=" * 130)
        return

    print(
        f"{'Rank':<5}"
        f"{'Ticker':<8}"
        f"{'Direction':<10}"
        f"{'Contracts':>10}"
        f"{'Premium':>13}"
        f"{'Max Loss':>13}"
        f"{'TradeScore':>12}"
        f"  {'Option':<24}"
    )

    print("-" * 130)

    total_premium = 0.0
    total_risk = 0.0

    for rank, position in enumerate(positions, start=1):
        trade = position.trade

        print(
            f"{rank:<5}"
            f"{trade.stock_symbol:<8}"
            f"{trade.direction:<10}"
            f"{position.contracts:>10}"
            f"${position.premium_deployed:>12,.2f}"
            f"${position.max_loss:>12,.2f}"
            f"{trade.trade_score:>12.1f}"
            f"  {trade.option_symbol:<24}"
        )

        total_premium += position.premium_deployed
        total_risk += position.max_loss

    print("-" * 130)

    print(
        f"Total premium deployed: ${total_premium:,.2f}"
    )

    print(
        f"Total planned max loss: ${total_risk:,.2f} "
        f"({total_risk / account_equity:.2%})"
    )

    print(
        f"Account equity:          ${account_equity:,.2f}"
    )

    print(
        f"Remaining capital:       "
        f"${account_equity - total_premium:,.2f}"
    )

    print("=" * 130)