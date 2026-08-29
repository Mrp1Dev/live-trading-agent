from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List

from config import (
    MAX_TOTAL_RISK_PCT,
    MAX_SINGLE_TRADE_RISK_PCT,
    MAX_UNDERLYING_RISK_PCT,
    MAX_DIRECTION_RISK_PCT,
    MAX_ABS_PORTFOLIO_DELTA,
    MAX_ABS_PORTFOLIO_GAMMA,
    MAX_ABS_PORTFOLIO_VEGA,
    EMERGENCY_DRAWDOWN_PCT,
)

from strategy.portfolio import PortfolioPosition


@dataclass
class RiskAssessment:
    approved: bool
    adjusted_contracts: int
    reason: str


@dataclass
class PortfolioRiskReport:
    approved_positions: List[PortfolioPosition]
    rejected_positions: List[PortfolioPosition]

    total_max_loss: float
    max_loss_pct: float

    bullish_risk_pct: float
    bearish_risk_pct: float

    portfolio_delta: float
    portfolio_gamma: float
    portfolio_vega: float
    portfolio_theta: float

    emergency_stop: bool

    warnings: List[str]


def _per_contract_loss(position: PortfolioPosition) -> float:
    if position.contracts <= 0:
        return 0.0

    return position.max_loss / position.contracts


def assess_position(
    position: PortfolioPosition,
    account_equity: float,
) -> RiskAssessment:
    """
    Validate and cap a single proposed position.
    """

    if account_equity <= 0:
        return RiskAssessment(
            approved=False,
            adjusted_contracts=0,
            reason="Invalid account equity.",
        )

    if position.contracts <= 0:
        return RiskAssessment(
            approved=False,
            adjusted_contracts=0,
            reason="Position has no contracts.",
        )

    per_contract_loss = _per_contract_loss(position)

    if per_contract_loss <= 0:
        return RiskAssessment(
            approved=False,
            adjusted_contracts=0,
            reason="Invalid per-contract loss.",
        )

    max_allowed_loss = (
        account_equity
        * MAX_SINGLE_TRADE_RISK_PCT
    )

    allowed_contracts = int(
        max_allowed_loss // per_contract_loss
    )

    if allowed_contracts <= 0:
        return RiskAssessment(
            approved=False,
            adjusted_contracts=0,
            reason="Single-trade risk limit exceeded.",
        )

    if allowed_contracts < position.contracts:
        return RiskAssessment(
            approved=True,
            adjusted_contracts=allowed_contracts,
            reason=(
                f"Reduced from {position.contracts} "
                f"to {allowed_contracts} contracts."
            ),
        )

    return RiskAssessment(
        approved=True,
        adjusted_contracts=position.contracts,
        reason="Passed position-level limits.",
    )


def _adjust_position(
    position: PortfolioPosition,
    contracts: int,
) -> PortfolioPosition:
    per_contract_loss = _per_contract_loss(position)

    new_loss = contracts * per_contract_loss

    return PortfolioPosition(
        trade=position.trade,
        contracts=contracts,
        max_loss=new_loss,
        premium_deployed=new_loss,
        risk_weight=position.risk_weight,
    )


def _directional_risk(
    positions: List[PortfolioPosition],
) -> tuple[float, float]:
    bullish = sum(
        p.max_loss
        for p in positions
        if p.trade.direction == "BULLISH"
    )

    bearish = sum(
        p.max_loss
        for p in positions
        if p.trade.direction == "BEARISH"
    )

    return bullish, bearish


def _calculate_greeks(
    positions: List[PortfolioPosition],
) -> tuple[float, float, float, float]:
    delta = 0.0
    gamma = 0.0
    vega = 0.0
    theta = 0.0

    for position in positions:
        trade = position.trade
        multiplier = position.contracts * 100

        delta += (
            (trade.option_delta or 0.0)
            * multiplier
        )

        gamma += (
            (trade.option_gamma or 0.0)
            * multiplier
        )

        vega += (
            (trade.option_vega or 0.0)
            * multiplier
        )

        theta += (
            (trade.option_theta or 0.0)
            * multiplier
        )

    return delta, gamma, vega, theta


def _enforce_directional_limits(
    positions: List[PortfolioPosition],
    max_direction_risk: float,
) -> tuple[List[PortfolioPosition], List[PortfolioPosition], List[str]]:
    """
    Enforce hard directional risk limits.

    Positions are considered independently for bullish and bearish
    exposure. Within each direction, higher trade-score positions
    receive priority.

    The returned approved set is guaranteed not to exceed the
    directional risk limit.
    """

    approved: List[PortfolioPosition] = []
    rejected: List[PortfolioPosition] = []
    warnings: List[str] = []

    for direction in ("BULLISH", "BEARISH"):

        direction_positions = [
            position
            for position in positions
            if position.trade.direction == direction
        ]

        direction_positions.sort(
            key=lambda position: position.trade.trade_score,
            reverse=True,
        )

        running_risk = 0.0

        for position in direction_positions:

            proposed_risk = (
                running_risk
                + position.max_loss
            )

            if proposed_risk <= max_direction_risk:
                approved.append(position)
                running_risk = proposed_risk
                continue

            rejected.append(position)

            warnings.append(
                f"{position.trade.stock_symbol}: "
                f"{direction.lower()} concentration limit "
                "would be exceeded; position rejected."
            )

    return approved, rejected, warnings


def assess_portfolio(
    positions: List[PortfolioPosition],
    account_equity: float,
    current_equity: float | None = None,
    peak_equity: float | None = None,
) -> PortfolioRiskReport:

    if account_equity <= 0:
        raise ValueError(
            "account_equity must be positive"
        )

    for position in positions:
        if position.contracts <= 0:
            raise ValueError(
                f"Invalid portfolio position for "
                f"{position.trade.stock_symbol}: "
                "contracts must be positive."
            )
        if position.max_loss <= 0:
            raise ValueError(
                f"Invalid portfolio position for "
                f"{position.trade.stock_symbol}: "
                "max_loss must be positive."
            )

    warnings: List[str] = []

    # ========================================================
    # EMERGENCY ACCOUNT PROTECTION
    # ========================================================

    if (
        current_equity is not None
        and peak_equity is not None
    ):

        if peak_equity <= 0:
            raise ValueError(
                "peak_equity must be positive."
            )

        drawdown_pct = (
            peak_equity - current_equity
        ) / peak_equity

        if drawdown_pct >= EMERGENCY_DRAWDOWN_PCT:

            return PortfolioRiskReport(
                approved_positions=[],
                rejected_positions=positions,
                total_max_loss=0.0,
                max_loss_pct=0.0,
                bullish_risk_pct=0.0,
                bearish_risk_pct=0.0,
                portfolio_delta=0.0,
                portfolio_gamma=0.0,
                portfolio_vega=0.0,
                portfolio_theta=0.0,
                emergency_stop=True,
                warnings=[
                    (
                        f"Account drawdown is "
                        f"{drawdown_pct:.2%} from peak equity "
                        f"of ${peak_equity:,.2f}."
                    )
                ],
            )

    # ========================================================
    # STEP 1 — POSITION-LEVEL LIMITS
    # ========================================================

    approved: List[PortfolioPosition] = []
    rejected: List[PortfolioPosition] = []

    for position in positions:

        assessment = assess_position(
            position,
            account_equity,
        )

        if not assessment.approved:
            rejected.append(position)
            warnings.append(
                f"{position.trade.stock_symbol}: "
                f"{assessment.reason}"
            )
            continue

        approved.append(
            _adjust_position(
                position,
                assessment.adjusted_contracts,
            )
        )

        if assessment.adjusted_contracts < position.contracts:
            warnings.append(
                f"{position.trade.stock_symbol}: "
                f"{assessment.reason}"
            )

    # ========================================================
    # STEP 2 — UNDERLYING CONCENTRATION
    # ========================================================

    max_underlying_risk = (
        account_equity
        * MAX_UNDERLYING_RISK_PCT
    )

    final_positions: List[PortfolioPosition] = []

    for position in approved:

        if position.max_loss <= max_underlying_risk:
            final_positions.append(position)
            continue

        per_contract_loss = _per_contract_loss(
            position
        )

        contracts = int(
            max_underlying_risk
            // per_contract_loss
        )

        if contracts <= 0:
            rejected.append(position)
            warnings.append(
                f"{position.trade.stock_symbol}: "
                "rejected by underlying-risk limit."
            )
            continue

        adjusted = _adjust_position(
            position,
            contracts,
        )

        final_positions.append(adjusted)

        warnings.append(
            f"{position.trade.stock_symbol}: "
            f"reduced to {contracts} contracts "
            "by underlying-risk limit."
        )

    # ========================================================
    # STEP 3 — TOTAL PORTFOLIO RISK
    # ========================================================

    max_total_risk = (
        account_equity
        * MAX_TOTAL_RISK_PCT
    )

    final_positions.sort(
        key=lambda p: p.trade.trade_score,
        reverse=True,
    )

    risk_limited: List[PortfolioPosition] = []

    total_risk = 0.0

    for position in final_positions:

        remaining = (
            max_total_risk
            - total_risk
        )

        if remaining <= 0:
            rejected.append(position)
            continue

        if position.max_loss <= remaining:
            risk_limited.append(position)
            total_risk += position.max_loss
            continue

        per_contract_loss = _per_contract_loss(
            position
        )

        contracts = int(
            remaining
            // per_contract_loss
        )

        if contracts <= 0:
            rejected.append(position)
            continue

        adjusted = _adjust_position(
            position,
            contracts,
        )

        risk_limited.append(adjusted)
        total_risk += adjusted.max_loss

        warnings.append(
            f"{position.trade.stock_symbol}: "
            "reduced by total portfolio risk limit."
        )

    # ========================================================
    # STEP 4 — DIRECTIONAL CONCENTRATION
    # ========================================================

    max_direction_risk = (
        account_equity
        * MAX_DIRECTION_RISK_PCT
    )

    (
        risk_limited,
        directional_rejected,
        directional_warnings,
    ) = _enforce_directional_limits(
        positions=risk_limited,
        max_direction_risk=max_direction_risk,
    )

    rejected.extend(directional_rejected)
    warnings.extend(directional_warnings)

    # ========================================================
    # STEP 5 — GREEKS
    # ========================================================

    delta, gamma, vega, theta = _calculate_greeks(
        risk_limited
    )

    # Greeks are currently advisory rather than hard vetoes.
    # The hard safety controls are defined-loss and concentration limits.
    if abs(delta) > MAX_ABS_PORTFOLIO_DELTA:
        warnings.append(
            f"Portfolio delta above advisory threshold: {delta:.1f}"
        )

    if abs(gamma) > MAX_ABS_PORTFOLIO_GAMMA:
        warnings.append(
            f"Portfolio gamma above advisory threshold: {gamma:.1f}"
        )

    if abs(vega) > MAX_ABS_PORTFOLIO_VEGA:
        warnings.append(
            f"Portfolio vega above advisory threshold: {vega:.1f}"
        )

    # ========================================================
    # FINAL METRICS
    # ========================================================

    total_risk = sum(
        p.max_loss
        for p in risk_limited
    )

    bullish, bearish = _directional_risk(
        risk_limited
    )

    return PortfolioRiskReport(
        approved_positions=risk_limited,
        rejected_positions=rejected,
        total_max_loss=total_risk,
        max_loss_pct=(
            total_risk
            / account_equity
        ),
        bullish_risk_pct=(
            bullish
            / account_equity
        ),
        bearish_risk_pct=(
            bearish
            / account_equity
        ),
        portfolio_delta=delta,
        portfolio_gamma=gamma,
        portfolio_vega=vega,
        portfolio_theta=theta,
        emergency_stop=False,
        warnings=warnings,
    )


def print_risk_report(
    report: PortfolioRiskReport,
    account_equity: float,
) -> None:

    print("\n" + "=" * 130)
    print(" RISK ASSESSMENT")
    print("=" * 130)

    if report.emergency_stop:
        print(
            "🚨 EMERGENCY STOP — "
            "NO NEW POSITIONS APPROVED"
        )

        for warning in report.warnings:
            print(f"  ⚠ {warning}")

        print("=" * 130)
        return

    print(
        f"Total max loss:  "
        f"${report.total_max_loss:,.2f} "
        f"({report.max_loss_pct:.2%})"
    )

    print(
        f"Bullish risk:    "
        f"{report.bullish_risk_pct:.2%}"
    )

    print(
        f"Bearish risk:    "
        f"{report.bearish_risk_pct:.2%}"
    )

    print(
        f"Portfolio Delta: "
        f"{report.portfolio_delta:,.2f}"
    )

    print(
        f"Portfolio Gamma: "
        f"{report.portfolio_gamma:,.2f}"
    )

    print(
        f"Portfolio Vega:  "
        f"{report.portfolio_vega:,.2f}"
    )

    print(
        f"Portfolio Theta: "
        f"{report.portfolio_theta:,.2f}"
    )

    print("\nAPPROVED POSITIONS")
    print("-" * 130)

    for position in report.approved_positions:

        trade = position.trade

        print(
            f"{trade.stock_symbol:<7} "
            f"{trade.direction:<9} "
            f"{position.contracts:>3} contracts  "
            f"risk=${position.max_loss:>8,.2f}  "
            f"score={trade.trade_score:>5.1f}  "
            f"{trade.option_symbol}"
        )

    print("\nREJECTED POSITIONS")
    print("-" * 130)

    for position in report.rejected_positions:

        trade = position.trade

        print(
            f"{trade.stock_symbol:<7} "
            f"{trade.direction:<9} "
            f"{position.contracts:>3} contracts  "
            f"risk=${position.max_loss:>8,.2f}  "
            f"score={trade.trade_score:>5.1f}  "
            f"{trade.option_symbol}"
        )

    if report.warnings:
        print("\nWARNINGS")
        print("-" * 130)

        for warning in report.warnings:
            print(f"⚠ {warning}")

    print("=" * 130)