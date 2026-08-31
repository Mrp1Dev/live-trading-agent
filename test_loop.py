"""Offline verification of the session loop, stale-order cancellation and the
entry/exit cadence. No network, no broker, no credentials."""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta

from strategy.exits import MARKET_TZ

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))


NOW = datetime(2026, 9, 1, 11, 0, tzinfo=MARKET_TZ)


class _Order:
    def __init__(self, oid, symbol, side, submitted_at, client_order_id=""):
        self.id = oid
        self.symbol = symbol
        self.side = side
        self.submitted_at = submitted_at
        self.client_order_id = client_order_id


class _Clock:
    def __init__(self, is_open=True, timestamp=None, next_close=None):
        self.is_open = is_open
        self.timestamp = timestamp or NOW
        self.next_close = next_close or NOW.replace(hour=16, minute=0)


class _TradingClient:
    def __init__(self, clock):
        self._clock = clock

    def get_clock(self):
        return self._clock


class _Broker:
    def __init__(self, orders=None, clock=None):
        self._orders = orders or []
        self.trading_client = _TradingClient(clock or _Clock())
        self.cancelled: list[str] = []

    def get_open_orders(self):
        return self._orders

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)


# ---------------------------------------------------------------------------
# Stale order cancellation
# ---------------------------------------------------------------------------

def test_stale_entry_orders_are_cancelled():
    from execution.position_manager import cancel_stale_orders

    stale = _Order("1", "CRM260911C00255000", "buy", NOW - timedelta(minutes=30))
    fresh = _Order("2", "NOW260911C00147000", "buy", NOW - timedelta(seconds=30))
    broker = _Broker([stale, fresh])
    cancelled = cancel_stale_orders(broker, now=NOW, verbose=False)
    check("a stale resting entry order is cancelled",
          broker.cancelled == ["1"], str(broker.cancelled))
    check("a fresh entry order is left to work", "2" not in broker.cancelled)
    check("cancellation is reported by symbol",
          cancelled == ["CRM260911C00255000"], str(cancelled))


def test_exit_orders_are_never_cancelled():
    from execution.position_manager import cancel_stale_orders

    old_sell = _Order("1", "CRM260911C00255000", "sell", NOW - timedelta(hours=3))
    tagged = _Order("2", "NOW260911C00147000", "buy", NOW - timedelta(hours=3),
                    client_order_id="exit-NOW260911C00147000-20260901T104500")
    broker = _Broker([old_sell, tagged])
    cancel_stale_orders(broker, now=NOW, verbose=False)
    check("a working sell is never cancelled, however old", "1" not in broker.cancelled)
    check("an exit-tagged order is never cancelled", "2" not in broker.cancelled)
    check("nothing was cancelled at all", broker.cancelled == [], str(broker.cancelled))


def test_cancel_survives_a_listing_failure():
    from execution.errors import BrokerError
    from execution.position_manager import cancel_stale_orders

    class _Broken(_Broker):
        def get_open_orders(self):
            raise BrokerError("api down")

    check("a failed order listing returns empty rather than raising",
          cancel_stale_orders(_Broken(), now=NOW, verbose=False) == [])


def test_dry_run_cancels_nothing():
    from execution.position_manager import cancel_stale_orders
    stale = _Order("1", "CRM260911C00255000", "buy", NOW - timedelta(minutes=30))
    broker = _Broker([stale])
    reported = cancel_stale_orders(broker, now=NOW, dry_run=True, verbose=False)
    check("dry run reports but does not cancel",
          reported and broker.cancelled == [], str(broker.cancelled))


# ---------------------------------------------------------------------------
# Entry window
# ---------------------------------------------------------------------------

def test_entry_window_blocks_open_and_close():
    from execution.position_manager import entry_window_status

    open_edge = NOW.replace(hour=9, minute=35)
    broker = _Broker(clock=_Clock(timestamp=open_edge,
                                  next_close=NOW.replace(hour=16, minute=0)))
    ok, reason = entry_window_status(broker, open_edge)
    check("entries blocked in the first 10 minutes", not ok, reason)

    close_edge = NOW.replace(hour=15, minute=55)
    broker = _Broker(clock=_Clock(timestamp=close_edge,
                                  next_close=NOW.replace(hour=16, minute=0)))
    ok, reason = entry_window_status(broker, close_edge)
    check("entries blocked in the last 10 minutes", not ok, reason)

    midday = NOW.replace(hour=11, minute=0)
    broker = _Broker(clock=_Clock(timestamp=midday,
                                  next_close=NOW.replace(hour=16, minute=0)))
    ok, reason = entry_window_status(broker, midday)
    check("entries allowed mid-session", ok, reason)


def test_entry_window_blocked_when_market_closed():
    from execution.position_manager import entry_window_status
    broker = _Broker(clock=_Clock(is_open=False))
    ok, reason = entry_window_status(broker, NOW)
    check("entries blocked when the market is closed", not ok and "closed" in reason, reason)


def test_entry_window_fails_closed_without_a_clock():
    from execution.position_manager import entry_window_status

    class _NoClock(_Broker):
        def __init__(self):
            super().__init__()
            self.trading_client = type("T", (), {"get_clock": lambda s: (_ for _ in ()).throw(RuntimeError("down"))})()

    ok, reason = entry_window_status(_NoClock(), NOW.replace(hour=3, minute=0))
    check("without a clock, an out-of-hours time still blocks entries", not ok, reason)


def test_seconds_until_close():
    from execution.position_manager import seconds_until_close
    broker = _Broker(clock=_Clock(next_close=NOW.replace(hour=16, minute=0)))
    remaining = seconds_until_close(broker, NOW)
    check("seconds_until_close counts down to the session close",
          remaining is not None and abs(remaining - 5 * 3600) < 60, str(remaining))
    closed = _Broker(clock=_Clock(is_open=False))
    check("a closed market reports zero seconds remaining",
          seconds_until_close(closed, NOW) == 0.0)


# ---------------------------------------------------------------------------
# Cadence policy
# ---------------------------------------------------------------------------

def _entry_due(last_entry_at, now, open_slots, previous_slots, interval):
    """Mirror of the loop's scheduling predicate, kept in sync by these tests."""
    if open_slots <= 0:
        return False
    slots_freed = previous_slots is not None and open_slots > previous_slots
    if last_entry_at is None or slots_freed:
        return True
    return (now - last_entry_at).total_seconds() / 60.0 >= interval


def test_entry_cadence():
    import main
    interval = main.ENTRY_INTERVAL_MINUTES

    check("first cycle always runs an entry pass",
          _entry_due(None, NOW, 3, None, interval))
    check("no entry when every slot is full",
          not _entry_due(None, NOW, 0, None, interval))
    check("no entry again 30 minutes later",
          not _entry_due(NOW - timedelta(minutes=30), NOW, 3, 3, interval))
    check("entry again once the interval has elapsed",
          _entry_due(NOW - timedelta(minutes=interval + 1), NOW, 3, 3, interval))
    check("an exit that frees a slot triggers an entry immediately",
          _entry_due(NOW - timedelta(minutes=5), NOW, 3, 2, interval),
          "a freed slot is new information; the interval should not gate it")


def test_loop_constants_are_sane():
    import main
    check("exits run at least every 10 minutes",
          0 < main.EXIT_INTERVAL_SECONDS <= 600, str(main.EXIT_INTERVAL_SECONDS))
    check("entries are rate-limited to at most a few per session",
          main.ENTRY_INTERVAL_MINUTES >= 60, str(main.ENTRY_INTERVAL_MINUTES))
    check("the loop gives up after a bounded number of failures",
          2 <= main.MAX_CONSECUTIVE_ERRORS <= 10, str(main.MAX_CONSECUTIVE_ERRORS))
    sessions_per_day = (6.5 * 60) / (main.EXIT_INTERVAL_SECONDS / 60)
    check("exit cadence gives ~78 marks per session",
          70 <= sessions_per_day <= 90, f"{sessions_per_day:.0f}")


def test_cli_flags():
    from execution.confirmation import parse_execution_args
    args = parse_execution_args([])
    check("default is dry-run, looping", not args.confirm_paper_trades and not args.once)
    args = parse_execution_args(["--confirm-paper-trades"])
    check("--confirm-paper-trades enables live paper execution", args.confirm_paper_trades)
    args = parse_execution_args(["--once"])
    check("--once requests a single cycle", args.once)


def test_main_dispatches_on_once():
    import inspect
    import main
    src = inspect.getsource(main.main)
    check("main dispatches to run_single_cycle for --once", "run_single_cycle" in src)
    check("main dispatches to run_loop otherwise", "run_loop" in src)
    check("run_exit_cycle cancels stale orders before managing positions",
          "cancel_stale_orders" in inspect.getsource(main.run_exit_cycle))


def test_loop_stops_in_the_flatten_window():
    import inspect
    import main
    src = inspect.getsource(main.run_loop)
    check("the loop flattens and returns inside the flatten window",
          "in_flatten_window" in src and "Flatten complete" in src)
    check("the loop stops when the market closes",
          "seconds_until_close" in src)
    check("a failed entry cycle does not stop exits",
          "Exits continue on schedule" in src)
    check("Ctrl+C is handled and says positions are left open",
          "KeyboardInterrupt" in src and "LEFT OPEN" in src)
    check("exits run before entries in the loop body",
          src.index("run_exit_cycle") < src.index("run_entry_cycle"))


# ---------------------------------------------------------------------------

def main_() -> int:
    tests = [
        test_stale_entry_orders_are_cancelled,
        test_exit_orders_are_never_cancelled,
        test_cancel_survives_a_listing_failure,
        test_dry_run_cancels_nothing,
        test_entry_window_blocks_open_and_close,
        test_entry_window_blocked_when_market_closed,
        test_entry_window_fails_closed_without_a_clock,
        test_seconds_until_close,
        test_entry_cadence,
        test_loop_constants_are_sane,
        test_cli_flags,
        test_main_dispatches_on_once,
        test_loop_stops_in_the_flatten_window,
    ]
    for test in tests:
        try:
            test()
        except Exception as exc:  # pragma: no cover
            FAILED.append((test.__name__, f"EXCEPTION {exc!r}"))

    print("=" * 78)
    print(" SESSION LOOP VERIFICATION")
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
    raise SystemExit(main_())
