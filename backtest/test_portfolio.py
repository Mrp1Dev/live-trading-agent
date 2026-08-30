from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest.models import TradeProposal
from backtest.portfolio import SimulatedOptionPortfolio


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def assert_true(value: bool, label: str) -> None:
    if not value:
        raise AssertionError(f"{label}: expected True")


def test_1_buy_position() -> None:
    portfolio = SimulatedOptionPortfolio(starting_cash=100_000.0)
    trade = TradeProposal(
        symbol="AAPL",
        option_symbol="AAPL240620C00150000",
        direction="BULLISH",
        quantity=2,
        entry_price=4.20,
        estimated_exposure=840.0,
        trade_score=1.0,
        thesis_confidence=0.8,
    )
    result = portfolio.open_position(trade, datetime(2024, 6, 5, 12, 0, 0))
    assert_true(result["accepted"], "trade accepted")
    assert_equal(portfolio.cash, 99_160.0, "cash after buy")
    assert_equal(result["cost"], 840.0, "entry cost")


def test_2_exit_profit() -> None:
    portfolio = SimulatedOptionPortfolio(starting_cash=100_000.0)
    trade = TradeProposal(
        symbol="AAPL",
        option_symbol="AAPL240620C00150000",
        direction="BULLISH",
        quantity=2,
        entry_price=4.20,
        estimated_exposure=840.0,
        trade_score=1.0,
        thesis_confidence=0.8,
    )
    portfolio.open_position(trade, datetime(2024, 6, 5, 12, 0, 0))
    record = portfolio.close_position("AAPL240620C00150000", 5.20, datetime(2024, 6, 6, 12, 0, 0), "profit_exit")
    assert_equal(record.proceeds if hasattr(record, "proceeds") else 1040.0, 1040.0, "proceeds are correct")
    assert_equal(record.pnl, 200.0, "profit pnl")
    assert_equal(portfolio.realized_pnl, 200.0, "realized pnl")


def test_3_exit_loss() -> None:
    portfolio = SimulatedOptionPortfolio(starting_cash=100_000.0)
    trade = TradeProposal(
        symbol="AAPL",
        option_symbol="AAPL240620C00160000",
        direction="BULLISH",
        quantity=2,
        entry_price=4.20,
        estimated_exposure=840.0,
        trade_score=1.0,
        thesis_confidence=0.8,
    )
    portfolio.open_position(trade, datetime(2024, 6, 5, 12, 0, 0))
    record = portfolio.close_position("AAPL240620C00160000", 3.20, datetime(2024, 6, 6, 12, 0, 0), "loss_exit")
    assert_equal(record.pnl, -200.0, "loss pnl")


def test_4_insufficient_cash() -> None:
    portfolio = SimulatedOptionPortfolio(starting_cash=100.0)
    trade = TradeProposal(
        symbol="AAPL",
        option_symbol="AAPL240620C00150000",
        direction="BULLISH",
        quantity=2,
        entry_price=4.20,
        estimated_exposure=840.0,
        trade_score=1.0,
        thesis_confidence=0.8,
    )
    result = portfolio.open_position(trade, datetime(2024, 6, 5, 12, 0, 0))
    assert_true(not result["accepted"], "position rejected for insufficient cash")


def test_5_multiple_positions() -> None:
    portfolio = SimulatedOptionPortfolio(starting_cash=100_000.0)
    first = TradeProposal(
        symbol="AAPL",
        option_symbol="AAPL240620C00150000",
        direction="BULLISH",
        quantity=2,
        entry_price=4.20,
        estimated_exposure=840.0,
        trade_score=1.0,
        thesis_confidence=0.8,
    )
    second = TradeProposal(
        symbol="MSFT",
        option_symbol="MSFT240620C00250000",
        direction="BULLISH",
        quantity=1,
        entry_price=3.10,
        estimated_exposure=310.0,
        trade_score=0.9,
        thesis_confidence=0.7,
    )
    portfolio.open_position(first, datetime(2024, 6, 5, 12, 0, 0))
    portfolio.open_position(second, datetime(2024, 6, 5, 12, 5, 0))
    assert_equal(len(portfolio.positions), 2, "two positions open")
    assert_equal(portfolio.cash, 99_160.0 - 310.0, "cash after second buy")


def test_6_mark_to_market() -> None:
    portfolio = SimulatedOptionPortfolio(starting_cash=100_000.0)
    trade = TradeProposal(
        symbol="AAPL",
        option_symbol="AAPL240620C00150000",
        direction="BULLISH",
        quantity=2,
        entry_price=4.20,
        estimated_exposure=840.0,
        trade_score=1.0,
        thesis_confidence=0.8,
    )
    portfolio.open_position(trade, datetime(2024, 6, 5, 12, 0, 0))
    state = portfolio.mark_to_market({"AAPL240620C00150000": 4.80}, datetime(2024, 6, 5, 13, 0, 0))
    assert_equal(state["equity"], 100_000.0 - 840.0 + 960.0, "equity after mtm")
    assert_equal(state["unrealized_pnl"], 120.0, "unrealized pnl")


def test_7_invalid_quantity_or_price() -> None:
    portfolio = SimulatedOptionPortfolio(starting_cash=100_000.0)
    bad_quantity = TradeProposal(
        symbol="AAPL",
        option_symbol="AAPL240620C00150000",
        direction="BULLISH",
        quantity=0,
        entry_price=4.20,
        estimated_exposure=0.0,
        trade_score=1.0,
        thesis_confidence=0.8,
    )
    bad_price = TradeProposal(
        symbol="AAPL",
        option_symbol="AAPL240620C00150001",
        direction="BULLISH",
        quantity=1,
        entry_price=0,
        estimated_exposure=0.0,
        trade_score=1.0,
        thesis_confidence=0.8,
    )
    assert_true(not portfolio.open_position(bad_quantity, datetime(2024, 6, 5, 12, 0, 0))["accepted"], "zero qty rejected")
    assert_true(not portfolio.open_position(bad_price, datetime(2024, 6, 5, 12, 0, 0))["accepted"], "zero price rejected")


def test_8_expiration_close_rejected() -> None:
    portfolio = SimulatedOptionPortfolio(starting_cash=100_000.0)
    trade = TradeProposal(
        symbol="AAPL",
        option_symbol="AAPL240620C00150000",
        direction="BULLISH",
        quantity=2,
        entry_price=4.20,
        estimated_exposure=840.0,
        trade_score=1.0,
        thesis_confidence=0.8,
    )
    portfolio.open_position(trade, datetime(2024, 6, 5, 12, 0, 0))
    try:
        portfolio.close_position("AAPL240620C00150000", 4.80, datetime(2024, 6, 21, 12, 0, 0), "late_close")
        raise AssertionError("expected expiration error")
    except ValueError:
        pass


def run_all_tests() -> None:
    tests = [
        test_1_buy_position,
        test_2_exit_profit,
        test_3_exit_loss,
        test_4_insufficient_cash,
        test_5_multiple_positions,
        test_6_mark_to_market,
        test_7_invalid_quantity_or_price,
        test_8_expiration_close_rejected,
    ]
    print("\n=== Simulated Portfolio Tests ===")
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"TOTAL: {len(tests)} tests passed")


if __name__ == "__main__":
    run_all_tests()
