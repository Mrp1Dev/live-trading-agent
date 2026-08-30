from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

from alpaca.common.enums import Sort
from alpaca.data.requests import OptionBarsRequest, OptionTradesRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.enums import AssetStatus
from alpaca.trading.requests import GetOptionContractsRequest

from alpaca_client.client import get_option_data_client, get_trading_client
from backtest.models import HistoricalOptionContract
from strategy.option_selector import parse_occ_symbol


def _normalize_to_utc(value: datetime) -> datetime:
    """Normalize a datetime to UTC. Naive datetimes are treated as UTC explicitly."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fetch_metadata_candidates(
    underlying: str,
    decision_date: datetime,
    expiration_window_days: tuple[int, int],
) -> list[dict[str, Any]]:
    """Fetch option metadata only as a candidate generator for the historical universe."""
    client = get_trading_client()

    lower_offset, upper_offset = expiration_window_days
    candidate_start = decision_date.date() + timedelta(days=lower_offset)
    candidate_end = decision_date.date() + timedelta(days=upper_offset)

    candidates: list[dict[str, Any]] = []
    page_token: str | None = None

    while True:
        request = GetOptionContractsRequest(
            underlying_symbols=[underlying],
            status=AssetStatus.ACTIVE,
            expiration_date_gte=candidate_start,
            expiration_date_lte=candidate_end,
            limit=1000,
            page_token=page_token,
        )

        response = client.get_option_contracts(request)
        for contract in response.option_contracts or []:
            symbol = str(getattr(contract, "symbol", "")).strip()
            if not symbol:
                continue

            option_type_value = getattr(contract, "type", None)
            if hasattr(option_type_value, "value"):
                option_type_value = option_type_value.value
            option_type = str(option_type_value or "").upper()

            status_value = getattr(contract, "status", None)
            if hasattr(status_value, "value"):
                status_value = status_value.value
            status = str(status_value or "UNKNOWN").upper()

            expiration_value = getattr(contract, "expiration_date", None)
            if expiration_value is None:
                try:
                    expiration_value = parse_occ_symbol(symbol)[0]
                except ValueError:
                    continue
            if isinstance(expiration_value, str):
                expiration_value = date.fromisoformat(expiration_value)

            strike_value = getattr(contract, "strike_price", None)
            if strike_value is None:
                try:
                    _, _, strike_value = parse_occ_symbol(symbol)
                except ValueError:
                    continue

            candidates.append(
                {
                    "symbol": symbol,
                    "underlying_symbol": (getattr(contract, "underlying_symbol", None) or underlying).upper(),
                    "expiration_date": expiration_value,
                    "strike_price": float(strike_value),
                    "option_type": option_type if option_type in {"CALL", "PUT"} else None,
                    "tradable": bool(getattr(contract, "tradable", False)),
                    "status": status,
                }
            )

        if not response.next_page_token:
            break
        page_token = response.next_page_token

    return candidates


def _extract_symbol_set(frame: Any) -> set[str]:
    if frame is None or getattr(frame, "empty", True):
        return set()

    index = frame.index
    if hasattr(index, "get_level_values"):
        try:
            values = index.get_level_values(0)
            return {str(value) for value in values.unique()}
        except Exception:
            pass

    try:
        return {str(value) for value in index.unique()}
    except Exception:
        return set()


def _decision_window(decision_date: datetime) -> tuple[datetime, datetime]:
    utc_dt = _normalize_to_utc(decision_date)
    day_start = utc_dt.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return day_start - timedelta(hours=6), day_end + timedelta(hours=6)


def get_historical_option_universe(
    underlying: str,
    decision_date: datetime,
    expiration_window_days: tuple[int, int] = (-30, 120),
) -> list[HistoricalOptionContract]:
    """Return the historical option universe for a single decision date.

    The trading API contract metadata is used only to create candidate contracts.
    The final universe is validated using historical option bars/trades on the target date.
    """
    if not isinstance(decision_date, datetime):
        raise TypeError("decision_date must be a datetime instance")

    normalized_underlying = str(underlying).upper().strip()
    if not normalized_underlying:
        raise ValueError("underlying must not be empty")

    normalized_dt = _normalize_to_utc(decision_date)
    candidates = _fetch_metadata_candidates(normalized_underlying, normalized_dt, expiration_window_days)
    if not candidates:
        return []

    symbol_list = [entry["symbol"] for entry in candidates]
    data_client = get_option_data_client()
    window_start, window_end = _decision_window(normalized_dt)

    bars_request = OptionBarsRequest(
        symbol_or_symbols=symbol_list,
        start=window_start,
        end=window_end,
        timeframe=TimeFrame.Day,
        sort=Sort.ASC,
    )
    trades_request = OptionTradesRequest(
        symbol_or_symbols=symbol_list,
        start=window_start,
        end=window_end,
        sort=Sort.ASC,
    )

    bars_df = data_client.get_option_bars(bars_request).df
    trades_df = data_client.get_option_trades(trades_request).df

    bars_symbols = _extract_symbol_set(bars_df)
    trades_symbols = _extract_symbol_set(trades_df)
    historical_symbols = bars_symbols | trades_symbols

    contracts: list[HistoricalOptionContract] = []
    for entry in candidates:
        symbol = entry["symbol"]
        if symbol not in historical_symbols:
            continue

        option_type = entry["option_type"]
        if option_type not in {"CALL", "PUT"}:
            try:
                _, parsed_type, _ = parse_occ_symbol(symbol)
                option_type = parsed_type.upper()
            except ValueError:
                continue

        if option_type not in {"CALL", "PUT"}:
            continue

        expiration_value = entry["expiration_date"]
        strike_value = float(entry["strike_price"])
        underlying_symbol = (entry["underlying_symbol"] or normalized_underlying).upper()

        contracts.append(
            HistoricalOptionContract(
                symbol=symbol,
                underlying_symbol=underlying_symbol,
                expiration_date=expiration_value,
                strike_price=strike_value,
                option_type=option_type,
                tradable=bool(entry.get("tradable", False)),
                status=str(entry.get("status", "UNKNOWN")).upper(),
            )
        )

    contracts.sort(key=lambda item: (item.expiration_date, item.strike_price, item.symbol))
    return contracts


__all__ = ["get_historical_option_universe"]
