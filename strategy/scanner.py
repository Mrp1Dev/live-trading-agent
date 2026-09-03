from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional
import numpy as np
import pandas as pd

from strategy.universe import BENCHMARK_SYMBOL, UNIVERSE

# ---------------------------------------------------------------------------
# Market clock
# ---------------------------------------------------------------------------
# ZoneInfo needs the IANA database, which a bare Windows install does not ship.
# Falling back to a fixed offset keeps the module importable; the exchange date
# is what matters here and an hour of DST drift cannot change it.

try:  # pragma: no cover - environment dependent
    from zoneinfo import ZoneInfo

    MARKET_TZ = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover - environment dependent
    from datetime import timezone

    MARKET_TZ = timezone(timedelta(hours=-4), name="America/New_York")

# ---------------------------------------------------------------------------
# Scoring configuration
# ---------------------------------------------------------------------------

# Composite weights. These sum to 1.0 and each component is on a 0-100 scale.
WEIGHT_MOMENTUM = 0.30
WEIGHT_VOLUME = 0.25
WEIGHT_VOLATILITY = 0.20
WEIGHT_RELATIVE_STRENGTH = 0.15
WEIGHT_TREND = 0.10

# Realized-volatility regime preference, annualized.
#
# The previous implementation scored volatility as an unsigned percentile, so the
# single highest-volatility name in the universe scored 100. High realized vol
# means high implied vol means expensive premium, and that premium was then
# bought outright as a short-dated single leg: the selection criterion and the
# instrument worked against each other.
#
# Volatility now expresses REGIME TRADEABILITY rather than "more is better":
# a name too quiet to travel far enough to cover a breakeven scores badly, and so
# does a name whose vol is so extreme that the premium is unaffordable and jump
# risk dominates. Whether the premium is fairly priced is a different question,
# and it is answered in one place only - the option selector's IV-vs-realized-vol
# gate - so the two stages no longer fight over the same signal.
# Calibrated against a live 249-name scan: with a [0.25, 0.55] band, 14 of the
# top 20 scored exactly 100 and this factor contributed no discrimination at all,
# because anything surviving a momentum screen already realizes 25-55% vol. The
# tighter band separates the field again and prices blow-off names (MSTR at 82%,
# CRM at 79%) well below merely-active ones.
VOL_BAND_LOW = 0.30       # below this, the name is too quiet to pay for premium
VOL_BAND_HIGH = 0.45      # above this, premium gets expensive fast
VOL_FLOOR = 0.10          # scores 0
VOL_CEILING = 0.90        # scores 0

MIN_BARS_REQUIRED = 21


@dataclass
class ScannedStock:
    """Represents a scanned stock with raw technical metrics, component sub-scores,
    and its composite score.
    """

    symbol: str
    price: float
    return_1d: float
    return_5d: float
    return_20d: float
    volume: float
    avg_volume_20d: float
    volume_ratio: float
    sma_20: float
    sma_50: float
    distance_sma20: float
    distance_sma50: float
    realized_volatility: float
    relative_strength_spy: float
    momentum_score: float
    volume_score: float
    volatility_score: float
    relative_strength_score: float
    trend_score: float
    score: float
    rank: Optional[int] = None
    intraday_return: Optional[float] = None


# ---------------------------------------------------------------------------
# Bar preparation
# ---------------------------------------------------------------------------


def _exchange_today(as_of: Optional[datetime] = None) -> date:
    """Today's date on the exchange clock.

    Never use the local system date. On a machine east of New York, the local
    date rolls over hours before the exchange date does, which would classify the
    most recent completed session as "today" and silently drop it.
    """
    if as_of is None:
        return datetime.now(MARKET_TZ).date()
    if as_of.tzinfo is None:
        return as_of.date()
    return as_of.astimezone(MARKET_TZ).date()


def _completed_daily_bars(
    df: pd.DataFrame,
    as_of: Optional[datetime] = None,
) -> pd.DataFrame:
    """Return only completed daily bars, strictly before the exchange date.

    `as_of` lets the scanner be replayed at a historical decision timestamp
    instead of the wall clock, which is what makes a deterministic backtest
    possible. Default behaviour is unchanged.
    """
    cutoff = _exchange_today(as_of)

    sorted_df = df.sort_index()

    index = sorted_df.index
    # A tz-naive DatetimeIndex still HAS a `.tz` attribute (it is None), so
    # testing with hasattr calls tz_convert on naive data and raises. Test the
    # value, not the attribute's existence.
    if getattr(index, "tz", None) is not None:
        index_dates = index.tz_convert(MARKET_TZ).date
    elif isinstance(index, pd.DatetimeIndex):
        index_dates = index.date
    else:
        try:
            index_dates = pd.to_datetime(index).date
        except (TypeError, ValueError):
            return sorted_df

    return sorted_df.loc[index_dates < cutoff]


def _clean_close_volume(sorted_df: pd.DataFrame) -> Optional[tuple[np.ndarray, np.ndarray]]:
    """Extract close/volume arrays, or None if the data cannot support metrics.

    Missing or non-positive prices are treated as a data gap and reject the
    symbol. They are deliberately NOT forward-filled or zero-filled: a fabricated
    price produces a real-looking score, which is worse than no score at all.
    """
    if "close" not in sorted_df.columns or "volume" not in sorted_df.columns:
        return None

    closes = pd.to_numeric(sorted_df["close"], errors="coerce").to_numpy(dtype=float)
    volumes = pd.to_numeric(sorted_df["volume"], errors="coerce").to_numpy(dtype=float)

    if closes.size < MIN_BARS_REQUIRED:
        return None
    if not np.all(np.isfinite(closes)) or np.any(closes <= 0):
        return None
    if not np.all(np.isfinite(volumes)) or np.any(volumes < 0):
        return None

    return closes, volumes


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


def extract_stock_metrics(
    symbol: str,
    stock_df: pd.DataFrame,
    spy_return_20d: float,
    as_of: Optional[datetime] = None,
    live_metric: Optional[Dict[str, float]] = None,
) -> Optional[Dict]:
    """Calculate raw technical metrics for a single stock from its daily bars

    and optional live market metrics.
    Returns None when the bars cannot support a trustworthy metric set, so the
    symbol is dropped rather than scored on defaults.
    """
    sorted_df = _completed_daily_bars(stock_df, as_of=as_of)
    if len(sorted_df) < MIN_BARS_REQUIRED:
        return None

    cleaned = _clean_close_volume(sorted_df)
    if cleaned is None:
        return None

    closes, volumes = cleaned

    live_price = float(live_metric.get("price", 0.0) or 0.0) if live_metric else 0.0
    if live_price > 0:
        # Live market data takes precedence over yesterday's close
        current_price = live_price

        # 1. Live Returns
        if live_metric.get("change_pct") is not None:
            ret_1d = float(live_metric["change_pct"])
        else:
            ret_1d = float((current_price - closes[-1]) / closes[-1])

        ret_5d = float((current_price - closes[-5]) / closes[-5]) if len(closes) >= 5 else ret_1d
        ret_20d = float((current_price - closes[-20]) / closes[-20]) if len(closes) >= 20 else ret_5d

        # 2. Volume: today's live accumulated volume vs 20-day historical average
        avg_volume_20d = float(np.mean(volumes[-20:]))
        live_vol = float(live_metric.get("volume", 0.0) or 0.0)
        current_volume = live_vol if live_vol > 0 else float(volumes[-1])
        volume_ratio = current_volume / avg_volume_20d if avg_volume_20d > 0 else 1.0

        # 3. Moving Averages updated with live price
        sma_20 = float(np.mean(np.append(closes[-19:], current_price)))
        dist_sma20 = (current_price - sma_20) / sma_20 if sma_20 > 0 else 0.0

        if len(closes) >= 49:
            sma_50 = float(np.mean(np.append(closes[-49:], current_price)))
            dist_sma50 = (current_price - sma_50) / sma_50 if sma_50 > 0 else dist_sma20
        else:
            sma_50 = sma_20
            dist_sma50 = dist_sma20

        # 4. Realized Volatility updated with live move
        log_ret_live = np.log(current_price / closes[-1]) if current_price > 0 and closes[-1] > 0 else 0.0
        log_returns = np.append(np.diff(np.log(closes[-20:])), log_ret_live)
        realized_vol = float(np.std(log_returns, ddof=1) * np.sqrt(252)) if log_returns.size > 1 else 0.0
        intraday_return = float(live_metric.get("intraday_return", 0.0) or 0.0)
    else:
        # Fallback to historical daily bars
        current_price = float(closes[-1])

        # 1. Returns: 1-day, 5-day, 20-day
        ret_1d = float((closes[-1] - closes[-2]) / closes[-2])
        ret_5d = float((closes[-1] - closes[-6]) / closes[-6])
        ret_20d = float((closes[-1] - closes[-21]) / closes[-21])

        # 2. Volume / Average Volume (20-day historical average excluding current bar)
        current_volume = float(volumes[-1])
        avg_volume_20d = float(np.mean(volumes[-21:-1]))

        if avg_volume_20d <= 0:
            return None

        volume_ratio = current_volume / avg_volume_20d

        # 3. Distance from Moving Averages (20d & 50d)
        sma_20 = float(np.mean(closes[-20:]))
        dist_sma20 = (current_price - sma_20) / sma_20 if sma_20 > 0 else 0.0

        if len(closes) >= 50:
            sma_50 = float(np.mean(closes[-50:]))
            dist_sma50 = (current_price - sma_50) / sma_50 if sma_50 > 0 else dist_sma20
        else:
            sma_50 = sma_20
            dist_sma50 = dist_sma20

        # 4. Realized Volatility (Annualized 20-day log return standard deviation)
        log_returns = np.diff(np.log(closes[-21:]))
        realized_vol = float(np.std(log_returns, ddof=1) * np.sqrt(252)) if log_returns.size > 1 else 0.0
        intraday_return = None

    # 5. Relative Strength vs SPY (20-day excess return over benchmark)
    rs_spy = float(ret_20d - spy_return_20d)

    # Raw factor indicators for ranking
    raw_momentum = 0.20 * ret_1d + 0.30 * ret_5d + 0.50 * ret_20d
    raw_volume = volume_ratio
    raw_volatility = realized_vol
    raw_relative_strength = rs_spy
    raw_trend = 0.50 * dist_sma20 + 0.50 * dist_sma50

    return {
        "symbol": symbol,
        "price": current_price,
        "return_1d": ret_1d,
        "return_5d": ret_5d,
        "return_20d": ret_20d,
        "volume": current_volume,
        "avg_volume_20d": avg_volume_20d,
        "volume_ratio": volume_ratio,
        "sma_20": sma_20,
        "sma_50": sma_50,
        "distance_sma20": dist_sma20,
        "distance_sma50": dist_sma50,
        "realized_volatility": realized_vol,
        "relative_strength_spy": rs_spy,
        "raw_momentum": raw_momentum,
        "raw_volume": raw_volume,
        "raw_volatility": raw_volatility,
        "raw_relative_strength": raw_relative_strength,
        "raw_trend": raw_trend,
        "intraday_return": intraday_return,
    }


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _average_rank(values: np.ndarray) -> np.ndarray:
    """Average-method ranks, identical to scipy.stats.rankdata(values).

    Implemented locally so the scanner does not pull scipy in for one function.
    Ties receive the mean of the ranks they span.
    """
    n = values.size
    order = np.argsort(values, kind="mergesort")
    ordered = values[order]
    ranks = np.empty(n, dtype=float)

    start = 0
    while start < n:
        stop = start
        while stop + 1 < n and ordered[stop + 1] == ordered[start]:
            stop += 1
        ranks[order[start : stop + 1]] = (start + stop) / 2.0 + 1.0
        start = stop + 1

    return ranks


def _percentile_scores(values: np.ndarray) -> np.ndarray:
    """Map raw indicator values onto a 0-100 percentile scale."""
    n = values.size
    if n <= 1:
        return np.full(n, 100.0)
    return (_average_rank(values) - 1.0) / (n - 1.0) * 100.0


def volatility_regime_score(realized_vol: float) -> float:
    """Score a realized-volatility regime for tradeability, on 0-100.

    Piecewise linear: full marks inside the band, tapering to zero at the floor
    and the ceiling. This is an ABSOLUTE score, not a percentile, so it does not
    depend on which other symbols happened to be scanned in the same batch -
    the same stock on the same day always scores the same.
    """
    if not np.isfinite(realized_vol) or realized_vol <= VOL_FLOOR:
        return 0.0
    if realized_vol >= VOL_CEILING:
        return 0.0
    if VOL_BAND_LOW <= realized_vol <= VOL_BAND_HIGH:
        return 100.0
    if realized_vol < VOL_BAND_LOW:
        return (realized_vol - VOL_FLOOR) / (VOL_BAND_LOW - VOL_FLOOR) * 100.0
    return (VOL_CEILING - realized_vol) / (VOL_CEILING - VOL_BAND_HIGH) * 100.0


def score_and_rank_stocks(metrics_list: List[Dict]) -> List[ScannedStock]:
    """Normalize raw indicators into 0-100 scores and compute the weighted composite:

        score = (
            0.30 * momentum_score
            + 0.25 * volume_score
            + 0.20 * volatility_score
            + 0.15 * relative_strength_score
            + 0.10 * trend_score
        )

    Momentum, volume, relative strength and trend are cross-sectional percentiles
    - they are comparative measures by nature. Volatility is scored absolutely
    against a regime band (see volatility_regime_score).
    """
    n = len(metrics_list)
    if n == 0:
        return []

    raw_momentum = np.array([m["raw_momentum"] for m in metrics_list], dtype=float)
    raw_volume = np.array([m["raw_volume"] for m in metrics_list], dtype=float)
    raw_relative_strength = np.array([m["raw_relative_strength"] for m in metrics_list], dtype=float)
    raw_trend = np.array([m["raw_trend"] for m in metrics_list], dtype=float)

    momentum_scores = _percentile_scores(raw_momentum)
    volume_scores = _percentile_scores(raw_volume)
    rs_scores = _percentile_scores(raw_relative_strength)
    trend_scores = _percentile_scores(raw_trend)

    scanned_stocks: List[ScannedStock] = []
    for i, m in enumerate(metrics_list):
        mom_score = float(momentum_scores[i])
        vol_score = float(volume_scores[i])
        vola_score = float(volatility_regime_score(m["raw_volatility"]))
        rs_score = float(rs_scores[i])
        tr_score = float(trend_scores[i])

        composite_score = (
            WEIGHT_MOMENTUM * mom_score
            + WEIGHT_VOLUME * vol_score
            + WEIGHT_VOLATILITY * vola_score
            + WEIGHT_RELATIVE_STRENGTH * rs_score
            + WEIGHT_TREND * tr_score
        )

        scanned_stocks.append(
            ScannedStock(
                symbol=m["symbol"],
                price=m["price"],
                return_1d=m["return_1d"],
                return_5d=m["return_5d"],
                return_20d=m["return_20d"],
                volume=m["volume"],
                avg_volume_20d=m["avg_volume_20d"],
                volume_ratio=m["volume_ratio"],
                sma_20=m["sma_20"],
                sma_50=m["sma_50"],
                distance_sma20=m["distance_sma20"],
                distance_sma50=m["distance_sma50"],
                realized_volatility=m["realized_volatility"],
                relative_strength_spy=m["relative_strength_spy"],
                momentum_score=mom_score,
                volume_score=vol_score,
                volatility_score=vola_score,
                relative_strength_score=rs_score,
                trend_score=tr_score,
                score=composite_score,
                intraday_return=m.get("intraday_return"),
            )
        )

    # Sort descending by score, breaking ties on symbol so an identical universe
    # always produces an identical ordering regardless of input order.
    scanned_stocks.sort(key=lambda s: (-s.score, s.symbol))
    for rank_idx, stock in enumerate(scanned_stocks, start=1):
        stock.rank = rank_idx

    return scanned_stocks


# ---------------------------------------------------------------------------
# Universe scan
# ---------------------------------------------------------------------------


def _available_symbols(bars_df: pd.DataFrame) -> set:
    """Symbols that actually have rows in the frame.

    `index.levels[0]` returns every category the index was BUILT with, including
    symbols with no surviving rows, so membership tests against it report symbols
    that are not really present. Use the realized values instead.
    """
    index = bars_df.index
    if isinstance(index, pd.MultiIndex):
        return set(index.get_level_values(0).unique())
    return set()


def _benchmark_return_20d(
    bars_df: pd.DataFrame,
    benchmark_symbol: str,
    as_of: Optional[datetime] = None,
    live_spy_price: Optional[float] = None,
) -> float:
    """20-day benchmark return, or 0.0 when the benchmark is unavailable.

    A missing benchmark makes relative strength collapse to plain return_20d,
    which is a defensible fallback because the factor is only ever used as a
    cross-sectional percentile: with no benchmark every symbol is offset by the
    same constant, so the ranking is unchanged.
    """
    if benchmark_symbol not in _available_symbols(bars_df):
        return 0.0

    spy_df = _completed_daily_bars(bars_df.xs(benchmark_symbol, level=0), as_of=as_of)
    if "close" not in spy_df.columns:
        return 0.0

    spy_closes = pd.to_numeric(spy_df["close"], errors="coerce").to_numpy(dtype=float)
    spy_closes = spy_closes[np.isfinite(spy_closes)]
    spy_closes = spy_closes[spy_closes > 0]

    if live_spy_price is not None and live_spy_price > 0:
        if spy_closes.size >= 20:
            return float((live_spy_price - spy_closes[-20]) / spy_closes[-20])
        if spy_closes.size > 0:
            return float((live_spy_price - spy_closes[0]) / spy_closes[0])

    if spy_closes.size >= MIN_BARS_REQUIRED:
        return float((spy_closes[-1] - spy_closes[-21]) / spy_closes[-21])
    if spy_closes.size > 1:
        return float((spy_closes[-1] - spy_closes[0]) / spy_closes[0])
    return 0.0


def scan_stock_bars(
    bars_df: pd.DataFrame,
    universe: Optional[List[str]] = None,
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    top_n: int = 15,
    as_of: Optional[datetime] = None,
    live_metrics: Optional[Dict[str, Dict[str, float]]] = None,
) -> List[ScannedStock]:
    """Pure strategy scanner logic that processes a DataFrame of daily bars

    and returns the top N ranked candidates. Incorporates live_metrics when supplied.
    """
    target_universe = list(universe) if universe else list(UNIVERSE)

    if bars_df is None or len(bars_df) == 0:
        return []

    # 1. Benchmark return for the relative-strength factor (with live price if available)
    live_spy_price = None
    if live_metrics and benchmark_symbol in live_metrics:
        live_spy_price = live_metrics[benchmark_symbol].get("price")

    spy_return_20d = _benchmark_return_20d(
        bars_df,
        benchmark_symbol,
        as_of=as_of,
        live_spy_price=live_spy_price,
    )

    # 2. Extract metrics for all available stocks in universe
    metrics_list = []
    available_symbols = _available_symbols(bars_df)

    for symbol in target_universe:
        if symbol not in available_symbols:
            continue
        sdf = bars_df.xs(symbol, level=0)
        live_metric = live_metrics.get(symbol) if live_metrics else None
        metrics = extract_stock_metrics(
            symbol,
            sdf,
            spy_return_20d,
            as_of=as_of,
            live_metric=live_metric,
        )
        if metrics is not None:
            metrics_list.append(metrics)

    # 3. Score and rank
    ranked_stocks = score_and_rank_stocks(metrics_list)
    return ranked_stocks[:top_n]


def print_scan_results(stocks: List[ScannedStock], title: str = "Top Stock Scanner Picks") -> None:
    """Display scan results in a clear terminal table."""
    print(f"\n{'=' * 118}")
    print(f" {title.upper()} (Top {len(stocks)})")
    print(f"{'=' * 118}")

    header = (
        f"{'Rank':<5} {'Symbol':<7} {'Price':>8} {'1D Ret':>8} {'5D Ret':>8} {'20D Ret':>9} "
        f"{'Vol/Avg':>8} {'SMA20%':>8} {'RealVol':>8} {'RS vs SPY':>10} {'Score':>7}"
    )
    print(header)
    print(f"{'-' * 118}")

    for stock in stocks:
        rank_str = f"#{stock.rank}" if stock.rank is not None else "-"
        print(
            f"{rank_str:<5} "
            f"{stock.symbol:<7} "
            f"${stock.price:>7.2f} "
            f"{stock.return_1d:>+7.2%} "
            f"{stock.return_5d:>+7.2%} "
            f"{stock.return_20d:>+8.2%} "
            f"{stock.volume_ratio:>7.2f}x "
            f"{stock.distance_sma20:>+7.1%} "
            f"{stock.realized_volatility:>7.1%} "
            f"{stock.relative_strength_spy:>+9.2%} "
            f"{stock.score:>7.1f}"
        )

    print(f"{'=' * 118}\n")
