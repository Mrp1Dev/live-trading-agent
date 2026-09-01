import unittest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

from execution.models import ExecutionIntent, OrderIntent, TradeCandidate, LiveOptionQuote
from execution.planner import build_order_instruction
from execution.policy import ExecutionPolicy
from execution.position_manager import manage_open_positions
from strategy.option_selector import OptionCandidate, SpreadCandidate
from strategy.state import PositionState, reconcile


class TestMLEGSpreads(unittest.TestCase):
    def setUp(self):
        self.policy = ExecutionPolicy()

    def test_mleg_order_instruction_signs(self):
        # Credit spread entry -> MUST produce negative limit_price in Alpaca MLEG
        credit_intent = ExecutionIntent(
            intent_id="intent-credit",
            strategy_run_id="run-1",
            stock_symbol="SPY",
            option_symbol="SPY_260902_BPC_P560/P555",
            direction="BULLISH",
            order_intent=OrderIntent.MLEG_OPEN,
            contracts=5,
            authorized_max_loss=1900.0,
            reference_entry_price=1.20,
            created_at=datetime.now(timezone.utc),
            expiration=date(2026, 9, 2),
            option_type="spread",
            strike=560.0,
            trade_score=75.0,
            stock_llm_rank=1,
            option_llm_rank=1,
            is_mleg=True,
            is_credit=True,
            spread_type="credit_bull_put",
            long_symbol="SPY260902P00555000",
            short_symbol="SPY260902P00560000",
            net_limit_price=1.20,
            legs=[
                {"symbol": "SPY260902P00560000", "side": "sell", "ratio_qty": 1, "position_intent": "sell_to_open"},
                {"symbol": "SPY260902P00555000", "side": "buy", "ratio_qty": 1, "position_intent": "buy_to_open"},
            ],
        )

        # Default policy (buffer = 0.0) -> limit_price is -1.20
        instr_credit = build_order_instruction(credit_intent, live_ask=1.20, policy=self.policy)
        self.assertEqual(instr_credit.order_class, "mleg")
        self.assertEqual(instr_credit.limit_price, -1.20)

        # With 2% buffer -> -(1.20 * 0.98) = -1.18
        buffered_policy = ExecutionPolicy(limit_price_buffer_pct=0.02)
        instr_credit_buffered = build_order_instruction(credit_intent, live_ask=1.20, policy=buffered_policy)
        self.assertEqual(instr_credit_buffered.limit_price, -1.18)

        # Debit spread entry -> MUST produce positive limit_price in Alpaca MLEG
        debit_intent = ExecutionIntent(
            intent_id="intent-debit",
            strategy_run_id="run-1",
            stock_symbol="QQQ",
            option_symbol="QQQ_260902_BCD_C480/C485",
            direction="BULLISH",
            order_intent=OrderIntent.MLEG_OPEN,
            contracts=5,
            authorized_max_loss=900.0,
            reference_entry_price=1.80,
            created_at=datetime.now(timezone.utc),
            expiration=date(2026, 9, 2),
            option_type="spread",
            strike=480.0,
            trade_score=78.0,
            stock_llm_rank=1,
            option_llm_rank=1,
            is_mleg=True,
            is_credit=False,
            spread_type="debit_bull_call",
            long_symbol="QQQ260902C00480000",
            short_symbol="QQQ260902C00485000",
            net_limit_price=1.80,
            legs=[
                {"symbol": "QQQ260902C00480000", "side": "buy", "ratio_qty": 1, "position_intent": "buy_to_open"},
                {"symbol": "QQQ260902C00485000", "side": "sell", "ratio_qty": 1, "position_intent": "sell_to_open"},
            ],
        )

        instr_debit = build_order_instruction(debit_intent, live_ask=1.80, policy=self.policy)
        self.assertEqual(instr_debit.order_class, "mleg")
        self.assertEqual(instr_debit.limit_price, 1.80)

        instr_debit_buffered = build_order_instruction(debit_intent, live_ask=1.80, policy=buffered_policy)
        self.assertEqual(instr_debit_buffered.limit_price, 1.84)

    def test_trade_candidate_from_spread_candidate(self):
        long_leg = OptionCandidate(
            symbol="SPY260902C00560000",
            expiration=date(2026, 9, 2),
            option_type="call",
            strike=560.0,
            bid=2.50,
            ask=2.55,
            mid=2.525,
            spread_pct=0.02,
            iv=0.15,
            delta=0.50,
            gamma=0.03,
            theta=-0.10,
            vega=0.20,
            dte=1,
            moneyness_pct=0.0,
            score=70.0,
        )
        short_leg = OptionCandidate(
            symbol="SPY260902C00565000",
            expiration=date(2026, 9, 2),
            option_type="call",
            strike=565.0,
            bid=0.90,
            ask=0.95,
            mid=0.925,
            spread_pct=0.05,
            iv=0.14,
            delta=0.25,
            gamma=0.02,
            theta=-0.06,
            vega=0.12,
            dte=1,
            moneyness_pct=0.01,
            score=60.0,
        )

        spread = SpreadCandidate(
            symbol="SPY_260902_BCD_C560/C565",
            underlying_symbol="SPY",
            spread_type="debit_bull_call",
            direction="bullish",
            expiration=date(2026, 9, 2),
            dte=1,
            strike=560.0,
            bid=1.55,
            ask=1.65,
            mid=1.60,
            spread_pct=0.06,
            iv=0.15,
            delta=0.25,
            gamma=0.01,
            theta=-0.04,
            vega=0.08,
            score=75.0,
            is_credit=False,
            is_mleg=True,
            long_leg=long_leg,
            short_leg=short_leg,
            long_strike=560.0,
            short_strike=565.0,
            strike_width=5.0,
            net_credit=0.0,
            net_debit=1.65,
            max_loss=1.65,
            max_profit=3.35,
            reward_to_risk=2.03,
            probability_of_profit=0.65,
            underlying_price=560.0,
            option_type="spread",
        )

        stock = MagicMock()
        stock.symbol = "SPY"
        stock.score = 80.0

        tc = TradeCandidate.from_ranked_option(
            option=spread,
            stock=stock,
            direction="BULLISH",
            stock_llm_rank=1,
            option_llm_rank=1,
        )

        self.assertTrue(tc.is_mleg)
        self.assertFalse(tc.is_credit)
        self.assertEqual(tc.long_symbol, "SPY260902C00560000")
        self.assertEqual(tc.short_symbol, "SPY260902C00565000")
        self.assertEqual(len(tc.legs), 2)
        self.assertEqual(tc.legs[0]["side"], "buy")
        self.assertEqual(tc.legs[1]["side"], "sell")

    def test_spread_state_reconciliation(self):
        state = {
            "SPY_260902_BCD_C560/C565": PositionState(
                option_symbol="SPY_260902_BCD_C560/C565",
                stock_symbol="SPY",
                direction="BULLISH",
                opened_at="2026-09-01T10:00:00",
                entry_price=1.60,
                contracts=5,
                is_spread=True,
                long_symbol="SPY260902C00560000",
                short_symbol="SPY260902C00565000",
            ),
            "NVDA260902C00125000": PositionState(
                option_symbol="NVDA260902C00125000",
                stock_symbol="NVDA",
                direction="BULLISH",
                opened_at="2026-09-01T10:00:00",
                entry_price=3.50,
                contracts=2,
                is_spread=False,
            ),
        }

        # If SPY long leg is open on broker, SPY spread stays active
        pruned = reconcile(state, ["SPY260902C00560000"])
        self.assertIn("NVDA260902C00125000", pruned)
        self.assertIn("SPY_260902_BCD_C560/C565", state)

        # If neither leg is open, SPY spread gets pruned
        pruned2 = reconcile(state, ["AAPL260902C00230000"])
        self.assertIn("SPY_260902_BCD_C560/C565", pruned2)
        self.assertEqual(len(state), 0)


if __name__ == "__main__":
    unittest.main()
