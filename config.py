from datetime import date

# ============================================================
# SCANNER / RESEARCH / LLM FUNNEL
# ============================================================

STOCK_SCANNER_TOP_N = 12
LLM_STOCK_TOP_K = 8
MAX_OPTIONS_PER_STOCK = 6
OPTION_LLM_TOP_K = 10

# ============================================================
# PORTFOLIO / RISK
# ============================================================

MAX_TOTAL_RISK_PCT = 0.35            # 35% total portfolio risk cap
MAX_SINGLE_TRADE_RISK_PCT = 0.035     # 3.5% risk per trade (allows active sizing)
MAX_UNDERLYING_RISK_PCT = 0.05       # 5% max risk on one ticker
MAX_DIRECTION_RISK_PCT = 0.25        # 25% max in one direction

MAX_POSITIONS = 8
MIN_TRADE_SCORE = 60.0

# Greeks monitoring limits
MAX_ABS_PORTFOLIO_DELTA = 2500.0
MAX_ABS_PORTFOLIO_GAMMA = 250.0
MAX_ABS_PORTFOLIO_VEGA = 4000.0

EMERGENCY_DRAWDOWN_PCT = 0.15

# ============================================================
# OPTIONS & SPREADS (0 - 2 DTE)
# ============================================================

MIN_DTE = 0                           # 0-DTE allowed (SPY, QQQ, IWM)
MAX_DTE = 2                           # 0-2 DTE for high gamma / rapid theta
MAX_OPTION_SPREAD_PCT = 0.035         # 3.5% max spread on legs (ultra-liquid only)
MIN_OPTION_ABS_DELTA = 0.10
MAX_OPTION_ABS_DELTA = 0.90
MIN_OPTION_PREMIUM = 0.15

# Latest date forbidden for multi-day holds (final hackathon snapshot)
LATEST_FORBIDDEN_EXPIRATION = date(2026, 9, 4)

# ============================================================
# INTRADAY EXITS (15 - 45 MIN TURNOVER)
# ============================================================

CREDIT_SPREAD_TAKE_PROFIT_PCT = 0.50  # +50% of credit captured
DEBIT_SPREAD_TAKE_PROFIT_PCT = 0.35   # +35% on debit spread
LONG_TAKE_PROFIT_PCT = 0.40           # +40% on single-leg long
TAKE_PROFIT_PCT = LONG_TAKE_PROFIT_PCT
STOP_LOSS_PCT = -0.20                 # -20% hard cut
TRAIL_ARM_PCT = 0.18                  # arms when position reaches +18%
TRAIL_GIVEBACK_PCT = 0.20             # give back at most 20% from peak

MAX_HOLD_MINUTES = 40.0               # time stop in minutes (kills stagnant trades)
MAX_HOLD_DAYS = MAX_HOLD_MINUTES / (6.5 * 60.0) # fractional trading day equivalent (~0.1d)
MIN_EXIT_DTE = -1                     # 0-DTE allowed; daily EOD at 15:50 ET flattens before close

# Daily EOD hard flatten (close all 0-DTE / intraday positions before market close)
DAILY_FLATTEN_HOUR_ET = 15
DAILY_FLATTEN_MINUTE_ET = 50

# Pre-valuation final flatten deadline
FLATTEN_DATE = date(2026, 9, 4)
FLATTEN_AFTER_HOUR_ET = 9
FLATTEN_AFTER_MINUTE_ET = 45

STATE_FILE = "state/positions.json"

# ============================================================
# EXECUTION WINDOWS & CADENCE
# ============================================================

NO_TRADE_MINUTES_AFTER_OPEN = 5
NO_TRADE_MINUTES_BEFORE_CLOSE = 12

ENTRY_ORDER_TIMEOUT_SECONDS = 60

# Fast loop cadence for high-turnover trading
EXIT_INTERVAL_SECONDS = 20            # 20 seconds (checks marks & evaluates fast exits)
ENTRY_INTERVAL_MINUTES = 3            # 3 minutes (scans for new setups when slots open)

