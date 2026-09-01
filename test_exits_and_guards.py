"""Offline verification for the exit engine, position state, earnings veto and
the option-ranker mapping fix.

No broker, no network, no credentials. The broker is a fake that records calls.
"""

from __future__ import annotations

import os
import random
import tempfile
from datetime import date, datetime, timedelta, timezone

from config import (
    MAX_HOLD_DAYS,
    STOP_LOSS_PCT,
    TAKE_PROFIT_PCT,
    TRAIL_ARM_PCT,
    TRAIL_GIVEBACK_PCT,
)
from strategy import state as position_state
from strategy.earnings import detect_earnings_risk, extract_candidate_dates, filter_earnings_risk
from strategy.exits import (
    MARKET_TZ,
    days_held,
    dte_from_occ_symbol,
    evaluate_exit,
    in_flatten_window,
    realisable_pnl_pct,
    sort_closes_immediate_first,
    update_peak,
)

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED.append(name) if cond else FAILED.append((name, detail)))


NOW = datetime(2026, 9, 1, 11, 0, tzinfo=MARKET_TZ)   # before the flatten window


def _exit(pnl, peak=0.0, dte=9, opened=None, now=NOW):
    return evaluate_exit(
        pnl_pct=pnl, peak_pnl_pct=peak, dte=dte,
        opened_at=opened if opened is not None else (now - timedelta(minutes=10)).isoformat(),
        now=now,
    )


# ---------------------------------------------------------------------------
# Exit rules
# ---------------------------------------------------------------------------

def test_rule_precedence():
    # A position that is simultaneously stopped out AND expiring must report
    # EXPIRY, because that is the more urgent fact.
    d = _exit(-0.80, dte=-1)
    check("expiry outranks stop loss", d.reason == "EXPIRY", d.reason)

    # A faded winner must report TRAILING_STOP, not TAKE_PROFIT, even when it is
    # still above the fixed target.
    d = _exit(1.30, peak=3.00)
    check("trailing stop outranks take profit for a faded winner",
          d.reason == "TRAILING_STOP", f"{d.reason}: {d.detail}")

    d = _exit(-0.60)
    check("stop loss fires at -55%", d.reason == "STOP_LOSS", d.reason)

    d = _exit(1.50, peak=1.50)
    check("take profit fires at +120% when not faded", d.reason == "TAKE_PROFIT", d.reason)


def test_trailing_stop_behaviour():
    """Verified table: arms at TRAIL_ARM_PCT, gives back at most TRAIL_GIVEBACK_PCT of peak."""
    arm = TRAIL_ARM_PCT
    gb = TRAIL_GIVEBACK_PCT
    cases = [
        (arm * 0.5, arm * 0.5, False, "unarmed: rides to the stop"),
        (0.30, 0.30 * (1.0 - gb) - 0.01, True, f"armed at +30%, closes below floor {0.30*(1.0-gb):.1%}"),
        (0.30, 0.30 * (1.0 - gb) + 0.02, False, "armed but still above the floor"),
        (1.00, 1.00 * (1.0 - gb) - 0.01, True, f"peak +100% closes below floor {1.00*(1.0-gb):.1%}"),
        (2.00, 2.00 * (1.0 - gb) - 0.01, True, f"peak +200% closes below floor {2.00*(1.0-gb):.1%}"),
        (3.00, 3.00 * (1.0 - gb) - 0.01, True, f"peak +300% closes below floor {3.00*(1.0-gb):.1%}"),
    ]
    for peak, pnl, should_close, label in cases:
        d = _exit(pnl, peak=peak)
        fired = d.reason == "TRAILING_STOP"
        check(f"trailing: {label}", fired == should_close,
              f"peak={peak} pnl={pnl} -> {d.reason}")

    # The arithmetic itself.
    for peak in (0.30, 1.00, 2.00, 3.00):
        floor = peak * (1.0 - TRAIL_GIVEBACK_PCT)
        check(f"trailing floor for peak {peak:+.0%} is {floor:+.1%}",
              _exit(floor - 0.001, peak=peak).reason == "TRAILING_STOP")


def test_unarmed_position_rides_to_the_stop():
    safe_pnl = STOP_LOSS_PCT / 2.0
    d = _exit(safe_pnl, peak=0.10)
    check("a trade that never worked is handled by the stop, not the trail",
          d.reason == "HOLD", d.reason)
    check("...and does close once it hits the stop",
          _exit(STOP_LOSS_PCT - 0.01, peak=0.10).reason == "STOP_LOSS")


def test_time_stop():
    old = (NOW - timedelta(minutes=50)).isoformat()
    check("time stop fires past MAX_HOLD_MINUTES", _exit(0.05, opened=old).reason == "TIME_STOP")
    fresh = (NOW - timedelta(minutes=5)).isoformat()
    check("time stop does not fire early", _exit(0.05, opened=fresh).reason == "HOLD")


def test_days_held_degrades_safely():
    for bad in (None, "", "not-a-date", "2026-13-45T99:99:99"):
        check(f"days_held({bad!r}) returns 0.0 rather than raising",
              days_held(bad, NOW) == 0.0)
    future = (NOW + timedelta(days=3)).isoformat()
    check("a future opened_at returns 0.0", days_held(future, NOW) == 0.0)
    check("days_held measures real elapsed time",
          abs(days_held((NOW - timedelta(days=2)).isoformat(), NOW) - 2.0) < 1e-6)


def test_flatten_window():
    from config import FLATTEN_DATE
    before = datetime.combine(FLATTEN_DATE, datetime.min.time()).replace(hour=9, minute=30, tzinfo=MARKET_TZ)
    after = datetime.combine(FLATTEN_DATE, datetime.min.time()).replace(hour=9, minute=46, tzinfo=MARKET_TZ)
    day_before = datetime.combine(FLATTEN_DATE - timedelta(days=1), datetime.min.time()).replace(hour=15, tzinfo=MARKET_TZ)
    check("not flattening the day before", not in_flatten_window(day_before))
    check("not flattening just before the deadline", not in_flatten_window(before))
    check("flattening after the deadline", in_flatten_window(after))
    check("flatten outranks everything",
          _exit(0.50, peak=0.50, dte=9, now=after).reason == "FLATTEN_WINDOW")


def test_update_peak_only_rises():
    check("peak rises", update_peak(0.10, 0.50) == 0.50)
    check("peak never falls", update_peak(0.50, 0.10) == 0.50)
    check("peak handles a None-ish previous value", update_peak(0.0, -0.20) == 0.0)


def test_pnl_measured_against_the_bid():
    # Entry 2.00, bid 1.80, ask 2.40 -> mid 2.10 looks like +5%, bid is -10%.
    pnl = realisable_pnl_pct(entry_price=2.00, bid=1.80, fallback_pnl_pct=0.05)
    check("P&L is measured against the bid, not the midpoint",
          abs(pnl - (-0.10)) < 1e-9, f"got {pnl}")
    check("falls back to the broker mark when no bid is available",
          realisable_pnl_pct(2.00, 0.0, fallback_pnl_pct=0.05) == 0.05)
    check("returns None when nothing is usable",
          realisable_pnl_pct(2.00, 0.0, fallback_pnl_pct=None) is None)


def test_dte_from_symbol():
    check("DTE parsed from the OCC symbol",
          dte_from_occ_symbol("CRM260911C00255000", NOW) == 10,
          str(dte_from_occ_symbol("CRM260911C00255000", NOW)))
    check("unparseable symbol returns None", dte_from_occ_symbol("GARBAGE", NOW) is None)


def test_immediate_closes_are_submitted_first():
    from strategy.exits import ExitDecision, URGENCY_IMMEDIATE, URGENCY_NORMAL
    items = [
        ("B", ExitDecision(True, "TAKE_PROFIT", URGENCY_NORMAL)),
        ("A", ExitDecision(True, "STOP_LOSS", URGENCY_IMMEDIATE)),
        ("C", ExitDecision(True, "TIME_STOP", URGENCY_NORMAL)),
    ]
    order = [s for s, _ in sort_closes_immediate_first(items)]
    check("immediate closes sort first", order[0] == "A", str(order))


# ---------------------------------------------------------------------------
# Position state
# ---------------------------------------------------------------------------

def test_state_roundtrip_and_corruption():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "state", "positions.json")
        state: dict = {}
        position_state.record_entry(
            state, option_symbol="CRM260911C00255000", stock_symbol="CRM",
            direction="BULLISH", entry_price=7.85, contracts=3, now=NOW,
        )
        check("save_state succeeds", position_state.save_state(state, path))
        loaded = position_state.load_state(path)
        check("state round-trips", loaded["CRM260911C00255000"].entry_price == 7.85)
        check("opened_at is recorded", loaded["CRM260911C00255000"].opened_at.startswith("2026-09-01"))

        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        check("corrupt state file degrades to empty, never raises",
              position_state.load_state(path) == {})

        check("missing state file degrades to empty",
              position_state.load_state(os.path.join(tmp, "nope.json")) == {})


def test_state_reconcile_prunes_vanished_positions():
    state: dict = {}
    for sym in ("AAA260911C00100000", "BBB260911C00100000"):
        position_state.record_entry(state, option_symbol=sym, stock_symbol=sym[:3],
                                    direction="BULLISH", entry_price=1.0, contracts=1, now=NOW)
    pruned = position_state.reconcile(state, ["AAA260911C00100000"])
    check("reconcile prunes what the broker no longer shows",
          pruned == ["BBB260911C00100000"] and "AAA260911C00100000" in state, str(pruned))


def test_atomic_write_leaves_no_partial_file():
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "positions.json")
        state: dict = {}
        position_state.record_entry(state, option_symbol="X260911C00100000", stock_symbol="X",
                                    direction="BULLISH", entry_price=1.0, contracts=1, now=NOW)
        position_state.save_state(state, path)
        leftovers = [f for f in os.listdir(tmp) if f.startswith(".positions-")]
        check("no temp files left behind after an atomic write", leftovers == [], str(leftovers))


# ---------------------------------------------------------------------------
# Earnings veto
# ---------------------------------------------------------------------------

def test_earnings_veto_on_real_dossiers():
    as_of = date(2026, 8, 31)
    mdb = ("MongoDB reports Q2 earnings after the close on Tuesday, Sept. 1; analysts "
           "expect EPS of $1.61. Risk: Earnings volatility could be extreme.")
    snps = ("Synopsys beat Q3 estimates on EDA strength, with shares rallying and "
            "analysts maintaining Buy ratings. Catalyst: Post-earnings momentum.")
    adsk = ("Autodesk beat Q2 estimates but gave cautious fiscal 2027 margin outlook "
            "and below-consensus Q3 EPS guidance. Catalyst: revisions following Q2 results.")

    r = detect_earnings_risk("MDB", mdb, as_of=as_of, horizon_days=14)
    check("future earnings inside the horizon is vetoed", r.has_risk, r.reason)
    check("the event date is extracted", r.event_date == date(2026, 9, 1), str(r.event_date))
    check("dated vetoes are marked as such", r.confidence == "dated")

    check("already-reported earnings is NOT vetoed",
          not detect_earnings_risk("SNPS", snps, as_of=as_of).has_risk)
    check("post-earnings guidance commentary is NOT vetoed",
          not detect_earnings_risk("ADSK", adsk, as_of=as_of).has_risk)
    check("empty research is NOT vetoed",
          not detect_earnings_risk("SLB", "No material recent news found.", as_of=as_of).has_risk)


def test_earnings_horizon_boundary():
    as_of = date(2026, 8, 31)
    far = "Acme will report Q3 earnings on Nov. 12 according to the company."
    check("an earnings date beyond the horizon is not vetoed",
          not detect_earnings_risk("ACME", far, as_of=as_of, horizon_days=14).has_risk)
    near = "Acme will report Q3 earnings on Sept. 8 according to the company."
    check("an earnings date inside the horizon is vetoed",
          detect_earnings_risk("ACME", near, as_of=as_of, horizon_days=14).has_risk)


def test_undated_forward_language_is_vetoed_conservatively():
    as_of = date(2026, 8, 31)
    text = "Investors are positioning ahead of earnings; the earnings call is expected shortly."
    r = detect_earnings_risk("ACME", text, as_of=as_of, horizon_days=14)
    check("undated forward-looking earnings language vetoes conservatively", r.has_risk)
    check("undated vetoes are marked inferred", r.confidence == "inferred", r.confidence)


def test_date_extraction():
    as_of = date(2026, 8, 31)
    got = extract_candidate_dates("reports on Tuesday, Sept. 1 and again on 9/15", as_of)
    check("month-name and numeric dates are both extracted",
          date(2026, 9, 1) in got and date(2026, 9, 15) in got, str(got))
    rollover = extract_candidate_dates("guidance due Jan. 5", date(2026, 12, 20))
    check("a month already past rolls forward to next year",
          date(2027, 1, 5) in rollover, str(rollover))


def test_filter_splits_universe():
    as_of = date(2026, 8, 31)
    research = {
        "MDB": "MongoDB reports Q2 earnings after the close on Tuesday, Sept. 1.",
        "CRM": "Salesforce rallied on AI demand, no company-specific news.",
    }
    safe, vetoed = filter_earnings_risk(["MDB", "CRM"], research, as_of=as_of, horizon_days=14)
    check("filter_earnings_risk splits correctly",
          safe == ["CRM"] and list(vetoed) == ["MDB"], f"safe={safe} vetoed={list(vetoed)}")


# ---------------------------------------------------------------------------
# Option ranker mapping (the scrambled-permutation bug)
# ---------------------------------------------------------------------------

def test_option_ranker_decodes_against_the_prompt_it_sent():
    """The regression test for the bug that made this stage a random permutation.

    The pool is shuffled for presentation; identifiers must be decoded against
    that same shuffled list. This drives the real rank_option_pool with a stubbed
    HTTP layer and asserts the returned symbols are the ones the model picked.
    """
    import strategy.option_ranker as ranker
    from strategy.option_selector import OptionCandidate

    options = [
        OptionCandidate(f"AAA2609{11:02d}C{int(strike*1000):08d}", date(2026, 9, 11),
                        "call", float(strike), 1.0, 1.1, 1.05, 0.05,
                        0.30, 0.5, 0.03, -0.02, 0.11, 11, 0.01, score=50.0)
        for strike in range(100, 112)
    ]

    class _Stock:
        symbol, rank, score, price = "AAA", 1, 90.0, 105.0
        return_1d = return_5d = return_20d = 0.01
        realized_volatility = 0.35
        relative_strength_spy = 0.05

    captured: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            # Read the prompt the code actually built and pick 3 known IDs.
            prompt = captured["prompt"]
            ids = []
            for line in prompt.splitlines():
                if line.startswith("OPT"):
                    ids.append(line.split(" |")[0].strip())
            captured["prompt_ids"] = ids
            captured["prompt_lines"] = {
                line.split(" |")[0].strip(): line.split("symbol=")[1].split(" |")[0]
                for line in prompt.splitlines() if line.startswith("OPT")
            }
            chosen = [ids[7], ids[2], ids[10]]
            captured["chosen"] = chosen
            import json as _json
            return {"choices": [{"message": {"content": _json.dumps(chosen)}}]}

    def _fake_post(url, headers=None, json=None, timeout=None):
        captured["prompt"] = json["messages"][1]["content"]
        return _Resp()

    real_post, real_key, real_model = ranker.requests.post, ranker._get_api_key, ranker._get_model
    ranker.requests.post = _fake_post
    ranker._get_api_key = lambda: "test"
    ranker._get_model = lambda m=None: "test-model"
    try:
        random.seed(1234)
        result = ranker.rank_option_pool(
            options=options,
            stocks={"AAA": _Stock()},
            directions={"AAA": "BULLISH"},
            research={"AAA": "test"},
            top_k=3,
        )
    finally:
        ranker.requests.post = real_post
        ranker._get_api_key = real_key
        ranker._get_model = real_model

    expected = [captured["prompt_lines"][i] for i in captured["chosen"]]
    check("ranker returns exactly the contracts the model chose",
          result == expected, f"got {result}\nexpected {expected}")
    check("returned symbols are all real pool members",
          set(result) <= {o.symbol for o in options})
    check("the pool really was shuffled for presentation",
          [captured["prompt_lines"][i] for i in captured["prompt_ids"]]
          != [o.symbol for o in options],
          "shuffle produced identity order; rerun (this is a probabilistic check)")


# ---------------------------------------------------------------------------
# Position manager against a fake broker
# ---------------------------------------------------------------------------

class _FakePosition:
    def __init__(self, symbol, qty, avg_entry_price, plpc=0.0):
        self.symbol = symbol
        self.qty = qty
        self.avg_entry_price = avg_entry_price
        self.unrealized_plpc = plpc
        self.asset_class = "us_option"


class _FakeQuote:
    def __init__(self, bid, ask):
        self.bid, self.ask = bid, ask


class _FakeBroker:
    def __init__(self, positions, quotes):
        self._positions, self._quotes = positions, quotes
        self.orders: list[dict] = []
        self.closed: list[str] = []

    def get_positions(self):
        return self._positions

    def get_option_quote(self, symbol):
        return self._quotes[symbol]

    def place_option_order(self, **kwargs):
        self.orders.append(kwargs)
        return {"id": "fake"}

    def close_position(self, symbol, qty=None):
        self.closed.append(symbol)


def test_position_manager_closes_the_right_positions():
    from execution.position_manager import manage_open_positions

    winner = "AAA260911C00100000"     # +150% -> take profit
    loser = "BBB260911C00100000"      # -60%  -> stop loss
    holder = "CCC260911C00100000"     # +10%  -> hold

    broker = _FakeBroker(
        positions=[
            _FakePosition(winner, 2, 2.00),
            _FakePosition(loser, 1, 2.00),
            _FakePosition(holder, 1, 2.00),
        ],
        quotes={
            winner: _FakeQuote(5.00, 5.20),
            loser: _FakeQuote(0.80, 0.90),
            holder: _FakeQuote(2.20, 2.30),
        },
    )

    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "positions.json")
        report = manage_open_positions(broker, now=NOW, dry_run=False,
                                       state_path=path, verbose=False)

        closed = {sym: reason for sym, reason in report.closed}
        check("winner is closed on take profit", closed.get(winner) == "TAKE_PROFIT", str(closed))
        check("loser is closed on stop loss", closed.get(loser) == "STOP_LOSS", str(closed))
        check("hold position stays open", holder not in closed, str(closed))
        check("held_count reflects survivors", report.held_count == 1, str(report.held_count))
        check("untracked broker positions are adopted", len(report.adopted) == 3)

        sells = {o["symbol"]: o for o in broker.orders}
        check("exits are sell_to_close limit orders",
              all(o["side"] == "sell" and o["position_intent"] == "sell_to_close"
                  and o["order_type"] == "limit" for o in broker.orders))
        check("exit limit is placed slightly through the bid",
              abs(sells[winner]["limit_price"] - 4.90) < 0.01,
              str(sells[winner]["limit_price"]))
        check("closed positions are dropped from state",
              set(position_state.load_state(path)) == {holder},
              str(list(position_state.load_state(path))))


def test_position_manager_survives_a_quote_failure():
    from execution.errors import BrokerError
    from execution.position_manager import manage_open_positions

    good, bad = "AAA260911C00100000", "BBB260911C00100000"

    class _PartialBroker(_FakeBroker):
        def get_option_quote(self, symbol):
            if symbol == bad:
                raise BrokerError("no quote")
            return self._quotes[symbol]

    broker = _PartialBroker(
        positions=[_FakePosition(good, 1, 2.00), _FakePosition(bad, 1, 2.00, plpc=-0.70)],
        quotes={good: _FakeQuote(5.00, 5.20)},
    )
    with tempfile.TemporaryDirectory() as tmp:
        report = manage_open_positions(broker, now=NOW, dry_run=False,
                                       state_path=os.path.join(tmp, "s.json"), verbose=False)
        closed = {s for s, _ in report.closed}
        check("a missing quote does not stop the other position closing", good in closed)
        check("the quote gap is recorded", report.quote_gaps == [bad], str(report.quote_gaps))
        check("the broker mark is used as a fallback so the loser still exits",
              bad in closed, str(report.closed))


def test_dry_run_places_no_orders():
    from execution.position_manager import manage_open_positions
    sym = "AAA260911C00100000"
    broker = _FakeBroker([_FakePosition(sym, 1, 2.00)], {sym: _FakeQuote(0.50, 0.60)})
    with tempfile.TemporaryDirectory() as tmp:
        report = manage_open_positions(broker, now=NOW, dry_run=True,
                                       state_path=os.path.join(tmp, "s.json"), verbose=False)
        check("dry run submits no orders", broker.orders == [] and broker.closed == [])
        check("dry run still reports the decision",
              report.closed and report.closed[0][1].startswith("DRY_RUN"), str(report.closed))


# ---------------------------------------------------------------------------

def main() -> int:
    tests = [
        test_rule_precedence,
        test_trailing_stop_behaviour,
        test_unarmed_position_rides_to_the_stop,
        test_time_stop,
        test_days_held_degrades_safely,
        test_flatten_window,
        test_update_peak_only_rises,
        test_pnl_measured_against_the_bid,
        test_dte_from_symbol,
        test_immediate_closes_are_submitted_first,
        test_state_roundtrip_and_corruption,
        test_state_reconcile_prunes_vanished_positions,
        test_atomic_write_leaves_no_partial_file,
        test_earnings_veto_on_real_dossiers,
        test_earnings_horizon_boundary,
        test_undated_forward_language_is_vetoed_conservatively,
        test_date_extraction,
        test_filter_splits_universe,
        test_option_ranker_decodes_against_the_prompt_it_sent,
        test_position_manager_closes_the_right_positions,
        test_position_manager_survives_a_quote_failure,
        test_dry_run_places_no_orders,
    ]
    for test in tests:
        try:
            test()
        except Exception as exc:  # pragma: no cover
            FAILED.append((test.__name__, f"EXCEPTION {exc!r}"))

    print("=" * 78)
    print(" EXITS / STATE / EARNINGS / RANKER VERIFICATION")
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
