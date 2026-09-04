from __future__ import annotations

import os
from datetime import datetime, timezone

from .alpaca_broker import AlpacaBroker
from .confirmation import print_confirmation_block
from .executor import execute_intents
from .journal import ExecutionJournal
from .planner import build_execution_intents
from .policy import execution_policy_from_env
from .report import print_execution_report


def run_execution_stage(
    positions,
    *,
    confirmed: bool,
    strategy_run_id: str | None = None,
    journal_path: str | None = None,
    expected_account_id: str | None = None,
):
    """Run the one-shot execution stage.

    Dry mode creates order instructions but never initializes the write-capable
    Alpaca broker. Confirmed mode is paper-only and submits approved orders.
    """
    intents = build_execution_intents(
        positions,
        strategy_run_id=strategy_run_id,
        created_at=datetime.now(timezone.utc),
    )
    print_confirmation_block(intents)
    actual_journal_path = journal_path or os.getenv("ALPACA_JOURNAL_PATH", "logs/execution.jsonl")
    journal = ExecutionJournal(actual_journal_path)
    policy = execution_policy_from_env()

    if not confirmed:
        report = execute_intents(
            intents,
            broker=_NoBroker(),
            policy=policy,
            journal=journal,
            dry_run=True,
        )
        print_execution_report(report)
        print("\nDRY RUN")
        return report

    broker = AlpacaBroker()
    if expected_account_id:
        account = broker.get_account()
        account_data = account.get("account") if isinstance(account, dict) and isinstance(account.get("account"), dict) else account
        actual_account_id = getattr(account_data, "id", None) or (account_data.get("id") if isinstance(account_data, dict) else None)
        if actual_account_id and str(actual_account_id) != str(expected_account_id):
            raise RuntimeError(
                "Alpaca account mismatch: the execution credentials do not "
                f"match the Alpaca account read by main.py ({expected_account_id}). "
                f"Broker returned account {actual_account_id}."
            )
    report = execute_intents(
        intents,
        broker=broker,
        policy=policy,
        journal=journal,
        dry_run=False,
    )
    print_execution_report(report)
    return report


class _NoBroker:
    """Guard object used by dry-run mode; any accidental broker access explodes."""

    def __getattr__(self, name):
        raise RuntimeError(f"Dry-run safety violation: broker method {name!r} was called")
