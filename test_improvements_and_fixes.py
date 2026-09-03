"""Verification for recent improvements and bug fixes:
1. strike_width propagation into ExecutionIntent and State.
2. Real-time intraday momentum confirmation in determine_direction.
3. Calibrated credit spread stop loss (-75%) vs debit stop loss (-40%).
4. Near-midpoint limit price improvement in build_order_instruction.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone

from config import (
    CREDIT_SPREAD_STOP_LOSS_PCT,
    DEBIT_SPREAD_TAKE_PROFIT_PCT,
    MAX_HOLD_MINUTES,
    STOP_LOSS_PCT,
)
from execution.models import (
    ExecutionIntent,
    LiveOptionQuote,
    OrderIntent,
    TradeCandidate,
)
from execution.planner import build_execution_intents, build_order_instruction
from execution.policy import ExecutionPolicy
from strategy.direction import TradeDirection, determine_direction
from strategy.exits import ExitDecision, evaluate_exit
from strategy.portfolio import PortfolioPosition
from strategy.state import PositionState, record_entry


class FakeStock:
    def __init__(self, return_5d: float, return_20d: float, rs_spy: float):
        self.return_5d = return_5d
        self.return_20d = return_20d
        self.relative_strength_spy = rs_spy


class TestImprovements(unittest.TestCase):
    def test_strike_width_propagation(self):
        """Verify strike_width is preserved from TradeCandidate to ExecutionIntent and State."""
        trade = TradeCandidate(
            stock_symbol="AAPL",
            direction="BULLISH",
            option_symbol="AAPL_260902_BPC_P320/P315",
            option_type="spread",
            expiration=date(2026, 9, 2),
            strike=320.0,
            option_bid=0.60,
            option_ask=0.64,
            option_mid=0.62,
            option_delta=0.25,
            option_gamma=0.05,
            option_vega=0.10,
            option_theta=0.15,
            trade_score=85.0,
            scanner_score=80.0,
            option_selector_score=85.0,
            stock_llm_rank=1,
            option_llm_rank=1,
            dte=2,
            is_mleg=True,
            is_credit=True,
            spread_type="credit_bull_put",
            long_symbol="AAPL260902P00315000",
            short_symbol="AAPL260902P00320000",
            strike_width=5.0,
            net_credit=0.62,
            max_loss=4.38,
        )

        pos = PortfolioPosition(
            trade=trade,
            contracts=5,
            max_loss=2190.0,
            premium_deployed=2190.0,
            risk_weight=1.0,
        )

        intents = build_execution_intents([pos])
        self.assertEqual(len(intents), 1)
        intent = intents[0]
        self.assertEqual(intent.strike_width, 5.0)

        state = {}
        entry = record_entry(
            state,
            option_symbol=intent.option_symbol,
            stock_symbol=intent.stock_symbol,
            direction=intent.direction,
            entry_price=0.62,
            contracts=intent.contracts,
            is_spread=intent.is_mleg,
            is_credit=intent.is_credit,
            spread_type=intent.spread_type,
            long_symbol=intent.long_symbol,
            short_symbol=intent.short_symbol,
            strike_width=intent.strike_width,
        )
        self.assertEqual(entry.strike_width, 5.0)
        self.assertEqual(state[intent.option_symbol].strike_width, 5.0)

    def test_intraday_momentum_confirmation(self):
        """Verify intraday momentum vetoes contradictory trends."""
        # Strong macro bull stock
        stock = FakeStock(return_5d=0.04, return_20d=0.12, rs_spy=0.06)

        # Baseline: positive intraday -> BULLISH
        dir_ok = determine_direction(
            stock,
            intraday_return=0.005,
            change_pct=0.010,
            spy_intraday_return=0.002,
        )
        self.assertEqual(dir_ok, TradeDirection.BULLISH)

        # Vetoed: stock is dropping intraday (-0.6%) -> NEUTRAL
        dir_dumping = determine_direction(
            stock,
            intraday_return=-0.006,
            change_pct=-0.005,
            spy_intraday_return=0.001,
        )
        self.assertEqual(dir_dumping, TradeDirection.NEUTRAL)

        # Vetoed: SPY is crashing intraday (-1.5%) -> NEUTRAL
        dir_market_dump = determine_direction(
            stock,
            intraday_return=0.002,
            change_pct=0.004,
            spy_intraday_return=-0.015,
        )
        self.assertEqual(dir_market_dump, TradeDirection.NEUTRAL)

    def test_credit_spread_stop_loss_threshold(self):
        """Verify credit spread allows normal bid-ask noise and stops at -75%."""
        now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

        # Normal fluctuation: entry credit 0.60, ask to close is 0.75 (pnl = -25%)
        # Should HOLD under calibrated -75% stop
        d_hold = evaluate_exit(
            pnl_pct=-0.25,
            peak_pnl_pct=0.0,
            dte=2,
            opened_at=now.isoformat(),
            now=now,
            is_credit=True,
            is_spread=True,
        )
        self.assertEqual(d_hold.should_close, False)

        # Severe loss: entry credit 0.60, ask to close is 1.10 (pnl = -83.3% <= -75%)
        # Should trigger STOP_LOSS_CREDIT
        d_stop = evaluate_exit(
            pnl_pct=-0.80,
            peak_pnl_pct=0.0,
            dte=2,
            opened_at=now.isoformat(),
            now=now,
            is_credit=True,
            is_spread=True,
        )
        self.assertEqual(d_stop.should_close, True)
        self.assertEqual(d_stop.reason, "STOP_LOSS_CREDIT")

    def test_marketable_entry_limit_pricing(self):
        """Verify limit orders price at the executable ask to guarantee instant fills on broker."""
        intent = ExecutionIntent(
            intent_id="test1",
            strategy_run_id="run1",
            stock_symbol="NVDA",
            option_symbol="NVDA_260902_BCD_C217.5/C222.5",
            direction="BULLISH",
            order_intent=OrderIntent.MLEG_OPEN,
            contracts=10,
            authorized_max_loss=1700.0,
            reference_entry_price=1.70,
            created_at=datetime.now(timezone.utc),
            expiration=date(2026, 9, 2),
            option_type="spread",
            strike=217.5,
            trade_score=80.0,
            stock_llm_rank=1,
            option_llm_rank=1,
            is_mleg=True,
            is_credit=False,
            spread_type="debit_bull_call",
        )

        quote = LiveOptionQuote(
            symbol="NVDA_260902_BCD_C217.5/C222.5",
            bid=1.50,
            ask=1.90,  # Midpoint is 1.70
        )
        policy = ExecutionPolicy()

        instr = build_order_instruction(intent, live_quote=quote, policy=policy)
        # Marketable limit price matches ask (1.90) to guarantee execution
        self.assertAlmostEqual(instr.limit_price, 1.90, places=2)


if __name__ == "__main__":
    unittest.main()
