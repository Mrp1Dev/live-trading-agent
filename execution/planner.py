from __future__ import annotations

from datetime import datetime, timezone
import hashlib

from .models import ExecutionIntent, OrderInstruction, OrderIntent
from .policy import ExecutionPolicy


def make_strategy_run_id(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y%m%dT%H%M%SZ")


def build_execution_intents(positions, *, strategy_run_id: str | None = None, created_at: datetime | None = None) -> list[ExecutionIntent]:
    created_at = created_at or datetime.now(timezone.utc)
    strategy_run_id = strategy_run_id or make_strategy_run_id(created_at)
    intents: list[ExecutionIntent] = []
    for index, position in enumerate(positions, start=1):
        trade = position.trade
        option_symbol = trade.option_symbol
        raw = f"{strategy_run_id}|{trade.stock_symbol}|{option_symbol}|{index}"
        intent_id = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
        expiration = getattr(trade, "expiration", None)
        option_type = getattr(trade, "option_type", "")
        strike = float(getattr(trade, "strike", 0.0))
        intents.append(
            ExecutionIntent(
                intent_id=intent_id,
                strategy_run_id=strategy_run_id,
                stock_symbol=trade.stock_symbol,
                option_symbol=option_symbol,
                direction=trade.direction,
                order_intent=OrderIntent.BUY_TO_OPEN,
                contracts=int(position.contracts),
                authorized_max_loss=float(position.max_loss),
                reference_entry_price=float(getattr(trade, "option_ask", position.max_loss / max(position.contracts * 100, 1))),
                created_at=created_at,
                expiration=expiration,
                option_type=option_type,
                strike=strike,
                trade_score=float(trade.trade_score),
                stock_llm_rank=int(getattr(trade, "stock_llm_rank", 0)),
                option_llm_rank=int(getattr(trade, "option_llm_rank", 0)),
            )
        )
    return intents


def build_order_instruction(intent: ExecutionIntent, *, live_ask: float, policy: ExecutionPolicy) -> OrderInstruction:
    if live_ask <= 0:
        raise ValueError("live_ask must be positive")
    limit_price = round(live_ask * (1.0 + policy.limit_price_buffer_pct), 2)
    client_order_id = f"oa-{intent.strategy_run_id}-{intent.intent_id}"[:48]
    return OrderInstruction(
        intent_id=intent.intent_id,
        option_symbol=intent.option_symbol,
        side="buy",
        position_intent="buy_to_open",
        qty=intent.contracts,
        order_type=policy.order_type,
        limit_price=limit_price,
        time_in_force=policy.time_in_force,
        client_order_id=client_order_id,
    )
