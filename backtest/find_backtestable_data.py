from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest.data_provider import get_option_bars, get_option_trades
from backtest.option_universe import _fetch_metadata_candidates
from strategy.option_selector import parse_occ_symbol


UNDERLYINGS = ["AAPL", "NVDA", "SPY", "QQQ", "TSLA", "MSFT"]
DATES = [
    datetime(2024, 6, 5, 12, 0, 0),
    datetime(2024, 7, 15, 12, 0, 0),
    datetime(2024, 9, 20, 12, 0, 0),
    datetime(2024, 11, 1, 12, 0, 0),
    datetime(2025, 1, 15, 12, 0, 0),
]


def _normalize_frame_rows(frame: Any) -> list[dict[str, Any]]:
    if frame is None:
        return []
    if hasattr(frame, "to_dict"):
        try:
            return frame.to_dict("records")
        except Exception:
            pass
    if isinstance(frame, list):
        return [r for r in frame if isinstance(r, dict)]
    return []


def _safe_row_value(row: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in row and row[key] is not None:
            return row[key]
    return 0


def _compute_usable_contracts(symbols: list[str], bars_df: Any, trades_df: Any) -> int:
    if not symbols:
        return 0

    bar_rows = _normalize_frame_rows(bars_df)
    trade_rows = _normalize_frame_rows(trades_df)

    bar_symbol_set = {str(row.get("symbol") or row.get("underlying") or row.get("ticker")) for row in bar_rows if row.get("symbol")}
    trade_symbol_set = {str(row.get("symbol") or row.get("underlying") or row.get("ticker")) for row in trade_rows if row.get("symbol")}
    candidate_symbols = set(symbols)
    usable = set()

    for symbol in candidate_symbols:
        has_bar = symbol in bar_symbol_set
        has_trade = symbol in trade_symbol_set
        if not (has_bar or has_trade):
            continue

        volume_hit = False
        trade_count_hit = False
        for row in bar_rows:
            if str(row.get("symbol")) != symbol:
                continue
            if float(_safe_row_value(row, "volume", "vol", "trade_count")) > 0:
                volume_hit = True
                break
        if not volume_hit:
            for row in trade_rows:
                if str(row.get("symbol")) != symbol:
                    continue
                if float(_safe_row_value(row, "trade_count", "count", "size")) > 0:
                    trade_count_hit = True
                    break

        if volume_hit or trade_count_hit:
            usable.add(symbol)

    return len(usable)


def _parse_contract_summary(symbols: list[str]) -> dict[str, Any]:
    expirations: list[date] = []
    strikes: list[float] = []
    calls = 0
    puts = 0

    for symbol in symbols:
        try:
            expiration, option_type, strike = parse_occ_symbol(symbol)
        except ValueError:
            continue

        expirations.append(expiration)
        strikes.append(float(strike))
        if option_type.lower() == "call":
            calls += 1
        elif option_type.lower() == "put":
            puts += 1

    return {
        "earliest_expiration": min(expirations) if expirations else None,
        "latest_expiration": max(expirations) if expirations else None,
        "min_strike": min(strikes) if strikes else None,
        "max_strike": max(strikes) if strikes else None,
        "calls": calls,
        "puts": puts,
    }


def _window_for_day(decision_dt: datetime) -> tuple[datetime, datetime]:
    day_start = datetime.combine(decision_dt.date(), datetime.min.time())
    day_end = day_start + timedelta(days=1)
    return day_start - timedelta(hours=6), day_end + timedelta(hours=6)


def investigate_underlying_date(symbol: str, decision_dt: datetime) -> dict[str, Any]:
    metadata_candidates = _fetch_metadata_candidates(symbol, decision_dt, (-30, 120))
    symbols = sorted({candidate["symbol"] for candidate in metadata_candidates})

    start_dt, end_dt = _window_for_day(decision_dt)
    if not symbols:
        bar_rows = []
        trade_rows = []
        contract_summary = {"calls": 0, "puts": 0, "earliest_expiration": None, "latest_expiration": None, "min_strike": None, "max_strike": None}
        usable_contracts = 0
    else:
        bar_df = get_option_bars(symbols, start_dt, end_dt)
        trade_df = get_option_trades(symbols, start_dt, end_dt)
        bar_rows = _normalize_frame_rows(bar_df)
        trade_rows = _normalize_frame_rows(trade_df)
        contract_summary = _parse_contract_summary(symbols)
        usable_contracts = _compute_usable_contracts(symbols, bar_df, trade_df)

    return {
        "underlying": symbol,
        "date": decision_dt.date().isoformat(),
        "metadata_candidates": len(metadata_candidates),
        "contracts": len(symbols),
        "bar_rows": len(bar_rows),
        "trade_rows": len(trade_rows),
        "calls": contract_summary["calls"],
        "puts": contract_summary["puts"],
        "usable_contracts": usable_contracts,
        "earliest_expiration": contract_summary["earliest_expiration"],
        "latest_expiration": contract_summary["latest_expiration"],
        "min_strike": contract_summary["min_strike"],
        "max_strike": contract_summary["max_strike"],
    }


def explain_gap(report: dict[str, Any]) -> str:
    if report["metadata_candidates"] == 0:
        return (
            "API/account limitation or metadata coverage gap: the option-contract endpoint returned no candidate contracts "
            "for this underlying/date window, so no historical universe could be reconstructed."
        )
    if report["contracts"] == 0 and report["metadata_candidates"] > 0:
        return (
            "Request construction/meta validation issue: candidate metadata was found but no valid OCC-parsed contracts survived "
            "the historical filtering step."
        )
    if report["bar_rows"] == 0 and report["trade_rows"] == 0:
        return (
            "Historical option data absence: there were candidate contracts, but none produced historical bars or trades on the target day."
        )
    if report["usable_contracts"] == 0:
        return (
            "Universe reconstruction limitation: the contracts exist in metadata but do not have enough bar/trade volume or trade_count "
            "to be realistically usable in a backtest."
        )
    return "Historical option coverage appears viable for a small backtest window."


def format_table(rows: list[dict[str, Any]]) -> str:
    headers = [
        "Underlying",
        "Date",
        "Contracts",
        "Bar Rows",
        "Trade Rows",
        "Calls",
        "Puts",
        "Usable Contracts",
        "Earliest Expiration",
        "Latest Expiration",
    ]
    values = [headers] + [
        [
            row["underlying"],
            row["date"],
            str(row["contracts"]),
            str(row["bar_rows"]),
            str(row["trade_rows"]),
            str(row["calls"]),
            str(row["puts"]),
            str(row["usable_contracts"]),
            row["earliest_expiration"].isoformat() if row["earliest_expiration"] else "-",
            row["latest_expiration"].isoformat() if row["latest_expiration"] else "-",
        ]
        for row in rows
    ]

    widths = [max(len(str(value[i])) for value in values) for i in range(len(headers))]
    lines = []
    for idx, row in enumerate(values):
        line = " | ".join(str(value).ljust(widths[i]) for i, value in enumerate(row))
        lines.append(line)
        if idx == 0:
            lines.append("-+-".join("-" * width for width in widths))
    return "\n".join(lines)


def recommend_ranges(rows: list[dict[str, Any]]) -> list[str]:
    viable = [row for row in rows if row["usable_contracts"] > 0]
    if not viable:
        return [
            "No viable backtest window identified in the current environment; the metadata/candidate pool is empty for the tested dates.",
            "If data access improves, prioritize AAPL, SPY, and QQQ for 2024-06 to 2024-10 as the first candidates to re-test.",
            "Monitor for option metadata availability and non-zero historical bar/trade coverage before running a first backtest.",
        ]

    ranked = sorted(viable, key=lambda r: (-r["usable_contracts"], -r["bar_rows"], -r["trade_rows"]))
    top = ranked[:3]
    return [f"{row['underlying']} on {row['date']} ({row['usable_contracts']} usable contracts)" for row in top]


def main() -> None:
    print("Scanning manageable historical option-data coverage for first backtest viability...\n")
    rows: list[dict[str, Any]] = []

    for underlying in UNDERLYINGS:
        for decision_dt in DATES:
            report = investigate_underlying_date(underlying, decision_dt)
            rows.append(report)

    print(format_table(rows))
    print("\nInterpretation:\n")
    for row in rows:
        print(f"- {row['underlying']} {row['date']}: {explain_gap(row)}")

    print("\nRecommended first backtest ranges:\n")
    for idx, recommendation in enumerate(recommend_ranges(rows), start=1):
        print(f"{idx}. {recommendation}")


if __name__ == "__main__":
    main()
