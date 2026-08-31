from __future__ import annotations

import json
import os
import random
from typing import Mapping, Sequence

import requests
from dotenv import load_dotenv

from strategy.option_selector import OptionCandidate
from strategy.scanner import ScannedStock

load_dotenv()

FEATHERLESS_URL = "https://api.featherless.ai/v1/chat/completions"
DEFAULT_MODEL_ENV = "FEATHERLESS_MODEL"
DEFAULT_MAX_TOKENS = 320
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


def _build_option_block(option: OptionCandidate, stock_symbol: str) -> str:
    return (
        f"{option.symbol} | stock={stock_symbol} | "
        f"{option.option_type} | exp={option.expiration.isoformat()} | DTE={option.dte} | "
        f"strike=${option.strike:.2f} | mny={_fmt_pct(option.moneyness_pct)} | "
        f"ask=${option.ask:.2f} | spread={_fmt_pct(option.spread_pct)} | "
        f"IV={_fmt_pct(option.iv)} | delta={_fmt(option.delta)} | "
        f"gamma={_fmt(option.gamma)} | theta={_fmt(option.theta)} | "
        f"vega={_fmt(option.vega)} | selector={option.score:.1f}"
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
    option: OptionCandidate,
    stocks: Mapping[str, ScannedStock],
) -> str:
    matches = [
        symbol.upper()
        for symbol in stocks
        if option.symbol.startswith(symbol.upper())
    ]
    if not matches:
        raise ValueError(f"Could not determine underlying for {option.symbol}")
    # Longest match handles any potential overlapping roots safely.
    return max(matches, key=len)


def build_option_pool_prompt(
    options: Sequence[OptionCandidate],
    stocks: Mapping[str, ScannedStock],
    directions: Mapping[str, str],
    research: Mapping[str, str] | None = None,
) -> str:
    """Build one compact prompt for a cross-underlying option pool."""
    if not options:
        raise ValueError("options must not be empty")

    research = research or {}
    stock_by_symbol = {symbol.upper(): stock for symbol, stock in stocks.items()}

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
            option=option,
            stock_symbol=_underlying_for_option(option, stock_by_symbol),
        )
        for option in options
    ]

    return f"""Rank the supplied option contracts GLOBALLY by attractiveness for the short-horizon options strategy.

You are comparing contracts across DIFFERENT underlyings. Do not rank options separately by stock.
The deterministic option selector has already removed invalid/unusable contracts.

Return ONLY a JSON array containing every supplied option symbol exactly once, strongest to weakest.
Do not assign scores, probabilities, explanations, or new contracts.

Judge each contract using:
- fit with its underlying's direction and thesis
- likely movement relative to strike and DTE
- premium paid versus responsiveness
- DTE and catalyst timing
- bid/ask spread and liquidity
- implied volatility
- delta, gamma, theta, and vega
- overall efficiency relative to ALL other contracts in the pool

Do not simply follow scanner rank or selector score.

STOCK CONTEXTS
{chr(10).join(stock_blocks)}

AVAILABLE OPTION POOL
{chr(10).join(option_blocks)}

FINAL OUTPUT REQUIREMENTS:
Return ONLY a JSON array.
Include every supplied option symbol exactly once.
Order from strongest overall opportunity to weakest overall opportunity.
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


def _validate_ranking(
    ranked_symbols: Sequence[str],
    options: Sequence[OptionCandidate],
) -> list[str]:
    expected = [option.symbol for option in options]
    expected_set = set(expected)
    actual = list(ranked_symbols)

    if len(actual) != len(expected):
        raise OptionRankerError(
            f"Option ranking length mismatch: expected {len(expected)}, got {len(actual)}."
        )
    if len(set(actual)) != len(actual):
        raise OptionRankerError("Option ranking contains duplicate symbols.")
    if set(actual) != expected_set:
        missing = sorted(expected_set - set(actual))
        unexpected = sorted(set(actual) - expected_set)
        raise OptionRankerError(
            f"Invalid option ranking. Missing={missing}, unexpected={unexpected}."
        )

    return actual


def rank_option_pool(
    options: Sequence[OptionCandidate],
    stocks: Mapping[str, ScannedStock],
    directions: Mapping[str, str],
    research: Mapping[str, str] | None = None,
    *,
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

    shuffled_options = list(options)
    random.SystemRandom().shuffle(shuffled_options)

    prompt = build_option_pool_prompt(
        options=shuffled_options,
        stocks=stocks,
        directions=directions,
        research=research,
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
                    "You rank a pool of options globally. "
                    "Return only the required JSON array."
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

    ranked = _extract_json_array(content)
    return _validate_ranking(ranked, options)


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
