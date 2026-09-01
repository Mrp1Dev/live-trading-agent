from __future__ import annotations

import json
import os
import random
from typing import Mapping, Sequence

import requests
from dotenv import load_dotenv

from config import OPTION_LLM_TOP_K
from strategy.option_selector import OptionCandidate
from strategy.scanner import ScannedStock

load_dotenv()

FEATHERLESS_URL = "https://api.featherless.ai/v1/chat/completions"
DEFAULT_MODEL_ENV = "FEATHERLESS_MODEL"
DEFAULT_MAX_TOKENS = 256
DEFAULT_TEMPERATURE = 0.0


class OptionRankerError(RuntimeError):
    """Raised when the option-ranking request or response is invalid."""


def _get_api_key() -> str:
    api_key = os.getenv("FEATHERLESS_API_KEY")
    if not api_key:
        raise OptionRankerError(
            "Missing FEATHERLESS_API_KEY in environment variables."
        )
    return api_key


def _get_model(model: str | None) -> str:
    selected = model or os.getenv(DEFAULT_MODEL_ENV)
    if not selected:
        raise OptionRankerError(
            f"No Featherless model configured. Set {DEFAULT_MODEL_ENV} "
            "or pass model explicitly."
        )
    return selected


def _fmt(value: float | None, digits: int = 3) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}f}"


def _fmt_pct(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "N/A"
    return f"{value:.{digits}%}"


def _build_option_block(
    option_id: str,
    option: Any,
    stock_symbol: str,
) -> str:
    spread_type = getattr(option, "spread_type", getattr(option, "option_type", "option")).upper()
    is_credit = getattr(option, "is_credit", False)
    price_tag = f"credit=${getattr(option, 'net_credit', 0.0):.2f}" if is_credit else f"debit=${getattr(option, 'net_debit', option.ask):.2f}"
    max_loss = getattr(option, "max_loss", option.ask)
    max_profit = getattr(option, "max_profit", 0.0)
    rr_str = f"R:R={getattr(option, 'reward_to_risk', 0.0):.1f}"
    pop_str = f"PoP={_fmt_pct(getattr(option, 'probability_of_profit', None))}"
    delta_str = f"delta={_fmt(option.delta)}" if option.delta is not None else "delta=N/A"
    theta_str = f"theta={_fmt(option.theta)}" if option.theta is not None else "theta=N/A"

    return (
        f"{option_id} | "
        f"symbol={option.symbol} | "
        f"stock={stock_symbol} | "
        f"type={spread_type} | "
        f"DTE={option.dte} | "
        f"{price_tag} | "
        f"max_loss=${max_loss:.2f} | "
        f"max_profit=${max_profit:.2f} | "
        f"{rr_str} | "
        f"{pop_str} | "
        f"{delta_str} | "
        f"{theta_str} | "
        f"score={option.score:.1f}"
    )


def _build_stock_context(
    stock: ScannedStock,
    direction: str,
    research_text: str,
) -> str:
    research = research_text.strip()
    return (
        f"{stock.symbol}: direction={direction.upper()} | "
        f"scanner_rank={stock.rank if stock.rank is not None else 'N/A'} | "
        f"scanner_score={stock.score:.1f} | price=${stock.price:.2f} | "
        f"1D={stock.return_1d:+.2%} | 5D={stock.return_5d:+.2%} | "
        f"20D={stock.return_20d:+.2%} | RV={_fmt_pct(stock.realized_volatility)} | "
        f"RS={_fmt_pct(stock.relative_strength_spy)} | "
        f"research={research or 'none'}"
    )


def _underlying_for_option(
    option: Any,
    stocks: Mapping[str, ScannedStock],
) -> str:
    if hasattr(option, "underlying_symbol") and option.underlying_symbol:
        sym = option.underlying_symbol.upper()
        if sym in stocks:
            return sym
    matches = [
        symbol.upper()
        for symbol in stocks
        if option.symbol.upper().startswith(symbol.upper()) or f"{symbol.upper()}_" in option.symbol.upper()
    ]
    if matches:
        return max(matches, key=len)
    for symbol in stocks:
        if symbol.upper() in option.symbol.upper():
            return symbol.upper()
    raise ValueError(f"Could not determine underlying for {option.symbol}")


def build_option_pool_prompt(
    options: Sequence[Any],
    stocks: Mapping[str, ScannedStock],
    directions: Mapping[str, str],
    research: Mapping[str, str] | None = None,
    top_k: int = OPTION_LLM_TOP_K,
) -> str:
    """Build one compact prompt for a cross-underlying top-K option/spread ranking."""
    if not options:
        raise ValueError("options must not be empty")

    if top_k <= 0:
        raise ValueError("top_k must be positive")

    top_k = min(top_k, len(options))
    research = research or {}

    stock_by_symbol = {
        symbol.upper(): stock
        for symbol, stock in stocks.items()
    }

    option_symbols = [option.symbol for option in options]

    if len(option_symbols) != len(set(option_symbols)):
        raise ValueError("options contains duplicate symbols")

    underlyings = {
        _underlying_for_option(option, stock_by_symbol)
        for option in options
    }

    missing_context = sorted(
        symbol
        for symbol in underlyings
        if symbol not in stock_by_symbol
    )

    if missing_context:
        raise ValueError(
            "Missing stock context for: " + ", ".join(missing_context)
        )

    stock_blocks = [
        _build_stock_context(
            stock=stock_by_symbol[symbol],
            direction=directions.get(symbol, "WATCH"),
            research_text=research.get(symbol, ""),
        )
        for symbol in sorted(underlyings)
    ]

    option_blocks = [
        _build_option_block(
            option_id=f"OPT{index:03d}",
            option=option,
            stock_symbol=_underlying_for_option(
                option,
                stock_by_symbol,
            ),
        )
        for index, option in enumerate(options, start=1)
    ]

    return f"""Rank the supplied option/spread structures GLOBALLY by attractiveness for a fast intraday options strategy (15-45 min turnover).

You are comparing trade structures (Vertical Credit Spreads, Vertical Debit Spreads, and Directional Longs) across DIFFERENT underlyings.

Each option/spread structure has a temporary identifier such as OPT001, OPT002, etc.
Use ONLY those temporary identifiers in your final answer. Do NOT return full symbols.

Return ONLY the TOP {top_k} identifiers, ordered from strongest overall setup to weakest.

Judge each candidate using:
- **Strategy & Regime Fit**: 
    - Credit Spreads: High PoP (>75%), positive theta, great for support bounces or ranges.
    - Debit Spreads: Low entry cost, high R:R, great for sharp momentum breaks.
    - Directional Longs: High gamma, best when high-conviction breakout.
- **Risk/Reward & PoP**: Balance between win probability and upside.
- **DTE (0-2 DTE)**: Short-duration responsiveness and momentum alignment.
- **Selection diversity**: Select the best setup for each promising underlying across different stocks.

STOCK CONTEXTS
{chr(10).join(stock_blocks)}

AVAILABLE SPREAD & OPTION POOL
{chr(10).join(option_blocks)}

FINAL OUTPUT REQUIREMENTS:
Return ONLY a JSON array of the top {top_k} identifiers.
Example:
["OPT001", "OPT004", "OPT002"]
"""


def _extract_json_array(content: str) -> list[str]:
    text = content.strip()
    if not text:
        raise OptionRankerError("Featherless returned an empty option ranking response.")

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("[")
        end = text.rfind("]")
        if start == -1 or end <= start:
            raise OptionRankerError(
                f"Could not parse JSON array from model response: {text!r}"
            )
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise OptionRankerError(
                f"Could not parse JSON array from model response: {text!r}"
            ) from exc

    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise OptionRankerError("Model response must be a JSON array of option symbols.")

    return [item.strip() for item in parsed]


def _validate_top_k_ranking(
    ranked_ids: Sequence[str],
    options: Sequence[OptionCandidate],
    top_k: int,
) -> list[str]:
    expected_count = min(top_k, len(options))

    actual = [
        item.strip().upper()
        for item in ranked_ids
    ]

    if len(actual) != expected_count:
        raise OptionRankerError(
            f"Option ranking returned {len(actual)} identifiers; "
            f"expected {expected_count}."
        )

    if len(set(actual)) != len(actual):
        raise OptionRankerError(
            "Option ranking contains duplicate identifiers."
        )

    expected_ids = {
        f"OPT{index:03d}"
        for index in range(1, len(options) + 1)
    }

    unexpected = sorted(
        set(actual) - expected_ids
    )

    if unexpected:
        raise OptionRankerError(
            "Option ranking contains unknown identifiers: "
            + ", ".join(unexpected)
        )

    return actual


def rank_option_pool(
    options: Sequence[OptionCandidate],
    stocks: Mapping[str, ScannedStock],
    directions: Mapping[str, str],
    research: Mapping[str, str] | None = None,
    *,
    top_k: int = OPTION_LLM_TOP_K,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: float = 60.0,
    debug: bool = False,
) -> list[str]:
    """
    Rank the entire option pool across all supplied underlyings.

    The caller decides how many top-ranked options to keep.
    """
    if not options:
        return []

    if top_k <= 0:
        return []

    top_k = min(top_k, len(options))

    # The pool is shuffled so the model cannot infer importance from presentation
    # order. CRITICAL: the OPTxxx identifiers are assigned over this shuffled
    # list, so the response MUST be decoded against the same list. Decoding
    # against `options` silently maps every identifier to the wrong contract and
    # turns this whole stage into a random permutation.
    presented_options = list(options)
    random.SystemRandom().shuffle(presented_options)

    prompt = build_option_pool_prompt(
        options=presented_options,
        stocks=stocks,
        directions=directions,
        research=research,
        top_k=top_k,
    )

    if debug:
        print("\n" + "=" * 120)
        print(" OPTION LLM GLOBAL RANKER INPUT")
        print("=" * 120)
        print(prompt)
        print("=" * 120)

    payload = {
        "model": _get_model(model),
        "messages": [
            {
                "role": "system",
                "content": (
                    f"You rank a pool of options globally. "
                    f"Return ONLY a JSON array containing the top {top_k} "
                    f"temporary option identifiers such as OPT001 and OPT042. "
                    f"Never return full option symbols. "
                    f"Never return more than {top_k} identifiers."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
        "chat_template_kwargs": {"thinking": False},
    }

    try:
        response = requests.post(
            FEATHERLESS_URL,
            headers={
                "Authorization": f"Bearer {_get_api_key()}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise OptionRankerError(
            f"Featherless option-pool ranking request failed: {exc}"
        ) from exc

    if response.status_code >= 400:
        raise OptionRankerError(
            f"Featherless option-pool ranking failed with HTTP "
            f"{response.status_code}: {response.text}"
        )

    try:
        response_payload = response.json()
    except ValueError as exc:
        raise OptionRankerError(
            f"Featherless returned non-JSON response: {response.text!r}"
        ) from exc

    choices = response_payload.get("choices") or []
    if not choices:
        raise OptionRankerError(
            f"Featherless returned no choices. response={response_payload!r}"
        )

    choice = choices[0]
    content = (choice.get("message") or {}).get("content") or ""

    if debug:
        print("\n" + "=" * 120)
        print(" RAW OPTION GLOBAL LLM RESPONSE")
        print("=" * 120)
        print(content)
        print("=" * 120)

    if not content:
        raise OptionRankerError(
            "Featherless returned empty message.content for option-pool ranking. "
            f"finish_reason={choice.get('finish_reason')!r}; "
            f"response={response_payload!r}"
        )

    ranked_ids = _extract_json_array(content)

    validated_ids = _validate_top_k_ranking(
        ranked_ids=ranked_ids,
        options=presented_options,
        top_k=top_k,
    )

    # Decoded against the SAME list the identifiers were minted from.
    option_by_id = {
        f"OPT{index:03d}": option
        for index, option in enumerate(presented_options, start=1)
    }

    return [
        option_by_id[option_id].symbol
        for option_id in validated_ids
    ]


def top_ranked_options(
    options: Sequence[OptionCandidate],
    stocks: Mapping[str, ScannedStock],
    directions: Mapping[str, str],
    research: Mapping[str, str] | None = None,
    *,
    top_k: int = 5,
    **kwargs,
) -> list[OptionCandidate]:
    """Rank the full pool and return the top K options globally."""
    if top_k <= 0 or not options:
        return []

    ranked_symbols = rank_option_pool(
        options=options,
        stocks=stocks,
        directions=directions,
        research=research,
        **kwargs,
    )

    by_symbol = {option.symbol: option for option in options}
    return [by_symbol[symbol] for symbol in ranked_symbols[:top_k]]


# Compatibility aliases.
rank_options = rank_option_pool
rank_option_candidates = rank_option_pool
