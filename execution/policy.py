from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import timedelta

from config import MAX_OPTION_SPREAD_PCT


@dataclass(frozen=True)
class ExecutionPolicy:
    max_intent_age: timedelta = timedelta(seconds=300)
    max_reference_price_move_pct: float = 0.15
    max_reference_price_drop_pct: float = 0.25
    max_spread_pct: float = MAX_OPTION_SPREAD_PCT
    cheap_contract_absolute_spread: float = 0.08
    limit_price_buffer_pct: float = 0.02
    time_in_force: str = "day"
    order_type: str = "limit"
    allow_existing_position: bool = False
    allow_existing_open_order: bool = False


def execution_policy_from_env() -> ExecutionPolicy:
    return ExecutionPolicy(
        max_intent_age=timedelta(seconds=float(os.getenv("EXECUTION_MAX_INTENT_AGE_SEC", "300"))),
        max_reference_price_move_pct=float(os.getenv("EXECUTION_MAX_PRICE_MOVE_PCT", "0.15")),
        max_reference_price_drop_pct=float(os.getenv("EXECUTION_MAX_PRICE_DROP_PCT", "0.25")),
        max_spread_pct=float(os.getenv("EXECUTION_MAX_SPREAD_PCT", str(MAX_OPTION_SPREAD_PCT))),
        cheap_contract_absolute_spread=float(os.getenv("EXECUTION_CHEAP_CONTRACT_ABS_SPREAD", "0.08")),
        limit_price_buffer_pct=float(os.getenv("EXECUTION_LIMIT_BUFFER_PCT", "0.02")),
        time_in_force=os.getenv("EXECUTION_TIME_IN_FORCE", "day").lower(),
        order_type=os.getenv("EXECUTION_ORDER_TYPE", "limit").lower(),
        allow_existing_position=os.getenv("EXECUTION_ALLOW_EXISTING_POSITION", "false").lower() == "true",
        allow_existing_open_order=os.getenv("EXECUTION_ALLOW_EXISTING_ORDER", "false").lower() == "true",
    )
