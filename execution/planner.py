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
        is_mleg = bool(getattr(trade, "is_mleg", False))
        is_credit = bool(getattr(trade, "is_credit", False))
        spread_type = str(getattr(trade, "spread_type", "single_leg"))
        long_symbol = str(getattr(trade, "long_symbol", option_symbol))
        short_symbol = getattr(trade, "short_symbol", None)
        net_limit_price = float(getattr(trade, "net_limit_price", 0.0) or getattr(trade, "option_ask", 0.0))
        legs = list(getattr(trade, "legs", []))

        order_intent = OrderIntent.MLEG_OPEN if is_mleg else (OrderIntent.SELL_TO_OPEN if is_credit else OrderIntent.BUY_TO_OPEN)

        intents.append(
            ExecutionIntent(
                intent_id=intent_id,
                strategy_run_id=strategy_run_id,
                stock_symbol=trade.stock_symbol,
                option_symbol=option_symbol,
                direction=trade.direction,
                order_intent=order_intent,
                contracts=int(position.contracts),
                authorized_max_loss=float(position.max_loss),
                reference_entry_price=net_limit_price if net_limit_price > 0 else float(position.max_loss / max(position.contracts * 100, 1)),
                created_at=created_at,
                expiration=expiration,
                option_type=option_type,
                strike=strike,
                trade_score=float(trade.trade_score),
                stock_llm_rank=int(getattr(trade, "stock_llm_rank", 0)),
                option_llm_rank=int(getattr(trade, "option_llm_rank", 0)),
                is_mleg=is_mleg,
                is_credit=is_credit,
                spread_type=spread_type,
                long_symbol=long_symbol,
                short_symbol=short_symbol,
                net_limit_price=net_limit_price,
                strike_width=float(getattr(trade, "strike_width", 0.0)),
                legs=legs,
            )
        )
    return intents


def build_order_instruction(
    intent: ExecutionIntent,
    *,
    live_ask: float | None = None,
    live_quote: LiveOptionQuote | None = None,
    policy: ExecutionPolicy,
) -> OrderInstruction:
    bid = float(getattr(live_quote, "bid", 0.0) or 0.0) if live_quote else 0.0
    ask = float(getattr(live_quote, "ask", 0.0) or 0.0) if live_quote else float(live_ask or 0.0)
    if ask <= 0 and live_ask is not None:
        ask = float(live_ask)

    if intent.is_mleg:
        if intent.is_credit:
            # For credit spreads: bid is net credit to receive at market; ask is cost to buy back
            # Near-mid credit: price slightly inside the spread to capture price improvement
            if bid > 0 and ask > 0 and ask >= bid:
                mid = (bid + ask) / 2.0
                credit_target = max(0.01, round(mid - 0.15 * (mid - bid), 2))
            else:
                ref_credit = bid if bid > 0 else (ask if ask > 0 else intent.reference_entry_price)
                credit_target = max(0.01, round(ref_credit * (1.0 - policy.limit_price_buffer_pct), 2))
            limit_price = -credit_target
        else:
            # For debit spreads: ask is net debit to pay at market; bid is sell value
            if bid > 0 and ask > 0 and ask >= bid:
                mid = (bid + ask) / 2.0
                limit_price = max(0.01, round(mid + 0.15 * (ask - mid), 2))
            else:
                ref_debit = ask if ask > 0 else intent.reference_entry_price
                limit_price = max(0.01, round(ref_debit * (1.0 + policy.limit_price_buffer_pct), 2))
    elif intent.is_credit:
        ref_price = bid if bid > 0 else (ask if ask > 0 else intent.reference_entry_price)
        limit_price = max(0.01, round(ref_price * (1.0 - policy.limit_price_buffer_pct), 2))
    else:
        if bid > 0 and ask > 0 and ask >= bid:
            mid = (bid + ask) / 2.0
            limit_price = max(0.01, round(mid + 0.15 * (ask - mid), 2))
        else:
            ref_price = ask if ask > 0 else intent.reference_entry_price
            limit_price = max(0.01, round(ref_price * (1.0 + policy.limit_price_buffer_pct), 2))

    client_order_id = f"oa-{intent.strategy_run_id}-{intent.intent_id}"[:48]
    order_class = "mleg" if intent.is_mleg else "simple"
    side = "sell" if intent.is_credit else "buy"
    position_intent = "sell_to_open" if intent.is_credit else "buy_to_open"

    return OrderInstruction(
        intent_id=intent.intent_id,
        option_symbol=intent.option_symbol,
        side=side,
        position_intent=position_intent,
        qty=intent.contracts,
        order_type=policy.order_type,
        limit_price=limit_price,
        time_in_force=policy.time_in_force,
        client_order_id=client_order_id,
        order_class=order_class,
        legs=intent.legs,
    )

