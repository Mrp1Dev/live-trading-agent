from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

from alpaca.data.historical.option import OptionsSnapshot

from config import (
    MIN_DTE,
    MAX_DTE,
    MAX_OPTION_SPREAD_PCT,
    MIN_OPTION_ABS_DELTA,
    MAX_OPTION_ABS_DELTA,
    MIN_OPTION_PREMIUM,
    LATEST_FORBIDDEN_EXPIRATION,
)


@dataclass
class OptionCandidate:
    symbol: str
    expiration: date
    option_type: str          # "call" or "put"
    strike: float

    bid: float
    ask: float
    mid: float
    spread_pct: float

    iv: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    theta: Optional[float]
    vega: Optional[float]

    dte: int
    moneyness_pct: float

    score: float = 0.0


def parse_occ_symbol(symbol: str) -> tuple[date, str, float]:
    """
    Parse a standard OCC option symbol.

    Example:
        NVDA260904C00217500

    Returns:
        (expiration_date, option_type, strike)
    """
    if len(symbol) < 15:
        raise ValueError(f"Invalid option symbol: {symbol}")

    # Standard OCC format:
    # ROOT + YYMMDD + C/P + 8-digit strike * 1000

    expiration_raw = symbol[-15:-9]
    option_type_raw = symbol[-9]
    strike_raw = symbol[-8:]

    expiration = datetime.strptime(expiration_raw, "%y%m%d").date()

    if option_type_raw == "C":
        option_type = "call"
    elif option_type_raw == "P":
        option_type = "put"
    else:
        raise ValueError(f"Unknown option type in symbol: {symbol}")

    strike = int(strike_raw) / 1000.0

    return expiration, option_type, strike


def build_candidate(
    symbol: str,
    snapshot: OptionsSnapshot,
    underlying_price: float,
) -> Optional[OptionCandidate]:
    """
    Convert an Alpaca option snapshot into a normalized candidate.
    """

    if underlying_price <= 0:
        return None

    quote = snapshot.latest_quote
    if quote is None:
        return None

    bid = float(quote.bid_price or 0)
    ask = float(quote.ask_price or 0)

    # Ignore broken/unusable quotes.
    if bid <= 0 or ask <= 0 or ask < bid:
        return None

    mid = (bid + ask) / 2.0

    if mid <= 0:
        return None

    spread_pct = (ask - bid) / mid

    try:
        expiration, option_type, strike = parse_occ_symbol(symbol)
    except ValueError:
        return None

    today = date.today()
    dte = (expiration - today).days

    if dte < 0:
        return None

    moneyness_pct = (strike - underlying_price) / underlying_price

    greeks = snapshot.greeks

    return OptionCandidate(
        symbol=symbol,
        expiration=expiration,
        option_type=option_type,
        strike=strike,
        bid=bid,
        ask=ask,
        mid=mid,
        spread_pct=spread_pct,
        iv=(
            float(snapshot.implied_volatility)
            if snapshot.implied_volatility is not None
            else None
        ),
        delta=(
            float(greeks.delta)
            if greeks is not None and greeks.delta is not None
            else None
        ),
        gamma=(
            float(greeks.gamma)
            if greeks is not None and greeks.gamma is not None
            else None
        ),
        theta=(
            float(greeks.theta)
            if greeks is not None and greeks.theta is not None
            else None
        ),
        vega=(
            float(greeks.vega)
            if greeks is not None and greeks.vega is not None
            else None
        ),
        dte=dte,
        moneyness_pct=moneyness_pct,
    )


OPTION_SCORE_MAX = 80.0


def _score_candidate(
    candidate: OptionCandidate,
    direction: str,
) -> float:
    """
    Score an option for a directional trade.

    This is deliberately a simple first version.
    We will later replace this with a much better quantitative model.
    """

    direction = direction.lower()

    score = 0.0

    # ---------------------------------------------------------
    # 1. Delta suitability
    # ---------------------------------------------------------
    if candidate.delta is not None:
        abs_delta = abs(candidate.delta)

        # Prefer reasonably responsive options.
        if 0.35 <= abs_delta <= 0.65:
            score += 35.0
        elif 0.25 <= abs_delta <= 0.75:
            score += 25.0
        elif 0.15 <= abs_delta <= 0.85:
            score += 10.0

    # ---------------------------------------------------------
    # 2. Time to expiry
    # ---------------------------------------------------------
    # We want short duration, but not zero-time lottery tickets.
    if 2 <= candidate.dte <= 7:
        score += 20.0
    elif 1 <= candidate.dte <= 10:
        score += 12.0
    elif candidate.dte <= 21:
        score += 5.0

    # ---------------------------------------------------------
    # 4. Moneyness
    # ---------------------------------------------------------
    abs_moneyness = abs(candidate.moneyness_pct)

    if abs_moneyness <= 0.03:
        score += 15.0
    elif abs_moneyness <= 0.07:
        score += 10.0
    elif abs_moneyness <= 0.12:
        score += 4.0

    # ---------------------------------------------------------
    # 5. Price sanity
    # ---------------------------------------------------------
    # Avoid ultra-cheap contracts for now.
    if candidate.mid >= 1.00:
        score += 5.0
    elif candidate.mid >= 0.50:
        score += 2.0

    # ---------------------------------------------------------
    # Direction sanity
    # ---------------------------------------------------------
    if candidate.delta is not None:
        if direction == "bullish" and candidate.delta > 0:
            score += 5.0

        elif direction == "bearish" and candidate.delta < 0:
            score += 5.0

        else:
            score -= 20.0

    return (score / OPTION_SCORE_MAX) * 100.0


def select_directional_options(
    chain: dict[str, OptionsSnapshot],
    underlying_price: float,
    direction: str = "bullish",
    min_dte: int = MIN_DTE,
    max_dte: int = MAX_DTE,
    max_spread_pct: float = MAX_OPTION_SPREAD_PCT,
    min_abs_delta: float = MIN_OPTION_ABS_DELTA,
    max_abs_delta: float = MAX_OPTION_ABS_DELTA,
    min_premium: float = MIN_OPTION_PREMIUM,
    max_candidates: int = 30,
) -> list[OptionCandidate]:
    """
    Select reasonably tradeable directional option candidates.

    Parameters are intentionally conservative/simple for the first version.
    """

    direction = direction.lower()

    if direction not in {"bullish", "bearish"}:
        raise ValueError("direction must be 'bullish' or 'bearish'")

    candidates: list[OptionCandidate] = []

    for symbol, snapshot in chain.items():
        candidate = build_candidate(
            symbol=symbol,
            snapshot=snapshot,
            underlying_price=underlying_price,
        )

        if candidate is None:
            continue

        # -----------------------------------------------------
        # Expiration
        # -----------------------------------------------------
        if candidate.dte < min_dte or candidate.dte > max_dte:
            continue

        # Do not initiate positions that can reach expiration
        # during the official measurement endpoint.
        if candidate.expiration <= LATEST_FORBIDDEN_EXPIRATION:
            continue

        # -----------------------------------------------------
        # Premium
        # -----------------------------------------------------
        if candidate.mid < min_premium:
            continue

        # -----------------------------------------------------
        # Liquidity / spread
        # -----------------------------------------------------
        if candidate.spread_pct > max_spread_pct:
            continue

        # -----------------------------------------------------
        # Greeks
        # -----------------------------------------------------
        if candidate.delta is None:
            continue

        abs_delta = abs(candidate.delta)

        if abs_delta < min_abs_delta:
            continue

        if abs_delta > max_abs_delta:
            continue

        # For now, calls for bullish and puts for bearish.
        if direction == "bullish" and candidate.option_type != "call":
            continue

        if direction == "bearish" and candidate.option_type != "put":
            continue

        candidate.score = _score_candidate(
            candidate=candidate,
            direction=direction,
        )

        candidates.append(candidate)

    candidates.sort(key=lambda x: x.score, reverse=True)

    return candidates[:max_candidates]


def print_option_candidates(
    symbol: str,
    underlying_price: float,
    candidates: list[OptionCandidate],
    direction: str,
) -> None:
    """
    Print selected option candidates in a readable table.
    """

    print()
    print("=" * 125)
    print(f" {symbol} OPTION CANDIDATES — {direction.upper()}")
    print("=" * 125)

    print(
        f"{'Rank':<5}"
        f"{'Contract':<22}"
        f"{'DTE':>5}"
        f"{'Strike':>9}"
        f"{'Mid':>9}"
        f"{'Spread':>9}"
        f"{'IV':>9}"
        f"{'Delta':>9}"
        f"{'Mny':>9}"
        f"{'Score':>9}"
    )

    print("-" * 125)

    for rank, candidate in enumerate(candidates, start=1):
        iv_str = (
            f"{candidate.iv:.1%}"
            if candidate.iv is not None
            else "N/A"
        )

        delta_str = (
            f"{candidate.delta:+.3f}"
            if candidate.delta is not None
            else "N/A"
        )

        print(
            f"{rank:<5}"
            f"{candidate.symbol:<22}"
            f"{candidate.dte:>5}"
            f"{candidate.strike:>9.2f}"
            f"{candidate.mid:>9.2f}"
            f"{candidate.spread_pct:>8.1%}"
            f"{iv_str:>9}"
            f"{delta_str:>9}"
            f"{candidate.moneyness_pct:>+8.1%}"
            f"{candidate.score:>9.1f}"
        )

    print("=" * 125)
    print()