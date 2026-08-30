from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
from scipy.stats import rankdata

from strategy.universe import BENCHMARK_SYMBOL, UNIVERSE


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


def _completed_daily_bars(df: pd.DataFrame) -> pd.DataFrame:
    """Return only completed daily bars."""
    ny_today = datetime.now(ZoneInfo("America/New_York")).date()

    sorted_df = df.sort_index().copy()

    if hasattr(sorted_df.index, "tz"):
        index_dates = sorted_df.index.tz_convert(
            "America/New_York"
        ).date
    else:
        index_dates = sorted_df.index.date

    return sorted_df.loc[index_dates < ny_today]


def extract_stock_metrics(
    symbol: str,
    stock_df: pd.DataFrame,
    spy_return_20d: float,
) -> Optional[Dict]:
    """Calculate raw technical metrics for a single stock from its daily bars."""
    sorted_df = _completed_daily_bars(stock_df)
    if len(sorted_df) < 21:
        return None

    closes = sorted_df["close"].values
    volumes = sorted_df["volume"].values
    current_price = float(closes[-1])

    # 1. Returns: 1-day, 5-day, 20-day
    ret_1d = float((closes[-1] - closes[-2]) / closes[-2]) if len(closes) >= 2 else 0.0
    ret_5d = float((closes[-1] - closes[-6]) / closes[-6]) if len(closes) >= 6 else 0.0
    ret_20d = float((closes[-1] - closes[-21]) / closes[-21]) if len(closes) >= 21 else 0.0

    # 2. Volume / Average Volume (20-day historical average excluding current bar)
    current_volume = float(volumes[-1])
    if len(volumes) >= 21:
        avg_volume_20d = float(np.mean(volumes[-21:-1]))
    elif len(volumes) >= 2:
        avg_volume_20d = float(np.mean(volumes[:-1]))
    else:
        avg_volume_20d = float(volumes[0])

    volume_ratio = current_volume / avg_volume_20d if avg_volume_20d > 0 else 1.0

    # 3. Distance from Moving Averages (20d & 50d)
    sma_20 = float(np.mean(closes[-20:])) if len(closes) >= 20 else float(np.mean(closes))
    dist_sma20 = (current_price - sma_20) / sma_20 if sma_20 > 0 else 0.0

    if len(closes) >= 50:
        sma_50 = float(np.mean(closes[-50:]))
        dist_sma50 = (current_price - sma_50) / sma_50 if sma_50 > 0 else dist_sma20
    else:
        sma_50 = sma_20
        dist_sma50 = dist_sma20

    # 4. Realized Volatility (Annualized 20-day log return standard deviation)
    if len(closes) >= 21:
        log_returns = np.diff(np.log(closes[-21:]))
    else:
        log_returns = np.diff(np.log(closes))

    if len(log_returns) > 1:
        realized_vol = float(np.std(log_returns, ddof=1) * np.sqrt(252))
    else:
        realized_vol = 0.0

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
    }


def score_and_rank_stocks(metrics_list: List[Dict]) -> List[ScannedStock]:
    """Normalize raw indicators into 0-100 percentile scores and compute

    the weighted composite score:
        score = (
            0.30 * momentum_score
            + 0.25 * volume_score
            + 0.20 * volatility_score
            + 0.15 * relative_strength_score
            + 0.10 * trend_score
        )
    """
    n = len(metrics_list)
    if n == 0:
        return []

    if n == 1:
        m = metrics_list[0]
        return [
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
                momentum_score=100.0,
                volume_score=100.0,
                volatility_score=100.0,
                relative_strength_score=100.0,
                trend_score=100.0,
                score=100.0,
                rank=1,
            )
        ]

    # Percentile ranking (0 to 100) across universe
    raw_m = np.array([m["raw_momentum"] for m in metrics_list])
    raw_v = np.array([m["raw_volume"] for m in metrics_list])
    raw_vol = np.array([m["raw_volatility"] for m in metrics_list])
    raw_rs = np.array([m["raw_relative_strength"] for m in metrics_list])
    raw_t = np.array([m["raw_trend"] for m in metrics_list])

    momentum_scores = (rankdata(raw_m) - 1.0) / (n - 1.0) * 100.0
    volume_scores = (rankdata(raw_v) - 1.0) / (n - 1.0) * 100.0
    volatility_scores = (rankdata(raw_vol) - 1.0) / (n - 1.0) * 100.0
    rs_scores = (rankdata(raw_rs) - 1.0) / (n - 1.0) * 100.0
    trend_scores = (rankdata(raw_t) - 1.0) / (n - 1.0) * 100.0

    scanned_stocks = []
    for i, m in enumerate(metrics_list):
        mom_score = float(momentum_scores[i])
        vol_score = float(volume_scores[i])
        vola_score = float(volatility_scores[i])
        rs_score = float(rs_scores[i])
        tr_score = float(trend_scores[i])

        composite_score = (
            0.30 * mom_score
            + 0.25 * vol_score
            + 0.20 * vola_score
            + 0.15 * rs_score
            + 0.10 * tr_score
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
            )
        )

    # Sort descending by score and assign ranks
    scanned_stocks.sort(key=lambda s: s.score, reverse=True)
    for rank_idx, stock in enumerate(scanned_stocks, start=1):
        stock.rank = rank_idx

    return scanned_stocks


def scan_stock_bars(
    bars_df: pd.DataFrame,
    universe: Optional[List[str]] = None,
    benchmark_symbol: str = BENCHMARK_SYMBOL,
    top_n: int = 15,
) -> List[ScannedStock]:
    """Pure strategy scanner logic that processes a DataFrame of daily bars

    and returns the top N ranked candidates.
    """
    target_universe = list(universe) if universe else list(UNIVERSE)

    # 1. Calculate SPY 20d benchmark return
    spy_return_20d = 0.0

    if benchmark_symbol in bars_df.index.levels[0]:
        spy_df = bars_df.xs(
            benchmark_symbol,
            level=0,
        )

        spy_df = _completed_daily_bars(spy_df)

        spy_closes = spy_df["close"].values

        if len(spy_closes) >= 21:
            spy_return_20d = float(
                (spy_closes[-1] - spy_closes[-21])
                / spy_closes[-21]
            )
        elif len(spy_closes) > 1:
            spy_return_20d = float(
                (spy_closes[-1] - spy_closes[0])
                / spy_closes[0]
            )

    # 2. Extract metrics for all available stocks in universe
    metrics_list = []
    available_symbols = set(bars_df.index.levels[0])

    for symbol in target_universe:
        if symbol not in available_symbols:
            continue
        sdf = bars_df.xs(symbol, level=0)
        metrics = extract_stock_metrics(symbol, sdf, spy_return_20d)
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
