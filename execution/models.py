from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Mapping


class OrderIntent(str, Enum):
    BUY_TO_OPEN = "BUY_TO_OPEN"
    SELL_TO_OPEN = "SELL_TO_OPEN"
    MLEG_OPEN = "MLEG_OPEN"


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

    # Multi-leg and spread parameters
    is_mleg: bool = False
    is_credit: bool = False
    spread_type: str = "single_leg"
    long_symbol: str = ""
    short_symbol: str | None = None
    net_limit_price: float = 0.0
    strike_width: float = 0.0
    net_credit: float = 0.0
    net_debit: float = 0.0
    max_loss: float = 0.0
    max_profit: float = 0.0
    legs: list[dict] = field(default_factory=list)

    @classmethod
    def from_ranked_option(
        cls,
        option: Any,
        stock: Any,
        direction: str,
        stock_llm_rank: int,
        option_llm_rank: int,
    ) -> "TradeCandidate":
        is_mleg = getattr(option, "is_mleg", False)
        is_credit = getattr(option, "is_credit", False)
        spread_type = getattr(option, "spread_type", "single_leg")
        long_leg = getattr(option, "long_leg", None)
        short_leg = getattr(option, "short_leg", None)
        long_sym = long_leg.symbol if long_leg else option.symbol
        short_sym = short_leg.symbol if short_leg else None

        legs: list[dict] = []
        if is_mleg and short_sym:
            if is_credit:  # Credit spread: sell short_leg, buy long_leg
                legs = [
                    {"symbol": short_sym, "side": "sell", "ratio_qty": 1, "position_intent": "sell_to_open"},
                    {"symbol": long_sym, "side": "buy", "ratio_qty": 1, "position_intent": "buy_to_open"},
                ]
            else:  # Debit spread: buy long_leg, sell short_leg
                legs = [
                    {"symbol": long_sym, "side": "buy", "ratio_qty": 1, "position_intent": "buy_to_open"},
                    {"symbol": short_sym, "side": "sell", "ratio_qty": 1, "position_intent": "sell_to_open"},
                ]
        else:
            legs = [
                {"symbol": option.symbol, "side": "buy", "ratio_qty": 1, "position_intent": "buy_to_open"}
            ]

        net_price = getattr(option, "net_credit", 0.0) if is_credit else getattr(option, "net_debit", getattr(option, "ask", 0.0))

        return cls(
            stock_symbol=stock.symbol,
            direction=str(direction).upper(),
            option_symbol=option.symbol,
            option_type=getattr(option, "option_type", "spread"),
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
            is_mleg=is_mleg,
            is_credit=is_credit,
            spread_type=spread_type,
            long_symbol=long_sym,
            short_symbol=short_sym,
            net_limit_price=float(net_price),
            strike_width=float(getattr(option, "strike_width", 0.0)),
            net_credit=float(getattr(option, "net_credit", 0.0)),
            net_debit=float(getattr(option, "net_debit", 0.0)),
            max_loss=float(getattr(option, "max_loss", getattr(option, "ask", 0.0))),
            max_profit=float(getattr(option, "max_profit", 0.0)),
            legs=legs,
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

    # Multi-leg spread fields
    is_mleg: bool = False
    is_credit: bool = False
    spread_type: str = "single_leg"
    long_symbol: str = ""
    short_symbol: str | None = None
    net_limit_price: float = 0.0
    legs: list[dict] = field(default_factory=list)


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
    order_class: str = "simple"
    legs: list[dict] = field(default_factory=list)



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
