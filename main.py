import time

from alpaca_client.client import get_trading_client
from alpaca_client.options import get_option_chain
from alpaca_client.stocks import (
    print_top_scanned_stocks,
    get_latest_underlying_prices,
)
from strategy.option_selector import select_directional_options
from strategy.direction import TradeDirection, determine_direction
from strategy.research_agent import (
    research_stocks,
    research_text_by_symbol,
    print_research_reports,
)
from strategy.llm_ranker import rank_stocks
from strategy.option_ranker import rank_option_pool
from strategy.portfolio import build_portfolio, print_portfolio_plan
from risk.risk import assess_portfolio, print_risk_report
from execution.alpaca_broker import AlpacaBroker
from execution.confirmation import parse_execution_args
from execution.position_manager import (
    cancel_stale_orders,
    entry_window_status,
    manage_open_positions,
    seconds_until_close,
    verify_fills,
)
from execution.stage import run_execution_stage
from execution.trade_factory import build_trade_candidates
from strategy.earnings import filter_earnings_risk
from strategy.exits import in_flatten_window, market_now
from strategy.universe import UNIVERSE
from config import (
    STOCK_SCANNER_TOP_N,
    LLM_STOCK_TOP_K,
    MAX_OPTIONS_PER_STOCK,
    MAX_DTE,
    MAX_POSITIONS,
    NO_TRADE_MINUTES_AFTER_OPEN,
    NO_TRADE_MINUTES_BEFORE_CLOSE,
    OPTION_LLM_TOP_K,
    ENTRY_INTERVAL_MINUTES,
    EXIT_INTERVAL_SECONDS,
)
from strategy import state as position_state

# Give marketable limits a moment to fill before deciding they have not.
FILL_CHECK_DELAY_SECONDS = 20

# ---------------------------------------------------------------------------
# Loop cadence
# ---------------------------------------------------------------------------

# Exits are cheap (no LLM, ~2 API calls) and run often (configured in config.py).
# Entries run on ENTRY_INTERVAL_MINUTES and immediately whenever an exit frees a slot.

# Transient API errors are expected; a persistent failure is not. Stop rather
# than hammer the API, and say clearly that positions were left open.
MAX_CONSECUTIVE_ERRORS = 5


def _record_submitted_entries(execution_report) -> None:
    """Write local state for every submitted entry.

    Recorded optimistically at submission and corrected (or removed) by
    verify_fills, so a crash between the two leaves a record we can reconcile
    against the broker rather than an untracked position.
    """
    state = position_state.load_state()
    for result in getattr(execution_report, "results", []):
        intent = getattr(result, "intent", None)
        if intent is None or not getattr(result, "order_id", None):
            continue
        position_state.record_entry(
            state,
            option_symbol=intent.option_symbol,
            stock_symbol=intent.stock_symbol,
            direction=intent.direction,
            entry_price=float(getattr(result, "limit_price", None) or intent.reference_entry_price),
            contracts=int(getattr(result, "submitted_qty", 0) or intent.contracts),
            trade_score=float(intent.trade_score),
        )
    position_state.save_state(state)



def run_entry_cycle(
    broker,
    *,
    account_id: str,
    account_equity: float,
    open_slots: int,
    held_underlyings: set,
    confirmed: bool,
) -> bool:
    """Scan -> research -> rank -> select -> size -> risk -> execute.

    Every bail-out path returns early and says why. A cycle that opens nothing is
    a normal outcome, not a failure.
    """
    print(f"\nScanning {len(UNIVERSE)}-stock universe...")
    top_candidates = print_top_scanned_stocks(top_n=STOCK_SCANNER_TOP_N)
    if not top_candidates:
        print("No scanner candidates.")
        return

    research_reports = research_stocks(top_candidates)
    print_research_reports(research_reports)
    research = research_text_by_symbol(research_reports)

    # Earnings veto BEFORE the ranker. A model asked to weigh a catalyst will
    # sometimes weigh a binary event positively; deterministic code cannot.
    # Vetoing here also means no tokens are spent ranking untradeable names.
    safe_symbols, earnings_vetoed = filter_earnings_risk(
        [stock.symbol for stock in top_candidates],
        research,
        as_of=market_now().date(),
        horizon_days=MAX_DTE,
    )
    if earnings_vetoed:
        print("\n" + "=" * 120)
        print(" EARNINGS VETO")
        print("=" * 120)
        for symbol, risk in earnings_vetoed.items():
            print(f"  {symbol:<6} [{risk.confidence}] {risk.reason}")
        print("=" * 120)

    top_candidates = [s for s in top_candidates if s.symbol in set(safe_symbols)]
    if not top_candidates:
        print("Every scanner candidate carries earnings risk. Refusing to trade.")
        return

    ranked_symbols = rank_stocks(
        stocks=top_candidates,
        research=research,
        debug=True,
    )
    stocks_by_symbol = {stock.symbol: stock for stock in top_candidates}
    ranked_candidates = [stocks_by_symbol[symbol] for symbol in ranked_symbols]

    print("\n" + "=" * 120)
    print(" LLM STOCK RANKING")
    print("=" * 120)
    for llm_rank, symbol in enumerate(ranked_symbols, start=1):
        stock = stocks_by_symbol[symbol]
        scanner_rank = stock.rank if stock.rank is not None else "-"
        print(
            f"#{llm_rank:<2} {symbol:<6} Scanner=#{scanner_rank:<3} "
            f"ScannerScore={stock.score:>5.1f}"
        )

    ranked_candidates = [s for s in ranked_candidates if s.symbol not in held_underlyings]
    if not ranked_candidates:
        print("Every ranked candidate is already held. No new entries.")
        return

    selected_stocks = ranked_candidates[:LLM_STOCK_TOP_K]
    print("\nLLM-RANKED STOCKS")
    for rank, stock in enumerate(selected_stocks, start=1):
        print(
            f"#{rank:<2} {stock.symbol:<6} Scanner=#{stock.rank:<2} "
            f"ScannerScore={stock.score:.1f}"
        )

    # Fetch latest prices for all ranked candidates in case top stocks are filtered out
    latest_prices = get_latest_underlying_prices([stock.symbol for stock in ranked_candidates])
    all_options = []
    stocks_by_symbol = {}
    directions_by_symbol = {}

    # Pass 1: Standard deterministic gates across ranked candidates
    for stock in ranked_candidates:
        underlying_price = latest_prices.get(stock.symbol)
        if underlying_price is None or underlying_price <= 0:
            print(f"Skipping {stock.symbol}: no valid live price.")
            continue
        direction = determine_direction(stock)
        if direction == TradeDirection.NEUTRAL:
            print(f"Skipping {stock.symbol}: direction is neutral.")
            continue
        try:
            chain = get_option_chain(stock.symbol)
        except Exception as exc:
            print(f"Skipping {stock.symbol}: option chain retrieval failed ({exc}).")
            continue
        options = select_directional_options(
            chain=chain,
            underlying_price=underlying_price,
            direction=direction.value.lower(),
            realized_vol=stock.realized_volatility,
            max_candidates=MAX_OPTIONS_PER_STOCK,
        )
        if not options:
            print(f"Skipping {stock.symbol}: no option candidates survived deterministic filtering.")
            continue
        stocks_by_symbol[stock.symbol] = stock
        directions_by_symbol[stock.symbol] = direction.value
        all_options.extend(options)

        # Stop early once we have built a sufficiently diversified candidate pool
        if len(stocks_by_symbol) >= LLM_STOCK_TOP_K and len(all_options) >= OPTION_LLM_TOP_K:
            break

    # Pass 2: If all candidates exhausted standard gates, retry with relaxed deterministic gates
    if not all_options:
        print("\nAll candidate stocks exhausted standard deterministic gates. Retrying candidate pool with relaxed filtering...")
        for stock in ranked_candidates:
            underlying_price = latest_prices.get(stock.symbol)
            if underlying_price is None or underlying_price <= 0:
                continue
            direction = determine_direction(stock)
            if direction == TradeDirection.NEUTRAL:
                continue
            try:
                chain = get_option_chain(stock.symbol)
            except Exception as exc:
                continue
            options = select_directional_options(
                chain=chain,
                underlying_price=underlying_price,
                direction=direction.value.lower(),
                realized_vol=stock.realized_volatility,
                max_candidates=MAX_OPTIONS_PER_STOCK,
                relaxed=True,
            )
            if options:
                print(f"  [relaxed gate] Found {len(options)} option candidate(s) for {stock.symbol}.")
                stocks_by_symbol[stock.symbol] = stock
                directions_by_symbol[stock.symbol] = direction.value
                all_options.extend(options)
                if len(all_options) >= 8:
                    break

    if not all_options:
        print("No option candidates survived deterministic filtering even after relaxed retry.")
        return

    ranked_option_symbols = rank_option_pool(
        options=all_options,
        stocks=stocks_by_symbol,
        directions=directions_by_symbol,
        research=research,
        top_k=OPTION_LLM_TOP_K,
        debug=True,
    )
    options_by_symbol = {option.symbol: option for option in all_options}
    selected_options = [options_by_symbol[symbol] for symbol in ranked_option_symbols]

    print("\n" + "=" * 120)
    print(" TOP RANKED OPTIONS (GLOBAL)")
    print("=" * 120)
    for rank, option in enumerate(selected_options, start=1):
        print(
            f"#{rank:<2} {option.symbol:<24} "
            f"Type={option.option_type.upper():<4} Strike=${option.strike:<7.2f} "
            f"DTE={option.dte:<3} Mid=${option.mid:<6.2f} "
            f"Spread={option.spread_pct:<6.1%} "
            f"Delta={option.delta if option.delta is not None else 0.0:+.3f} "
            f"Score={option.score:>5.1f}"
        )

    stock_rank_by_symbol = {stock.symbol: rank for rank, stock in enumerate(selected_stocks, start=1)}
    option_rank_by_symbol = {symbol: rank for rank, symbol in enumerate(ranked_option_symbols, start=1)}

    trade_candidates = build_trade_candidates(
        selected_options,
        stocks_by_symbol=stocks_by_symbol,
        directions_by_symbol=directions_by_symbol,
        stock_rank_by_symbol=stock_rank_by_symbol,
        option_rank_by_symbol=option_rank_by_symbol,
    )

    # ------------------------------------------------------------------
    # PHASE 3 - size, risk-check and execute.
    #
    # Size against the slots actually free, not the global cap: positions we
    # already hold are consuming both risk budget and position count.
    # ------------------------------------------------------------------
    positions = build_portfolio(
        trades=trade_candidates,
        account_equity=account_equity,
        max_positions=open_slots,
    )
    print_portfolio_plan(positions, account_equity)

    risk_report = assess_portfolio(
        positions=positions,
        account_equity=account_equity,
        current_equity=account_equity,
        peak_equity=account_equity,
    )
    print_risk_report(risk_report, account_equity)

    if risk_report.emergency_stop:
        print("Execution skipped: emergency risk stop is active.")
        return

    execution_report = run_execution_stage(
        risk_report.approved_positions,
        confirmed=confirmed,
        expected_account_id=account_id,
    )

    # ------------------------------------------------------------------
    # PHASE 4 - confirm what actually filled.
    #
    # A submitted order is not a position. Without this the agent believes it
    # holds contracts it never got, and a stale resting limit can fill later
    # against a quote the model already rejected.
    # ------------------------------------------------------------------
    if confirmed and execution_report is not None:
        _record_submitted_entries(execution_report)
        print("\n" + "=" * 78)
        print(" FILL VERIFICATION")
        print("=" * 78)
        time.sleep(FILL_CHECK_DELAY_SECONDS)
        outcomes = verify_fills(broker, execution_report)
        filled = sum(1 for _, status, _ in outcomes if status == "FILLED")
        print(f" {filled} of {len(outcomes)} order(s) filled.")
        print("=" * 78)



    return True


def run_exit_cycle(broker, *, confirmed: bool):
    """Cancel stale entry orders, then mark and manage every open position."""
    cancel_stale_orders(broker, dry_run=not confirmed)
    return manage_open_positions(broker, dry_run=not confirmed)


def _heartbeat(message: str) -> None:
    print(f"[{market_now().strftime('%H:%M:%S ET')}] {message}", flush=True)


def run_loop(args) -> None:
    """Run the agent across a single trading session.

    Two cadences, deliberately different:

      * EXITS every EXIT_INTERVAL_SECONDS. Deterministic, no LLM, ~2 API calls.
        This is where P&L is actually made or lost, and short-dated contracts
        move fast enough that a long gap is a real cost.

      * ENTRIES at most every ENTRY_INTERVAL_MINUTES, and only when a slot is
        free. The scanner reads COMPLETED daily bars, so its ranking cannot
        change during a session - re-running it more often re-ranks identical
        inputs and burns model budget for nothing. Entries are retried sooner
        when an exit frees a slot, because that is genuinely new information.

    No exception from a single cycle is allowed to end the session.
    """
    trading_client = get_trading_client()
    account = trading_client.get_account()
    account_id = str(account.id)

    print("=== Connected to Alpaca (Paper Trading) ===")
    print(f"Account ID:     {account_id}")
    print(f"Equity:         ${float(account.equity):,.2f}")
    print(f"Buying power:   ${float(account.buying_power):,.2f}")
    print(f"Options level:  {account.options_trading_level} (approved: {account.options_approved_level})")
    mode = "LIVE PAPER EXECUTION" if args.confirm_paper_trades else "DRY RUN (no orders will be sent)"
    print(f"Mode:           {mode}")
    print(f"Cadence:        exits every {EXIT_INTERVAL_SECONDS // 60} min | "
          f"entries at most every {ENTRY_INTERVAL_MINUTES} min")
    print("Press Ctrl+C to stop. Positions are LEFT OPEN on stop.")

    broker = AlpacaBroker()
    last_entry_at = None
    previous_slots = None
    consecutive_errors = 0

    while True:
        try:
            now = market_now()

            if in_flatten_window(now):
                _heartbeat("Flatten window reached - closing everything and stopping.")
                run_exit_cycle(broker, confirmed=args.confirm_paper_trades)
                _heartbeat("Flatten complete. Session over.")
                return

            remaining = seconds_until_close(broker)
            if remaining is not None and remaining <= 0:
                _heartbeat("Market is closed. Session over.")
                return

            # ---- exits: every cycle, unconditionally ----
            position_report = run_exit_cycle(broker, confirmed=args.confirm_paper_trades)
            equity = float(broker.get_account().equity)
            open_slots = max(0, MAX_POSITIONS - position_report.held_count)

            _heartbeat(
                f"equity ${equity:,.2f} | held {position_report.held_count} "
                f"| closed {len(position_report.closed)} | free slots {open_slots}"
            )

            # ---- entries: scheduled, and only when there is room ----
            slots_freed = previous_slots is not None and open_slots > previous_slots
            minutes_since_entry = (
                None if last_entry_at is None
                else (now - last_entry_at).total_seconds() / 60.0
            )
            due = (
                last_entry_at is None
                or slots_freed
                or (minutes_since_entry is not None and minutes_since_entry >= ENTRY_INTERVAL_MINUTES)
            )
            window_open, window_reason = entry_window_status(broker, now)

            if open_slots <= 0:
                pass
            elif not window_open:
                _heartbeat(f"entries held: {window_reason}")
            elif not due:
                pass
            else:
                held_underlyings = {
                    p.stock_symbol for p in position_report.positions
                    if not p.decision.should_close
                }
                _heartbeat(f"running entry cycle ({open_slots} slot(s) free)")
                try:
                    run_entry_cycle(
                        broker,
                        account_id=account_id,
                        account_equity=equity,
                        open_slots=open_slots,
                        held_underlyings=held_underlyings,
                        confirmed=args.confirm_paper_trades,
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"Entry cycle failed ({type(exc).__name__}: {exc}). "
                          "Exits continue on schedule.")
                last_entry_at = now

            previous_slots = open_slots
            consecutive_errors = 0

        except KeyboardInterrupt:
            print("\nInterrupted. Open positions are LEFT OPEN and unmanaged - "
                  "re-run to resume management, or flatten from the dashboard.")
            return
        except Exception as exc:  # noqa: BLE001
            consecutive_errors += 1
            print(f"Cycle error #{consecutive_errors} ({type(exc).__name__}: {exc})")
            if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                print(f"Aborting after {MAX_CONSECUTIVE_ERRORS} consecutive failures. "
                      "Positions are left open - check the dashboard.")
                return

        try:
            time.sleep(EXIT_INTERVAL_SECONDS)
        except KeyboardInterrupt:
            print("\nInterrupted between cycles. Open positions are LEFT OPEN.")
            return


def run_single_cycle(args) -> None:
    """One exit pass plus, if a slot is free, one entry pass. Then stop."""
    trading_client = get_trading_client()
    account = trading_client.get_account()
    broker = AlpacaBroker()

    position_report = run_exit_cycle(broker, confirmed=args.confirm_paper_trades)
    open_slots = max(0, MAX_POSITIONS - position_report.held_count)
    print(f"\nOpen positions: {position_report.open_count} "
          f"(closing {len(position_report.closed)}) | free slots: {open_slots}")

    if in_flatten_window():
        print("Flatten window is active. No new positions will be opened.")
        return
    if open_slots <= 0:
        print("All position slots are full. No new entries this cycle.")
        return

    window_open, reason = entry_window_status(broker)
    if not window_open:
        print(f"Entry stage skipped: {reason}")
        return

    run_entry_cycle(
        broker,
        account_id=str(account.id),
        account_equity=float(account.equity),
        open_slots=open_slots,
        held_underlyings={
            p.stock_symbol for p in position_report.positions
            if not p.decision.should_close
        },
        confirmed=args.confirm_paper_trades,
    )


def main() -> None:
    args = parse_execution_args()
    if getattr(args, "once", False):
        run_single_cycle(args)
    else:
        run_loop(args)


if __name__ == "__main__":
    main()
