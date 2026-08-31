from __future__ import annotations

import json
import os
import re
from random import SystemRandom
from typing import Mapping, Sequence

from dotenv import load_dotenv
import requests

from strategy.scanner import ScannedStock

load_dotenv()

FEATHERLESS_BASE_URL = "https://api.featherless.ai/v1"
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 128


SYSTEM_PROMPT = """
You are the ranking component of an autonomous short-horizon options trading system.

Your job is to RANK candidate UNDERLYINGS relative to one another for the next few
trading days. You are not choosing an option contract, sizing a position, or deciding
whether to execute a trade.

Use two evidence sources:
1. Quantitative scanner data.
2. A research dossier containing recent market/news/contextual information.

Important principles:
- Rank candidates comparatively, not independently.
- Do not simply reproduce the scanner ranking.
- Treat the scanner score as a useful prior, not as the answer.
- Favor opportunities with a plausible, timely catalyst and a credible chance of a
  meaningful move during the short evaluation window.
- Consider momentum, volume, realized volatility, relative strength, technical trend,
  catalyst quality/timing, contradictory evidence, and thesis uncertainty together.
- High volatility alone is not sufficient.
- Strong past performance alone is not sufficient.
- Penalize stale, vague, already-digested, poorly timed, or weakly supported narratives.
- Do not invent facts. If important information is missing, treat that as uncertainty.
- This is an ORDINAL task. Do not assign scores, probabilities, confidence values, or
  explanations in the final answer.

Return ONLY a JSON array containing every candidate symbol exactly once, ordered from
MOST attractive to LEAST attractive.
Example:
["CRM", "MSTR", "NVDA", "AMD"]
""".strip()


class LLMRankerError(RuntimeError):
    """Raised when the Featherless ranking request or response is invalid."""


def _get_api_key() -> str:
    api_key = os.getenv("FEATHERLESS_API_KEY")
    if not api_key:
        raise LLMRankerError(
            "Missing FEATHERLESS_API_KEY. Add it to .env or the environment."
        )
    return api_key


def _get_model(model: str | None) -> str:
    selected_model = model or os.getenv("FEATHERLESS_MODEL")
    if not selected_model:
        raise LLMRankerError(
            "No Featherless model selected. Pass model=... to rank_stocks() "
            "or set FEATHERLESS_MODEL in .env."
        )
    return selected_model


def _pct(value: float) -> str:
    return f"{value:.2%}"


def _format_scanner_row(stock: ScannedStock) -> str:
    """Format the complete scanner information compactly for the LLM."""
    rank = stock.rank if stock.rank is not None else "-"
    return (
        f"Rank #{rank} | Symbol: {stock.symbol} | Price: ${stock.price:.2f} | "
        f"1D: {_pct(stock.return_1d)} | 5D: {_pct(stock.return_5d)} | "
        f"20D: {_pct(stock.return_20d)} | Vol/Avg: {stock.volume_ratio:.2f}x | "
        f"SMA20 dist: {_pct(stock.distance_sma20)} | "
        f"SMA50 dist: {_pct(stock.distance_sma50)} | "
        f"Realized vol: {_pct(stock.realized_volatility)} | "
        f"RS vs SPY: {_pct(stock.relative_strength_spy)} | "
        f"Momentum score: {stock.momentum_score:.1f} | "
        f"Volume score: {stock.volume_score:.1f} | "
        f"Volatility score: {stock.volatility_score:.1f} | "
        f"RS score: {stock.relative_strength_score:.1f} | "
        f"Trend score: {stock.trend_score:.1f} | "
        f"Scanner score: {stock.score:.1f}"
    )


def build_ranker_prompt(
    stocks: Sequence[ScannedStock],
    research: Mapping[str, str],
) -> str:
    """Build the comparative ranking prompt sent to the model."""
    if not stocks:
        raise ValueError("stocks must contain at least one candidate")

    symbols = [stock.symbol.upper() for stock in stocks]
    duplicate_symbols = len(symbols) != len(set(symbols))
    if duplicate_symbols:
        raise ValueError("stocks contains duplicate symbols")

    sections: list[str] = [
        "RANK THESE CANDIDATES FOR A SHORT-HORIZON OPTIONS OPPORTUNITY.",
        "",
        "Candidate presentation order may be randomized; do not infer importance from presentation order.",
        "The scanner rank is included as data, but you should independently compare the evidence.",
        "",
    ]

    for index, stock in enumerate(stocks, start=1):
        symbol = stock.symbol.upper()
        research_text = research.get(symbol, "")
        if not research_text:
            research_text = "NO RESEARCH DOSSIER AVAILABLE. Treat missing research as uncertainty."

        sections.append(f"=== CANDIDATE {index}: {symbol} ===")
        sections.append("SCANNER DATA:")
        sections.append(_format_scanner_row(stock))
        sections.append("RESEARCH DOSSIER:")
        sections.append(research_text.strip())
        sections.append("")

    sections.extend(
        [
            "FINAL OUTPUT REQUIREMENTS:",
            "Return ONLY a JSON array of symbols.",
            f"The array must contain exactly these symbols: {', '.join(symbols)}",
            "Each symbol must appear exactly once.",
            "Order from strongest short-horizon opportunity to weakest.",
        ]
    )
    return "\n".join(sections)


def _extract_json_array(content: str) -> list[str]:
    """Parse a model response containing a JSON array of symbols."""
    text = content.strip()

    # First, try the entire response as JSON.
    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return [str(item).strip().upper() for item in parsed]
        if isinstance(parsed, dict):
            for key in ("ranked_symbols", "ranking", "symbols"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return [str(item).strip().upper() for item in value]
    except json.JSONDecodeError:
        pass

    # Fallback: extract the first JSON-looking array from surrounding text.
    match = re.search(r"\[[\s\S]*?\]", text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, list):
                return [str(item).strip().upper() for item in parsed]
        except json.JSONDecodeError:
            pass

    raise LLMRankerError(
        "Featherless returned an invalid ranking response. "
        f"Raw response: {content!r}"
    )


def _validate_ranking(ranking: Sequence[str], expected_symbols: Sequence[str]) -> list[str]:
    """Ensure the model returned a complete permutation of the candidates."""
    expected = [symbol.upper() for symbol in expected_symbols]
    normalized = [symbol.strip().upper() for symbol in ranking]

    if len(normalized) != len(expected):
        raise LLMRankerError(
            f"Ranking contains {len(normalized)} symbols; expected {len(expected)}."
        )

    if len(set(normalized)) != len(normalized):
        raise LLMRankerError("Ranking contains duplicate symbols.")

    if set(normalized) != set(expected):
        missing = sorted(set(expected) - set(normalized))
        unexpected = sorted(set(normalized) - set(expected))
        raise LLMRankerError(
            "Ranking does not match candidate universe. "
            f"Missing={missing}, unexpected={unexpected}."
        )

    return normalized


def rank_stocks(
    stocks: Sequence[ScannedStock],
    research: Mapping[str, str],
    *,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    debug: bool = False,
) -> list[str]:
    """
    Rank scanned stocks using a Featherless chat-completion model.

    Parameters
    ----------
    stocks:
        ScannedStock objects produced by the quantitative scanner.
    research:
        Mapping of ticker -> research-agent text for each candidate.
    model:
        Featherless model ID. If omitted, FEATHERLESS_MODEL is read from .env.
    temperature:
        Sampling temperature. Defaults to 0 for reproducible ranking behavior.
    max_tokens:
        Small output budget because the desired output is only an ordered symbol list.
    debug:
        If True, print ranker prompt input and raw LLM response.

    Returns
    -------
    list[str]
        Symbols ordered from strongest to weakest short-horizon opportunity.
    """
    if not stocks:
        return []

    selected_model = _get_model(model)
    expected_symbols = [stock.symbol.upper() for stock in stocks]

    # Randomize presentation order so the model does not simply inherit the
    # scanner table order. The original scanner rank remains visible as a feature.
    prompt_stocks = list(stocks)
    SystemRandom().shuffle(prompt_stocks)
    prompt = build_ranker_prompt(prompt_stocks, research)

    if debug:
        print("\n" + "=" * 120)
        print(" LLM RANKER INPUT")
        print("=" * 120)
        print(prompt)
        print("=" * 120)

    try:
        response = requests.post(
            f"{FEATHERLESS_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {_get_api_key()}",
                "Content-Type": "application/json",
                "HTTP-Referer": "alpaca-agent",
                "X-Title": "Alpaca Options Trading Agent",
            },
            json={
                "model": selected_model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "chat_template_kwargs": {
                    "thinking": False,
                },
            },
            timeout=60,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise LLMRankerError(
            f"Featherless ranking request failed: {exc}"
        ) from exc
    except ValueError as exc:
        raise LLMRankerError(
            "Featherless returned a non-JSON response."
        ) from exc

    try:
        choices = payload["choices"]
        content = choices[0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMRankerError(
            f"Unexpected Featherless response format: {payload!r}"
        ) from exc
    if not content:
        raise LLMRankerError(
            "Featherless returned empty message.content. "
            f"finish_reason={choices[0].get('finish_reason')!r}, "
            f"response={payload!r}"
        )

    if debug:
        print("\n" + "=" * 120)
        print(" RAW LLM RANKER RESPONSE")
        print("=" * 120)
        print(content)
        print("=" * 120)

    ranking = _extract_json_array(content)
    return _validate_ranking(ranking, expected_symbols)


# Clear alias for use from orchestration code.
rank_candidates = rank_stocks
