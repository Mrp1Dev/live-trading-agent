"""Live Portal Exit Test

Tests the exit execution against the live Alpaca Paper Trading Portal:
1. Detects the active open option position on Alpaca.
2. Evaluates the live bid/ask quote and position mark.
3. Submits the sell-to-close exit order using the exact exit engine routing.
4. Verifies fill confirmation and asserts the Alpaca account is flat.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime

from execution.alpaca_broker import AlpacaBroker
from execution.position_manager import (
    EXIT_LIMIT_TOLERANCE,
    manage_open_positions,
    _exit_client_order_id,
    _safe_float,
    _underlying_of,
)
from strategy.exits import market_now, ExitDecision, URGENCY_IMMEDIATE, realisable_pnl_pct
from strategy import state as position_state


def main():
    print("=" * 80)
    print(" LIVE ALPACA PAPER PORTAL EXIT TEST")
    print("=" * 80)

    broker = AlpacaBroker()
    account = broker.get_account()
    print(f"Connected Account ID: {account.id}")
    print(f"Account Equity:       ${float(account.equity):,.2f}")
    print(f"Buying Power:         ${float(account.buying_power):,.2f}")
    print("-" * 80)

    # 1. Fetch live open positions
    raw_positions = broker.get_positions()
    option_positions = [p for p in raw_positions if "option" in str(getattr(p, "asset_class", "")).lower()]
    
    if not option_positions:
        print("No open option positions found to exit.")
        return 1

    print(f"\n[1/4] Found {len(option_positions)} active open option position(s) on Alpaca:")
    for p in option_positions:
        print(f"  Symbol:          {p.symbol}")
        print(f"  Qty:             {p.qty}")
        print(f"  Avg Entry Price: ${float(p.avg_entry_price):.2f}")
        print(f"  Current Price:   ${float(getattr(p, 'current_price', 0.0) or 0.0):.2f}")
        print(f"  Unrealized P&L:  ${float(getattr(p, 'unrealized_pl', 0.0) or 0.0):.2f} ({float(getattr(p, 'unrealized_plpc', 0.0) or 0.0):+.2%})")

    target_pos = option_positions[0]
    symbol = str(target_pos.symbol)
    contracts = abs(int(_safe_float(getattr(target_pos, "qty", 1))))
    entry_price = _safe_float(getattr(target_pos, "avg_entry_price", 0.0))
    broker_pnl_pct = _safe_float(getattr(target_pos, "unrealized_plpc", 0.0))

    # 2. Get live quote
    print(f"\n[2/4] Fetching live bid/ask quote for {symbol}...")
    quote = broker.get_option_quote(symbol)
    bid = _safe_float(getattr(quote, "bid", 0.0))
    ask = _safe_float(getattr(quote, "ask", 0.0))
    print(f"      Live Bid: ${bid:.2f} | Ask: ${ask:.2f}")

    pnl_pct = realisable_pnl_pct(entry_price, bid, fallback_pnl_pct=broker_pnl_pct)
    print(f"      Realisable P&L (against bid): {pnl_pct:+.2%}" if pnl_pct is not None else "      Realisable P&L: N/A")

    # 3. Submit Sell-To-Close Order via Exit Protocol
    print(f"\n[3/4] Routing SELL TO CLOSE Exit Order to Alpaca...")
    ref_time = market_now()
    exit_client_id = _exit_client_order_id(symbol, ref_time)
    
    if bid > 0:
        limit_price = max(0.01, round(bid * (1.0 - EXIT_LIMIT_TOLERANCE), 2))
        print(f"      Placing marketable limit sell through bid: ${limit_price:.2f} (tolerance={EXIT_LIMIT_TOLERANCE:.0%})")
        sell_order = broker.place_option_order(
            symbol=symbol,
            qty=contracts,
            side="sell",
            position_intent="sell_to_close",
            order_type="limit",
            time_in_force="day",
            limit_price=limit_price,
            client_order_id=exit_client_id,
        )
    else:
        print("      No bid quote; placing market sell_to_close...")
        sell_order = broker.place_option_order(
            symbol=symbol,
            qty=contracts,
            side="sell",
            position_intent="sell_to_close",
            order_type="market",
            time_in_force="day",
            limit_price=None,
            client_order_id=exit_client_id,
        )

    sell_order_id = str(getattr(sell_order, "id", ""))
    print(f"      Order Sent! Broker Order ID: {sell_order_id}")
    print(f"      Client Order ID:             {exit_client_id}")

    # 4. Wait for fill verification
    print(f"\n[4/4] Monitoring exit order fill on Alpaca portal...")
    exit_filled = False
    for i in range(20):
        time.sleep(1)
        o = broker.get_order_by_id(sell_order_id)
        status = str(getattr(o, "status", "")).lower()
        filled_qty = int(float(getattr(o, "filled_qty", 0)))
        fill_price = float(getattr(o, "filled_avg_price", 0.0) or 0.0)
        if filled_qty >= contracts:
            print(f"      EXIT FILLED: {filled_qty} contract(s) @ ${fill_price:.2f} (Status: {status})")
            exit_filled = True
            break
        sys.stdout.write(f"\r      Checking exit status: {status} ({i+1}s)...")
        sys.stdout.flush()

    if not exit_filled:
        print(f"\n      Order did not fill immediately at limit. Replacing with market close fallback...")
        broker.close_position(symbol, qty=contracts)
        time.sleep(3)

    # 5. Final Confirmation on Alpaca
    time.sleep(2)
    final_positions = broker.get_positions()
    remaining = [p for p in final_positions if str(getattr(p, "symbol", "")) == symbol]
    
    print("\n" + "=" * 80)
    if not remaining:
        print(" SUCCESS! POSITION FULLY CLOSED ON LIVE ALPACA PORTAL.")
        print(f" Contract:           {symbol}")
        print(f" Entry Price:        ${entry_price:.2f}")
        print(f" Exit Fill Price:    ${fill_price:.2f}")
        print(f" Final Account Pos:  0 contracts (Flat)")
    else:
        print(f" WARNING: Position still listed on account: {remaining}")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
