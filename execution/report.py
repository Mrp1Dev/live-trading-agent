from __future__ import annotations

from .models import ExecutionReport


def print_execution_report(report: ExecutionReport) -> None:
    print("\n" + "=" * 110)
    print("EXECUTION REPORT")
    print("=" * 110)
    print(f"Strategy run: {report.strategy_run_id}")
    print(f"Mode: {'DRY RUN' if report.dry_run else 'PAPER EXECUTION'}")
    if not report.results:
        print("No execution intents.")
        print("=" * 110)
        return
    for index, result in enumerate(report.results, start=1):
        print(f"\n{index}. {result.intent.option_symbol}")
        print(f"  Action:       BUY TO OPEN")
        print(f"  Requested:    {result.requested_qty}")
        print(f"  Submitted:    {result.submitted_qty}")
        print(f"  Filled:       {result.filled_qty}")
        print(f"  Status:       {result.status.value}")
        if result.order_id:
            print(f"  Order ID:     {result.order_id}")
        if result.limit_price is not None:
            print(f"  Limit price:  ${result.limit_price:.2f}")
        if result.average_fill_price is not None:
            print(f"  Avg fill:     ${result.average_fill_price:.2f}")
        print(f"  Result:       {result.reason}")
    print("\n" + "-" * 110)
    print(f"Intents:        {len(report.results)}")
    print(f"Filled:         {report.filled_count}")
    print(f"Rejected:       {report.rejected_count}")
    if report.dry_run:
        print("DRY RUN — no trading orders were submitted to Alpaca.")
    print("=" * 110)
