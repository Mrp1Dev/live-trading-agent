from __future__ import annotations

import ast
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest.executor import BacktestExecutor
from backtest.models import TradeProposal


def assert_true(value: bool, label: str) -> None:
    if not value:
        raise AssertionError(label)


def test_successful_entry() -> None:
    executor = BacktestExecutor(starting_cash=100_000.0)
    proposal = TradeProposal(
        symbol="AAPL",
        option_symbol="AAPL240620C00150000",
        direction="BULLISH",
        quantity=2,
        entry_price=4.20,
        estimated_exposure=840.0,
        trade_score=1.0,
        thesis_confidence=0.8,
    )
    result = executor.execute_entry(proposal, 4.20, datetime(2024, 6, 5, 12, 0, 0))
    assert_true(result["accepted"], "entry should be accepted")
    assert_true(executor.portfolio.cash < 100_000.0, "cash should decrease")


def test_successful_exit() -> None:
    executor = BacktestExecutor(starting_cash=100_000.0)
    proposal = TradeProposal(
        symbol="AAPL",
        option_symbol="AAPL240620C00150000",
        direction="BULLISH",
        quantity=2,
        entry_price=4.20,
        estimated_exposure=840.0,
        trade_score=1.0,
        thesis_confidence=0.8,
    )
    executor.execute_entry(proposal, 4.20, datetime(2024, 6, 5, 12, 0, 0))
    record = executor.execute_exit("AAPL240620C00150000", 5.20, datetime(2024, 6, 6, 12, 0, 0), "profit_exit")
    assert_true(record.option_symbol == "AAPL240620C00150000", "exit record should match")
    assert_true(record.pnl == 200.0, "exit pnl should be correct")


def test_insufficient_cash() -> None:
    executor = BacktestExecutor(starting_cash=100.0)
    proposal = TradeProposal(
        symbol="AAPL",
        option_symbol="AAPL240620C00150000",
        direction="BULLISH",
        quantity=2,
        entry_price=4.20,
        estimated_exposure=840.0,
        trade_score=1.0,
        thesis_confidence=0.8,
    )
    result = executor.execute_entry(proposal, 4.20, datetime(2024, 6, 5, 12, 0, 0))
    assert_true(not result["accepted"], "entry should be rejected for insufficient cash")


def test_invalid_price() -> None:
    executor = BacktestExecutor(starting_cash=100_000.0)
    proposal = TradeProposal(
        symbol="AAPL",
        option_symbol="AAPL240620C00150000",
        direction="BULLISH",
        quantity=2,
        entry_price=4.20,
        estimated_exposure=840.0,
        trade_score=1.0,
        thesis_confidence=0.8,
    )
    result = executor.execute_entry(proposal, 0.0, datetime(2024, 6, 5, 12, 0, 0))
    assert_true(not result["accepted"], "zero entry price rejected")
    assert_true(result["reason"] == "invalid price", "invalid price reason should be explicit")


def test_invalid_quantity() -> None:
    executor = BacktestExecutor(starting_cash=100_000.0)
    proposal = TradeProposal(
        symbol="AAPL",
        option_symbol="AAPL240620C00150000",
        direction="BULLISH",
        quantity=0,
        entry_price=4.20,
        estimated_exposure=0.0,
        trade_score=1.0,
        thesis_confidence=0.8,
    )
    result = executor.execute_entry(proposal, 4.20, datetime(2024, 6, 5, 12, 0, 0))
    assert_true(not result["accepted"], "zero quantity rejected")
    assert_true(result["reason"] == "invalid quantity", "invalid quantity reason should be explicit")


def test_multiple_positions() -> None:
    executor = BacktestExecutor(starting_cash=100_000.0)
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
    executor.execute_entry(first, 4.20, datetime(2024, 6, 5, 12, 0, 0))
    executor.execute_entry(second, 3.10, datetime(2024, 6, 5, 12, 5, 0))
    assert_true(len(executor.portfolio.positions) == 2, "two positions should be tracked")


def test_no_alpaca_trading_client_import() -> None:
    source = Path("backtest/executor.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imports = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imports.append(node.module)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
    assert_true("alpaca.trading.client" not in imports and "TradingClient" not in imports, "executor must not import TradingClient")


def run_all_tests() -> None:
    tests = [
        test_successful_entry,
        test_successful_exit,
        test_insufficient_cash,
        test_invalid_price,
        test_invalid_quantity,
        test_multiple_positions,
        test_no_alpaca_trading_client_import,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"TOTAL: {len(tests)} tests passed")


if __name__ == "__main__":
    run_all_tests()
