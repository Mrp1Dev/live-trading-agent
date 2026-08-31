from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from alpaca.common.exceptions import APIError
from alpaca.trading.enums import OrderSide, OrderStatus, PositionIntent, TimeInForce
from alpaca.trading.models import OptionContract, TradeAccount

from execution.alpaca_broker import AlpacaBroker
from execution.errors import BrokerError, SafetyViolation
from execution.executor import execute_intents
from execution.journal import ExecutionJournal
from execution.models import (
    ExecutionIntent,
    ExecutionReport,
    ExecutionResult,
    ExecutionStatus,
    LiveOptionQuote,
    OrderInstruction,
    OrderIntent,
)
from execution.planner import build_execution_intents, build_order_instruction
from execution.policy import ExecutionPolicy
from execution.validator import validate_intent


class TestExecutionLayer(unittest.TestCase):
    def setUp(self):
        self.now = datetime.now(timezone.utc)
        self.intent = ExecutionIntent(
            intent_id="intent-12345",
            strategy_run_id="20260831T200000Z",
            stock_symbol="CRM",
            option_symbol="CRM260904C00255000",
            direction="BULLISH",
            order_intent=OrderIntent.BUY_TO_OPEN,
            contracts=2,
            authorized_max_loss=2000.0,
            reference_entry_price=7.50,
            created_at=self.now,
            expiration=date(2026, 9, 4),
            option_type="call",
            strike=255.0,
            trade_score=95.0,
            stock_llm_rank=1,
            option_llm_rank=1,
        )
        self.policy = ExecutionPolicy(
            max_intent_age=timedelta(seconds=90),
            max_reference_price_move_pct=0.08,
            max_spread_pct=0.10,
            cheap_contract_absolute_spread=0.05,
            limit_price_buffer_pct=0.0,
            time_in_force="day",
            order_type="limit",
        )

    def test_paper_guard_safety_violation(self):
        """Broker must reject live trading configuration."""
        mock_tc = MagicMock()
        mock_tc._base_url = "https://api.alpaca.markets"
        mock_tc._paper = False
        with patch.dict("os.environ", {"ALPACA_PAPER_TRADE": "false"}):
            with self.assertRaises(SafetyViolation):
                AlpacaBroker(trading_client=mock_tc, paper=False)

    def test_paper_guard_accepts_paper(self):
        """Broker initializes cleanly when paper mode is active."""
        mock_tc = MagicMock()
        mock_tc._base_url = "https://paper-api.alpaca.markets"
        mock_tc._paper = True
        mock_odc = MagicMock()
        with patch.dict("os.environ", {"ALPACA_PAPER_TRADE": "true"}):
            broker = AlpacaBroker(trading_client=mock_tc, option_data_client=mock_odc, paper=True)
            self.assertIsNotNone(broker)

    def test_quote_and_contract_validation_success(self):
        """Valid quote and tradable contract pass validation."""
        quote = LiveOptionQuote(
            symbol="CRM260904C00255000",
            bid=7.40,
            ask=7.60,
            timestamp=self.now,
        )
        contract = {
            "symbol": "CRM260904C00255000",
            "tradable": True,
            "expiration_date": "2026-09-04",
        }
        res = validate_intent(
            self.intent,
            live_quote_raw=quote,
            contract_raw=contract,
            policy=self.policy,
            now=self.now,
        )
        self.assertTrue(res.approved)
        self.assertEqual(res.reasons, ())

    def test_validation_rejects_stale_intent(self):
        """Intent older than max_intent_age must be rejected."""
        stale_intent = ExecutionIntent(
            intent_id="intent-old",
            strategy_run_id="20260831T200000Z",
            stock_symbol="CRM",
            option_symbol="CRM260904C00255000",
            direction="BULLISH",
            order_intent=OrderIntent.BUY_TO_OPEN,
            contracts=2,
            authorized_max_loss=2000.0,
            reference_entry_price=7.50,
            created_at=self.now - timedelta(seconds=120),
            expiration=date(2026, 9, 4),
            option_type="call",
            strike=255.0,
            trade_score=95.0,
            stock_llm_rank=1,
            option_llm_rank=1,
        )
        quote = LiveOptionQuote(symbol="CRM260904C00255000", bid=7.40, ask=7.60)
        res = validate_intent(
            stale_intent,
            live_quote_raw=quote,
            contract_raw={"tradable": True},
            policy=self.policy,
            now=self.now,
        )
        self.assertFalse(res.approved)
        self.assertTrue(any("stale" in r for r in res.reasons))

    def test_validation_rejects_wide_spread(self):
        """Quotes with excessive spread percentage must be rejected."""
        quote = LiveOptionQuote(symbol="CRM260904C00255000", bid=5.00, ask=8.00)
        res = validate_intent(
            self.intent,
            live_quote_raw=quote,
            contract_raw={"tradable": True},
            policy=self.policy,
            now=self.now,
        )
        self.assertFalse(res.approved)
        self.assertTrue(any("spread" in r for r in res.reasons))

    def test_build_order_instruction(self):
        """Order instruction is built with correct limit price and client_order_id."""
        inst = build_order_instruction(self.intent, live_ask=7.55, policy=self.policy)
        self.assertEqual(inst.option_symbol, "CRM260904C00255000")
        self.assertEqual(inst.qty, 2)
        self.assertEqual(inst.side, "buy")
        self.assertEqual(inst.position_intent, "buy_to_open")
        self.assertEqual(inst.limit_price, 7.55)
        self.assertTrue(inst.client_order_id.startswith("oa-20260831T200000Z-intent-12345"))

    def test_execute_intents_dry_run(self):
        """Dry-run mode records DRY_RUN status and submits 0 orders."""
        journal = MagicMock()
        mock_broker = MagicMock()
        report = execute_intents(
            [self.intent],
            broker=mock_broker,
            policy=self.policy,
            journal=journal,
            dry_run=True,
            now=self.now,
        )
        self.assertTrue(report.dry_run)
        self.assertEqual(len(report.results), 1)
        self.assertEqual(report.results[0].status, ExecutionStatus.DRY_RUN)
        self.assertEqual(report.results[0].submitted_qty, 0)
        mock_broker.place_option_order.assert_not_called()

    def test_execute_intents_submitted_successfully(self):
        """Live paper execution places limit order and returns SUBMITTED result."""
        mock_broker = MagicMock()
        mock_broker.get_positions.return_value = []
        mock_broker.get_open_orders.return_value = []
        mock_broker.get_order_by_client_id.return_value = None
        mock_broker.get_option_quote.return_value = LiveOptionQuote(
            symbol="CRM260904C00255000",
            bid=7.45,
            ask=7.55,
            timestamp=self.now,
        )
        mock_broker.get_option_contract.return_value = {
            "symbol": "CRM260904C00255000",
            "tradable": True,
            "expiration_date": "2026-09-04",
        }
        mock_order = MagicMock()
        mock_order.id = "ord-abc-123"
        mock_order.status = "accepted"
        mock_order.filled_qty = 0
        mock_order.filled_avg_price = None
        mock_broker.place_option_order.return_value = mock_order

        journal = MagicMock()
        report = execute_intents(
            [self.intent],
            broker=mock_broker,
            policy=self.policy,
            journal=journal,
            dry_run=False,
            now=self.now,
        )
        self.assertFalse(report.dry_run)
        self.assertEqual(len(report.results), 1)
        res = report.results[0]
        self.assertTrue(res.approved)
        self.assertEqual(res.status, ExecutionStatus.SUBMITTED)
        self.assertEqual(res.order_id, "ord-abc-123")
        self.assertEqual(res.submitted_qty, 2)
        mock_broker.place_option_order.assert_called_once()

    def test_execute_intents_idempotent_existing_order(self):
        """If an order with client_order_id exists, it is not resubmitted."""
        mock_broker = MagicMock()
        mock_broker.get_positions.return_value = []
        mock_broker.get_open_orders.return_value = []
        existing_order = MagicMock()
        existing_order.id = "existing-ord-999"
        existing_order.status = "new"
        existing_order.filled_qty = 0
        existing_order.filled_avg_price = None
        mock_broker.get_order_by_client_id.return_value = existing_order

        journal = MagicMock()
        report = execute_intents(
            [self.intent],
            broker=mock_broker,
            policy=self.policy,
            journal=journal,
            dry_run=False,
            now=self.now,
        )
        self.assertEqual(len(report.results), 1)
        res = report.results[0]
        self.assertEqual(res.order_id, "existing-ord-999")
        self.assertIn("Existing Alpaca order found", res.reason)
        mock_broker.place_option_order.assert_not_called()

    def test_alpaca_broker_get_order_by_client_id_404_returns_none(self):
        """404 on get_order_by_client_id returns None instead of raising."""
        mock_tc = MagicMock()
        mock_tc._base_url = "https://paper-api.alpaca.markets"
        mock_tc._paper = True
        mock_odc = MagicMock()

        err_404 = APIError('{"message": "order not found", "code": 40410000}')
        mock_tc.get_order_by_client_id.side_effect = err_404

        with patch.dict("os.environ", {"ALPACA_PAPER_TRADE": "true"}):
            broker = AlpacaBroker(trading_client=mock_tc, option_data_client=mock_odc, paper=True)
            result = broker.get_order_by_client_id("non-existent-order")
            self.assertIsNone(result)

    def test_alpaca_broker_place_option_order_limit_request(self):
        """AlpacaBroker constructs and submits LimitOrderRequest properly."""
        mock_tc = MagicMock()
        mock_tc._base_url = "https://paper-api.alpaca.markets"
        mock_tc._paper = True
        mock_odc = MagicMock()

        submitted_order = MagicMock()
        submitted_order.id = "ord-uuid-1"
        mock_tc.submit_order.return_value = submitted_order

        with patch.dict("os.environ", {"ALPACA_PAPER_TRADE": "true"}):
            broker = AlpacaBroker(trading_client=mock_tc, option_data_client=mock_odc, paper=True)
            res = broker.place_option_order(
                symbol="CRM260904C00255000",
                qty=3,
                side="buy",
                position_intent="buy_to_open",
                order_type="limit",
                time_in_force="day",
                limit_price=7.27,
                client_order_id="oa-run-intent-1",
            )
            self.assertEqual(res, submitted_order)
            mock_tc.submit_order.assert_called_once()
            called_req = mock_tc.submit_order.call_args[0][0]
            self.assertEqual(called_req.symbol, "CRM260904C00255000")
            self.assertEqual(called_req.qty, 3.0)
            self.assertEqual(called_req.side, OrderSide.BUY)
            self.assertEqual(called_req.time_in_force, TimeInForce.DAY)
            self.assertEqual(called_req.position_intent, PositionIntent.BUY_TO_OPEN)
            self.assertEqual(called_req.limit_price, 7.27)
            self.assertEqual(called_req.client_order_id, "oa-run-intent-1")

    def test_alpaca_broker_get_option_quote(self):
        """AlpacaBroker fetches and returns LiveOptionQuote."""
        mock_tc = MagicMock()
        mock_tc._base_url = "https://paper-api.alpaca.markets"
        mock_tc._paper = True
        mock_odc = MagicMock()

        raw_quote = MagicMock()
        raw_quote.bid_price = 7.10
        raw_quote.ask_price = 7.30
        raw_quote.timestamp = self.now
        mock_odc.get_option_latest_quote.return_value = {"CRM260904C00255000": raw_quote}

        with patch.dict("os.environ", {"ALPACA_PAPER_TRADE": "true"}):
            broker = AlpacaBroker(trading_client=mock_tc, option_data_client=mock_odc, paper=True)
            quote = broker.get_option_quote("CRM260904C00255000")
            self.assertEqual(quote.symbol, "CRM260904C00255000")
            self.assertEqual(quote.bid, 7.10)
            self.assertEqual(quote.ask, 7.30)
            self.assertEqual(quote.source, "alpaca-api")


if __name__ == "__main__":
    unittest.main()
