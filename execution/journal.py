from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class ExecutionJournal:
    """Append-only JSONL execution audit log."""

    def __init__(self, path: str | Path = "logs/execution.jsonl") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, event: dict[str, Any]) -> None:
        record = dict(event)
        record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str, sort_keys=True) + "\n")

    def record_intent(self, intent: Any) -> None:
        self.append({
            "event": "EXECUTION_INTENT",
            "intent_id": intent.intent_id,
            "strategy_run_id": intent.strategy_run_id,
            "stock_symbol": intent.stock_symbol,
            "option_symbol": intent.option_symbol,
            "contracts": intent.contracts,
            "authorized_max_loss": intent.authorized_max_loss,
            "reference_entry_price": intent.reference_entry_price,
            "direction": intent.direction,
        })

    def record_result(self, result: Any) -> None:
        self.append({
            "event": "EXECUTION_RESULT",
            "intent_id": result.intent.intent_id,
            "strategy_run_id": result.intent.strategy_run_id,
            "option_symbol": result.intent.option_symbol,
            "status": result.status.value,
            "approved": result.approved,
            "reason": result.reason,
            "order_id": result.order_id,
            "requested_qty": result.requested_qty,
            "submitted_qty": result.submitted_qty,
            "filled_qty": result.filled_qty,
            "average_fill_price": result.average_fill_price,
            "limit_price": result.limit_price,
        })
