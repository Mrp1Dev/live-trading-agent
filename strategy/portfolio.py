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
    """Estimate the executable entry price or debit for sizing."""
    if hasattr(trade, "net_debit") and trade.net_debit > 0:
        return trade.net_debit
    if hasattr(trade, "net_credit") and trade.net_credit > 0:
        return trade.net_credit
    if hasattr(trade, "option_ask") and trade.option_ask > 0:
        return trade.option_ask
    if hasattr(trade, "ask") and trade.ask > 0:
        return trade.ask
    if hasattr(trade, "option_mid") and trade.option_mid > 0:
        return trade.option_mid
    return getattr(trade, "mid", 1.0)


def _per_contract_risk(trade) -> float:
    """Calculate defined maximum loss per contract (in dollars).

    - For Credit Spreads: Max loss = (Strike Width - Net Credit) × 100
    - For Debit Spreads: Max loss = Net Debit × 100
    - For Single Legs: Max loss = Premium Paid (Ask) × 100
    """
    if getattr(trade, "is_credit", False):
        max_loss = getattr(trade, "max_loss", 0.0)
        if max_loss <= 0:
            width = getattr(trade, "strike_width", 0.0)
            credit = getattr(trade, "net_credit", 0.0)
            max_loss = max(0.10, width - credit)
        return max_loss * 100.0

    if hasattr(trade, "max_loss") and trade.max_loss > 0:
        return trade.max_loss * 100.0

    entry_price = _estimated_entry_price(trade)
    if entry_price <= 0:
        return 10.0
    return entry_price * 100.0


def _trade_weight(
    trade: object,
    rank_index: int = 0,
    total_candidates: int = 1,
) -> float:
    """
    Convert trade priority into an allocation weight.

    Higher-ranked trades receive higher risk allocation weight.
    """
    if total_candidates <= 1:
        return 1.0

    # Rank-based decay weight: rank #1 gets the highest weight
    return float(max(1, total_candidates - rank_index) ** 1.5)


def select_best_trade_per_stock(
    trades: List[object],
) -> List[object]:
    """
    Only keep the highest-priority trade for each underlying.

    Preserves incoming LLM rank order so that the LLM's top choice
    for each underlying is retained and evaluated first.
    """
    # If trades have explicit option_llm_rank > 0, ensure sorted by rank
    has_llm_ranks = any(getattr(t, "option_llm_rank", 0) > 0 for t in trades)
    if has_llm_ranks:
        sorted_trades = sorted(
            trades,
            key=lambda t: getattr(t, "option_llm_rank", 0) if getattr(t, "option_llm_rank", 0) > 0 else float("inf"),
        )
    else:
        sorted_trades = trades

    best_trades: List[object] = []
    seen_symbols = set()

    for trade in sorted_trades:
        symbol = trade.stock_symbol
        if symbol not in seen_symbols:
            seen_symbols.add(symbol)
            best_trades.append(trade)

    return best_trades


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
    Convert ranked trades into a sized portfolio.

    Current instrument assumption:
        long calls / long puts.

    The portfolio is allocated by MAXIMUM LOSS, not buying power.
    Priority order is determined upstream by the LLM option ranker;
    the portfolio layer decides sizing and risk allocation ("how much").
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
    # 1. One trade per underlying, preserving LLM rank priority.
    # ---------------------------------------------------------

    eligible = select_best_trade_per_stock(trades)

    # ---------------------------------------------------------
    # 2. Keep only top N underlyings by LLM rank.
    # ---------------------------------------------------------

    eligible = eligible[:max_positions]

    if not eligible:
        return []

    # ---------------------------------------------------------
    # 3. Calculate relative weights based on priority.
    # ---------------------------------------------------------

    weighted_trades = []
    total_eligible = len(eligible)

    for rank_idx, trade in enumerate(eligible):
        weight = _trade_weight(
            trade,
            rank_index=rank_idx,
            total_candidates=total_eligible,
        )

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
    # 4. Convert risk budget into contracts.
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
    # 5. Ensure total risk never exceeds budget (preserving rank order).
    # ---------------------------------------------------------

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
        f"{'Rank':<6}"
        f"{'Ticker':<8}"
        f"{'Direction':<10}"
        f"{'Contracts':>10}"
        f"{'Premium':>13}"
        f"{'Max Loss':>13}"
        f"{'LLM Rank':>10}"
        f"  {'Option':<24}"
    )

    print("-" * 130)

    total_premium = 0.0
    total_risk = 0.0

    for rank, position in enumerate(positions, start=1):
        trade = position.trade
        llm_rank = getattr(trade, "option_llm_rank", None)
        llm_rank_str = f"#{llm_rank}" if llm_rank else f"#{rank}"

        print(
            f"#{rank:<5}"
            f"{trade.stock_symbol:<8}"
            f"{trade.direction:<10}"
            f"{position.contracts:>10}"
            f"${position.premium_deployed:>12,.2f}"
            f"${position.max_loss:>12,.2f}"
            f"{llm_rank_str:>10}"
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