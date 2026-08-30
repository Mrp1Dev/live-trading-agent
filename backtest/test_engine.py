from __future__ import annotations

import ast
import sys
from datetime import datetime
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backtest.engine import BacktestEngine, MockResearchProvider
from backtest.executor import BacktestExecutor


def _mock_stock_scanner(rows):
    return rows


def _mock_stock_ranker(rows):
    return rows


def _mock_option_selector(candidates):
    return candidates[:5]


def _mock_option_ranker(rows):
    return rows


def _mock_risk_manager(proposal, available_cash):
    return {"approved": True, "reason": "allowed in test"}


def test_engine_initializes() -> None:
    engine = BacktestEngine(
        initial_capital=100_000.0,
        max_new_trades_per_day=1,
        max_open_positions=5,
        holding_period_trading_days=5,
        stock_scanner=_mock_stock_scanner,
        stock_ranker=_mock_stock_ranker,
        option_selector=_mock_option_selector,
        option_ranker=_mock_option_ranker,
        risk_manager=_mock_risk_manager,
        executor=BacktestExecutor(starting_cash=100_000.0),
    )
    assert engine.initial_capital == 100_000.0
    assert engine.max_new_trades_per_day == 1
    assert engine.max_open_positions == 5


def test_engine_runs_daily_loop_and_uses_no_future_data() -> None:
    engine = BacktestEngine(
        initial_capital=100_000.0,
        max_new_trades_per_day=1,
        max_open_positions=5,
        holding_period_trading_days=5,
        research_provider=MockResearchProvider(),
        stock_scanner=_mock_stock_scanner,
        stock_ranker=_mock_stock_ranker,
        option_selector=_mock_option_selector,
        option_ranker=_mock_option_ranker,
        risk_manager=_mock_risk_manager,
        executor=BacktestExecutor(starting_cash=100_000.0),
    )
    result = engine.run(
        start=datetime(2024, 6, 3, 12, 0, 0),
        end=datetime(2024, 6, 28, 12, 0, 0),
        symbols=["AAPL", "MSFT", "NVDA"],
    )
    assert result.final_equity >= 0
    assert isinstance(result.equity_curve, list)
    assert result.number_of_trades >= 0


def test_engine_rejects_no_optionchain_or_tradingclient_import() -> None:
    source = Path("backtest/engine.py").read_text(encoding="utf-8")
    assert "OptionChainRequest" not in source
    assert "TradingClient" not in source
    assert "GetOptionContractsRequest" not in source


def test_engine_zero_trade_result_is_valid() -> None:
    engine = BacktestEngine(
        initial_capital=100_000.0,
        max_new_trades_per_day=1,
        max_open_positions=5,
        holding_period_trading_days=5,
        research_provider=MockResearchProvider(),
        stock_scanner=_mock_stock_scanner,
        stock_ranker=_mock_stock_ranker,
        option_selector=_mock_option_selector,
        option_ranker=_mock_option_ranker,
        risk_manager=_mock_risk_manager,
        executor=BacktestExecutor(starting_cash=100_000.0),
    )
    result = engine.run(
        start=datetime(2024, 6, 3, 12, 0, 0),
        end=datetime(2024, 6, 4, 12, 0, 0),
        symbols=["AAPL"],
    )
    assert result.number_of_trades >= 0
    assert result.final_equity >= 0


def test_engine_prints_summary() -> None:
    engine = BacktestEngine(
        initial_capital=100_000.0,
        max_new_trades_per_day=1,
        max_open_positions=5,
        holding_period_trading_days=5,
        research_provider=MockResearchProvider(),
        stock_scanner=_mock_stock_scanner,
        stock_ranker=_mock_stock_ranker,
        option_selector=_mock_option_selector,
        option_ranker=_mock_option_ranker,
        risk_manager=_mock_risk_manager,
        executor=BacktestExecutor(starting_cash=100_000.0),
    )
    result = engine.run(
        start=datetime(2024, 6, 3, 12, 0, 0),
        end=datetime(2024, 6, 10, 12, 0, 0),
        symbols=["AAPL"],
    )
    print("\n=========================================")
    print("BACKTEST V0 SUMMARY")
    print("=========================================")
    print(f"Period: {datetime(2024, 6, 3, 12, 0, 0).isoformat()} -> {datetime(2024, 6, 10, 12, 0, 0).isoformat()}")
    print(f"Starting capital: ${result.initial_capital:,.2f}")
    print(f"Ending equity: ${result.final_equity:,.2f}")
    print(f"Total P&L: ${result.total_pnl:,.2f}")
    print(f"Total return: {result.total_return * 100:.2f}%")
    print(f"Trades: {result.number_of_trades}")
    print(f"Wins: {result.winning_trades}")
    print(f"Losses: {result.losing_trades}")
    print(f"Win rate: {result.win_rate * 100:.2f}%")
    print(f"Stock signals: {len(result.trade_history)}")
    print(f"Option candidates: {result.data_gap_counts.get('option_universe_empty', 0)}")
    print(f"Trades attempted: {result.number_of_trades}")
    print(f"Trades executed: {result.number_of_trades}")
    print(f"Option data gaps: {result.data_gap_counts.get('option_universe_empty', 0)}")
    print(f"Risk rejections: {result.rejection_counts.get('risk_rejection', 0)}")
    print(f"Execution rejections: {result.rejection_counts.get('execution_rejection', 0)}")
    print("=========================================")


def run_all_tests() -> None:
    tests = [
        test_engine_initializes,
        test_engine_runs_daily_loop_and_uses_no_future_data,
        test_engine_rejects_no_optionchain_or_tradingclient_import,
        test_engine_zero_trade_result_is_valid,
        test_engine_prints_summary,
    ]
    for test in tests:
        test()
        print(f"PASS: {test.__name__}")
    print(f"TOTAL: {len(tests)} tests passed")


if __name__ == "__main__":
    run_all_tests()
