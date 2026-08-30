from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class HistoricalOptionContract(BaseModel):
    """A contract-level record for a historical option candidate."""

    symbol: str
    underlying_symbol: str
    expiration_date: date
    strike_price: float
    option_type: Literal["CALL", "PUT"]
    tradable: bool
    status: str


class HistoricalOptionMarketData(BaseModel):
    """Historical OHLCV-style option bar data available from the market-data API."""

    symbol: str
    timestamp: datetime
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int | float | None = None
    trade_count: int | None = None
    vwap: float | None = None


class HistoricalOptionCandidate(BaseModel):
    """A fallback candidate generated from the historical underlying price and decision date only."""

    symbol: str
    underlying: str
    expiration: date
    strike: float
    option_type: Literal["CALL", "PUT"]
    dte: int
    historical_price: float | None = None
    volume: int | float | None = None
    trade_count: int | None = None
    vwap: float | None = None
    data_timestamp: datetime | None = None


class ResearchThesis(BaseModel):
    """Research Agent output for a single stock."""

    symbol: str
    direction: Literal["BULLISH", "BEARISH", "NEUTRAL"]
    confidence: float = Field(..., ge=0.0, le=1.0)
    thesis: str
    catalysts: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    time_horizon_days: int | None = None
    supporting_evidence: list[str] = Field(default_factory=list)


class RankedThesis(BaseModel):
    """A thesis after the stock-ranker has scored it."""

    thesis: ResearchThesis
    rank: int
    ranking_score: float
    ranking_reason: str


class OptionCandidate(BaseModel):
    """A candidate contract after deterministic filtering by the option selector."""

    contract: HistoricalOptionContract
    underlying_price: float
    strike: float
    expiration: date
    option_type: Literal["CALL", "PUT"]
    dte: int
    moneyness: float
    historical_price: float | None = None
    volume: int | float | None = None
    trade_count: int | None = None
    vwap: float | None = None


class RankedOption(BaseModel):
    """A candidate option after the option-ranker has scored it."""

    candidate: OptionCandidate
    rank: int
    ranking_score: float
    ranking_reason: str


class TradeStatistics(BaseModel):
    """Deterministic trade statistics used for proposal and portfolio evaluation."""

    expected_return: float | None = None
    historical_win_rate: float | None = None
    historical_loss_rate: float | None = None
    maximum_loss: float | None = None
    estimated_profit: float | None = None
    estimated_loss: float | None = None
    liquidity_score: float | None = None
    trade_score: float | None = None


class TradeProposal(BaseModel):
    """A trade proposal ready for portfolio construction and risk checks."""

    symbol: str
    option_symbol: str
    direction: Literal["BULLISH", "BEARISH"]
    quantity: int
    entry_price: float
    estimated_exposure: float
    trade_score: float
    thesis_confidence: float | None = None


class TradeRecord(BaseModel):
    """A trade after it has been simulated in the backtest."""

    entry_time: datetime
    exit_time: datetime | None = None
    underlying: str
    option_symbol: str
    option_type: Literal["CALL", "PUT"]
    quantity: int
    entry_price: float
    exit_price: float | None = None
    pnl: float | None = None
    pnl_pct: float | None = None
    exit_reason: str | None = None
    trade_score: float | None = None


class PortfolioState(BaseModel):
    """Portfolio state at a point in time in the backtest."""

    timestamp: datetime
    cash: float
    equity: float
    positions: list[TradeRecord] = Field(default_factory=list)
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None


class BacktestSnapshot(BaseModel):
    """A single backtest observation used by the strategy pipeline."""

    timestamp: datetime
    stock_data: list[Any] = Field(default_factory=list)
    news: list[Any] = Field(default_factory=list)
    option_universe: list[HistoricalOptionContract] = Field(default_factory=list)
    portfolio_state: PortfolioState


__all__ = [
    "HistoricalOptionContract",
    "HistoricalOptionMarketData",
    "HistoricalOptionCandidate",
    "ResearchThesis",
    "RankedThesis",
    "OptionCandidate",
    "RankedOption",
    "TradeStatistics",
    "TradeProposal",
    "TradeRecord",
    "PortfolioState",
    "BacktestSnapshot",
]
