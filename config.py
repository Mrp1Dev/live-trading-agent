from datetime import date

# ============================================================
# SCANNER / RESEARCH / LLM FUNNEL
# ============================================================

STOCK_SCANNER_TOP_N = 20
LLM_STOCK_TOP_K = 8
MAX_OPTIONS_PER_STOCK = 6
OPTION_LLM_TOP_K = 8

# ============================================================
# PORTFOLIO / RISK
# ============================================================

MAX_TOTAL_RISK_PCT = 0.12
MAX_SINGLE_TRADE_RISK_PCT = 0.025
MAX_UNDERLYING_RISK_PCT = 0.03
MAX_DIRECTION_RISK_PCT = 0.075

MAX_POSITIONS = 5
MIN_TRADE_SCORE = 70.0

# Greeks are warnings for now, not hard portfolio vetoes.
MAX_ABS_PORTFOLIO_DELTA = 1500.0
MAX_ABS_PORTFOLIO_GAMMA = 100.0
MAX_ABS_PORTFOLIO_VEGA = 3000.0

EMERGENCY_DRAWDOWN_PCT = 0.15

# ============================================================
# OPTIONS
# ============================================================

MIN_DTE = 1
MAX_DTE = 14
MAX_OPTION_SPREAD_PCT = 0.15
MIN_OPTION_ABS_DELTA = 0.15
MAX_OPTION_ABS_DELTA = 0.90
MIN_OPTION_PREMIUM = 0.25

# For this hackathon, avoid contracts that expire on or before
# the last official portfolio valuation date.
#
# Official EOD valuation is September 3, 2026.
LATEST_FORBIDDEN_EXPIRATION = date(2026, 9, 3)
