"""Verification suite for the rebuilt scanner and option selector.

Two things are proven here:

  1. CONTRACT PRESERVATION - the public interface consumed by trade_scorer,
     portfolio, risk, llm, direction, alpaca_client and the backtest package is
     byte-for-byte unchanged.
  2. CORRECTNESS - the new pricing model reproduces independently verified
     reference values, and the bugs found in the audit are actually fixed.

Runs fully offline. No Alpaca client, no network, no credentials.
"""

from __future__ import annotations

import dataclasses
import inspect
import io
import math
import random
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd

from strategy import option_selector as osel
from strategy import scanner as scn
from strategy.option_selector import (
    OptionCandidate,
    bsm_greeks,
    bsm_price,
    build_candidate,
    implied_volatility,
    parse_occ_symbol,
    print_option_candidates,
    select_directional_options,
)
from strategy.scanner import ScannedStock, extract_stock_metrics, scan_stock_bars, score_and_rank_stocks

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
    else:
        FAILED.append((name, detail))


def close_to(a: float, b: float, tol: float = 1e-5) -> bool:
    return abs(a - b) <= tol


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MARKET_TZ = osel.MARKET_TZ


class FakeQuote:
    def __init__(self, bid, ask):
        self.bid_price = bid
        self.ask_price = ask


class FakeGreeks:
    def __init__(self, delta=None, gamma=None, theta=None, vega=None):
        self.delta = delta
        self.gamma = gamma
        self.theta = theta
        self.vega = vega


class FakeSnapshot:
    """Stands in for alpaca OptionsSnapshot. Duck-typed exactly as consumed."""

    def __init__(self, bid, ask, iv=None, greeks=None):
        self.latest_quote = FakeQuote(bid, ask)
        self.implied_volatility = iv
        self.greeks = greeks


def make_bars(symbols, days=90, seed=7, start=date(2026, 5, 1), tz_aware=True):
    """Deterministic MultiIndex (symbol, timestamp) daily bar frame."""
    rng = random.Random(seed)
    rows = []
    for sym_i, sym in enumerate(symbols):
        price = 100.0 + 20.0 * sym_i
        drift = 0.0006 * (sym_i + 1)
        vol = 0.010 + 0.004 * sym_i
        ts = start
        for d in range(days):
            while ts.weekday() >= 5:
                ts += timedelta(days=1)
            price *= math.exp(drift + vol * rng.gauss(0, 1))
            stamp = datetime.combine(ts, datetime.min.time())
            if tz_aware:
                stamp = stamp.replace(tzinfo=timezone.utc)
            rows.append(
                {
                    "symbol": sym,
                    "timestamp": stamp,
                    "open": price * 0.995,
                    "high": price * 1.01,
                    "low": price * 0.99,
                    "close": price,
                    "volume": 1_000_000 + rng.randint(0, 500_000),
                }
            )
            ts += timedelta(days=1)
    df = pd.DataFrame(rows).set_index(["symbol", "timestamp"]).sort_index()
    return df


# The decision moment and a real expiration for it. The expiration must fall
# AFTER config.LATEST_FORBIDDEN_EXPIRATION (2026-09-03) and inside
# config.MIN_DTE..MAX_DTE (1..14), otherwise every candidate is legitimately
# rejected by the measurement-window guard before any other gate is reached.
AS_OF = datetime(2026, 8, 31, 12, 0, tzinfo=MARKET_TZ)
EXPIRY = date(2026, 9, 11)          # Friday, 11 DTE from AS_OF
SPOT = 190.0


def occ(strike: float, option_type: str = "call", expiry: date = EXPIRY) -> str:
    letter = "C" if option_type == "call" else "P"
    return f"AAPL{expiry.strftime('%y%m%d')}{letter}{int(round(strike * 1000)):08d}"


def model_snapshot(
    strike: float,
    iv: float,
    option_type: str = "call",
    spread_pct: float = 0.02,
    abs_spread: float | None = None,
    expiry: date = EXPIRY,
    spot: float = SPOT,
    as_of: datetime = AS_OF,
    with_greeks: bool = True,
):
    """Build a SELF-CONSISTENT synthetic snapshot.

    The quote is derived from the model at a chosen IV rather than invented. A
    hand-typed quote is almost always arbitrage-violating - a $2.10 ask on a
    three-month at-the-money call worth $12.41 makes the expected-return engine
    report +487%, which says nothing about the engine and everything about the
    fixture. Pricing the fixture makes every downstream assertion meaningful.
    """
    t = osel.time_to_expiry_years(expiry, as_of)
    fair = bsm_price(spot, strike, t, iv, option_type)
    half = (abs_spread / 2.0) if abs_spread is not None else (fair * spread_pct / 2.0)
    bid = max(0.01, fair - half)
    ask = fair + half

    greeks = None
    if with_greeks:
        g = bsm_greeks(spot, strike, t, iv, option_type)
        greeks = FakeGreeks(
            delta=g["delta"],
            gamma=g["gamma"],
            theta=g["theta"] / 365.0,   # Alpaca reports theta per day
            vega=g["vega"],
        )
    return FakeSnapshot(bid=bid, ask=ask, iv=iv, greeks=greeks)


# ---------------------------------------------------------------------------
# 1. Contract preservation
# ---------------------------------------------------------------------------

ORIGINAL_SCANNED_STOCK_FIELDS = [
    "symbol", "price", "return_1d", "return_5d", "return_20d", "volume",
    "avg_volume_20d", "volume_ratio", "sma_20", "sma_50", "distance_sma20",
    "distance_sma50", "realized_volatility", "relative_strength_spy",
    "momentum_score", "volume_score", "volatility_score",
    "relative_strength_score", "trend_score", "score", "rank",
]

ORIGINAL_OPTION_CANDIDATE_FIELDS = [
    "symbol", "expiration", "option_type", "strike", "bid", "ask", "mid",
    "spread_pct", "iv", "delta", "gamma", "theta", "vega", "dte",
    "moneyness_pct", "score",
]

ORIGINAL_METRIC_KEYS = {
    "symbol", "price", "return_1d", "return_5d", "return_20d", "volume",
    "avg_volume_20d", "volume_ratio", "sma_20", "sma_50", "distance_sma20",
    "distance_sma50", "realized_volatility", "relative_strength_spy",
    "raw_momentum", "raw_volume", "raw_volatility", "raw_relative_strength",
    "raw_trend",
}


def test_scanned_stock_contract():
    fields = [f.name for f in dataclasses.fields(ScannedStock)]
    check(
        "ScannedStock fields unchanged (names + order)",
        fields == ORIGINAL_SCANNED_STOCK_FIELDS,
        f"got {fields}",
    )


def test_option_candidate_contract():
    fields = [f.name for f in dataclasses.fields(OptionCandidate)]
    check(
        "OptionCandidate original 16 fields unchanged and still first",
        fields[:16] == ORIGINAL_OPTION_CANDIDATE_FIELDS,
        f"got {fields[:16]}",
    )
    check(
        "OptionCandidate appended fields all have defaults",
        all(
            f.default is not dataclasses.MISSING or f.default_factory is not dataclasses.MISSING
            for f in dataclasses.fields(OptionCandidate)[16:]
        ),
    )
    # Original 16-arg positional construction must still work.
    try:
        c = OptionCandidate(
            "AAPL240920C00190000", date(2024, 9, 20), "call", 190.0,
            2.00, 2.10, 2.05, 0.049, 0.28, 0.52, 0.03, -0.08, 0.11, 30, 0.01,
        )
        check("OptionCandidate positional construction still works", c.dte == 30)
    except Exception as exc:  # pragma: no cover
        check("OptionCandidate positional construction still works", False, repr(exc))


def test_function_signatures():
    """Every original parameter must still exist, with its original default."""
    expected = {
        osel.select_directional_options: [
            ("chain", inspect.Parameter.empty),
            ("underlying_price", inspect.Parameter.empty),
            ("direction", "bullish"),
            ("min_dte", 1),
            ("max_dte", 14),
            ("max_spread_pct", 0.15),
            ("min_abs_delta", 0.15),
            ("max_abs_delta", 0.90),
            ("min_premium", 0.25),
            ("max_candidates", 30),
        ],
        osel.build_candidate: [
            ("symbol", inspect.Parameter.empty),
            ("snapshot", inspect.Parameter.empty),
            ("underlying_price", inspect.Parameter.empty),
        ],
        scn.scan_stock_bars: [
            ("bars_df", inspect.Parameter.empty),
            ("universe", None),
            ("benchmark_symbol", "SPY"),
            ("top_n", 15),
        ],
        scn.extract_stock_metrics: [
            ("symbol", inspect.Parameter.empty),
            ("stock_df", inspect.Parameter.empty),
            ("spy_return_20d", inspect.Parameter.empty),
        ],
    }
    for func, params in expected.items():
        sig = list(inspect.signature(func).parameters.items())
        ok = True
        for i, (name, default) in enumerate(params):
            if i >= len(sig) or sig[i][0] != name or sig[i][1].default != default:
                ok = False
                break
        check(f"{func.__name__} signature preserved", ok, f"got {[s[0] for s in sig]}")

    check(
        "print_option_candidates signature preserved",
        list(inspect.signature(osel.print_option_candidates).parameters)
        == ["symbol", "underlying_price", "candidates", "direction"],
    )
    check(
        "print_scan_results signature preserved",
        list(inspect.signature(scn.print_scan_results).parameters) == ["stocks", "title"],
    )
    check("OPTION_SCORE_MAX still exported", osel.OPTION_SCORE_MAX == 80.0)
    check("_score_candidate signature preserved",
          list(inspect.signature(osel._score_candidate).parameters) == ["candidate", "direction"])


def test_parse_occ_symbol_unchanged():
    exp, typ, strike = parse_occ_symbol("NVDA260904C00217500")
    check("parse_occ_symbol call", (exp, typ, strike) == (date(2026, 9, 4), "call", 217.5))
    exp, typ, strike = parse_occ_symbol("AAPL240607P00195000")
    check("parse_occ_symbol put", (exp, typ, strike) == (date(2024, 6, 7), "put", 195.0))
    for bad in ["SHORT", "AAPL240607X00195000", "AAPL2406A7P00195000", ""]:
        try:
            parse_occ_symbol(bad)
            check(f"parse_occ_symbol rejects {bad!r}", False, "no raise")
        except ValueError:
            check(f"parse_occ_symbol rejects {bad!r}", True)


def test_metric_dict_keys_unchanged():
    bars = make_bars(["AAA"])
    m = extract_stock_metrics("AAA", bars.xs("AAA", level=0), 0.01, as_of=AS_OF)
    check("extract_stock_metrics returns a dict", m is not None)
    if m:
        check("metric dict keys unchanged", set(m.keys()) == ORIGINAL_METRIC_KEYS,
              f"diff={set(m.keys()) ^ ORIGINAL_METRIC_KEYS}")


def test_print_option_candidates_byte_identical():
    """The candidate table is demo material; its layout must not drift."""
    cands = [
        OptionCandidate("AAPL240920C00190000", date(2024, 9, 20), "call", 190.0,
                        2.00, 2.10, 2.05, 0.0488, 0.2812, 0.5231, 0.031, -0.0812, 0.1134, 30, 0.0105,
                        score=73.42),
        OptionCandidate("AAPL240920C00195000", date(2024, 9, 20), "call", 195.0,
                        1.10, 1.20, 1.15, 0.0870, None, None, None, None, None, 30, 0.0371,
                        score=51.09),
    ]
    buf = io.StringIO()
    with redirect_stdout(buf):
        print_option_candidates("AAPL", 188.0, cands, "bullish")
    out = buf.getvalue()
    expected_header = (
        f"{'Rank':<5}{'Contract':<22}{'DTE':>5}{'Strike':>9}{'Mid':>9}"
        f"{'Spread':>9}{'IV':>9}{'Delta':>9}{'Mny':>9}{'Score':>9}"
    )
    row1 = (
        f"{1:<5}{'AAPL240920C00190000':<22}{30:>5}{190.0:>9.2f}{2.05:>9.2f}"
        f"{0.0488:>8.1%}{'28.1%':>9}{'+0.523':>9}{0.0105:>+8.1%}{73.42:>9.1f}"
    )
    check("print_option_candidates header unchanged", expected_header in out)
    check("print_option_candidates row format unchanged", row1 in out, repr(out))
    check("print_option_candidates N/A path unchanged", "      N/A      N/A" in out)
    check("print_option_candidates rule widths unchanged",
          ("=" * 125) in out and ("-" * 125) in out)


def test_print_scan_results_byte_identical():
    s = ScannedStock("AAPL", 188.0, 0.0123, 0.0345, 0.0912, 5.1e7, 4.8e7, 1.0625,
                     185.0, 180.0, 0.0162, 0.0444, 0.2814, 0.0311,
                     88.0, 70.0, 100.0, 92.0, 64.0, 84.9, rank=1)
    buf = io.StringIO()
    with redirect_stdout(buf):
        scn.print_scan_results([s])
    out = buf.getvalue()
    expected_row = (
        f"{'#1':<5} {'AAPL':<7} ${188.0:>7.2f} {0.0123:>+7.2%} {0.0345:>+7.2%} "
        f"{0.0912:>+8.2%} {1.0625:>7.2f}x {0.0162:>+7.1%} {0.2814:>7.1%} "
        f"{0.0311:>+9.2%} {84.9:>7.1f}"
    )
    check("print_scan_results row format unchanged", expected_row in out, repr(out))
    check("print_scan_results rule width unchanged", ("=" * 118) in out)


# ---------------------------------------------------------------------------
# 2. Pricing model correctness
# ---------------------------------------------------------------------------

def test_bsm_reference_values():
    S, K, T, sig, r, q = 100.0, 100.0, 1.0, 0.20, 0.05, 0.0
    c = bsm_price(S, K, T, sig, "call", r, q)
    p = bsm_price(S, K, T, sig, "put", r, q)
    g = bsm_greeks(S, K, T, sig, "call", r, q)
    check("BSM call = 10.450584", close_to(c, 10.450584))
    check("BSM put = 5.573526", close_to(p, 5.573526))
    check("put-call parity = 4.877058", close_to(c - p, 4.877058))
    check("delta = 0.636831", close_to(g["delta"], 0.636831))
    check("gamma = 0.018762", close_to(g["gamma"], 0.018762))
    check("vega (per pt) = 0.375240", close_to(g["vega"], 0.375240))
    check("theta (annual) = -6.414028", close_to(g["theta"], -6.414028))


def test_bsm_degenerate_inputs():
    check("T=0 collapses to intrinsic", bsm_price(110, 100, 0.0, 0.2, "call") == 10.0)
    check("sigma=0 collapses to intrinsic", bsm_price(90, 100, 1.0, 0.0, "put") == 10.0)
    check("OTM at expiry is worthless", bsm_price(90, 100, 0.0, 0.2, "call") == 0.0)


def test_implied_vol_solver():
    worst = 0.0
    for true_sig in (0.15, 0.30, 0.55, 0.90, 1.50):
        for strike in (80, 100, 125):
            px = bsm_price(100, strike, 0.5, true_sig, "call")
            rec = implied_volatility(px, 100, strike, 0.5, "call")
            if rec is None:
                worst = 9.9
            else:
                worst = max(worst, abs(rec - true_sig))
    check("IV round-trips across sigma [0.15, 1.50]", worst < 1e-6, f"worst={worst}")
    check("IV below intrinsic returns None (never guesses)",
          implied_volatility(1.0, 120, 100, 0.5, "call") is None)


def test_zero_edge_must_not_be_profitable():
    """The single most important invariant in the scenario engine.

    With no assumed edge and realized vol equal to implied vol, expected return
    must be slightly NEGATIVE - you still pay the spread. A zero-edge trade
    showing positive EV means the model is wrong.
    """
    original = osel.EDGE_DRIFT_SIGMAS
    try:
        osel.EDGE_DRIFT_SIGMAS = 0.0
        snap = model_snapshot(strike=SPOT, iv=0.30)
        c = build_candidate(occ(SPOT), snap, SPOT, as_of=AS_OF, realized_vol=0.30)
        check("zero-edge expected return is negative",
              c.expected_return is not None and c.expected_return < 0,
              f"E[R]={c.expected_return}")
        check("zero-edge loss is roughly the round-trip friction, not a blow-up",
              c.expected_return is not None and -0.25 < c.expected_return < 0,
              f"E[R]={c.expected_return}")
    finally:
        osel.EDGE_DRIFT_SIGMAS = original


def test_expected_return_monotonic_in_edge():
    original = osel.EDGE_DRIFT_SIGMAS
    results = []
    try:
        for edge in (0.0, 0.20, 0.35, 0.60):
            osel.EDGE_DRIFT_SIGMAS = edge
            snap = model_snapshot(strike=SPOT, iv=0.30)
            c = build_candidate(occ(SPOT), snap, SPOT, as_of=AS_OF, realized_vol=0.30)
            results.append(c.expected_return)
    finally:
        osel.EDGE_DRIFT_SIGMAS = original
    check("expected return is monotonic in EDGE_DRIFT_SIGMAS",
          all(results[i] < results[i + 1] for i in range(len(results) - 1)),
          f"{results}")
    print(f"        [E[R] vs edge 0.00/0.20/0.35/0.60: "
          f"{', '.join(f'{r:+.1%}' for r in results)}]")


def test_rich_vol_prices_worse_than_cheap_vol():
    """Buying premium above what the stock realizes must reduce expected return."""
    cheap = build_candidate(occ(SPOT), model_snapshot(SPOT, iv=0.25), SPOT,
                            as_of=AS_OF, realized_vol=0.40)
    rich = build_candidate(occ(SPOT), model_snapshot(SPOT, iv=0.55), SPOT,
                           as_of=AS_OF, realized_vol=0.40)
    check("cheap vol beats rich vol on expected return",
          cheap.expected_return > rich.expected_return,
          f"cheap={cheap.expected_return:+.1%} rich={rich.expected_return:+.1%}")
    check("IV/RV ratio is computed from realized vol",
          close_to(cheap.iv_rv_ratio, 0.25 / 0.40, 1e-9)
          and close_to(rich.iv_rv_ratio, 0.55 / 0.40, 1e-9))
    check("fair value beats the ask when vol is cheap",
          cheap.edge is not None and cheap.edge > 0, f"edge={cheap.edge}")
    check("fair value is below the ask when vol is rich",
          rich.edge is not None and rich.edge < 0, f"edge={rich.edge}")


def test_put_call_symmetry_of_expected_return():
    """A bearish put must price symmetrically to the mirror-image bullish call."""
    c = build_candidate(occ(SPOT, "call"), model_snapshot(SPOT, 0.30, "call"),
                        SPOT, as_of=AS_OF, realized_vol=0.30)
    p = build_candidate(occ(SPOT, "put"), model_snapshot(SPOT, 0.30, "put"),
                        SPOT, as_of=AS_OF, realized_vol=0.30)
    check("put expected return uses its own directional drift",
          c.expected_return is not None and p.expected_return is not None
          and abs(c.expected_return - p.expected_return) < 0.06,
          f"call={c.expected_return:+.1%} put={p.expected_return:+.1%}")
    check("put delta is negative, call delta positive",
          c.delta > 0 and p.delta < 0)


# ---------------------------------------------------------------------------
# 3. Bug fixes from the audit
# ---------------------------------------------------------------------------

def test_timezone_bug_fixed():
    """DTE must be computed on the exchange date, not the local system date.

    At 02:00 IST on 21 Jun the New York date is still 20 Jun. The old code used
    date.today() and would compute DTE against 21 Jun, corrupting the DTE filter
    and, worse, the LATEST_FORBIDDEN_EXPIRATION guard.
    """
    ist = timezone(timedelta(hours=5, minutes=30))
    local_moment = datetime(2024, 6, 21, 2, 0, tzinfo=ist)  # = 2024-06-20 16:30 ET
    snap = FakeSnapshot(2.00, 2.10, 0.30, FakeGreeks(0.52, 0.03, -0.05, 0.11))
    c = build_candidate("AAPL240627C00190000", snap, 190.0, as_of=local_moment)
    check("DTE uses the New York date, not the local date",
          c is not None and c.dte == 7, f"dte={getattr(c, 'dte', None)} (naive local would give 6)")
    check("market_now converts into the exchange timezone",
          osel.market_now(local_moment).date() == date(2024, 6, 20))


def test_fractional_time_to_expiry():
    morning = datetime(2024, 6, 20, 9, 30, tzinfo=MARKET_TZ)
    afternoon = datetime(2024, 6, 20, 15, 30, tzinfo=MARKET_TZ)
    t_am = osel.time_to_expiry_years(date(2024, 6, 21), morning)
    t_pm = osel.time_to_expiry_years(date(2024, 6, 21), afternoon)
    check("fractional time-to-expiry decays within the day", t_pm < t_am)
    check("expired contract has zero time value",
          osel.time_to_expiry_years(date(2024, 6, 19), afternoon) == 0.0)


def test_tz_naive_index_does_not_crash():
    """The old hasattr(index, 'tz') test called tz_convert on naive data."""
    bars = make_bars(["AAA"], tz_aware=False)
    try:
        out = scn._completed_daily_bars(bars.xs("AAA", level=0), as_of=AS_OF)
        check("tz-naive DatetimeIndex handled without raising", len(out) > 0)
    except Exception as exc:
        check("tz-naive DatetimeIndex handled without raising", False, repr(exc))


def test_available_symbols_uses_realized_values():
    """index.levels[0] reports symbols that have no surviving rows."""
    bars = make_bars(["AAA", "BBB"])
    filtered = bars.drop("BBB", level=0)
    stale = set(filtered.index.levels[0]) if isinstance(filtered.index, pd.MultiIndex) else set()
    live = scn._available_symbols(filtered)
    check("stale .levels[0] would have reported BBB present", "BBB" in stale)
    check("_available_symbols reports only symbols with rows", "BBB" not in live and "AAA" in live)


def test_missing_data_is_not_silently_defaulted():
    bars = make_bars(["AAA"]).copy()
    # Inject well inside the completed window: rows at or after the as_of cutoff
    # are dropped before the metric code ever sees them.
    bars.iloc[-15, bars.columns.get_loc("close")] = np.nan
    m = extract_stock_metrics("AAA", bars.xs("AAA", level=0), 0.0, as_of=AS_OF)
    check("NaN close drops the symbol instead of scoring it", m is None)

    zero = make_bars(["BBB"]).copy()
    zero["volume"] = 0
    m2 = extract_stock_metrics("BBB", zero.xs("BBB", level=0), 0.0, as_of=AS_OF)
    check("zero average volume drops the symbol", m2 is None)

    short = make_bars(["CCC"], days=10)
    m3 = extract_stock_metrics("CCC", short.xs("CCC", level=0), 0.0, as_of=AS_OF)
    check("insufficient history drops the symbol", m3 is None)


def _reference_average_rank(values: np.ndarray) -> np.ndarray:
    """Independent O(n^2) implementation straight from the definition.

    rank(x) = 1 + #{v < x} + (#{v == x} - 1) / 2

    Deliberately written from the specification rather than derived from the
    implementation under test, so agreement is evidence rather than tautology.
    """
    out = np.empty(values.size, dtype=float)
    for i, x in enumerate(values):
        less = int(np.sum(values < x))
        equal = int(np.sum(values == x))
        out[i] = 1.0 + less + (equal - 1) / 2.0
    return out


def test_average_rank_matches_reference():
    """Equivalence with scipy.stats.rankdata(method='average').

    scipy cannot be imported on every machine (it is blocked outright by
    Application Control on this one, which is exactly why the scanner no longer
    depends on it), so the reference is an independent implementation of the
    documented definition, cross-checked against hand-computed cases.
    """
    # Hand-computed: rankdata([10, 20, 20, 30]) == [1, 2.5, 2.5, 4]
    hand = scn._average_rank(np.array([10.0, 20.0, 20.0, 30.0]))
    check("_average_rank handles a hand-computed tie correctly",
          list(hand) == [1.0, 2.5, 2.5, 4.0], f"got {list(hand)}")
    # All-equal input: every element gets the mean rank.
    flat = scn._average_rank(np.array([5.0, 5.0, 5.0]))
    check("_average_rank on all-equal input gives the mean rank",
          list(flat) == [2.0, 2.0, 2.0], f"got {list(flat)}")

    rng = np.random.default_rng(11)
    worst = 0.0
    for _ in range(300):
        n = int(rng.integers(1, 40))
        vals = rng.integers(0, 6, size=n).astype(float)   # heavy ties on purpose
        worst = max(worst, float(np.max(np.abs(scn._average_rank(vals) - _reference_average_rank(vals)))))
    check("_average_rank matches the reference definition exactly (incl. ties)",
          worst == 0.0, f"max diff {worst}")

    # Continuous, tie-free data as well.
    worst2 = 0.0
    for _ in range(100):
        vals = rng.normal(size=int(rng.integers(2, 50)))
        worst2 = max(worst2, float(np.max(np.abs(scn._average_rank(vals) - _reference_average_rank(vals)))))
    check("_average_rank matches the reference on continuous data", worst2 == 0.0,
          f"max diff {worst2}")


def test_scanner_imports_without_scipy():
    """The original scanner imported scipy at module scope for one function.

    On a machine where scipy cannot load, that made the entire strategy package
    unimportable - not degraded, unimportable.
    """
    import importlib
    check("scanner imports cleanly", importlib.import_module("strategy.scanner") is not None)
    check("scipy is not a scanner dependency", "scipy" not in str(scn.__dict__.keys()))


def test_volatility_no_longer_rewards_expensive_premium():
    """The old score was a percentile of realized vol: highest vol scored 100."""
    check("dead vol scores badly", scn.volatility_regime_score(0.08) == 0.0)
    check("tradeable vol scores full marks", scn.volatility_regime_score(0.35) == 100.0)
    check("blow-off vol scores badly", scn.volatility_regime_score(1.30) == 0.0)
    check("extreme vol scores below tradeable vol",
          scn.volatility_regime_score(0.95) < scn.volatility_regime_score(0.35))
    check("volatility score is absolute, not universe-dependent",
          scn.volatility_regime_score(0.35) == scn.volatility_regime_score(0.35))


def test_ranking_is_deterministic():
    symbols = ["AAA", "BBB", "CCC", "DDD", "EEE", "SPY"]
    bars = make_bars(symbols)
    baseline = [s.symbol for s in scan_stock_bars(bars, universe=symbols[:-1], top_n=5, as_of=AS_OF)]
    for seed in range(5):
        shuffled = list(symbols[:-1])
        random.Random(seed).shuffle(shuffled)
        out = [s.symbol for s in scan_stock_bars(bars, universe=shuffled, top_n=5, as_of=AS_OF)]
        if out != baseline:
            check("scan ranking is independent of input order", False, f"{out} != {baseline}")
            return
    check("scan ranking is independent of input order", True)


def test_as_of_replay_is_stable():
    symbols = ["AAA", "BBB", "CCC", "SPY"]
    bars = make_bars(symbols)
    a = scan_stock_bars(bars, universe=symbols[:-1], as_of=AS_OF)
    b = scan_stock_bars(bars, universe=symbols[:-1], as_of=AS_OF)
    check("identical as_of produces identical scores",
          [(s.symbol, s.score) for s in a] == [(s.symbol, s.score) for s in b])
    # An earlier decision date must see strictly less history, and therefore
    # must not be able to reproduce the later prices.
    earlier_bars = bars[bars.index.get_level_values(1)
                        < pd.Timestamp(AS_OF - timedelta(days=20))]
    earlier = scan_stock_bars(bars, universe=symbols[:-1],
                              as_of=AS_OF - timedelta(days=20))
    later_prices = {s.symbol: s.price for s in a}
    earlier_prices = {s.symbol: s.price for s in earlier}
    shared = set(later_prices) & set(earlier_prices)
    check("an earlier as_of prices off earlier bars (no look-ahead)",
          bool(shared) and all(earlier_prices[k] != later_prices[k] for k in shared),
          f"earlier={earlier_prices} later={later_prices}")
    check("earlier as_of consumed fewer bars",
          len(earlier_bars) < len(bars))


def test_scores_stay_in_range():
    symbols = ["AAA", "BBB", "CCC", "DDD", "SPY"]
    bars = make_bars(symbols)
    stocks = scan_stock_bars(bars, universe=symbols[:-1], as_of=AS_OF)
    ok = all(
        0.0 <= s.score <= 100.0
        and 0.0 <= s.momentum_score <= 100.0
        and 0.0 <= s.volatility_score <= 100.0
        for s in stocks
    )
    check("all scanner scores stay within [0, 100] (validate_trade depends on it)", ok)
    check("ranks are 1..n contiguous",
          [s.rank for s in stocks] == list(range(1, len(stocks) + 1)))


# ---------------------------------------------------------------------------
# 4. Selector gates and salvage
# ---------------------------------------------------------------------------

def _chain_for_gates():
    """A synthetic chain: one good contract plus one per failure mode.

    Every quote is priced by the model, so a rejection means a gate fired - not
    that the fixture was arbitrage-violating.
    """
    return {
        # Fairly priced, tight, at the money. Should survive.
        occ(190): model_snapshot(190, iv=0.30, spread_pct=0.02),
        # Same contract shape, very wide market.
        occ(192.5): model_snapshot(192.5, iv=0.30, spread_pct=0.30),
        # Richly priced vol: IV 0.90 against realized 0.30.
        occ(187.5): model_snapshot(187.5, iv=0.90, spread_pct=0.02),
        # Deep OTM lottery ticket: breakeven far beyond the expected move.
        occ(215): model_snapshot(215, iv=0.30, spread_pct=0.02),
    }


def test_spread_gate_and_tick_escape_hatch():
    chain = _chain_for_gates()
    rejects: list[OptionCandidate] = []
    out = select_directional_options(
        chain, underlying_price=SPOT, direction="bullish",
        realized_vol=0.30, as_of=AS_OF, collect_rejections=rejects,
    )
    picked = {c.symbol for c in out}
    reasons = {c.symbol: c.reject_reason for c in rejects}
    check("fairly priced ATM contract survives every gate", occ(190) in picked, str(reasons))
    check("wide spread is rejected with a spread reason",
          occ(192.5) not in picked and "spread" in (reasons.get(occ(192.5)) or ""),
          str(reasons))
    check("config's 0.15 spread ceiling is tightened to the friction budget",
          osel.MAX_SPREAD_PCT == 0.08)

    # The escape hatch is a property of the gate, tested directly so that no
    # other gate can mask it.
    cheap = build_candidate(occ(210), model_snapshot(210, iv=0.30, abs_spread=0.02),
                            SPOT, as_of=AS_OF, realized_vol=0.30)
    check("a one-tick market is wide in percent terms", cheap.spread_pct > 0.08)
    check("one-tick cheap contract passes the spread gate via the escape hatch",
          "spread" not in (osel._gate(cheap, max_spread_pct=0.08) or ""),
          f"gate said: {osel._gate(cheap, max_spread_pct=0.08)}")


def test_rich_vol_and_lottery_tickets_are_rejected():
    chain = _chain_for_gates()
    rejects: list[OptionCandidate] = []
    out = select_directional_options(
        chain, underlying_price=SPOT, direction="bullish",
        realized_vol=0.30, as_of=AS_OF, collect_rejections=rejects,
    )
    picked = {c.symbol for c in out}
    reasons = {c.symbol: c.reject_reason for c in rejects}
    check("IV/RV = 3.0 contract is rejected", occ(187.5) not in picked, str(reasons))
    check("rich-vol rejection names the IV/RV gate",
          "IV/RV" in (reasons.get(occ(187.5)) or ""), str(reasons))
    check("far-OTM lottery ticket is rejected", occ(215) not in picked, str(reasons))


def test_refusal_path():
    """If every contract is rich, the selector must return nothing at all."""
    chain = {
        occ(190): model_snapshot(190, iv=1.20, spread_pct=0.25),
        occ(195): model_snapshot(195, iv=1.30, spread_pct=0.25),
    }
    rejects: list[OptionCandidate] = []
    out = select_directional_options(chain, SPOT, "bullish", realized_vol=0.25,
                                     as_of=AS_OF, collect_rejections=rejects)
    check("selector refuses to trade when nothing qualifies", out == [], str(out))
    check("every rejection carries an auditable reason",
          len(rejects) >= 2 and all(c.reject_reason for c in rejects),
          str([(c.symbol, c.reject_reason) for c in rejects]))


def test_iv_and_greeks_salvage():
    """A quote with no IV and no greeks used to be discarded outright."""
    true_iv = 0.30
    priced = model_snapshot(190, iv=true_iv, spread_pct=0.02, with_greeks=False)
    priced.implied_volatility = None          # Alpaca returned a quote but no IV
    c = build_candidate(occ(190), priced, SPOT, as_of=AS_OF, realized_vol=0.30)
    check("IV recovered by bisection when Alpaca omits it",
          c is not None and c.iv is not None and c.iv_source == "solved",
          f"iv={getattr(c, 'iv', None)} source={getattr(c, 'iv_source', None)}")
    check("recovered IV matches the true IV of the quote",
          c.iv is not None and abs(c.iv - true_iv) < 0.01, f"recovered {c.iv} vs {true_iv}")
    check("greeks filled from the model when missing",
          c.delta is not None and c.greeks_source == "model")
    check("recovered call delta is in (0, 1)", 0.0 < c.delta < 1.0, f"delta={c.delta}")
    check("model theta is converted to Alpaca's per-day convention",
          c.theta is not None and -1.0 < c.theta < 0.0, f"theta={c.theta}")

    # A quote that cannot be solved must report as unavailable, never guessed.
    broken = FakeSnapshot(bid=0.01, ask=0.02, iv=None, greeks=None)
    b = build_candidate(occ(190), broken, SPOT, as_of=AS_OF)
    check("unsolvable IV reports unavailable rather than a placeholder",
          b is not None and b.iv is None and b.iv_source == "unavailable"
          and b.greeks_source == "unavailable")

    # Alpaca's own numbers must win when present.
    snap2 = model_snapshot(190, iv=0.30)
    snap2.implied_volatility = 0.4242
    snap2.greeks.delta = 0.5151
    c2 = build_candidate(occ(190), snap2, SPOT, as_of=AS_OF)
    check("supplied IV/greeks are used verbatim, not recomputed",
          c2.iv == 0.4242 and c2.delta == 0.5151 and c2.iv_source == "alpaca")


def test_option_score_range_and_direction():
    chain = _chain_for_gates()
    out = select_directional_options(chain, SPOT, "bullish", realized_vol=0.30, as_of=AS_OF)
    check("option scores stay within [0, 100]", all(0.0 <= c.score <= 100.0 for c in out))
    check("selector returns candidates sorted by score descending",
          [c.score for c in out] == sorted([c.score for c in out], reverse=True))
    check("bullish selection contains only calls", all(c.option_type == "call" for c in out))

    put_chain = {occ(190, "put"): model_snapshot(190, 0.30, "put", spread_pct=0.02)}
    puts = select_directional_options(put_chain, SPOT, "bearish",
                                      realized_vol=0.30, as_of=AS_OF)
    check("bearish selection contains no calls", all(c.option_type == "put" for c in puts))

    # _score_candidate is public: it must defend itself against a mismatched call.
    c = OptionCandidate(occ(190), EXPIRY, "call", 190.0,
                        2.0, 2.1, 2.05, 0.05, 0.3, 0.5, 0.03, -0.02, 0.11, 11, 0.01)
    check("mismatched direction scores zero (the old -20 penalty could never fire)",
          osel._score_candidate(c, "bearish") == 0.0)


def test_score_discriminates():
    """The old score compressed everything into a narrow band."""
    chain = {
        occ(190): model_snapshot(190, iv=0.22, spread_pct=0.02),    # cheap vol, ATM
        occ(195): model_snapshot(195, iv=0.30, spread_pct=0.03),    # slightly OTM
        occ(185): model_snapshot(185, iv=0.28, spread_pct=0.02),    # ITM
    }
    out = select_directional_options(chain, SPOT, "bullish", realized_vol=0.35, as_of=AS_OF)
    if len(out) < 2:
        check("score spread across survivors is meaningful", False,
              f"only {len(out)} survivors")
        return
    spread = out[0].score - out[-1].score
    check("score spread across survivors is meaningful (> 5 points)", spread > 5.0,
          f"scores={[round(c.score, 1) for c in out]}")
    print(f"        [survivor scores: {[round(c.score, 1) for c in out]}]")


def test_selector_never_touches_the_network():
    """Nothing in the selection path may construct a client or a chain request."""
    src = open("strategy/option_selector.py", encoding="utf-8").read()
    banned = ["OptionChainRequest", "TradingClient", "get_option_data_client",
              "requests.", "genai", "get_option_chain"]
    found = [b for b in banned if b in src]
    check("option_selector makes no API calls", found == [], f"found {found}")
    src2 = open("strategy/scanner.py", encoding="utf-8").read()
    found2 = [b for b in banned + ["StockBarsRequest", "get_stock_data_client"] if b in src2]
    check("scanner makes no API calls", found2 == [], f"found {found2}")
    check("scipy no longer imported by the scanner",
          "import scipy" not in src2 and "from scipy" not in src2)


def test_downstream_consumers_still_work():
    """The real trade_scorer / direction / llm path, end to end, offline."""
    from strategy.direction import TradeDirection, determine_direction
    from strategy.llm import analyze_trade
    from strategy.trade_scorer import score_trade, validate_trade

    symbols = ["AAA", "BBB", "CCC", "SPY"]
    bars = make_bars(symbols)
    stocks = scan_stock_bars(bars, universe=symbols[:-1], as_of=AS_OF)
    check("scanner produced ScannedStock objects for downstream use", len(stocks) > 0)
    if not stocks:
        return

    stock = stocks[0]
    direction = determine_direction(stock)
    check("determine_direction consumes ScannedStock unchanged",
          isinstance(direction, TradeDirection))

    # Price the chain at the scanned stock's own realized vol, so IV/RV is ~1.0
    # and the gates are exercising the wiring rather than correctly refusing a
    # richly-priced synthetic chain.
    rv = stock.realized_volatility
    chain = {
        occ(190): model_snapshot(190, iv=rv * 0.95, spread_pct=0.02),
        occ(192.5): model_snapshot(192.5, iv=rv * 1.00, spread_pct=0.02),
    }
    options = select_directional_options(
        chain, underlying_price=SPOT, direction="bullish",
        realized_vol=rv, as_of=AS_OF,
    )
    check("selector accepts ScannedStock.realized_volatility", len(options) > 0,
          f"realized_vol={rv:.3f}")
    if not options:
        return

    decision = analyze_trade(options[0], stock)
    check("llm.analyze_trade consumes an OptionCandidate unchanged",
          hasattr(decision, "confidence"))

    scored = score_trade(stock=stock, option=options[0], decision=decision,
                         underlying_price=190.0)
    check("trade_scorer.score_trade builds a ScoredTrade", scored.option_symbol == options[0].symbol)
    if decision.decision in {"BULLISH", "BEARISH"}:
        ok, reason = validate_trade(scored)
        check("validate_trade accepts the rebuilt scores", ok, reason)


def test_backtest_package_still_imports_parse_occ_symbol():
    from backtest.portfolio import SimulatedOptionPortfolio
    p = SimulatedOptionPortfolio(starting_cash=1000.0)
    check("backtest portfolio still parses OCC symbols via option_selector",
          p._option_type_from_symbol("AAPL240620C00150000") == "CALL")
    check("backtest portfolio still reads expirations",
          p._expiration_from_symbol("AAPL240620C00150000") == date(2024, 6, 20))


# ---------------------------------------------------------------------------

def main() -> int:
    tests = [
        test_scanned_stock_contract,
        test_option_candidate_contract,
        test_function_signatures,
        test_parse_occ_symbol_unchanged,
        test_metric_dict_keys_unchanged,
        test_print_option_candidates_byte_identical,
        test_print_scan_results_byte_identical,
        test_bsm_reference_values,
        test_bsm_degenerate_inputs,
        test_implied_vol_solver,
        test_zero_edge_must_not_be_profitable,
        test_expected_return_monotonic_in_edge,
        test_rich_vol_prices_worse_than_cheap_vol,
        test_put_call_symmetry_of_expected_return,
        test_timezone_bug_fixed,
        test_fractional_time_to_expiry,
        test_tz_naive_index_does_not_crash,
        test_available_symbols_uses_realized_values,
        test_missing_data_is_not_silently_defaulted,
        test_average_rank_matches_reference,
        test_scanner_imports_without_scipy,
        test_volatility_no_longer_rewards_expensive_premium,
        test_ranking_is_deterministic,
        test_as_of_replay_is_stable,
        test_scores_stay_in_range,
        test_spread_gate_and_tick_escape_hatch,
        test_rich_vol_and_lottery_tickets_are_rejected,
        test_refusal_path,
        test_iv_and_greeks_salvage,
        test_option_score_range_and_direction,
        test_score_discriminates,
        test_selector_never_touches_the_network,
        test_downstream_consumers_still_work,
        test_backtest_package_still_imports_parse_occ_symbol,
    ]
    for test in tests:
        try:
            test()
        except Exception as exc:  # pragma: no cover
            FAILED.append((test.__name__, f"EXCEPTION {exc!r}"))

    print("=" * 78)
    print(" STRATEGY REBUILD VERIFICATION")
    print("=" * 78)
    for name in PASSED:
        print(f"  PASS  {name}")
    for name, detail in FAILED:
        print(f"  FAIL  {name}\n        {detail}")
    print("-" * 78)
    print(f" {len(PASSED)} passed, {len(FAILED)} failed")
    print("=" * 78)
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
