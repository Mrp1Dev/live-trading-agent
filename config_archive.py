"""Archive of Configuration Values and Baseline Settings.

This file documents the original baseline settings alongside the expanded settings.
You can use this file as a reference or copy values back to config.py at any time.
"""

from datetime import date

# ==============================================================================
# ORIGINAL BASELINE CONFIGURATION (PRE-EXPANSION)
# ==============================================================================

ORIGINAL_CONFIG = {
    # Scanner / Funnel
    "STOCK_SCANNER_TOP_N": 20,
    "LLM_STOCK_TOP_K": 8,
    "MAX_OPTIONS_PER_STOCK": 6,
    "OPTION_LLM_TOP_K": 8,

    # Portfolio / Risk
    "MAX_TOTAL_RISK_PCT": 0.12,          # 12% total portfolio max loss
    "MAX_SINGLE_TRADE_RISK_PCT": 0.025,  # 2.5% max risk per trade
    "MAX_UNDERLYING_RISK_PCT": 0.03,     # 3.0% max risk per underlying
    "MAX_DIRECTION_RISK_PCT": 0.075,     # 7.5% max directional risk (capped at ~3 trades)
    "MAX_POSITIONS": 5,                  # 5 position slots
    "MIN_TRADE_SCORE": 70.0,

    # Greeks Advisory Thresholds
    "MAX_ABS_PORTFOLIO_DELTA": 1500.0,
    "MAX_ABS_PORTFOLIO_GAMMA": 100.0,
    "MAX_ABS_PORTFOLIO_VEGA": 3000.0,
    "EMERGENCY_DRAWDOWN_PCT": 0.15,

    # Options Filtering
    "MIN_DTE": 1,
    "MAX_DTE": 14,
    "MAX_OPTION_SPREAD_PCT": 0.15,
    "MIN_OPTION_ABS_DELTA": 0.15,
    "MAX_OPTION_ABS_DELTA": 0.90,
    "MIN_OPTION_PREMIUM": 0.25,
    "LATEST_FORBIDDEN_EXPIRATION": date(2026, 9, 4),

    # Exit Rules
    "TAKE_PROFIT_PCT": 1.20,             # +120%
    "STOP_LOSS_PCT": -0.55,              # -55%
    "TRAIL_ARM_PCT": 0.30,               # Arm trailing stop at +30%
    "TRAIL_GIVEBACK_PCT": 0.35,          # Give back max 35% of peak
    "MAX_HOLD_DAYS": 2.5,                # Time stop
    "MIN_EXIT_DTE": 1,                   # Exit at <= 1 DTE
    "FLATTEN_DATE": date(2026, 9, 4),
    "FLATTEN_AFTER_HOUR_ET": 9,
    "FLATTEN_AFTER_MINUTE_ET": 45,

    # Execution Windows & Cadence
    "NO_TRADE_MINUTES_AFTER_OPEN": 10,
    "NO_TRADE_MINUTES_BEFORE_CLOSE": 10,
    "ENTRY_ORDER_TIMEOUT_SECONDS": 180,
    "EXIT_INTERVAL_SECONDS": 300,        # 5 minutes
    "ENTRY_INTERVAL_MINUTES": 120,       # 120 minutes (original)
}


# ==============================================================================
# CURRENT EXPANDED CONFIGURATION (ACTIVE IN CONFIG.PY)
# ==============================================================================

EXPANDED_CONFIG = {
    # Scanner / Funnel (Expanded for higher multi-stock diversity)
    "STOCK_SCANNER_TOP_N": 25,           # (was 20)
    "LLM_STOCK_TOP_K": 10,               # (was 8)
    "MAX_OPTIONS_PER_STOCK": 4,          # (was 6 - limited per stock so pool covers more distinct underlyings)
    "OPTION_LLM_TOP_K": 12,              # (was 8)

    # Portfolio / Risk (Expanded to allow 5-8 positions)
    "MAX_TOTAL_RISK_PCT": 0.20,          # (was 0.12 - allows up to 20% risk deployment)
    "MAX_SINGLE_TRADE_RISK_PCT": 0.025,  # 2.5% max risk per trade
    "MAX_UNDERLYING_RISK_PCT": 0.03,     # 3.0% max risk per underlying
    "MAX_DIRECTION_RISK_PCT": 0.15,      # (was 0.075 - allows 6-8 positions in same direction)
    "MAX_POSITIONS": 8,                  # (was 5 - allows up to 8 concurrent positions)
    "MIN_TRADE_SCORE": 70.0,

    # Greeks Advisory Thresholds
    "MAX_ABS_PORTFOLIO_DELTA": 1500.0,
    "MAX_ABS_PORTFOLIO_GAMMA": 100.0,
    "MAX_ABS_PORTFOLIO_VEGA": 3000.0,
    "EMERGENCY_DRAWDOWN_PCT": 0.15,

    # Options Filtering
    "MIN_DTE": 1,
    "MAX_DTE": 14,
    "MAX_OPTION_SPREAD_PCT": 0.15,
    "MIN_OPTION_ABS_DELTA": 0.15,
    "MAX_OPTION_ABS_DELTA": 0.90,
    "MIN_OPTION_PREMIUM": 0.25,
    "LATEST_FORBIDDEN_EXPIRATION": date(2026, 9, 4),

    # Exit Rules
    "TAKE_PROFIT_PCT": 1.20,             # +120%
    "STOP_LOSS_PCT": -0.55,              # -55%
    "TRAIL_ARM_PCT": 0.30,               # Arm trailing stop at +30%
    "TRAIL_GIVEBACK_PCT": 0.35,          # Give back max 35% of peak
    "MAX_HOLD_DAYS": 2.5,                # Time stop
    "MIN_EXIT_DTE": 1,                   # Exit at <= 1 DTE
    "FLATTEN_DATE": date(2026, 9, 4),
    "FLATTEN_AFTER_HOUR_ET": 9,
    "FLATTEN_AFTER_MINUTE_ET": 45,

    # Execution Windows & Cadence
    "NO_TRADE_MINUTES_AFTER_OPEN": 10,
    "NO_TRADE_MINUTES_BEFORE_CLOSE": 10,
    "ENTRY_ORDER_TIMEOUT_SECONDS": 180,
    "EXIT_INTERVAL_SECONDS": 300,        # 5 minutes
    "ENTRY_INTERVAL_MINUTES": 15,        # 30 minutes (was 120 min)
}
