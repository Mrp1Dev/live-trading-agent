from __future__ import annotations

import argparse

from .models import ExecutionIntent

CONFIRM_FLAG = "--confirm-paper-trades"


def parse_execution_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        CONFIRM_FLAG,
        action="store_true",
        help="Authorize submission of risk-approved orders to the configured Alpaca PAPER account.",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single exit+entry cycle and stop, instead of looping for the session.",
    )
    return parser.parse_args(argv)


def print_confirmation_block(intents: list[ExecutionIntent], *, paper_required: bool = True) -> None:
    print("\n" + "=" * 76)
    print("PAPER TRADE EXECUTION CONFIRMATION")
    print("=" * 76)
    print("The following exact orders are authorized for submission to the configured Alpaca PAPER account:")
    print()
    if not intents:
        print("No orders are authorized.")
    total_risk = 0.0
    for i, intent in enumerate(intents, start=1):
        total_risk += intent.authorized_max_loss
        print(f"{i}.")
        print(f"  Symbol:              {intent.option_symbol}")
        print("  Action:              BUY TO OPEN")
        print(f"  Quantity:            {intent.contracts} contract(s)")
        print("  Order type:          LIMIT")
        print(f"  Reference limit:     ${intent.reference_entry_price:.2f}")
        print("  Time in force:       DAY")
        print(f"  Authorized max loss: ${intent.authorized_max_loss:,.2f}")
        print(f"  Underlying:          {intent.stock_symbol}")
        print(f"  Direction:           {intent.direction}")
        print()
    print(f"Total authorized orders: {len(intents)}")
    print(f"Total authorized max loss: ${total_risk:,.2f}")
    print("Account mode: ALPACA PAPER ACCOUNT")
    if paper_required:
        print("Execution safety: paper-only broker guard is required.")
    print("=" * 76)
