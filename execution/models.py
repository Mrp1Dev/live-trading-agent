from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping


class OrderIntent(str, Enum):
    BUY_TO_OPEN = "BUY_TO_OPEN"


class ExecutionStatus(str, Enum):
    PLANNED = "PLANNED"
    REJECTED = "REJECTED"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    EXPIRED = "EXPIRED"
    FAILED = "FAILED"
    DRY_RUN = "DRY_RUN"


@dataclass(frozen=True)
class TradeCandidate:
    stock_symbol: str
    direction: str
    option_symbol: str
    option_type: str
    expiration: date
    strike: float
    option_bid: float
    option_ask: float
    option_mid: float
    option_delta: float | None
    option_gamma: float | None
    option_vega: float | None
    option_theta: float | None
    trade_score: float
    scanner_score: float
    option_selector_score: float
    stock_llm_rank: int
    option_llm_rank: int
    dte: int

    @classmethod
    def from_ranked_option(
        cls,
        option: Any,
        stock: Any,
        direction: str,
        stock_llm_rank: int,
        option_llm_rank: int,
    ) -> "TradeCandidate":
        return cls(
            stock_symbol=stock.symbol,
            direction=str(direction).upper(),
            option_symbol=option.symbol,
            option_type=option.option_type,
            expiration=option.expiration,
            strike=float(option.strike),
            option_bid=float(option.bid),
            option_ask=float(option.ask),
            option_mid=float(option.mid),
            option_delta=option.delta,
            option_gamma=option.gamma,
            option_vega=option.vega,
            option_theta=option.theta,
            trade_score=float(option.score),
            scanner_score=float(stock.score),
            option_selector_score=float(option.score),
            stock_llm_rank=int(stock_llm_rank),
            option_llm_rank=int(option_llm_rank),
            dte=int(option.dte),
        )


@dataclass(frozen=True)
class ExecutionIntent:
    intent_id: str
    strategy_run_id: str
    stock_symbol: str
    option_symbol: str
    direction: str
    order_intent: OrderIntent
    contracts: int
    authorized_max_loss: float
    reference_entry_price: float
    created_at: datetime
    expiration: date
    option_type: str
    strike: float
    trade_score: float
    stock_llm_rank: int
    option_llm_rank: int


@dataclass(frozen=True)
class LiveOptionQuote:
    symbol: str
    bid: float
    ask: float
    timestamp: datetime | None = None
    source: str = "alpaca-api"

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread_pct(self) -> float:
        mid = self.mid
        return (self.ask - self.bid) / mid if mid > 0 else float("inf")


@dataclass(frozen=True)
class OrderInstruction:
    intent_id: str
    option_symbol: str
    side: str
    position_intent: str
    qty: int
    order_type: str
    limit_price: float
    time_in_force: str
    client_order_id: str


@dataclass(frozen=True)
class ValidationResult:
    approved: bool
    reasons: tuple[str, ...] = ()
    live_quote: LiveOptionQuote | None = None


@dataclass
class ExecutionResult:
    intent: ExecutionIntent
    instruction: OrderInstruction | None
    status: ExecutionStatus
    approved: bool
    reason: str
    order_id: str | None = None
    requested_qty: int = 0
    submitted_qty: int = 0
    filled_qty: int = 0
    average_fill_price: float | None = None
    limit_price: float | None = None
    raw_order: Mapping[str, Any] | None = None


@dataclass
class ExecutionReport:
    strategy_run_id: str
    dry_run: bool
    results: list[ExecutionResult] = field(default_factory=list)

    @property
    def submitted_count(self) -> int:
        return sum(1 for r in self.results if r.status not in {ExecutionStatus.DRY_RUN, ExecutionStatus.REJECTED, ExecutionStatus.FAILED})

    @property
    def filled_count(self) -> int:
        return sum(1 for r in self.results if r.status == ExecutionStatus.FILLED)

    @property
    def rejected_count(self) -> int:
        return sum(1 for r in self.results if not r.approved)
