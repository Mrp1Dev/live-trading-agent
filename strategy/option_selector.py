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

# Gates: tightened to 3.5% for ultra-liquid assets (SPY/QQQ/NVDA/TSLA)
MAX_SPREAD_PCT = 0.035
ABS_SPREAD_TOLERANCE = 0.02      # one-tick escape hatch for cheap contracts
MAX_DAILY_THETA_PCT = 0.25       # abs(theta_per_day) / ask (relaxed for 0-1 DTE)
MAX_BREAKEVEN_RATIO = 1.35       # breakeven move / expected move over the hold
MAX_IV_RV_RATIO = 1.80           # do not buy heavily overpriced premium
MIN_EXPECTED_RETURN = -0.15      # net of the round trip

# Expected-return model for fast intraday hold (~0.1 trading days = ~40 mins)
PLANNED_HOLD_DAYS = 0.10
EDGE_DRIFT_SIGMAS = 0.40
SCENARIO_NODES = (-3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0)

# Score weights
WEIGHT_EXPECTED_RETURN = 0.40
WEIGHT_PROBABILITY_OF_PROFIT = 0.30
WEIGHT_VARIANCE_RISK_PREMIUM = 0.20
WEIGHT_CONTRACT_SHAPE = 0.10

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


@dataclass
class SpreadCandidate:
    """Represents a multi-leg spread or single-leg candidate ready for ranking and execution."""
    symbol: str                                 # Unique composite ID or OCC symbol
    underlying_symbol: str                      # e.g. "SPY"
    spread_type: str                            # "credit_bull_put" | "credit_bear_call" | "debit_bull_call" | "debit_bear_put" | "single_leg_call" | "single_leg_put"
    direction: str                              # "bullish" | "bearish"
    expiration: date
    dte: int
    strike: float                               # Primary / Long strike

    bid: float                                  # Net executable sell price (credit or bid)
    ask: float                                  # Net executable buy price (debit or ask)
    mid: float                                  # Net midpoint price
    spread_pct: float                           # Net friction percentage

    iv: Optional[float]
    delta: Optional[float]                      # Net delta
    gamma: Optional[float]                      # Net gamma
    theta: Optional[float]                      # Net theta (positive for credit spreads)
    vega: Optional[float]                       # Net vega

    moneyness_pct: float = 0.0
    score: float = 0.0

    # Multi-leg structural fields
    is_credit: bool = False                     # True for credit spreads, False for debit / single leg
    is_mleg: bool = False                       # True for multi-leg spreads
    long_leg: Optional[OptionCandidate] = None  # Long leg candidate
    short_leg: Optional[OptionCandidate] = None # Short leg candidate (None for single leg)
    long_strike: Optional[float] = None
    short_strike: Optional[float] = None
    strike_width: float = 0.0                   # Absolute width between strikes

    net_credit: float = 0.0                     # Upfront credit per share ($)
    net_debit: float = 0.0                      # Net debit per share ($)
    max_loss: float = 0.0                       # Max loss per share ($)
    max_profit: float = 0.0                     # Max profit per share ($)
    reward_to_risk: float = 0.0                 # max_profit / max_loss
    probability_of_profit: Optional[float] = None
    expected_return: Optional[float] = None

    underlying_price: Optional[float] = None
    option_type: str = "spread"                 # "call" | "put" | "spread"
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


def _gate(candidate: OptionCandidate, *, max_spread_pct: float) -> Optional[str]:
    """Return a rejection reason, or None if the candidate passes every gate.

    Gate ORDER is load-bearing, because the first failure is the reason that gets
    written to the decision log. They are ordered cause before symptom:

        spread          a cost that exists before any model runs
        IV/RV           the contract is priced richly
        theta burn      the contract decays too fast
        breakeven       the strike is too far for the expected move
        expected return the aggregate verdict

    IV/RV precedes the breakeven check on purpose. Rich implied vol inflates the
    premium, which inflates the breakeven - so a rich contract fails both, and
    "you are paying 3.0x realized vol" is the useful reason to record, while
    "breakeven is 2.6x the expected move" is only its consequence.
    """

    # Spread. A one-tick market on a cheap contract is a tick-size floor, not
    # evidence of illiquidity, so an absolute-cents escape hatch applies.
    # The epsilon matters: option quotes are in whole cents, so an exactly
    # two-cent market is the common case for the escape hatch, and binary
    # floating point makes `0.02 > 0.02` intermittently true.
    absolute_spread = candidate.ask - candidate.bid
    if candidate.spread_pct > max_spread_pct and absolute_spread > ABS_SPREAD_TOLERANCE + 1e-9:
        return f"spread {candidate.spread_pct:.1%} > {max_spread_pct:.1%}"

    if candidate.iv_rv_ratio is not None and candidate.iv_rv_ratio > MAX_IV_RV_RATIO:
        return f"IV/RV {candidate.iv_rv_ratio:.2f} > {MAX_IV_RV_RATIO:.2f}"

    if candidate.theta_burn_pct is not None and candidate.theta_burn_pct > MAX_DAILY_THETA_PCT:
        return f"theta burn {candidate.theta_burn_pct:.1%}/day > {MAX_DAILY_THETA_PCT:.0%}"

    if candidate.breakeven_ratio is not None and candidate.breakeven_ratio > MAX_BREAKEVEN_RATIO:
        return f"breakeven/expected move {candidate.breakeven_ratio:.2f} > {MAX_BREAKEVEN_RATIO:.2f}"

    if candidate.expected_return is not None and candidate.expected_return < MIN_EXPECTED_RETURN:
        return f"expected return {candidate.expected_return:+.1%} < {MIN_EXPECTED_RETURN:+.0%}"

    return None


def construct_spread_candidates(
    chain: dict[str, OptionsSnapshot],
    underlying_price: float,
    direction: str = "bullish",
    min_dte: int = MIN_DTE,
    max_dte: int = MAX_DTE,
    max_spread_pct: float = MAX_OPTION_SPREAD_PCT,
    min_premium: float = MIN_OPTION_PREMIUM,
    max_candidates: int = 6,
    realized_vol: Optional[float] = None,
    as_of: Optional[datetime] = None,
    collect_rejections: Optional[list[Any]] = None,
) -> list[SpreadCandidate]:
    """
    Construct high-velocity multi-leg spreads (Credit & Debit) and high-gamma single legs.

    Generates:
      1. Vertical Debit Spreads (Bull Call / Bear Put) - Capped cost, fast directional gamma.
      2. Vertical Credit Spreads (Bull Put / Bear Call) - Positive theta, high probability.
      3. High-Gamma Single Legs (ATM Calls/Puts) - Momentum explosion.
    """
    direction = direction.lower()
    if direction not in {"bullish", "bearish"}:
        raise ValueError("direction must be 'bullish' or 'bearish'")

    if max_spread_pct > MAX_SPREAD_PCT:
        max_spread_pct = MAX_SPREAD_PCT

    # Step 1: Build and filter raw single-leg candidates
    raw_calls: list[OptionCandidate] = []
    raw_puts: list[OptionCandidate] = []

    for symbol, snapshot in chain.items():
        candidate = build_candidate(
            symbol=symbol,
            snapshot=snapshot,
            underlying_price=underlying_price,
            as_of=as_of,
            realized_vol=realized_vol,
        )
        if candidate is None or candidate.delta is None:
            continue

        if candidate.dte < min_dte or candidate.dte > max_dte:
            continue

        # Strict bid/ask spread gate on the leg
        abs_spread = candidate.ask - candidate.bid
        if candidate.spread_pct > max_spread_pct and abs_spread > ABS_SPREAD_TOLERANCE + 1e-9:
            continue

        if candidate.mid < min_premium:
            continue

        candidate.score = _score_candidate(candidate=candidate, direction=direction)

        if candidate.option_type == "call":
            raw_calls.append(candidate)
        else:
            raw_puts.append(candidate)

    # Sort legs by strike ascending
    raw_calls.sort(key=lambda x: (x.expiration, x.strike))
    raw_puts.sort(key=lambda x: (x.expiration, x.strike))

    # Group by expiration
    expirations = sorted(list({c.expiration for c in raw_calls + raw_puts}))
    spread_candidates: list[SpreadCandidate] = []

    underlying_symbol = ""
    if raw_calls:
        underlying_symbol = raw_calls[0].symbol[:-15]
    elif raw_puts:
        underlying_symbol = raw_puts[0].symbol[:-15]
    else:
        underlying_symbol = "STOCK"

    for exp in expirations:
        exp_calls = [c for c in raw_calls if c.expiration == exp]
        exp_puts = [c for c in raw_puts if c.expiration == exp]
        dte = exp_calls[0].dte if exp_calls else (exp_puts[0].dte if exp_puts else 0)

        if direction == "bullish":
            # -------------------------------------------------------------
            # 1. Bull Call Debit Spread: Buy ATM (0.40 - 0.65), Sell OTM (0.18 - 0.38)
            # -------------------------------------------------------------
            long_calls = [c for c in exp_calls if 0.38 <= (c.delta or 0.0) <= 0.68]
            for long_c in long_calls:
                otm_calls = [c for c in exp_calls if c.strike > long_c.strike and 0.15 <= (c.delta or 0.0) <= 0.38]
                for short_c in otm_calls:
                    width = short_c.strike - long_c.strike
                    if width <= 0 or width > underlying_price * 0.10:
                        continue

                    net_debit = long_c.ask - short_c.bid
                    net_bid = long_c.bid - short_c.ask
                    net_mid = long_c.mid - short_c.mid
                    if net_debit <= 0.10 or net_debit >= width * 0.85:
                        continue

                    max_loss = net_debit
                    max_profit = width - net_debit
                    rr = max_profit / max(0.01, max_loss)

                    net_delta = (long_c.delta or 0.0) - (short_c.delta or 0.0)
                    net_gamma = (long_c.gamma or 0.0) - (short_c.gamma or 0.0)
                    net_theta = (long_c.theta or 0.0) - (short_c.theta or 0.0)
                    net_vega = (long_c.vega or 0.0) - (short_c.vega or 0.0)

                    pop = _norm_cdf(1.0 - (net_debit / max(0.01, width)))
                    spread_pct = (net_debit - net_bid) / max(0.01, net_debit)

                    score = 50.0 + 25.0 * min(2.0, rr) + 20.0 * min(1.0, net_delta / 0.30) + 15.0 * pop

                    sym = f"{underlying_symbol}_{exp.strftime('%y%m%d')}_BCD_C{long_c.strike:g}/C{short_c.strike:g}"
                    spread_candidates.append(
                        SpreadCandidate(
                            symbol=sym,
                            underlying_symbol=underlying_symbol,
                            spread_type="debit_bull_call",
                            direction="bullish",
                            expiration=exp,
                            dte=dte,
                            strike=long_c.strike,
                            bid=round(net_bid, 2),
                            ask=round(net_debit, 2),
                            mid=round(net_mid, 2),
                            spread_pct=spread_pct,
                            iv=long_c.iv,
                            delta=net_delta,
                            gamma=net_gamma,
                            theta=net_theta,
                            vega=net_vega,
                            moneyness_pct=long_c.moneyness_pct,
                            score=score,
                            is_credit=False,
                            is_mleg=True,
                            long_leg=long_c,
                            short_leg=short_c,
                            long_strike=long_c.strike,
                            short_strike=short_c.strike,
                            strike_width=width,
                            net_credit=0.0,
                            net_debit=round(net_debit, 2),
                            max_loss=round(max_loss, 2),
                            max_profit=round(max_profit, 2),
                            reward_to_risk=round(rr, 2),
                            probability_of_profit=pop,
                            underlying_price=underlying_price,
                            option_type="spread",
                        )
                    )

            # -------------------------------------------------------------
            # 2. Bull Put Credit Spread: Sell OTM Put (-0.18 to -0.38), Buy further OTM Put (-0.05 to -0.18)
            # -------------------------------------------------------------
            short_puts = [p for p in exp_puts if -0.38 <= (p.delta or 0.0) <= -0.18 and p.strike < underlying_price]
            for short_p in short_puts:
                otm_long_puts = [p for p in exp_puts if p.strike < short_p.strike and -0.18 <= (p.delta or 0.0) <= -0.04]
                for long_p in otm_long_puts:
                    width = short_p.strike - long_p.strike
                    if width <= 0 or width > underlying_price * 0.10:
                        continue

                    net_credit = short_p.bid - long_p.ask
                    net_cost_to_close = short_p.ask - long_p.bid
                    net_mid = short_p.mid - long_p.mid
                    if net_credit <= 0.12 or net_credit >= width * 0.70:
                        continue

                    max_profit = net_credit
                    max_loss = width - net_credit
                    rr = max_profit / max(0.01, max_loss)

                    # For short spread: net delta is positive, net theta is positive!
                    net_delta = -(short_p.delta or 0.0) + (long_p.delta or 0.0)
                    net_gamma = -(short_p.gamma or 0.0) + (long_p.gamma or 0.0)
                    net_theta = -(short_p.theta or 0.0) + (long_p.theta or 0.0)
                    net_vega = -(short_p.vega or 0.0) + (long_p.vega or 0.0)

                    pop = 1.0 - abs(short_p.delta or 0.25)
                    spread_pct = (net_cost_to_close - net_credit) / max(0.01, width)

                    # Higher PoP and positive theta yield
                    theta_yield = net_theta / max(0.01, max_loss)
                    score = 55.0 + 30.0 * pop + 15.0 * min(1.0, net_credit / (width * 0.35)) + 10.0 * min(1.0, theta_yield * 5.0)

                    sym = f"{underlying_symbol}_{exp.strftime('%y%m%d')}_BPC_P{short_p.strike:g}/P{long_p.strike:g}"
                    spread_candidates.append(
                        SpreadCandidate(
                            symbol=sym,
                            underlying_symbol=underlying_symbol,
                            spread_type="credit_bull_put",
                            direction="bullish",
                            expiration=exp,
                            dte=dte,
                            strike=short_p.strike,
                            bid=round(net_credit, 2),
                            ask=round(net_cost_to_close, 2),
                            mid=round(net_mid, 2),
                            spread_pct=spread_pct,
                            iv=short_p.iv,
                            delta=net_delta,
                            gamma=net_gamma,
                            theta=net_theta,
                            vega=net_vega,
                            moneyness_pct=short_p.moneyness_pct,
                            score=score,
                            is_credit=True,
                            is_mleg=True,
                            long_leg=long_p,
                            short_leg=short_p,
                            long_strike=long_p.strike,
                            short_strike=short_p.strike,
                            strike_width=width,
                            net_credit=round(net_credit, 2),
                            net_debit=0.0,
                            max_loss=round(max_loss, 2),
                            max_profit=round(max_profit, 2),
                            reward_to_risk=round(rr, 2),
                            probability_of_profit=pop,
                            underlying_price=underlying_price,
                            option_type="spread",
                        )
                    )

            # -------------------------------------------------------------
            # 3. High-Gamma Single Leg Long Call (ATM)
            # -------------------------------------------------------------
            for call in exp_calls:
                if 0.42 <= (call.delta or 0.0) <= 0.65:
                    spread_candidates.append(
                        SpreadCandidate(
                            symbol=call.symbol,
                            underlying_symbol=underlying_symbol,
                            spread_type="single_leg_call",
                            direction="bullish",
                            expiration=exp,
                            dte=dte,
                            strike=call.strike,
                            bid=call.bid,
                            ask=call.ask,
                            mid=call.mid,
                            spread_pct=call.spread_pct,
                            iv=call.iv,
                            delta=call.delta,
                            gamma=call.gamma,
                            theta=call.theta,
                            vega=call.vega,
                            moneyness_pct=call.moneyness_pct,
                            score=call.score * 0.95,  # slight spread preference
                            is_credit=False,
                            is_mleg=False,
                            long_leg=call,
                            short_leg=None,
                            long_strike=call.strike,
                            short_strike=None,
                            strike_width=0.0,
                            net_credit=0.0,
                            net_debit=call.ask,
                            max_loss=call.ask,
                            max_profit=underlying_price * 0.15,
                            reward_to_risk=1.5,
                            probability_of_profit=call.probability_of_profit,
                            expected_return=call.expected_return,
                            underlying_price=underlying_price,
                            option_type="call",
                        )
                    )

        else:
            # -------------------------------------------------------------
            # 1. Bear Put Debit Spread: Buy ATM Put (-0.40 to -0.65), Sell OTM Put (-0.18 to -0.38)
            # -------------------------------------------------------------
            long_puts = [p for p in exp_puts if -0.68 <= (p.delta or 0.0) <= -0.38]
            for long_p in long_puts:
                otm_puts = [p for p in exp_puts if p.strike < long_p.strike and -0.38 <= (p.delta or 0.0) <= -0.15]
                for short_p in otm_puts:
                    width = long_p.strike - short_p.strike
                    if width <= 0 or width > underlying_price * 0.10:
                        continue

                    net_debit = long_p.ask - short_p.bid
                    net_bid = long_p.bid - short_p.ask
                    net_mid = long_p.mid - short_p.mid
                    if net_debit <= 0.10 or net_debit >= width * 0.85:
                        continue

                    max_loss = net_debit
                    max_profit = width - net_debit
                    rr = max_profit / max(0.01, max_loss)

                    net_delta = (long_p.delta or 0.0) - (short_p.delta or 0.0)
                    net_gamma = (long_p.gamma or 0.0) - (short_p.gamma or 0.0)
                    net_theta = (long_p.theta or 0.0) - (short_p.theta or 0.0)
                    net_vega = (long_p.vega or 0.0) - (short_p.vega or 0.0)

                    pop = _norm_cdf(1.0 - (net_debit / max(0.01, width)))
                    spread_pct = (net_debit - net_bid) / max(0.01, net_debit)

                    score = 50.0 + 25.0 * min(2.0, rr) + 20.0 * min(1.0, abs(net_delta) / 0.30) + 15.0 * pop

                    sym = f"{underlying_symbol}_{exp.strftime('%y%m%d')}_BPD_P{long_p.strike:g}/P{short_p.strike:g}"
                    spread_candidates.append(
                        SpreadCandidate(
                            symbol=sym,
                            underlying_symbol=underlying_symbol,
                            spread_type="debit_bear_put",
                            direction="bearish",
                            expiration=exp,
                            dte=dte,
                            strike=long_p.strike,
                            bid=round(net_bid, 2),
                            ask=round(net_debit, 2),
                            mid=round(net_mid, 2),
                            spread_pct=spread_pct,
                            iv=long_p.iv,
                            delta=net_delta,
                            gamma=net_gamma,
                            theta=net_theta,
                            vega=net_vega,
                            moneyness_pct=long_p.moneyness_pct,
                            score=score,
                            is_credit=False,
                            is_mleg=True,
                            long_leg=long_p,
                            short_leg=short_p,
                            long_strike=long_p.strike,
                            short_strike=short_p.strike,
                            strike_width=width,
                            net_credit=0.0,
                            net_debit=round(net_debit, 2),
                            max_loss=round(max_loss, 2),
                            max_profit=round(max_profit, 2),
                            reward_to_risk=round(rr, 2),
                            probability_of_profit=pop,
                            underlying_price=underlying_price,
                            option_type="spread",
                        )
                    )

            # -------------------------------------------------------------
            # 2. Bear Call Credit Spread: Sell OTM Call (0.18 to 0.38), Buy further OTM Call (0.05 to 0.18)
            # -------------------------------------------------------------
            short_calls = [c for c in exp_calls if 0.18 <= (c.delta or 0.0) <= 0.38 and c.strike > underlying_price]
            for short_c in short_calls:
                otm_long_calls = [c for c in exp_calls if c.strike > short_c.strike and 0.04 <= (c.delta or 0.0) <= 0.18]
                for long_c in otm_long_calls:
                    width = long_c.strike - short_c.strike
                    if width <= 0 or width > underlying_price * 0.10:
                        continue

                    net_credit = short_c.bid - long_c.ask
                    net_cost_to_close = short_c.ask - long_c.bid
                    net_mid = short_c.mid - long_c.mid
                    if net_credit <= 0.12 or net_credit >= width * 0.70:
                        continue

                    max_profit = net_credit
                    max_loss = width - net_credit
                    rr = max_profit / max(0.01, max_loss)

                    # For bear call credit spread: net delta is negative, net theta is positive!
                    net_delta = -(short_c.delta or 0.0) + (long_c.delta or 0.0)
                    net_gamma = -(short_c.gamma or 0.0) + (long_c.gamma or 0.0)
                    net_theta = -(short_c.theta or 0.0) + (long_c.theta or 0.0)
                    net_vega = -(short_c.vega or 0.0) + (long_c.vega or 0.0)

                    pop = 1.0 - (short_c.delta or 0.25)
                    spread_pct = (net_cost_to_close - net_credit) / max(0.01, width)

                    theta_yield = net_theta / max(0.01, max_loss)
                    score = 55.0 + 30.0 * pop + 15.0 * min(1.0, net_credit / (width * 0.35)) + 10.0 * min(1.0, theta_yield * 5.0)

                    sym = f"{underlying_symbol}_{exp.strftime('%y%m%d')}_BCC_C{short_c.strike:g}/C{long_c.strike:g}"
                    spread_candidates.append(
                        SpreadCandidate(
                            symbol=sym,
                            underlying_symbol=underlying_symbol,
                            spread_type="credit_bear_call",
                            direction="bearish",
                            expiration=exp,
                            dte=dte,
                            strike=short_c.strike,
                            bid=round(net_credit, 2),
                            ask=round(net_cost_to_close, 2),
                            mid=round(net_mid, 2),
                            spread_pct=spread_pct,
                            iv=short_c.iv,
                            delta=net_delta,
                            gamma=net_gamma,
                            theta=net_theta,
                            vega=net_vega,
                            moneyness_pct=short_c.moneyness_pct,
                            score=score,
                            is_credit=True,
                            is_mleg=True,
                            long_leg=long_c,
                            short_leg=short_c,
                            long_strike=long_c.strike,
                            short_strike=short_c.strike,
                            strike_width=width,
                            net_credit=round(net_credit, 2),
                            net_debit=0.0,
                            max_loss=round(max_loss, 2),
                            max_profit=round(max_profit, 2),
                            reward_to_risk=round(rr, 2),
                            probability_of_profit=pop,
                            underlying_price=underlying_price,
                            option_type="spread",
                        )
                    )

            # -------------------------------------------------------------
            # 3. High-Gamma Single Leg Long Put (ATM)
            # -------------------------------------------------------------
            for put in exp_puts:
                if -0.65 <= (put.delta or 0.0) <= -0.42:
                    spread_candidates.append(
                        SpreadCandidate(
                            symbol=put.symbol,
                            underlying_symbol=underlying_symbol,
                            spread_type="single_leg_put",
                            direction="bearish",
                            expiration=exp,
                            dte=dte,
                            strike=put.strike,
                            bid=put.bid,
                            ask=put.ask,
                            mid=put.mid,
                            spread_pct=put.spread_pct,
                            iv=put.iv,
                            delta=put.delta,
                            gamma=put.gamma,
                            theta=put.theta,
                            vega=put.vega,
                            moneyness_pct=put.moneyness_pct,
                            score=put.score * 0.95,
                            is_credit=False,
                            is_mleg=False,
                            long_leg=put,
                            short_leg=None,
                            long_strike=put.strike,
                            short_strike=None,
                            strike_width=0.0,
                            net_credit=0.0,
                            net_debit=put.ask,
                            max_loss=put.ask,
                            max_profit=underlying_price * 0.15,
                            reward_to_risk=1.5,
                            probability_of_profit=put.probability_of_profit,
                            expected_return=put.expected_return,
                            underlying_price=underlying_price,
                            option_type="put",
                        )
                    )

    # Sort spreads by score descending
    spread_candidates.sort(key=lambda x: (-x.score, x.symbol))
    return spread_candidates[:max_candidates]


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
    max_candidates: int = 6,
    realized_vol: Optional[float] = None,
    as_of: Optional[datetime] = None,
    collect_rejections: Optional[list[Any]] = None,
) -> list[SpreadCandidate]:
    """Select tradeable spreads and high-gamma directional candidates."""
    return construct_spread_candidates(
        chain=chain,
        underlying_price=underlying_price,
        direction=direction,
        min_dte=min_dte,
        max_dte=max_dte,
        max_spread_pct=max_spread_pct,
        min_premium=min_premium,
        max_candidates=max_candidates,
        realized_vol=realized_vol,
        as_of=as_of,
        collect_rejections=collect_rejections,
    )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def print_option_candidates(
    symbol: str,
    underlying_price: float,
    candidates: list[Any],
    direction: str,
) -> None:
    """Print selected spread and option candidates in a readable table."""
    print()
    print("=" * 135)
    print(f" {symbol} SPREAD & OPTION CANDIDATES — {direction.upper()} (Underlying: ${underlying_price:.2f})")
    print("=" * 135)
    print(
        f"{'Rank':<5}"
        f"{'Structure':<18}"
        f"{'Symbol/Strikes':<26}"
        f"{'DTE':>4}"
        f"{'NetPrice':>10}"
        f"{'MaxLoss':>10}"
        f"{'MaxProfit':>10}"
        f"{'Delta':>8}"
        f"{'Theta':>8}"
        f"{'PoP':>7}"
        f"{'Score':>7}"
    )
    print("-" * 135)

    for rank, candidate in enumerate(candidates, start=1):
        struct_type = getattr(candidate, "spread_type", getattr(candidate, "option_type", "option")).upper()
        net_price_str = f"+${candidate.net_credit:.2f}cr" if getattr(candidate, "is_credit", False) else f"-${candidate.net_debit:.2f}db"
        max_loss_str = f"${getattr(candidate, 'max_loss', candidate.ask):.2f}"
        max_profit_str = f"${getattr(candidate, 'max_profit', 0.0):.2f}"
        delta_str = f"{candidate.delta:+.2f}" if candidate.delta is not None else "N/A"
        theta_str = f"{candidate.theta:+.2f}" if candidate.theta is not None else "N/A"
        pop_str = f"{candidate.probability_of_profit:.0%}" if getattr(candidate, "probability_of_profit", None) is not None else "N/A"

        print(
            f"#{rank:<4}"
            f"{struct_type:<18}"
            f"{candidate.symbol:<26}"
            f"{candidate.dte:>4}"
            f"{net_price_str:>10}"
            f"{max_loss_str:>10}"
            f"{max_profit_str:>10}"
            f"{delta_str:>8}"
            f"{theta_str:>8}"
            f"{pop_str:>7}"
            f"{candidate.score:>7.1f}"
        )

    print("=" * 135)
    print()


def print_option_diagnostics(
    symbol: str,
    underlying_price: float,
    candidates: list[Any],
    direction: str,
) -> None:
    """Diagnostic reporting."""
    print_option_candidates(symbol, underlying_price, candidates, direction)

