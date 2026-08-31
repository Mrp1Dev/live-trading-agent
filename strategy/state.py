"""Local position state.

Alpaca is the source of truth for WHAT is held and at what average price. It does
not know when we opened a position or how far it ran before fading. Only those
two things are stored here.

Every read path degrades to empty rather than raising. A corrupt state file must
never stop the agent from exiting real positions - losing the trailing high-water
mark is a small problem, being unable to close is a large one.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping

from config import STATE_FILE
from strategy.exits import market_now


@dataclass
class PositionState:
    option_symbol: str
    stock_symbol: str
    direction: str
    opened_at: str                 # ISO 8601, exchange clock
    entry_price: float
    contracts: int
    peak_pnl_pct: float = 0.0
    trade_score: float = 0.0
    thesis: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "PositionState | None":
        try:
            return cls(
                option_symbol=str(raw["option_symbol"]),
                stock_symbol=str(raw.get("stock_symbol", "")),
                direction=str(raw.get("direction", "")),
                opened_at=str(raw.get("opened_at", "")),
                entry_price=float(raw.get("entry_price", 0.0) or 0.0),
                contracts=int(raw.get("contracts", 0) or 0),
                peak_pnl_pct=float(raw.get("peak_pnl_pct", 0.0) or 0.0),
                trade_score=float(raw.get("trade_score", 0.0) or 0.0),
                thesis=str(raw.get("thesis", "")),
            )
        except (KeyError, TypeError, ValueError):
            return None


def _state_path() -> str:
    return STATE_FILE


def load_state(path: str | None = None) -> dict[str, PositionState]:
    """Load position state. Returns {} on any failure - never raises."""
    target = path or _state_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (FileNotFoundError, PermissionError, OSError, json.JSONDecodeError, UnicodeDecodeError):
        return {}

    if not isinstance(raw, dict):
        return {}

    state: dict[str, PositionState] = {}
    for symbol, payload in raw.items():
        if not isinstance(payload, dict):
            continue
        entry = PositionState.from_dict(payload)
        if entry is not None:
            state[str(symbol)] = entry
    return state


def save_state(state: Mapping[str, PositionState], path: str | None = None) -> bool:
    """Atomically persist state. Returns False on failure instead of raising.

    Writes to a temp file in the same directory then os.replace()s it, so a crash
    mid-write leaves the previous good file intact. A half-written state file is
    worse than no state file.
    """
    target = path or _state_path()
    directory = os.path.dirname(os.path.abspath(target))

    try:
        os.makedirs(directory, exist_ok=True)
        payload = {symbol: entry.to_dict() for symbol, entry in state.items()}
        handle = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=directory,
            prefix=".positions-", suffix=".tmp", delete=False,
        )
        try:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            handle.close()
        os.replace(handle.name, target)
        return True
    except (OSError, TypeError, ValueError):
        try:
            os.unlink(handle.name)  # type: ignore[possibly-undefined]
        except Exception:
            pass
        return False


def record_entry(
    state: dict[str, PositionState],
    *,
    option_symbol: str,
    stock_symbol: str,
    direction: str,
    entry_price: float,
    contracts: int,
    trade_score: float = 0.0,
    thesis: str = "",
    now: datetime | None = None,
) -> PositionState:
    """Register a newly opened position."""
    entry = PositionState(
        option_symbol=option_symbol,
        stock_symbol=stock_symbol,
        direction=str(direction).upper(),
        opened_at=market_now(now).isoformat(),
        entry_price=float(entry_price),
        contracts=int(contracts),
        peak_pnl_pct=0.0,
        trade_score=float(trade_score),
        thesis=thesis,
    )
    state[option_symbol] = entry
    return entry


def update_peak(state: dict[str, PositionState], option_symbol: str, pnl_pct: float) -> float:
    """Raise the high-water mark. Never lowers it."""
    entry = state.get(option_symbol)
    if entry is None:
        return 0.0
    entry.peak_pnl_pct = max(entry.peak_pnl_pct, float(pnl_pct))
    return entry.peak_pnl_pct


def forget(state: dict[str, PositionState], option_symbol: str) -> None:
    state.pop(option_symbol, None)


def reconcile(state: dict[str, PositionState], open_symbols: Iterable[str]) -> list[str]:
    """Drop state for anything the broker no longer shows.

    Positions disappear via expiry, assignment, or a manual close in the
    dashboard. Without pruning, the time stop starts firing at positions that do
    not exist. Returns the symbols removed.
    """
    live = {str(symbol) for symbol in open_symbols}
    stale = [symbol for symbol in state if symbol not in live]
    for symbol in stale:
        state.pop(symbol, None)
    return stale


def adopt_untracked(
    state: dict[str, PositionState],
    *,
    option_symbol: str,
    stock_symbol: str,
    entry_price: float,
    contracts: int,
    now: datetime | None = None,
) -> PositionState:
    """Create state for a broker position we have no record of.

    Happens after a crash, a manual trade, or a fill that landed after the run
    that placed it exited. Adopting with opened_at = now means the time stop
    restarts rather than firing immediately on an unknown position.
    """
    return record_entry(
        state,
        option_symbol=option_symbol,
        stock_symbol=stock_symbol,
        direction="BULLISH",
        entry_price=entry_price,
        contracts=contracts,
        thesis="adopted: no local state at reconcile time",
        now=now,
    )


__all__ = [
    "PositionState",
    "adopt_untracked",
    "forget",
    "load_state",
    "reconcile",
    "record_entry",
    "save_state",
    "update_peak",
]
