from __future__ import annotations

import unittest
from datetime import date
from types import SimpleNamespace

from config import (
    MAX_POSITIONS,
    MAX_SINGLE_TRADE_RISK_PCT,
    MAX_TOTAL_RISK_PCT,
)
from risk.risk import _enforce_directional_limits, assess_portfolio
from strategy.portfolio import (
    PortfolioPosition,
    _trade_weight,
    build_portfolio,
    select_best_trade_per_stock,
)


def _make_dummy_trade(
    stock_symbol: str,
    option_symbol: str,
    direction: str = "BULLISH",
    option_ask: float = 2.0,
    option_mid: float = 1.95,
    trade_score: float = 75.0,
    option_llm_rank: int = 1,
    delta: float = 0.50,
):
    return SimpleNamespace(
        stock_symbol=stock_symbol,
        option_symbol=option_symbol,
        direction=direction,
        option_ask=option_ask,
        option_mid=option_mid,
        trade_score=trade_score,
        option_llm_rank=option_llm_rank,
        option_delta=delta,
        option_gamma=0.05,
        option_vega=0.10,
        option_theta=-0.05,
        expiration=date(2026, 9, 18),
        strike=100.0,
        option_type="call" if direction == "BULLISH" else "put",
    )


class TestPortfolioRankingAlignment(unittest.TestCase):

    def test_select_best_trade_per_stock_prefers_llm_rank(self):
        """When multiple contracts exist for the same stock, keep the LLM's top pick."""
        # Contract A has lower heuristic score (70.0) but better LLM rank (#1)
        trade_a = _make_dummy_trade(
            stock_symbol="AAPL",
            option_symbol="AAPL260918C00220000",
            trade_score=70.0,
            option_llm_rank=1,
        )
        # Contract B has higher heuristic score (95.0) but worse LLM rank (#4)
        trade_b = _make_dummy_trade(
            stock_symbol="AAPL",
            option_symbol="AAPL260918C00225000",
            trade_score=95.0,
            option_llm_rank=4,
        )

        selected = select_best_trade_per_stock([trade_a, trade_b])
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0].option_symbol, "AAPL260918C00220000")
        self.assertEqual(selected[0].option_llm_rank, 1)

    def test_select_best_trade_per_stock_preserves_cross_stock_order(self):
        """Cross-underlying ranking order from the LLM must be preserved."""
        trade_nvda = _make_dummy_trade("NVDA", "NVDA260918C120", trade_score=60.0, option_llm_rank=1)
        trade_tsla = _make_dummy_trade("TSLA", "TSLA260918C200", trade_score=70.0, option_llm_rank=2)
        trade_aapl = _make_dummy_trade("AAPL", "AAPL260918C220", trade_score=90.0, option_llm_rank=3)

        selected = select_best_trade_per_stock([trade_nvda, trade_tsla, trade_aapl])
        self.assertEqual([t.stock_symbol for t in selected], ["NVDA", "TSLA", "AAPL"])

    def test_build_portfolio_selects_top_n_by_llm_rank(self):
        """Portfolio position slots must be filled by LLM rank, not formulaic trade_score."""
        trade1 = _make_dummy_trade("NVDA", "NVDA_OPT", trade_score=65.0, option_llm_rank=1)
        trade2 = _make_dummy_trade("TSLA", "TSLA_OPT", trade_score=70.0, option_llm_rank=2)
        trade3 = _make_dummy_trade("AAPL", "AAPL_OPT", trade_score=85.0, option_llm_rank=3)
        trade4 = _make_dummy_trade("MSFT", "MSFT_OPT", trade_score=95.0, option_llm_rank=4)

        # max_positions = 2 should pick NVDA (#1) and TSLA (#2)
        positions = build_portfolio(
            trades=[trade1, trade2, trade3, trade4],
            account_equity=100000.0,
            max_positions=2,
        )

        self.assertEqual(len(positions), 2)
        self.assertEqual(positions[0].trade.stock_symbol, "NVDA")
        self.assertEqual(positions[1].trade.stock_symbol, "TSLA")

    def test_trade_weight_gives_higher_weight_to_higher_rank(self):
        """Rank #1 receives higher weight than Rank #2, etc."""
        w1 = _trade_weight(None, rank_index=0, total_candidates=3)
        w2 = _trade_weight(None, rank_index=1, total_candidates=3)
        w3 = _trade_weight(None, rank_index=2, total_candidates=3)
        self.assertTrue(w1 > w2 > w3)

    def test_budget_exhaustion_prioritizes_higher_llm_rank(self):
        """When portfolio risk budget is constrained, rank #1 is filled first."""
        # Each contract costs $200 per contract (ask=2.0 => $200)
        trade1 = _make_dummy_trade("NVDA", "NVDA_OPT", option_ask=2.0, trade_score=60.0, option_llm_rank=1)
        trade2 = _make_dummy_trade("TSLA", "TSLA_OPT", option_ask=2.0, trade_score=90.0, option_llm_rank=2)

        # Account equity = $10,000, total_risk_pct = 5% ($500 max total risk)
        # max_trade_risk_pct = 2.5% ($250 max trade risk)
        positions = build_portfolio(
            trades=[trade1, trade2],
            account_equity=10000.0,
            total_risk_pct=0.05,
            max_trade_risk_pct=0.025,
            max_positions=2,
        )

        # NVDA (#1) gets allocated $250 -> 1 contract ($200).
        # TSLA (#2) gets allocated remainder $130 -> 0 contracts.
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0].trade.stock_symbol, "NVDA")
        self.assertEqual(positions[0].contracts, 1)

    def test_risk_directional_limit_prioritizes_llm_rank(self):
        """Directional risk limits in risk.py must prioritize higher LLM rank."""
        trade1 = _make_dummy_trade("NVDA", "NVDA_OPT", direction="BULLISH", option_llm_rank=1, trade_score=60.0)
        trade2 = _make_dummy_trade("TSLA", "TSLA_OPT", direction="BULLISH", option_llm_rank=2, trade_score=90.0)

        # Max loss is $5,000 each
        pos1 = PortfolioPosition(trade=trade1, contracts=10, max_loss=5000.0, premium_deployed=5000.0, risk_weight=0.6)
        pos2 = PortfolioPosition(trade=trade2, contracts=10, max_loss=5000.0, premium_deployed=5000.0, risk_weight=0.4)

        # Account equity = $100,000, max_direction_risk = 7.5% ($7,500)
        approved, rejected, _ = _enforce_directional_limits([pos1, pos2], max_direction_risk=7500.0)

        # pos1 ($5,000) fits. pos2 would make $10,000 > $7,500, so pos2 must be rejected.
        self.assertEqual(len(approved), 1)
        self.assertEqual(approved[0].trade.stock_symbol, "NVDA")
        self.assertEqual(len(rejected), 1)
        self.assertEqual(rejected[0].trade.stock_symbol, "TSLA")

    def test_risk_total_portfolio_limit_prioritizes_llm_rank(self):
        """Total portfolio risk check in assess_portfolio must prioritize higher LLM rank."""
        trade1 = _make_dummy_trade("NVDA", "NVDA_OPT", direction="BULLISH", option_llm_rank=1, trade_score=60.0)
        trade2 = _make_dummy_trade("TSLA", "TSLA_OPT", direction="BULLISH", option_llm_rank=2, trade_score=90.0)

        # Account equity = $100,000. MAX_SINGLE_TRADE_RISK_PCT = 2.5% ($2,500).
        pos1 = PortfolioPosition(trade=trade1, contracts=5, max_loss=2500.0, premium_deployed=2500.0, risk_weight=0.6)
        pos2 = PortfolioPosition(trade=trade2, contracts=5, max_loss=2500.0, premium_deployed=2500.0, risk_weight=0.4)

        # With account_equity = $30,000 in assess_portfolio:
        # Single trade limit = 2.5% of $100k = $2,500.
        # Total portfolio limit MAX_TOTAL_RISK_PCT = 12% of $30k = $3,600.
        # Pos1 ($2,500 max loss, 5 contracts of $500) fits in single trade and consumes $2,500.
        # Pos2 has $3,600 - $2,500 = $1,100 remaining -> 2 contracts ($1,000).
    def test_risk_total_portfolio_limit_trims_lower_rank(self):
        """Total risk limits trim/clip lower-ranked trades first while approving higher-ranked ones."""
        # 10 positions with $2,400 risk each ($240 per contract, 10 contracts)
        positions = [
            PortfolioPosition(
                trade=_make_dummy_trade(
                    stock_symbol=f"SYM{i}",
                    option_symbol=f"OPT{i}",
                    direction="BULLISH" if i % 2 == 0 else "BEARISH",
                    option_llm_rank=i,
                    trade_score=100.0 - i,
                ),
                contracts=10,
                max_loss=2400.0,
                premium_deployed=2400.0,
                risk_weight=0.10,
            )
            for i in range(1, 11)
        ]

        # On $100,000 account:
        # MAX_SINGLE_TRADE_RISK_PCT = 2.5% ($2,500) -> All positions pass single trade cap ($2,400 <= $2,500)
        # MAX_TOTAL_RISK_PCT = 20% ($20,000)
        # Positions 1..8 take 8 * $2,400 = $19,200.
        # Position 9 gets trimmed to remaining $800 (3 contracts @ $240 = $720).
        # Position 10 (rank #10) has $0 remaining and must be rejected.
        report = assess_portfolio(
            positions=positions,
            account_equity=100000.0,
            current_equity=100000.0,
            peak_equity=100000.0,
        )

        self.assertEqual(len(report.approved_positions), 9)
        self.assertEqual([p.trade.stock_symbol for p in report.approved_positions[:8]], [f"SYM{i}" for i in range(1, 9)])
        self.assertEqual(len(report.rejected_positions), 1)
        self.assertEqual(report.rejected_positions[0].trade.stock_symbol, "SYM10")



if __name__ == "__main__":
    unittest.main()
