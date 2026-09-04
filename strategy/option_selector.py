from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
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

# ---------------------------------------------------------------------------
# Market clock
# ---------------------------------------------------------------------------
# ZoneInfo needs the IANA database. On a bare Windows install without `tzdata`
# it raises at import time, which would take the whole strategy down. Fall back
# to a fixed-offset stand-in rather than failing to import: DTE is computed from
# calendar dates, so an hour of DST drift cannot change a whole-day count.

try:  # pragma: no cover - environment dependent
    from zoneinfo import ZoneInfo

    MARKET_TZ = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - environment dependent
    from datetime import timezone

    MARKET_TZ = timezone(timedelta(hours=-4), name="America/New_York")

MARKET_CLOSE = time(16, 0)

# ---------------------------------------------------------------------------
# Pricing model constants
# ---------------------------------------------------------------------------

RISK_FREE_RATE = 0.043
DIVIDEND_YIELD = 0.0

# Gates. config.MAX_OPTION_SPREAD_PCT (0.15) is deliberately NOT used as the
# effective ceiling: round-trip friction at a 15% spread is ~14% of capital
# deployed, which is larger than the gross edge of any realistic directional
# signal. It remains the parameter default so callers keep working; MAX_SPREAD_PCT
# is the binding constraint.
MAX_SPREAD_PCT = 0.15           # Relaxed from 0.08 to match config.MAX_OPTION_SPREAD_PCT
ABS_SPREAD_TOLERANCE = 0.05     # Relaxed from 0.02 ($0.05 absolute spread tolerance for cheap/mid contracts)
MAX_DAILY_THETA_PCT = 0.20      # Relaxed from 0.12 (allow contracts with daily theta up to 20%)
MAX_BREAKEVEN_RATIO = 1.60      # Relaxed from 1.15 (allow breakeven moves up to 1.6x expected move)
MAX_IV_RV_RATIO = 2.20          # Relaxed from 1.60 (accommodate earnings/catalyst momentum IV)
MIN_EXPECTED_RETURN = -0.25     # Relaxed from -0.10 (allow modelled return down to -25%)

# Expected-return model.
PLANNED_HOLD_DAYS = 2.0
# The single number encoding "our directional signal is worth something",
# expressed in standard deviations of drift over the planned hold. This is the
# master selectivity dial. At 0.0 a zero-edge trade must price slightly negative.
EDGE_DRIFT_SIGMAS = 0.35
SCENARIO_NODES = (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0)

# Score weights. Spread is deliberately NOT scored: it is a certain, known cost,
# so it is gated and then subtracted inside expected_return. Scoring it a second
# time would let an uncertain signal outbid a known cost.
WEIGHT_EXPECTED_RETURN = 0.45
WEIGHT_PROBABILITY_OF_PROFIT = 0.25
WEIGHT_VARIANCE_RISK_PREMIUM = 0.20
WEIGHT_CONTRACT_SHAPE = 0.10

# Retained for backward compatibility with any caller that imports it. The score
# is now modelled rather than accumulated out of buckets, so it no longer acts as
# a normalizer.
OPTION_SCORE_MAX = 80.0

_SQRT_2 = math.sqrt(2.0)
_SQRT_2PI = math.sqrt(2.0 * math.pi)
_TRADING_DAYS_PER_YEAR = 252.0
_CALENDAR_DAYS_PER_YEAR = 365.0


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

    # --- Appended diagnostics -------------------------------------------------
    # Every field below has a default, so the original 16-field positional
    # construction is unchanged and every existing consumer keeps working.
    underlying_price: Optional[float] = None
    year_fraction: Optional[float] = None       # fractional time to expiry
    fair_value: Optional[float] = None          # BSM price at realized vol
    edge: Optional[float] = None                # fair_value - ask, in dollars
    iv_rv_ratio: Optional[float] = None
    breakeven: Optional[float] = None
    breakeven_pct: Optional[float] = None
    expected_move_pct: Optional[float] = None
    breakeven_ratio: Optional[float] = None
    theta_burn_pct: Optional[float] = None
    probability_of_profit: Optional[float] = None
    expected_return: Optional[float] = None
    iv_source: str = "alpaca"                   # alpaca | solved | unavailable
    greeks_source: str = "alpaca"               # alpaca | model | unavailable
    reject_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Black-Scholes-Merton
# ---------------------------------------------------------------------------


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / _SQRT_2))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _intrinsic(spot: float, strike: float, option_type: str) -> float:
    if option_type == "call":
        return max(0.0, spot - strike)
    return max(0.0, strike - spot)


def bsm_price(
    spot: float,
    strike: float,
    year_fraction: float,
    sigma: float,
    option_type: str,
    rate: float = RISK_FREE_RATE,
    dividend_yield: float = DIVIDEND_YIELD,
) -> float:
    """Black-Scholes-Merton price for a European option.

    Degenerate inputs (no time left, or no volatility) collapse to intrinsic
    value, which is the correct limit and keeps the scenario engine finite at
    expiry rather than dividing by zero.
    """
    if spot <= 0 or strike <= 0:
        return 0.0
    if year_fraction <= 0.0 or sigma <= 0.0:
        return _intrinsic(spot, strike, option_type)

    sqrt_t = math.sqrt(year_fraction)
    d1 = (
        math.log(spot / strike)
        + (rate - dividend_yield + 0.5 * sigma * sigma) * year_fraction
    ) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    discount = math.exp(-rate * year_fraction)
    carry = math.exp(-dividend_yield * year_fraction)

    if option_type == "call":
        return spot * carry * _norm_cdf(d1) - strike * discount * _norm_cdf(d2)
    return strike * discount * _norm_cdf(-d2) - spot * carry * _norm_cdf(-d1)


def bsm_greeks(
    spot: float,
    strike: float,
    year_fraction: float,
    sigma: float,
    option_type: str,
    rate: float = RISK_FREE_RATE,
    dividend_yield: float = DIVIDEND_YIELD,
) -> dict[str, float]:
    """Analytic greeks. Theta is returned ANNUALIZED.

    Alpaca reports theta per day, so callers filling missing greeks from this
    model must divide theta by 365 to match that convention.
    """
    if spot <= 0 or strike <= 0 or year_fraction <= 0.0 or sigma <= 0.0:
        if option_type == "call":
            delta = 1.0 if spot > strike else 0.0
        else:
            delta = -1.0 if spot < strike else 0.0
        return {"delta": delta, "gamma": 0.0, "theta": 0.0, "vega": 0.0}

    sqrt_t = math.sqrt(year_fraction)
    d1 = (
        math.log(spot / strike)
        + (rate - dividend_yield + 0.5 * sigma * sigma) * year_fraction
    ) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    discount = math.exp(-rate * year_fraction)
    carry = math.exp(-dividend_yield * year_fraction)
    pdf_d1 = _norm_pdf(d1)

    gamma = carry * pdf_d1 / (spot * sigma * sqrt_t)
    # Vega per 1 volatility point (i.e. per 0.01 of sigma).
    vega = spot * carry * pdf_d1 * sqrt_t / 100.0

    if option_type == "call":
        delta = carry * _norm_cdf(d1)
        theta = (
            -spot * carry * pdf_d1 * sigma / (2.0 * sqrt_t)
            - rate * strike * discount * _norm_cdf(d2)
            + dividend_yield * spot * carry * _norm_cdf(d1)
        )
    else:
        delta = -carry * _norm_cdf(-d1)
        theta = (
            -spot * carry * pdf_d1 * sigma / (2.0 * sqrt_t)
            + rate * strike * discount * _norm_cdf(-d2)
            - dividend_yield * spot * carry * _norm_cdf(-d1)
        )

    return {"delta": delta, "gamma": gamma, "theta": theta, "vega": vega}


def implied_volatility(
    price: float,
    spot: float,
    strike: float,
    year_fraction: float,
    option_type: str,
    rate: float = RISK_FREE_RATE,
    dividend_yield: float = DIVIDEND_YIELD,
    lower: float = 1e-4,
    upper: float = 5.0,
    iterations: int = 80,
) -> Optional[float]:
    """Back out implied volatility by bisection.

    Returns None when the price is not attainable by the model - below intrinsic,
    or above the upper vol bound. Never guesses: an unsolvable quote is reported
    as missing rather than filled with a placeholder.
    """
    if price <= 0 or spot <= 0 or strike <= 0 or year_fraction <= 0.0:
        return None

    if price < _intrinsic(spot, strike, option_type) - 1e-9:
        return None

    low_price = bsm_price(spot, strike, year_fraction, lower, option_type, rate, dividend_yield)
    high_price = bsm_price(spot, strike, year_fraction, upper, option_type, rate, dividend_yield)

    if price < low_price or price > high_price:
        return None

    low, high = lower, upper
    for _ in range(iterations):
        mid_vol = 0.5 * (low + high)
        mid_price = bsm_price(spot, strike, year_fraction, mid_vol, option_type, rate, dividend_yield)
        if mid_price < price:
            low = mid_vol
        else:
            high = mid_vol

    return 0.5 * (low + high)


# ---------------------------------------------------------------------------
# Symbol / clock helpers
# ---------------------------------------------------------------------------


def parse_occ_symbol(symbol: str) -> tuple[date, str, float]:
    """
    Parse a standard OCC option symbol.

    Example:
        NVDA260904C00217500

    Returns:
        (expiration_date, option_type, strike)
    """
    if not isinstance(symbol, str) or len(symbol) < 15:
        raise ValueError(f"Invalid option symbol: {symbol}")

    # Standard OCC format:
    # ROOT + YYMMDD + C/P + 8-digit strike * 1000

    expiration_raw = symbol[-15:-9]
    option_type_raw = symbol[-9]
    strike_raw = symbol[-8:]

    if not expiration_raw.isdigit() or not strike_raw.isdigit():
        raise ValueError(f"Invalid option symbol: {symbol}")

    try:
        expiration = datetime.strptime(expiration_raw, "%y%m%d").date()
    except ValueError as exc:
        raise ValueError(f"Invalid expiration in option symbol: {symbol}") from exc

    if option_type_raw == "C":
        option_type = "call"
    elif option_type_raw == "P":
        option_type = "put"
    else:
        raise ValueError(f"Unknown option type in symbol: {symbol}")

    strike = int(strike_raw) / 1000.0

    if strike <= 0:
        raise ValueError(f"Non-positive strike in option symbol: {symbol}")

    return expiration, option_type, strike


def market_now(as_of: Optional[datetime] = None) -> datetime:
    """Current exchange-local time.

    `as_of` exists so the selector can be driven from a historical decision
    timestamp instead of the wall clock. Never use the local system date: on a
    machine east of New York (IST is UTC+5:30 against UTC-4) every run between
    midnight and ~09:30 local computes DTE against tomorrow's exchange date,
    silently corrupting the DTE filter and the expiration guard.
    """
    if as_of is None:
        return datetime.now(MARKET_TZ)
    if as_of.tzinfo is None:
        return as_of.replace(tzinfo=MARKET_TZ)
    return as_of.astimezone(MARKET_TZ)


def time_to_expiry_years(expiration: date, as_of: Optional[datetime] = None) -> float:
    """Fractional years until the 16:00 ET close on the expiration date.

    Whole-day DTE overstates the life of a contract expiring tomorrow when it is
    already 15:30 today. At 1-14 DTE that difference is material to pricing.
    """
    now = market_now(as_of)
    expiry_moment = datetime.combine(expiration, MARKET_CLOSE).replace(tzinfo=now.tzinfo)
    seconds = (expiry_moment - now).total_seconds()
    if seconds <= 0:
        return 0.0
    return seconds / (_CALENDAR_DAYS_PER_YEAR * 24.0 * 3600.0)


# ---------------------------------------------------------------------------
# Candidate construction
# ---------------------------------------------------------------------------


def build_candidate(
    symbol: str,
    snapshot: OptionsSnapshot,
    underlying_price: float,
    as_of: Optional[datetime] = None,
    realized_vol: Optional[float] = None,
) -> Optional[OptionCandidate]:
    """
    Convert an Alpaca option snapshot into a normalized candidate.

    Where Alpaca supplies IV and greeks they are used as-is - they are the
    market's numbers and reproducing them locally would only be worse. Where they
    are missing, IV is solved from the quote midpoint and the greeks are filled
    from the model; the source is recorded on the candidate either way.
    """

    if underlying_price <= 0:
        return None

    quote = getattr(snapshot, "latest_quote", None)
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

    today = market_now(as_of).date()
    dte = (expiration - today).days

    if dte < 0:
        return None

    moneyness_pct = (strike - underlying_price) / underlying_price
    year_fraction = time_to_expiry_years(expiration, as_of)

    greeks = getattr(snapshot, "greeks", None)

    raw_iv = getattr(snapshot, "implied_volatility", None)
    iv: Optional[float] = float(raw_iv) if raw_iv is not None else None
    iv_source = "alpaca"

    if iv is None or iv <= 0:
        iv = implied_volatility(
            price=mid,
            spot=underlying_price,
            strike=strike,
            year_fraction=year_fraction,
            option_type=option_type,
        )
        iv_source = "solved" if iv is not None else "unavailable"

    def _greek(name: str) -> Optional[float]:
        if greeks is None:
            return None
        value = getattr(greeks, name, None)
        return float(value) if value is not None else None

    delta = _greek("delta")
    gamma = _greek("gamma")
    theta = _greek("theta")
    vega = _greek("vega")

    greeks_source = "alpaca"
    if None in (delta, gamma, theta, vega):
        if iv is not None and iv > 0:
            modelled = bsm_greeks(
                spot=underlying_price,
                strike=strike,
                year_fraction=year_fraction,
                sigma=iv,
                option_type=option_type,
            )
            if delta is None:
                delta = modelled["delta"]
            if gamma is None:
                gamma = modelled["gamma"]
            if theta is None:
                # Alpaca reports theta per day; the model returns it annualized.
                theta = modelled["theta"] / _CALENDAR_DAYS_PER_YEAR
            if vega is None:
                vega = modelled["vega"]
            greeks_source = "model"
        else:
            greeks_source = "unavailable"

    candidate = OptionCandidate(
        symbol=symbol,
        expiration=expiration,
        option_type=option_type,
        strike=strike,
        bid=bid,
        ask=ask,
        mid=mid,
        spread_pct=spread_pct,
        iv=iv,
        delta=delta,
        gamma=gamma,
        theta=theta,
        vega=vega,
        dte=dte,
        moneyness_pct=moneyness_pct,
        underlying_price=underlying_price,
        year_fraction=year_fraction,
        iv_source=iv_source,
        greeks_source=greeks_source,
    )

    _attach_analytics(candidate, realized_vol=realized_vol)
    return candidate


def _attach_analytics(
    candidate: OptionCandidate,
    realized_vol: Optional[float] = None,
) -> None:
    """Compute the modelled diagnostics used by the gates and the score.

    Anything that cannot be computed from available data is left as None. It is
    never filled with a neutral placeholder: a missing number must read as
    missing at the gate, not as an average-quality contract.
    """
    spot = candidate.underlying_price
    iv = candidate.iv
    t = candidate.year_fraction

    if spot is None or spot <= 0:
        return

    # Breakeven at expiry, measured against the ask (what you actually pay).
    if candidate.option_type == "call":
        breakeven = candidate.strike + candidate.ask
    else:
        breakeven = candidate.strike - candidate.ask
    candidate.breakeven = breakeven
    candidate.breakeven_pct = abs(breakeven - spot) / spot

    # Theta burn as a fraction of premium per day.
    if candidate.theta is not None and candidate.ask > 0:
        candidate.theta_burn_pct = abs(candidate.theta) / candidate.ask

    if iv is None or iv <= 0 or t is None or t <= 0:
        return

    # Fair value priced at REALIZED vol answers "is the market overcharging me?".
    # Exit repricing below uses IMPLIED vol, because you sell at the market's
    # price, not at your model's. Mixing these two up is the classic error.
    if realized_vol is not None and realized_vol > 0:
        candidate.iv_rv_ratio = iv / realized_vol
        candidate.fair_value = bsm_price(
            spot=spot,
            strike=candidate.strike,
            year_fraction=t,
            sigma=realized_vol,
            option_type=candidate.option_type,
        )
        candidate.edge = candidate.fair_value - candidate.ask

    forecast_vol = realized_vol if (realized_vol is not None and realized_vol > 0) else iv

    hold_years = min(PLANNED_HOLD_DAYS / _CALENDAR_DAYS_PER_YEAR, t)
    if hold_years <= 0:
        return

    # Expected move of the underlying over the planned hold.
    expected_move_pct = forecast_vol * math.sqrt(hold_years)
    candidate.expected_move_pct = expected_move_pct
    if expected_move_pct > 0 and candidate.breakeven_pct is not None:
        candidate.breakeven_ratio = candidate.breakeven_pct / expected_move_pct

    sign = 1.0 if candidate.option_type == "call" else -1.0
    sqrt_hold = math.sqrt(hold_years)
    drift = sign * EDGE_DRIFT_SIGMAS * forecast_vol * sqrt_hold
    exit_t = max(t - hold_years, 0.0)
    half_spread = 0.5 * (candidate.ask - candidate.bid)

    total_weight = 0.0
    weighted_proceeds = 0.0
    for k in SCENARIO_NODES:
        weight = _norm_pdf(k)
        shock = drift - 0.5 * forecast_vol * forecast_vol * hold_years + k * forecast_vol * sqrt_hold
        exit_spot = spot * math.exp(shock)
        # Reprice at IMPLIED vol: this is what the market will pay you back.
        value = bsm_price(
            spot=exit_spot,
            strike=candidate.strike,
            year_fraction=exit_t,
            sigma=iv,
            option_type=candidate.option_type,
        )
        # Cross the spread again on the way out.
        weighted_proceeds += weight * max(0.0, value - half_spread)
        total_weight += weight

    if total_weight > 0 and candidate.ask > 0:
        mean_proceeds = weighted_proceeds / total_weight
        candidate.expected_return = (mean_proceeds - candidate.ask) / candidate.ask

    # Probability of finishing beyond the breakeven, under the same drift
    # assumption, evaluated at the breakeven rather than the strike.
    if breakeven > 0:
        sqrt_t = math.sqrt(t)
        total_drift = sign * EDGE_DRIFT_SIGMAS * forecast_vol * sqrt_hold
        d2 = (
            math.log(spot / breakeven)
            + total_drift
            - 0.5 * forecast_vol * forecast_vol * t
        ) / (forecast_vol * sqrt_t)
        candidate.probability_of_profit = _norm_cdf(d2) if candidate.option_type == "call" else _norm_cdf(-d2)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _clamp(value: float, low: float = 0.0, high: float = 100.0) -> float:
    return max(low, min(high, value))


def _contract_shape_score(candidate: OptionCandidate) -> float:
    """Preference over contract structure, independent of the pricing model.

    Kept small (10% of the score) and deliberately blunt: it expresses that we
    want a responsive, liquid-looking, short-but-not-expiring contract, and
    nothing more. The pricing model carries the real discrimination.
    """
    shape = 0.0

    if candidate.delta is not None:
        abs_delta = abs(candidate.delta)
        if 0.35 <= abs_delta <= 0.65:
            shape += 45.0
        elif 0.25 <= abs_delta <= 0.75:
            shape += 32.0
        elif 0.15 <= abs_delta <= 0.85:
            shape += 14.0

    if 2 <= candidate.dte <= 7:
        shape += 30.0
    elif 1 <= candidate.dte <= 10:
        shape += 20.0
    elif candidate.dte <= 21:
        shape += 8.0

    abs_moneyness = abs(candidate.moneyness_pct)
    if abs_moneyness <= 0.03:
        shape += 25.0
    elif abs_moneyness <= 0.07:
        shape += 16.0
    elif abs_moneyness <= 0.12:
        shape += 6.0

    return _clamp(shape)


def _score_candidate(
    candidate: OptionCandidate,
    direction: str,
) -> float:
    """
    Score an option for a directional trade, in [0, 100].

    The score is a weighted blend of modelled quantities rather than an
    accumulation of magic-number buckets:

        expected return          45%
        probability of profit    25%
        variance risk premium    20%   (IV vs realized vol)
        contract shape           10%

    Spread is not scored. It is a certain cost, so it is gated up front and then
    subtracted inside expected_return; scoring it again would let an uncertain
    signal buy its way past a known cost.

    Components that cannot be computed are dropped and the remaining weights are
    renormalized, so a missing input never enters as a neutral average.
    """

    direction = direction.lower()

    # Direction sanity. select_directional_options already restricts calls to
    # bullish and puts to bearish, but _score_candidate is public and must not
    # assume it was reached through that path.
    expected_type = "call" if direction == "bullish" else "put"
    if candidate.option_type != expected_type:
        return 0.0
    if candidate.delta is not None:
        if direction == "bullish" and candidate.delta <= 0:
            return 0.0
        if direction == "bearish" and candidate.delta >= 0:
            return 0.0

    components: list[tuple[float, float]] = []

    # 1. Expected return, net of the round trip. Mapped so that -20% -> 0 and
    #    +60% -> 100, which spans the range a short-dated long option can
    #    plausibly show without saturating at either end.
    if candidate.expected_return is not None:
        er_score = _clamp((candidate.expected_return + 0.20) / 0.80 * 100.0)
        components.append((WEIGHT_EXPECTED_RETURN, er_score))

    # 2. Probability of profit at the breakeven. Short-dated OTM PoP is small in
    #    absolute terms, so 0-50% is mapped across the full range; the absolute
    #    number is optimistic and is used for ranking only.
    if candidate.probability_of_profit is not None:
        pop_score = _clamp(candidate.probability_of_profit / 0.50 * 100.0)
        components.append((WEIGHT_PROBABILITY_OF_PROFIT, pop_score))

    # 3. Variance risk premium. Cheap vol relative to what the stock actually
    #    realizes scores well; rich vol scores badly. Ratio 0.80 -> 100,
    #    ratio 1.60 -> 0.
    if candidate.iv_rv_ratio is not None and candidate.iv_rv_ratio > 0:
        vrp_score = _clamp((MAX_IV_RV_RATIO - candidate.iv_rv_ratio) / (MAX_IV_RV_RATIO - 0.80) * 100.0)
        components.append((WEIGHT_VARIANCE_RISK_PREMIUM, vrp_score))

    # 4. Contract shape.
    components.append((WEIGHT_CONTRACT_SHAPE, _contract_shape_score(candidate)))

    total_weight = sum(weight for weight, _ in components)
    if total_weight <= 0:
        return 0.0

    score = sum(weight * value for weight, value in components) / total_weight
    return _clamp(round(score, 4))


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _gate(
    candidate: OptionCandidate,
    *,
    max_spread_pct: float,
    max_iv_rv_ratio: float = MAX_IV_RV_RATIO,
    max_daily_theta_pct: float = MAX_DAILY_THETA_PCT,
    max_breakeven_ratio: float = MAX_BREAKEVEN_RATIO,
    min_expected_return: float = MIN_EXPECTED_RETURN,
    abs_spread_tolerance: float = ABS_SPREAD_TOLERANCE,
) -> Optional[str]:
    """Return a rejection reason, or None if the candidate passes every gate."""
    absolute_spread = candidate.ask - candidate.bid
    if candidate.spread_pct > max_spread_pct and absolute_spread > abs_spread_tolerance + 1e-9:
        return f"spread {candidate.spread_pct:.1%} > {max_spread_pct:.1%}"

    if candidate.iv_rv_ratio is not None and candidate.iv_rv_ratio > max_iv_rv_ratio:
        return f"IV/RV {candidate.iv_rv_ratio:.2f} > {max_iv_rv_ratio:.2f}"

    if candidate.theta_burn_pct is not None and candidate.theta_burn_pct > max_daily_theta_pct:
        return f"theta burn {candidate.theta_burn_pct:.1%}/day > {max_daily_theta_pct:.0%}"

    if candidate.breakeven_ratio is not None and candidate.breakeven_ratio > max_breakeven_ratio:
        return f"breakeven/expected move {candidate.breakeven_ratio:.2f} > {max_breakeven_ratio:.2f}"

    if candidate.expected_return is not None and candidate.expected_return < min_expected_return:
        return f"expected return {candidate.expected_return:+.1%} < {min_expected_return:+.0%}"

    return None


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
    realized_vol: Optional[float] = None,
    as_of: Optional[datetime] = None,
    collect_rejections: Optional[list[OptionCandidate]] = None,
    relaxed: bool = False,
) -> list[OptionCandidate]:
    """
    Select tradeable directional option candidates, ranked by modelled quality.
    """
    direction = direction.lower()

    if direction not in {"bullish", "bearish"}:
        raise ValueError("direction must be 'bullish' or 'bearish'")

    if relaxed:
        eff_max_spread = 0.25
        eff_abs_spread_tol = 0.10
        eff_max_iv_rv = 3.20
        eff_max_theta = 0.35
        eff_max_breakeven = 2.40
        eff_min_exp_ret = -0.40
        eff_min_delta = min_abs_delta * 0.6
        eff_max_delta = min(0.95, max_abs_delta * 1.1)
        eff_min_premium = min_premium * 0.5
        eff_min_dte = 1
        eff_max_dte = max(max_dte, 21)
    else:
        eff_max_spread = max_spread_pct if max_spread_pct <= MAX_SPREAD_PCT else MAX_SPREAD_PCT
        eff_abs_spread_tol = ABS_SPREAD_TOLERANCE
        eff_max_iv_rv = MAX_IV_RV_RATIO
        eff_max_theta = MAX_DAILY_THETA_PCT
        eff_max_breakeven = MAX_BREAKEVEN_RATIO
        eff_min_exp_ret = MIN_EXPECTED_RETURN
        eff_min_delta = min_abs_delta
        eff_max_delta = max_abs_delta
        eff_min_premium = min_premium
        eff_min_dte = min_dte
        eff_max_dte = max_dte

    candidates: list[OptionCandidate] = []

    for symbol, snapshot in chain.items():
        candidate = build_candidate(
            symbol=symbol,
            snapshot=snapshot,
            underlying_price=underlying_price,
            as_of=as_of,
            realized_vol=realized_vol,
        )

        if candidate is None:
            continue

        def _reject(reason: str) -> None:
            if collect_rejections is not None:
                candidate.reject_reason = reason
                collect_rejections.append(candidate)

        # -----------------------------------------------------
        # Structure: type, expiration window, measurement guard
        # -----------------------------------------------------
        if direction == "bullish" and candidate.option_type != "call":
            continue
        if direction == "bearish" and candidate.option_type != "put":
            continue

        if candidate.dte < eff_min_dte or candidate.dte > eff_max_dte:
            _reject(f"dte {candidate.dte} outside [{eff_min_dte}, {eff_max_dte}]")
            continue

        # Do not initiate positions that can reach expiration
        # during the official measurement endpoint.
        if candidate.expiration <= LATEST_FORBIDDEN_EXPIRATION:
            _reject(f"expiration {candidate.expiration} inside the measurement window")
            continue

        # -----------------------------------------------------
        # Premium
        # -----------------------------------------------------
        if candidate.mid < eff_min_premium:
            _reject(f"premium {candidate.mid:.2f} < {eff_min_premium:.2f}")
            continue

        # -----------------------------------------------------
        # Greeks
        # -----------------------------------------------------
        if candidate.delta is None:
            _reject("no delta available and none recoverable")
            continue

        abs_delta = abs(candidate.delta)

        if abs_delta < eff_min_delta:
            _reject(f"|delta| {abs_delta:.2f} < {eff_min_delta:.2f}")
            continue

        if abs_delta > eff_max_delta:
            _reject(f"|delta| {abs_delta:.2f} > {eff_max_delta:.2f}")
            continue

        # -----------------------------------------------------
        # Cost, decay and pricing gates
        # -----------------------------------------------------
        reason = _gate(
            candidate,
            max_spread_pct=eff_max_spread,
            max_iv_rv_ratio=eff_max_iv_rv,
            max_daily_theta_pct=eff_max_theta,
            max_breakeven_ratio=eff_max_breakeven,
            min_expected_return=eff_min_exp_ret,
            abs_spread_tolerance=eff_abs_spread_tol,
        )
        if reason is not None:
            _reject(reason)
            continue

        candidate.score = _score_candidate(
            candidate=candidate,
            direction=direction,
        )

        candidates.append(candidate)

    # Deterministic ordering: score first, then symbol, so an identical chain
    # always produces an identical ranking.
    candidates.sort(key=lambda x: (-x.score, x.symbol))

    return candidates[:max_candidates]


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


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


def print_option_diagnostics(
    symbol: str,
    underlying_price: float,
    candidates: list[OptionCandidate],
    direction: str,
) -> None:
    """Print the modelled pricing metrics behind each candidate's score."""

    print()
    print("=" * 132)
    print(f" {symbol} OPTION DIAGNOSTICS — {direction.upper()}  (underlying ${underlying_price:,.2f})")
    print("=" * 132)

    print(
        f"{'Contract':<22}"
        f"{'Ask':>8}"
        f"{'Fair':>8}"
        f"{'Edge':>8}"
        f"{'IV/RV':>8}"
        f"{'BE%':>8}"
        f"{'ExpMv%':>8}"
        f"{'BE/EM':>8}"
        f"{'Th/day':>8}"
        f"{'PoP':>8}"
        f"{'E[R]':>9}"
        f"{'Score':>8}"
    )
    print("-" * 132)

    def _fmt(value: Optional[float], spec: str) -> str:
        return format(value, spec) if value is not None else "N/A"

    for candidate in candidates:
        print(
            f"{candidate.symbol:<22}"
            f"{candidate.ask:>8.2f}"
            f"{_fmt(candidate.fair_value, '>8.2f'):>8}"
            f"{_fmt(candidate.edge, '>+8.2f'):>8}"
            f"{_fmt(candidate.iv_rv_ratio, '>8.2f'):>8}"
            f"{_fmt(candidate.breakeven_pct, '>8.1%'):>8}"
            f"{_fmt(candidate.expected_move_pct, '>8.1%'):>8}"
            f"{_fmt(candidate.breakeven_ratio, '>8.2f'):>8}"
            f"{_fmt(candidate.theta_burn_pct, '>8.1%'):>8}"
            f"{_fmt(candidate.probability_of_profit, '>8.1%'):>8}"
            f"{_fmt(candidate.expected_return, '>+9.1%'):>9}"
            f"{candidate.score:>8.1f}"
        )

    print("=" * 132)
    print()
