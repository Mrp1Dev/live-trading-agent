from datetime import date

# ============================================================
# SCANNER / RESEARCH / LLM FUNNEL
# ============================================================

STOCK_SCANNER_TOP_N = 25
LLM_STOCK_TOP_K = 10
MAX_OPTIONS_PER_STOCK = 4
OPTION_LLM_TOP_K = 12

# ============================================================
# PORTFOLIO / RISK
# ============================================================

MAX_TOTAL_RISK_PCT = 0.20
MAX_SINGLE_TRADE_RISK_PCT = 0.025
MAX_UNDERLYING_RISK_PCT = 0.03
MAX_DIRECTION_RISK_PCT = 0.15

MAX_POSITIONS = 8
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

# Avoid contracts that expire on or before the official valuation.
#
# Submissions close 4 Sep 2026, 8:30 PM IST, which is ~11:00 AM ET on the 4th.
# A contract expiring ON the 4th is a near-zero-DTE coin flip at the exact moment
# judges snapshot the account, so the 4th is forbidden too - not just the 3rd.
LATEST_FORBIDDEN_EXPIRATION = date(2026, 9, 4)

# ============================================================
# EXITS
#
# Breakeven win rate = |stop| / (avg_win + |stop|). A flat +60% target against a
# -55% stop needs a 47.8% win rate, which short-dated directional options do not
# achieve. So the fixed target sits far out and rarely fires; the TRAILING stop
# is the real profit taker.
# ============================================================

TAKE_PROFIT_PCT = 1.20        # +120%: deliberately far out
STOP_LOSS_PCT = -0.55         # -55%
TRAIL_ARM_PCT = 0.30          # trailing engages once the position has run +30%
TRAIL_GIVEBACK_PCT = 0.35     # then give back at most 35% of the peak
MAX_HOLD_DAYS = 2.5           # time stop; tracks PLANNED_HOLD_DAYS in the selector
MIN_EXIT_DTE = 1              # force out at 1 DTE rather than hold into expiry

# Hard flatten before the valuation snapshot.
FLATTEN_DATE = date(2026, 9, 4)
FLATTEN_AFTER_HOUR_ET = 9
FLATTEN_AFTER_MINUTE_ET = 45

STATE_FILE = "state/positions.json"

# ============================================================
# EXECUTION WINDOWS & CADENCE
# ============================================================

# Spreads are widest at the open and into the close, which is exactly when a
# marketable limit costs the most. Do not open new risk in those windows.
NO_TRADE_MINUTES_AFTER_OPEN = 10
NO_TRADE_MINUTES_BEFORE_CLOSE = 10

# A resting entry limit that has not filled is stale information; cancel it
# rather than let it fill against a quote we would no longer accept.
ENTRY_ORDER_TIMEOUT_SECONDS = 180

# Loop Cadence
EXIT_INTERVAL_SECONDS = 300           # 5 minutes (marks and manages open positions)
ENTRY_INTERVAL_MINUTES = 30           # 30 minutes (was 120 min)
