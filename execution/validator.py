from __future__ import annotations

from datetime import datetime, timezone

from .models import ExecutionIntent, LiveOptionQuote, ValidationResult
from .policy import ExecutionPolicy


def _quote_from_any(symbol: str, raw) -> LiveOptionQuote:
    if isinstance(raw, LiveOptionQuote):
        return raw
    if hasattr(raw, "bid_price") and hasattr(raw, "ask_price"):
        bid = getattr(raw, "bid_price", 0.0) or 0.0
        ask = getattr(raw, "ask_price", 0.0) or 0.0
        ts = getattr(raw, "timestamp", None)
        sym = getattr(raw, "symbol", symbol) or symbol
        return LiveOptionQuote(
            symbol=str(sym),
            bid=float(bid),
            ask=float(ask),
            timestamp=ts if isinstance(ts, datetime) else None,
            source="alpaca-api",
        )
    if isinstance(raw, dict):
        data = raw
        direct_bid = data.get("bid_price", data.get("bid", data.get("b")))
        direct_ask = data.get("ask_price", data.get("ask", data.get("a")))
        if direct_bid is None and direct_ask is None:
            nested = data.get(symbol)
            if isinstance(nested, dict):
                data = nested
            elif hasattr(nested, "bid_price"):
                return _quote_from_any(symbol, nested)
            elif isinstance(data.get("data"), dict):
                nested = data["data"].get(symbol)
                if isinstance(nested, dict):
                    data = nested
                elif hasattr(nested, "bid_price"):
                    return _quote_from_any(symbol, nested)
        bid = data.get("bid_price", data.get("bid", data.get("b", 0)))
        ask = data.get("ask_price", data.get("ask", data.get("a", 0)))
        ts = data.get("timestamp") or data.get("t") or data.get("updated_at")
        parsed_ts = None
        if ts:
            if isinstance(ts, datetime):
                parsed_ts = ts
            else:
                try:
                    parsed_ts = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                except ValueError:
                    parsed_ts = None
        return LiveOptionQuote(
            symbol=symbol,
            bid=float(bid or 0),
            ask=float(ask or 0),
            timestamp=parsed_ts,
            source="alpaca-api",
        )
    elif isinstance(raw, list) and raw:
        return _quote_from_any(symbol, raw[0])
    return LiveOptionQuote(symbol=symbol, bid=0.0, ask=0.0, source="alpaca-api")


def validate_intent(
    intent: ExecutionIntent,
    *,
    live_quote_raw,
    contract_raw,
    account_raw=None,
    positions: list | None = None,
    open_orders: list | None = None,
    now: datetime | None = None,
    policy: ExecutionPolicy,
) -> ValidationResult:
    now = now or datetime.now(timezone.utc)
    quote = _quote_from_any(intent.option_symbol, live_quote_raw)
    reasons: list[str] = []

    if intent.order_intent.value not in {"BUY_TO_OPEN", "SELL_TO_OPEN", "MLEG_OPEN"}:
        reasons.append("unsupported order intent")
    if intent.contracts <= 0:
        reasons.append("contracts must be positive")
    if intent.authorized_max_loss <= 0:
        reasons.append("authorized max loss must be positive")
    if intent.expiration is None:
        reasons.append("missing expiration")
    elif intent.expiration < now.date():
        reasons.append("option expiration is in the past")

    age = now - intent.created_at
    if age.total_seconds() < 0:
        reasons.append("execution intent timestamp is in the future")
    elif age > policy.max_intent_age:
        reasons.append(f"execution intent is stale ({age.total_seconds():.0f}s > {policy.max_intent_age.total_seconds():.0f}s)")

    # For multi-leg spreads, quote might be synthetic or from leg
    if not intent.is_mleg:
        if quote.symbol != intent.option_symbol:
            reasons.append("live quote symbol does not match execution intent")
        if quote.bid <= 0 or quote.ask <= 0:
            reasons.append("invalid live option quote")
        elif quote.ask < quote.bid:
            reasons.append("live option ask is below bid")
        else:
            absolute_spread = quote.ask - quote.bid
            if quote.spread_pct > policy.max_spread_pct and absolute_spread > policy.cheap_contract_absolute_spread + 1e-9:
                reasons.append(f"live spread {quote.spread_pct:.1%} exceeds {policy.max_spread_pct:.1%}")
            if intent.reference_entry_price > 0:
                move = (quote.ask - intent.reference_entry_price) / intent.reference_entry_price
                if move > policy.max_reference_price_move_pct:
                    reasons.append(f"live ask moved {move:.1%} above reference (limit: {policy.max_reference_price_move_pct:.0%})")
                if move < -policy.max_reference_price_drop_pct:
                    reasons.append(f"live ask dropped {abs(move):.1%} below reference (limit: {policy.max_reference_price_drop_pct:.0%})")
    else:
        # Spread validation
        if intent.is_credit:
            if quote.bid <= 0 and intent.reference_entry_price <= 0:
                reasons.append("invalid credit spread entry pricing")
        else:
            if quote.ask <= 0 and intent.reference_entry_price <= 0:
                reasons.append("invalid debit spread entry pricing")

    # Contract validation is deliberately defensive and tolerates SDK objects and raw dicts.
    if contract_raw is not None:
        tradable = getattr(contract_raw, "tradable", None) if hasattr(contract_raw, "tradable") else (contract_raw.get("tradable") if isinstance(contract_raw, dict) else None)
        if tradable is False:
            reasons.append("option contract is not tradable")
        c_symbol = getattr(contract_raw, "symbol", None) if hasattr(contract_raw, "symbol") else (contract_raw.get("symbol") or contract_raw.get("id") if isinstance(contract_raw, dict) else None)
        if c_symbol and str(c_symbol) != intent.option_symbol:
            reasons.append("option contract identity mismatch")
        expiration = getattr(contract_raw, "expiration_date", None) if hasattr(contract_raw, "expiration_date") else (contract_raw.get("expiration_date") or contract_raw.get("expiration") if isinstance(contract_raw, dict) else None)
        if expiration:
            exp_str = expiration.isoformat() if hasattr(expiration, "isoformat") else str(expiration)[:10]
            if exp_str != intent.expiration.isoformat():
                reasons.append("option contract expiration mismatch")

    target_symbols = {intent.option_symbol}
    if intent.is_mleg:
        if intent.long_symbol:
            target_symbols.add(intent.long_symbol)
        if intent.short_symbol:
            target_symbols.add(intent.short_symbol)

    if positions and not policy.allow_existing_position:
        for p in positions:
            p_symbol = getattr(p, "symbol", None) if hasattr(p, "symbol") else (p.get("symbol") if isinstance(p, dict) else None)
            if p_symbol and str(p_symbol) in target_symbols:
                reasons.append("existing position already held for option")
                break

    if open_orders and not policy.allow_existing_open_order:
        for o in open_orders:
            o_symbol = getattr(o, "symbol", None) if hasattr(o, "symbol") else (o.get("symbol") if isinstance(o, dict) else None)
            if o_symbol and str(o_symbol) in target_symbols:
                reasons.append("existing open order already exists for option")
                break

    return ValidationResult(approved=not reasons, reasons=tuple(reasons), live_quote=quote)
