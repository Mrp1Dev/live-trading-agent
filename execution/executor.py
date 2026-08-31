from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol

from .errors import BrokerError, ExecutionError, MCPBrokerError
from .journal import ExecutionJournal
from .models import ExecutionIntent, ExecutionReport, ExecutionResult, ExecutionStatus
from .planner import build_order_instruction
from .policy import ExecutionPolicy
from .validator import validate_intent


class BrokerLike(Protocol):
    def get_positions(self) -> list: ...
    def get_open_orders(self) -> list: ...
    def get_option_contract(self, symbol: str): ...
    def get_option_quote(self, symbol: str): ...
    def get_order_by_client_id(self, client_order_id: str): ...
    def place_option_order(self, **kwargs): ...


def _to_data_dict(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if hasattr(raw, "model_dump"):
        return raw.model_dump()
    if hasattr(raw, "to_dict"):
        return raw.to_dict()
    if hasattr(raw, "dict"):
        return raw.dict()
    return getattr(raw, "__dict__", {})


def _extract_status(raw) -> tuple[str, Any]:
    if raw is None:
        return "unknown", {}
    if isinstance(raw, dict):
        data = raw.get("order") if isinstance(raw.get("order"), dict) else raw
        status_val = data.get("status", "unknown")
        status_str = status_val.value if hasattr(status_val, "value") else str(status_val)
        return status_str.lower(), data
    status_val = getattr(raw, "status", "unknown")
    status_str = status_val.value if hasattr(status_val, "value") else str(status_val)
    return status_str.lower(), raw


def _extract_order_id(data) -> str | None:
    if isinstance(data, dict):
        raw_id = data.get("id") or data.get("order_id")
        return str(raw_id) if raw_id is not None else None
    raw_id = getattr(data, "id", None) or getattr(data, "order_id", None)
    return str(raw_id) if raw_id is not None else None


def _fill_qty(data) -> int:
    if isinstance(data, dict):
        value = data.get("filled_qty", data.get("filled_quantity", 0))
    else:
        value = getattr(data, "filled_qty", getattr(data, "filled_quantity", 0))
    try:
        return int(float(value or 0))
    except (TypeError, ValueError):
        return 0


def _avg_fill(data) -> float | None:
    for key in ("filled_avg_price", "average_fill_price", "avg_fill_price"):
        if isinstance(data, dict):
            value = data.get(key)
        else:
            value = getattr(data, key, None)
        if value not in (None, ""):
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return None


def execute_intents(
    intents: list[ExecutionIntent],
    *,
    broker: BrokerLike,
    policy: ExecutionPolicy,
    journal: ExecutionJournal | None = None,
    dry_run: bool = False,
    now: datetime | None = None,
) -> ExecutionReport:
    now = now or datetime.now(timezone.utc)
    journal = journal or ExecutionJournal()
    report = ExecutionReport(strategy_run_id=intents[0].strategy_run_id if intents else "none", dry_run=dry_run)

    positions = broker.get_positions() if not dry_run else []
    open_orders = broker.get_open_orders() if not dry_run else []

    for intent in intents:
        journal.record_intent(intent)
        if dry_run:
            instruction = build_order_instruction(intent, live_ask=intent.reference_entry_price, policy=policy)
            result = ExecutionResult(
                intent=intent,
                instruction=instruction,
                status=ExecutionStatus.DRY_RUN,
                approved=True,
                reason="Dry run: no order submitted.",
                requested_qty=intent.contracts,
                submitted_qty=0,
                filled_qty=0,
                limit_price=instruction.limit_price,
            )
            report.results.append(result)
            journal.record_result(result)
            continue

        try:
            # Idempotency/recovery check before any write.
            instruction = build_order_instruction(intent, live_ask=intent.reference_entry_price, policy=policy)
            try:
                existing = broker.get_order_by_client_id(instruction.client_order_id)
            except (BrokerError, MCPBrokerError, ExecutionError, Exception):
                existing = None
            if existing:
                status, data = _extract_status(existing)
                status_map = {
                    "filled": ExecutionStatus.FILLED,
                    "partially_filled": ExecutionStatus.PARTIALLY_FILLED,
                    "canceled": ExecutionStatus.CANCELED,
                    "expired": ExecutionStatus.EXPIRED,
                    "new": ExecutionStatus.SUBMITTED,
                    "accepted": ExecutionStatus.SUBMITTED,
                    "pending_new": ExecutionStatus.SUBMITTED,
                    "accepted_for_bidding": ExecutionStatus.SUBMITTED,
                    "done_for_day": ExecutionStatus.SUBMITTED,
                    "stopped": ExecutionStatus.SUBMITTED,
                    "rejected": ExecutionStatus.FAILED,
                    "suspended": ExecutionStatus.FAILED,
                }
                order_id = _extract_order_id(data)
                result = ExecutionResult(
                    intent=intent,
                    instruction=instruction,
                    status=status_map.get(status, ExecutionStatus.SUBMITTED),
                    approved=True,
                    reason="Existing Alpaca order found by client_order_id; no duplicate submitted.",
                    order_id=order_id,
                    requested_qty=intent.contracts,
                    submitted_qty=intent.contracts,
                    filled_qty=_fill_qty(data),
                    average_fill_price=_avg_fill(data),
                    limit_price=instruction.limit_price,
                    raw_order=_to_data_dict(data),
                )
                report.results.append(result)
                journal.record_result(result)
                continue

            live_quote = broker.get_option_quote(intent.option_symbol)
            contract = broker.get_option_contract(intent.option_symbol)
            validation = validate_intent(
                intent,
                live_quote_raw=live_quote,
                contract_raw=contract,
                positions=positions,
                open_orders=open_orders,
                now=now,
                policy=policy,
            )
            if not validation.approved or validation.live_quote is None:
                result = ExecutionResult(
                    intent=intent,
                    instruction=None,
                    status=ExecutionStatus.REJECTED,
                    approved=False,
                    reason="; ".join(validation.reasons),
                    requested_qty=intent.contracts,
                )
                report.results.append(result)
                journal.record_result(result)
                continue

            instruction = build_order_instruction(intent, live_ask=validation.live_quote.ask, policy=policy)
            # Recheck actual executable premium against authorized max loss with policy price move tolerance.
            max_tolerated_risk = intent.authorized_max_loss * (1.0 + policy.max_reference_price_move_pct)
            actual_authorized_risk = validation.live_quote.ask * 100.0 * intent.contracts
            if actual_authorized_risk > max_tolerated_risk + 1e-9:
                result = ExecutionResult(
                    intent=intent,
                    instruction=instruction,
                    status=ExecutionStatus.REJECTED,
                    approved=False,
                    reason=(
                        f"live ask implies ${actual_authorized_risk:,.2f} max loss, "
                        f"above authorized ${intent.authorized_max_loss:,.2f} "
                        f"(max allowed with {policy.max_reference_price_move_pct:.0%} price move buffer: ${max_tolerated_risk:,.2f})"
                    ),
                    requested_qty=intent.contracts,
                    limit_price=instruction.limit_price,
                )
                report.results.append(result)
                journal.record_result(result)
                continue

            raw_order = broker.place_option_order(
                symbol=instruction.option_symbol,
                qty=instruction.qty,
                side=instruction.side,
                position_intent=instruction.position_intent,
                order_type=instruction.order_type,
                time_in_force=instruction.time_in_force,
                limit_price=instruction.limit_price,
                client_order_id=instruction.client_order_id,
            )
            status, data = _extract_status(raw_order)
            order_id = _extract_order_id(data)
            status_enum = {
                "filled": ExecutionStatus.FILLED,
                "partially_filled": ExecutionStatus.PARTIALLY_FILLED,
                "canceled": ExecutionStatus.CANCELED,
                "expired": ExecutionStatus.EXPIRED,
                "rejected": ExecutionStatus.FAILED,
                "suspended": ExecutionStatus.FAILED,
            }.get(status, ExecutionStatus.SUBMITTED)
            result = ExecutionResult(
                intent=intent,
                instruction=instruction,
                status=status_enum,
                approved=True,
                reason="Order submitted to Alpaca paper account.",
                order_id=order_id,
                requested_qty=intent.contracts,
                submitted_qty=intent.contracts,
                filled_qty=_fill_qty(data),
                average_fill_price=_avg_fill(data),
                limit_price=instruction.limit_price,
                raw_order=_to_data_dict(data),
            )
        except (BrokerError, MCPBrokerError, ExecutionError, ValueError, Exception) as exc:
            result = ExecutionResult(
                intent=intent,
                instruction=locals().get("instruction"),
                status=ExecutionStatus.FAILED,
                approved=False,
                reason=str(exc),
                requested_qty=intent.contracts,
            )
        report.results.append(result)
        journal.record_result(result)

    return report
