from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from alpaca.common.enums import Sort
from alpaca.data.requests import OptionBarsRequest, OptionTradesRequest
from alpaca.data.timeframe import TimeFrame

from alpaca_client.client import get_option_data_client
from backtest.models import HistoricalOptionCandidate
from strategy.option_selector import parse_occ_symbol

logger = logging.getLogger(__name__)

_VALID_EXPIRATION_WEEKDAYS = {1, 2, 3, 4, 5}


def _normalize_to_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _is_plausible_expiration(expiration_date: date, decision_date: datetime) -> bool:
    """Only accept expiration dates that are plausible option expirations, using the decision date as the sole source of truth.

    This fallback does not reconstruct a full option chain. It generates a historically observable candidate set only.
    """
    delta_days = (expiration_date - decision_date.date()).days
    if delta_days < 0:
        return False

    if expiration_date.weekday() not in _VALID_EXPIRATION_WEEKDAYS:
        return False

    # US equity monthly expirations commonly occur on Fridays; weekend/weekdays not used for plausibility.
    return True


def _build_occ_symbol(underlying: str, expiration: date, option_type: str, strike: float) -> str:
    root = underlying.upper().strip()
    if not root:
        raise ValueError("underlying must not be empty")

    if option_type.upper() not in {"CALL", "PUT"}:
        raise ValueError(f"option_type must be CALL or PUT: {option_type}")

    if strike <= 0:
        raise ValueError(f"strike must be positive: {strike}")

    yymmdd = expiration.strftime("%y%m%d")
    strike_int = int(round(strike * 1000))
    if strike_int <= 0:
        raise ValueError(f"strike converts to non-positive OCC strike: {strike}")

    return f"{root}{yymmdd}{option_type.upper()[0]}{strike_int:08d}"


def generate_candidate_symbols(
    underlying: str,
    underlying_price: float,
    decision_date: datetime,
    min_dte: int = 7,
    max_dte: int = 60,
    strike_range_pct: float = 0.15,
    strike_step: float | None = None,
    max_candidates: int = 80,
) -> list[str]:
    """Generate a fallback candidate universe from only the underlying, underlying price, and decision date.

    This is intentionally a historically observable candidate generator, not a complete historical option-chain reconstruction.
    It never reads current option snapshots, current metadata, or future market information.
    """
    if underlying_price <= 0:
        raise ValueError("underlying_price must be > 0")
    if min_dte <= 0 or max_dte <= 0 or min_dte > max_dte:
        raise ValueError("min_dte and max_dte must be positive and min_dte <= max_dte")
    if strike_range_pct <= 0:
        raise ValueError("strike_range_pct must be > 0")
    if max_candidates <= 0:
        raise ValueError("max_candidates must be > 0")

    if strike_step is None:
        # Fallback step based on the price band; deliberately coarse and deterministic.
        strike_step = max(0.25, round(underlying_price * 0.01, 2))

    lower_bound = underlying_price * (1.0 - strike_range_pct)
    upper_bound = underlying_price * (1.0 + strike_range_pct)

    candidate_symbols: list[str] = []
    seen: set[str] = set()

    current_day = decision_date.date()
    for dte in range(min_dte, max_dte + 1):
        expiration_date = current_day + timedelta(days=dte)
        if not _is_plausible_expiration(expiration_date, decision_date):
            continue

        for strike in _iter_strikes(lower_bound, upper_bound, strike_step):
            for option_type in ("CALL", "PUT"):
                if len(candidate_symbols) >= max_candidates:
                    break
                symbol = _build_occ_symbol(underlying, expiration_date, option_type, strike)
                if symbol in seen:
                    continue
                seen.add(symbol)
                candidate_symbols.append(symbol)
            if len(candidate_symbols) >= max_candidates:
                break
        if len(candidate_symbols) >= max_candidates:
            break

    logger.info("Generated candidates: %s", len(candidate_symbols))
    return candidate_symbols


def _iter_strikes(lower_bound: float, upper_bound: float, step: float):
    value = lower_bound
    while value <= upper_bound + step / 2:
        yield round(value, 4)
        value = round(value + step, 4)


def _frame_to_records(frame: Any) -> list[dict[str, Any]]:
    if frame is None or getattr(frame, "empty", True):
        return []
    try:
        result = frame.reset_index().to_dict(orient="records")
    except Exception:
        result = []
    return result


def validate_historical_candidates(
    candidates: list[str],
    decision_date: datetime,
    min_volume: int = 50,
    min_trade_count: int = 2,
    batch_size: int = 8,
) -> list[HistoricalOptionCandidate]:
    """Keep only candidates with actual historical option bars/trades at or before the decision date.

    IMPORTANT: This function validates the candidate set against historical option data only. It does not rely on current snapshots,
    metadata, or any live option chain. It is a historical observable filter for the fallback generator.
    """
    if not candidates:
        return []

    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")

    decision_dt_utc = _normalize_to_utc(decision_date)
    normalized_candidates = sorted(set(candidates))
    start_dt = decision_dt_utc - timedelta(days=2)
    end_dt = decision_dt_utc + timedelta(days=1)

    client = get_option_data_client()
    bars_records: list[dict[str, Any]] = []
    trades_records: list[dict[str, Any]] = []
    for index in range(0, len(normalized_candidates), batch_size):
        batch = normalized_candidates[index : index + batch_size]
        try:
            bars_request = OptionBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TimeFrame.Day,
                start=start_dt,
                end=end_dt,
                sort=Sort.ASC,
            )
            trades_request = OptionTradesRequest(
                symbol_or_symbols=batch,
                start=start_dt,
                end=end_dt,
                sort=Sort.ASC,
            )
            bars_df = client.get_option_bars(bars_request).df
            trades_df = client.get_option_trades(trades_request).df
        except Exception:
            logger.warning("Skipping historical validation batch for %s symbols due to API failure", len(batch))
            continue
        bars_records.extend(_frame_to_records(bars_df))
        trades_records.extend(_frame_to_records(trades_df))

    bar_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in bars_records:
        symbol = str(row.get("symbol", "")).strip()
        if not symbol:
            continue
        bar_by_symbol.setdefault(symbol, []).append(row)

    trade_by_symbol: dict[str, list[dict[str, Any]]] = {}
    for row in trades_records:
        symbol = str(row.get("symbol", "")).strip()
        if not symbol:
            continue
        trade_by_symbol.setdefault(symbol, []).append(row)

    final: list[HistoricalOptionCandidate] = []
    for symbol in normalized_candidates:
        bars = bar_by_symbol.get(symbol, [])
        trades = trade_by_symbol.get(symbol, [])
        if not bars and not trades:
            continue

        usable_bars = []
        for row in bars:
            ts = row.get("timestamp")
            if ts is None:
                continue
            ts_utc = _normalize_to_utc(ts)
            if ts_utc <= decision_dt_utc:
                usable_bars.append(row)

        usable_trades = []
        for row in trades:
            ts = row.get("timestamp")
            if ts is None:
                continue
            ts_utc = _normalize_to_utc(ts)
            if ts_utc <= decision_dt_utc:
                usable_trades.append(row)

        if not usable_bars and not usable_trades:
            continue

        selected_bar = usable_bars[-1] if usable_bars else None
        if selected_bar is None:
            volume = 0
            trade_count = 0
            vwap = None
            data_timestamp = usable_trades[-1].get("timestamp") if usable_trades else None
        else:
            volume = int(selected_bar.get("volume", 0) or 0)
            trade_count = int(selected_bar.get("trade_count", 0) or 0)
            vwap = float(selected_bar.get("vwap", 0.0) or 0.0)
            data_timestamp = selected_bar.get("timestamp")

        if volume < min_volume or trade_count < min_trade_count:
            continue

        try:
            expiration_date, option_type, strike_price = parse_occ_symbol(symbol)
        except ValueError:
            continue

        historical_price = float(selected_bar.get("close")) if selected_bar is not None and selected_bar.get("close") is not None else 0.0
        underlying_symbol = symbol[:-15]
        if not underlying_symbol:
            continue

        final.append(
            HistoricalOptionCandidate(
                symbol=symbol,
                underlying=underlying_symbol,
                expiration=expiration_date,
                strike=float(strike_price),
                option_type=option_type.upper(),
                dte=(expiration_date - decision_dt_utc.date()).days,
                historical_price=float(historical_price),
                volume=int(volume),
                trade_count=int(trade_count),
                vwap=float(vwap) if vwap is not None else None,
                data_timestamp=data_timestamp,
            )
        )

    logger.info("Historical candidates: %s", len(final))
    logger.info("Liquid candidates: %s", sum(1 for item in final if item.volume is not None and item.trade_count is not None and item.volume >= min_volume and item.trade_count >= min_trade_count))
    return final


__all__ = [
    "generate_candidate_symbols",
    "validate_historical_candidates",
]
