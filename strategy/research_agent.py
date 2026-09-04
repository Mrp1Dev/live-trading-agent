from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Sequence

import requests
from dotenv import load_dotenv

from strategy.scanner import ScannedStock

load_dotenv()

ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
FEATHERLESS_BASE_URL = "https://api.featherless.ai/v1"
DEFAULT_LOOKBACK_DAYS = 5
DEFAULT_MAX_ARTICLES = 3
DEFAULT_CANDIDATE_POOL = 30
DEFAULT_MAX_ARTICLE_SUMMARY_CHARS = 350
DEFAULT_MAX_WORKERS = 8
DEFAULT_TEMPERATURE = 0.0
DEFAULT_MAX_TOKENS = 160
DEFAULT_TIMEOUT_SECONDS = 30


SYSTEM_PROMPT = """
You are a market-news compression agent for an autonomous short-horizon options system.

Your job is NOT to rank the stock, score it, recommend a trade, or predict a return.
Compress the recent news into only the information that could matter over the next few
trading sessions.

The quantitative setup has already been computed deterministically. Translate it into
short qualitative descriptors when useful, but do not repeat the raw numbers.

Use only the supplied evidence. Do not invent catalysts, risks, dates, or facts.
If there is no meaningful recent news, say so.

Return ONLY JSON with exactly these keys:
{
  "news": "one concise sentence, maximum 40 words",
  "catalyst": "one concise near-term catalyst, or null",
  "risk": "one concise news-related risk/contradiction, or null",
  "timing": "IMMEDIATE | NEAR_TERM | UNKNOWN | NONE"
}

IMMEDIATE means likely relevant within 1-2 trading days.
NEAR_TERM means relevant within the short evaluation window but not clearly immediate.
UNKNOWN means potentially relevant but timing is unclear.
NONE means there is no meaningful recent catalyst.
""".strip()


@dataclass(frozen=True)
class NewsArticle:
    symbol: str
    headline: str
    summary: str
    source: str
    published_at: str
    url: str | None = None


@dataclass(frozen=True)
class ResearchReport:
    symbol: str
    quantitative_summary: str
    news: str
    catalyst: str | None
    risk: str | None
    timing: str
    article_count: int
    articles: tuple[NewsArticle, ...] = ()

    def to_text(self) -> str:
        parts = [self.symbol, f"Quant: {self.quantitative_summary}"]
        parts.append(f"News: {self.news}")
        if self.catalyst:
            parts.append(f"Catalyst: {self.catalyst}")
        if self.risk:
            parts.append(f"Risk: {self.risk}")
        parts.append(f"Timing: {self.timing}")
        return "\n".join(parts)


class ResearchAgentError(RuntimeError):
    """Raised when research retrieval or compression fails."""


def _get_alpaca_credentials() -> tuple[str, str]:
    api_key = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
    secret_key = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
    if not api_key or not secret_key:
        raise ResearchAgentError(
            "Missing Alpaca API credentials. Set ALPACA_API_KEY and ALPACA_SECRET_KEY "
            "(or APCA_API_KEY_ID and APCA_API_SECRET_KEY) in .env."
        )
    return api_key, secret_key


def _get_research_model(model: str | None) -> str:
    selected = model or os.getenv("FEATHERLESS_RESEARCH_MODEL")
    if not selected:
        raise ResearchAgentError(
            "No research model selected. Pass model=... to research_stock(s) "
            "or set FEATHERLESS_RESEARCH_MODEL in .env."
        )
    return selected


def _clean_text(value: Any, max_chars: int | None = None) -> str:
    if value is None:
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip()
    if max_chars is not None and len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


def _qualitative_quant_summary(stock: ScannedStock) -> str:
    """Turn scanner values into compact semantic labels without an LLM call."""
    labels: list[str] = []

    # Momentum
    if stock.return_20d >= 0.20 or stock.momentum_score >= 90:
        labels.append("exceptionally strong momentum")
    elif stock.return_20d >= 0.08 or stock.momentum_score >= 70:
        labels.append("strong momentum")
    elif stock.return_20d <= -0.20 or stock.momentum_score <= 10:
        labels.append("exceptionally weak momentum")
    elif stock.return_20d <= -0.08 or stock.momentum_score <= 30:
        labels.append("weak momentum")

    # Volume
    if stock.volume_ratio >= 2.0 or stock.volume_score >= 90:
        labels.append("unusually high volume")
    elif stock.volume_ratio >= 1.25 or stock.volume_score >= 70:
        labels.append("elevated volume")

    # Volatility
    if stock.realized_volatility >= 0.70 or stock.volatility_score >= 90:
        labels.append("very high volatility")
    elif stock.realized_volatility >= 0.45 or stock.volatility_score >= 70:
        labels.append("elevated volatility")

    # Relative strength
    if stock.relative_strength_spy >= 0.15 or stock.relative_strength_score >= 90:
        labels.append("exceptional relative strength")
    elif stock.relative_strength_spy >= 0.05 or stock.relative_strength_score >= 70:
        labels.append("strong relative strength")
    elif stock.relative_strength_spy <= -0.15 or stock.relative_strength_score <= 10:
        labels.append("exceptionally weak relative strength")
    elif stock.relative_strength_spy <= -0.05 or stock.relative_strength_score <= 30:
        labels.append("weak relative strength")

    # Trend / extension
    max_distance = max(abs(stock.distance_sma20), abs(stock.distance_sma50))
    if max_distance >= 0.15:
        labels.append("materially extended from trend")
    elif max_distance >= 0.08:
        labels.append("noticeably extended from trend")

    if not labels:
        return "mixed or moderate quantitative setup"
    return ", ".join(labels)


def _parse_article(raw: Mapping[str, Any], symbol: str) -> NewsArticle:
    return NewsArticle(
        symbol=symbol,
        headline=_clean_text(raw.get("headline") or raw.get("title")),
        summary=_clean_text(
            raw.get("summary") or raw.get("content"),
            DEFAULT_MAX_ARTICLE_SUMMARY_CHARS,
        ),
        source=_clean_text(raw.get("source") or raw.get("author") or "Unknown source", 80),
        published_at=_clean_text(raw.get("created_at") or raw.get("updated_at") or "Unknown time", 40),
        url=raw.get("url"),
    )


# Pre-mapped company aliases for fast deterministic headline & summary matching.
COMPANY_ALIASES: dict[str, tuple[str, ...]] = {
    "CRM": ("salesforce",),
    "NOW": ("servicenow",),
    "COIN": ("coinbase",),
    "MSTR": ("microstrategy", "strategy"),
    "NVDA": ("nvidia",),
    "MSFT": ("microsoft",),
    "AAPL": ("apple",),
    "GOOGL": ("google", "alphabet"),
    "GOOG": ("google", "alphabet"),
    "AMZN": ("amazon",),
    "META": ("meta", "facebook"),
    "TSLA": ("tesla",),
    "AMD": ("amd", "advanced micro devices"),
    "AVGO": ("broadcom",),
    "ORCL": ("oracle",),
    "ADBE": ("adobe",),
    "CSCO": ("cisco",),
    "ACN": ("accenture",),
    "IBM": ("ibm", "international business machines"),
    "INTU": ("intuit",),
    "QCOM": ("qualcomm",),
    "TXN": ("texas instruments",),
    "AMAT": ("applied materials",),
    "MU": ("micron",),
    "LRCX": ("lam research",),
    "KLAC": ("kla", "kla-tencor"),
    "ADI": ("analog devices",),
    "MRVL": ("marvell",),
    "INTC": ("intel",),
    "NXPI": ("nxp",),
    "MCHP": ("microchip",),
    "ON": ("on semi", "on semiconductor"),
    "MPWR": ("monolithic power",),
    "CDNS": ("cadence",),
    "SNPS": ("synopsys",),
    "ANSS": ("ansys",),
    "FTNT": ("fortinet",),
    "PANW": ("palo alto", "palo alto networks"),
    "CRWD": ("crowdstrike",),
    "PLTR": ("palantir",),
    "APP": ("applovin",),
    "ADSK": ("autodesk",),
    "ROP": ("roper",),
    "APH": ("amphenol",),
    "KEYS": ("keysight",),
    "DELL": ("dell",),
    "HPQ": ("hp inc", "hp", "hewlett packard"),
    "NFLX": ("netflix",),
    "DIS": ("disney", "walt disney"),
    "UBER": ("uber",),
    "DASH": ("doordash",),
    "ABNB": ("airbnb",),
    "LULU": ("lululemon",),
    "HD": ("home depot",),
    "LOW": ("lowe's", "lowes"),
    "COST": ("costco",),
    "CMG": ("chipotle",),
    "MCD": ("mcdonald's", "mcdonalds"),
    "SBUX": ("starbucks",),
    "NKE": ("nike",),
    "CAT": ("caterpillar",),
    "DE": ("deere", "john deere"),
    "GE": ("ge", "general electric", "ge aerospace"),
    "RTX": ("rtx", "raytheon"),
    "LMT": ("lockheed", "lockheed martin"),
    "BA": ("boeing",),
    "UPS": ("ups", "united parcel service"),
    "FDX": ("fedex",),
    "UNP": ("union pacific",),
    "XOM": ("exxon", "exxonmobil"),
    "CVX": ("chevron",),
    "COP": ("conocophillips",),
    "EOG": ("eog",),
    "SLB": ("schlumberger", "slb"),
    "HAL": ("halliburton",),
    "OXY": ("occidental",),
    "FCX": ("freeport", "freeport-mcmoran"),
    "NEM": ("newmont",),
    "JPM": ("jpmorgan", "jp morgan", "chase"),
    "BAC": ("bank of america", "bofa"),
    "WFC": ("wells fargo",),
    "C": ("citigroup", "citi"),
    "GS": ("goldman sachs", "goldman"),
    "MS": ("morgan stanley",),
    "BLK": ("blackrock",),
    "SCHW": ("charles schwab", "schwab"),
    "PYPL": ("paypal",),
    "HOOD": ("robinhood",),
    "V": ("visa",),
    "MA": ("mastercard",),
    "LLY": ("eli lilly", "lilly"),
    "UNH": ("unitedhealth", "unitedhealthcare"),
    "JNJ": ("johnson & johnson", "j&j"),
    "ABBV": ("abbvie",),
    "MRK": ("merck",),
    "PFE": ("pfizer",),
    "BMY": ("bristol-myers", "bristol myers"),
    "AMGN": ("amgen",),
    "GILD": ("gilead",),
    "VRTX": ("vertex",),
    "REGN": ("regeneron",),
    "ISRG": ("intuitive surgical",),
    "ABT": ("abbott",),
    "TMO": ("thermo fisher",),
    "DHR": ("danaher",),
    "SYK": ("stryker",),
    "BSX": ("boston scientific",),
    "MDT": ("medtronic",),
    "CVS": ("cvs",),
    "MRNA": ("moderna",),
    "BIIB": ("biogen",),
    "WMT": ("walmart",),
    "PG": ("procter & gamble", "p&g"),
    "KO": ("coca-cola", "coke"),
    "PEP": ("pepsi", "pepsico"),
    "PM": ("philip morris",),
    "MO": ("altria",),
}

GENERIC_NOISE_PATTERNS: tuple[str, ...] = (
    r"\bwhale (?:alerts?|activity)\b",
    r"\bmarket (?:wrap|today|update|recap|summary)\b",
    r"\bweekend tech roundup\b",
    r"\btop \d+ (?:stocks|software|financials|movers)\b",
    r"\bwall street (?:breakfast|roundup|today)\b",
    r"\bcnbc[’']?s [‘'\"“]?final trades[‘'\"”]?\b",
    r"\bhalftime report\b",
    r"\bthis week (?:on wall street|in tech)\b",
    r"\bstocks that hit 52-week\b",
)


def score_article_relevance(raw_article: Mapping[str, Any], symbol: str) -> float:
    """Compute a deterministic relevance score for a raw Alpaca news item."""
    symbol = symbol.upper()
    headline = (raw_article.get("headline") or raw_article.get("title") or "").strip()
    summary = (raw_article.get("summary") or raw_article.get("content") or "").strip()
    raw_symbols = raw_article.get("symbols", [])
    symbols = [s.upper() for s in raw_symbols] if isinstance(raw_symbols, list) else []

    score = 0.0

    # 1. Symbol tag density & exclusivity
    if symbols == [symbol]:
        score += 50.0  # Exclusively about this symbol
    elif symbol in symbols:
        if len(symbols) <= 3:
            score += 30.0  # Highly focused group
        elif len(symbols) <= 6:
            score += 10.0  # Moderate group
        elif len(symbols) >= 15:
            score -= 40.0  # Massive multi-ticker basket / noise
        elif len(symbols) >= 8:
            score -= 20.0
    else:
        score -= 50.0

    # 2. Headline matches (ticker symbol or company alias)
    headline_lower = headline.lower()
    symbol_pattern = rf"\b{re.escape(symbol.lower())}\b|\${re.escape(symbol.lower())}\b"
    symbol_in_headline = bool(re.search(symbol_pattern, headline_lower))

    aliases = COMPANY_ALIASES.get(symbol, (symbol.lower(),))
    company_in_headline = any(
        re.search(rf"\b{re.escape(alias)}\b", headline_lower) for alias in aliases
    )

    if company_in_headline:
        score += 45.0
    elif symbol_in_headline:
        score += 35.0

    # 3. Summary / lead text matches
    summary_lower = summary.lower()
    symbol_in_summary = bool(re.search(symbol_pattern, summary_lower))
    company_in_summary = any(
        re.search(rf"\b{re.escape(alias)}\b", summary_lower) for alias in aliases
    )
    if company_in_summary or symbol_in_summary:
        score += 15.0

    # 4. Generic market wrap penalties (unless company/ticker is explicitly in the headline)
    if not (company_in_headline or symbol_in_headline):
        for pattern in GENERIC_NOISE_PATTERNS:
            if re.search(pattern, headline_lower):
                score -= 35.0
                break

    return score


def _article_dedupe_key(article: NewsArticle) -> str:
    title = re.sub(r"[^a-z0-9 ]+", "", article.headline.lower())
    title = re.sub(r"\s+", " ", title).strip()
    return title


def _dedupe_articles(articles: Sequence[NewsArticle]) -> list[NewsArticle]:
    seen: set[str] = set()
    result: list[NewsArticle] = []
    for article in articles:
        key = _article_dedupe_key(article)
        if not key:
            continue
        if key in seen:
            continue
        seen.add(key)
        result.append(article)
    return result


def fetch_stock_news(
    symbol: str,
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    max_articles: int = DEFAULT_MAX_ARTICLES,
    candidate_limit: int = DEFAULT_CANDIDATE_POOL,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> list[NewsArticle]:
    """
    Fetch recent Alpaca news for one stock, rank by deterministic relevancy,
    and return up to max_articles.

    Uses a tiered fallback so high-relevance company news is always prioritized,
    while guaranteeing that we never return fewer than max_articles if candidate
    news exists for the stock. Automatically widens the lookback window if too few
    articles are found in a short lookback.
    """
    if lookback_days <= 0:
        raise ValueError("lookback_days must be positive")
    if max_articles <= 0:
        return []

    api_key, secret_key = _get_alpaca_credentials()

    def _fetch_window(days: int) -> list[NewsArticle]:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=days)

        fetch_limit = min(50, max(candidate_limit, max_articles * 10))
        params = {
            "symbols": symbol.upper(),
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "sort": "desc",
            "limit": fetch_limit,
            "include_content": "false",
            "exclude_contentless": "true",
        }
        payload = {}
        for attempt in range(2):
            try:
                response = requests.get(
                    ALPACA_NEWS_URL,
                    headers={
                        "APCA-API-KEY-ID": api_key,
                        "APCA-API-SECRET-KEY": secret_key,
                    },
                    params=params,
                    timeout=min(timeout, 15),
                )
                response.raise_for_status()
                payload = response.json()
                break
            except Exception as exc:
                if attempt == 1:
                    print(f"Alpaca news query timed out for {symbol} ({type(exc).__name__}). Continuing with quantitative profile.")
                    return []
                import time
                time.sleep(1)

        raw_articles = payload.get("news", [])
        if not isinstance(raw_articles, list):
            raise ResearchAgentError(
                f"Unexpected Alpaca news response for {symbol}."
            )
        if not raw_articles:
            return []

        # Score and deduplicate raw articles
        scored_candidates: list[tuple[float, str, NewsArticle]] = []
        seen_dedupe_keys: set[str] = set()

        for item in raw_articles:
            article = _parse_article(item, symbol.upper())
            key = _article_dedupe_key(article)
            if not key or key in seen_dedupe_keys:
                continue
            seen_dedupe_keys.add(key)

            score = score_article_relevance(item, symbol.upper())
            scored_candidates.append((score, article.published_at, article))

        # Tiered ranking:
        # High tier: Explicit headline match or single-symbol exclusivity (score >= 30)
        # Med tier:  Focused symbol basket or summary mention (0 <= score < 30)
        # Low tier:  Fallback candidates (score < 0) to ensure we fill all required slots
        high_tier = [c for c in scored_candidates if c[0] >= 30.0]
        med_tier = [c for c in scored_candidates if 0.0 <= c[0] < 30.0]
        low_tier = [c for c in scored_candidates if c[0] < 0.0]

        high_tier.sort(key=lambda x: (x[0], x[1]), reverse=True)
        med_tier.sort(key=lambda x: (x[0], x[1]), reverse=True)
        low_tier.sort(key=lambda x: (x[0], x[1]), reverse=True)

        selected: list[NewsArticle] = []
        for tier in (high_tier, med_tier, low_tier):
            for _, _, art in tier:
                if len(selected) >= max_articles:
                    break
                selected.append(art)
            if len(selected) >= max_articles:
                break

        return selected

    results = _fetch_window(lookback_days)
    # If the initial window yielded fewer than max_articles, expand lookback to 14 days
    if len(results) < max_articles and lookback_days < 14:
        wider_results = _fetch_window(14)
        if len(wider_results) > len(results):
            results = wider_results

    return results


def _format_news_for_llm(articles: Sequence[NewsArticle]) -> str:
    if not articles:
        return "NO MATERIAL RECENT NEWS FOUND."

    lines: list[str] = []
    for index, article in enumerate(articles, start=1):
        headline = article.headline or "(no headline)"
        summary = article.summary or "(no provider summary available)"
        lines.extend(
            [
                f"ARTICLE {index}: {article.source} | {article.published_at}",
                f"Headline: {headline}",
                f"Summary: {summary}",
            ]
        )
    return "\n".join(lines)


def build_research_prompt(
    stock: ScannedStock,
    articles: Sequence[NewsArticle],
) -> str:
    """Build a compact stock-specific prompt for the cheap research model."""
    return "\n".join(
        [
            "STOCK:",
            stock.symbol.upper(),
            "",
            "QUANTITATIVE SETUP (already computed; do not repeat raw numbers):",
            _qualitative_quant_summary(stock),
            "",
            "RECENT ALPACA NEWS:",
            _format_news_for_llm(articles),
            "",
            "TASK:",
            "Compress only the news/context that could matter over the next few trading sessions.",
            "Do not score, rank, recommend a trade, or invent information.",
        ]
    )


def _extract_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

    raise ResearchAgentError(
        f"Featherless returned invalid research JSON: {content!r}"
    )


def _normalize_field(value: Any, max_words: int | None = None) -> str | None:
    if value is None:
        return None
    text = _clean_text(value)
    if not text or text.lower() in {"null", "none", "n/a"}:
        return None
    if max_words is not None:
        words = text.split()
        text = " ".join(words[:max_words])
    return text or None


def compress_news(
    stock: ScannedStock,
    articles: Sequence[NewsArticle],
    *,
    model: str | None = None,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    timeout: int = DEFAULT_TIMEOUT_SECONDS,
) -> ResearchReport:
    """Compress one stock's recent news into a small structured report."""
    selected_model = _get_research_model(model)

    # If there is no news, avoid spending an LLM call just to say so.
    if not articles:
        return ResearchReport(
            symbol=stock.symbol.upper(),
            quantitative_summary=_qualitative_quant_summary(stock),
            news="No material recent news found.",
            catalyst=None,
            risk=None,
            timing="NONE",
            article_count=0,
            articles=tuple(articles),
        )

    prompt = build_research_prompt(stock, articles)
    api_key = os.getenv("FEATHERLESS_API_KEY")
    if not api_key:
        raise ResearchAgentError(
            "Missing FEATHERLESS_API_KEY. Add it to .env or the environment."
        )

    try:
        response = requests.post(
            f"{FEATHERLESS_BASE_URL}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "HTTP-Referer": "alpaca-agent",
                "X-Title": "Alpaca Options Trading Agent - Research",
            },
            json={
                "model": selected_model,
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": temperature,
                "max_tokens": max_tokens,
                "chat_template_kwargs": {"thinking": False},
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException as exc:
        raise ResearchAgentError(
            f"Featherless research request failed for {stock.symbol}: {exc}"
        ) from exc
    except ValueError as exc:
        raise ResearchAgentError(
            f"Featherless returned non-JSON response for {stock.symbol}."
        ) from exc

    try:
        content = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ResearchAgentError(
            f"Unexpected Featherless response format for {stock.symbol}: {payload!r}"
        ) from exc
    if not content:
        raise ResearchAgentError(
            f"Featherless returned an empty research response for {stock.symbol}."
        )

    result = _extract_json_object(content)
    timing = _normalize_field(result.get("timing")) or "UNKNOWN"
    timing = timing.upper()
    if timing not in {"IMMEDIATE", "NEAR_TERM", "UNKNOWN", "NONE"}:
        timing = "UNKNOWN"

    return ResearchReport(
        symbol=stock.symbol.upper(),
        quantitative_summary=_qualitative_quant_summary(stock),
        news=_normalize_field(result.get("news"), max_words=40)
        or "No material recent news summary was produced.",
        catalyst=_normalize_field(result.get("catalyst"), max_words=22),
        risk=_normalize_field(result.get("risk"), max_words=22),
        timing=timing,
        article_count=len(articles),
        articles=tuple(articles),
    )


def research_stock(
    stock: ScannedStock,
    *,
    model: str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    max_articles: int = DEFAULT_MAX_ARTICLES,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> ResearchReport:
    """Fetch Alpaca news and produce a compact research report for one stock."""
    articles = fetch_stock_news(
        stock.symbol,
        lookback_days=lookback_days,
        max_articles=max_articles,
    )
    return compress_news(
        stock,
        articles,
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def research_stocks(
    stocks: Sequence[ScannedStock],
    *,
    model: str | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    max_articles: int = DEFAULT_MAX_ARTICLES,
    max_workers: int = DEFAULT_MAX_WORKERS,
    temperature: float = DEFAULT_TEMPERATURE,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> dict[str, ResearchReport]:
    """
    Research multiple scanner candidates concurrently.

    Failures for individual symbols are converted to compact fallback reports so one
    bad news/model response does not kill the whole scanner -> research -> ranker path.
    """
    if not stocks:
        return {}
    if max_workers <= 0:
        raise ValueError("max_workers must be positive")

    reports: dict[str, ResearchReport] = {}

    def _run(stock: ScannedStock) -> ResearchReport:
        try:
            return research_stock(
                stock,
                model=model,
                lookback_days=lookback_days,
                max_articles=max_articles,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        except ResearchAgentError as exc:
            return ResearchReport(
                symbol=stock.symbol.upper(),
                quantitative_summary=_qualitative_quant_summary(stock),
                news=f"Research unavailable: {str(exc)[:120]}",
                catalyst=None,
                risk="Recent news could not be evaluated.",
                timing="UNKNOWN",
                article_count=0,
                articles=(),
            )

    with ThreadPoolExecutor(max_workers=min(max_workers, len(stocks))) as executor:
        futures = {executor.submit(_run, stock): stock.symbol.upper() for stock in stocks}
        for future in as_completed(futures):
            symbol = futures[future]
            report = future.result()
            reports[symbol] = report

    # Preserve scanner order for deterministic downstream prompt construction.
    return {stock.symbol.upper(): reports[stock.symbol.upper()] for stock in stocks}


def print_research_reports(
    reports: Sequence[ResearchReport] | Mapping[str, ResearchReport],
) -> None:
    report_items = reports.values() if isinstance(reports, Mapping) else reports

    print("\n" + "=" * 120)
    print(" RESEARCH REPORTS")
    print("=" * 120)

    for report in report_items:
        print(f"\n[{report.symbol}]")
        print(f"Articles:  {report.article_count}")
        print(f"Quant:     {report.quantitative_summary}")
        print(f"News:      {report.news}")
        print(f"Catalyst:  {report.catalyst or 'None'}")
        print(f"Risk:      {report.risk or 'None'}")
        print(f"Timing:    {report.timing}")

        if report.articles:
            print("Source articles:")
            for article in report.articles:
                print(
                    f"  - {article.source} | "
                    f"{article.published_at} | "
                    f"{article.headline}"
                )


def research_text_by_symbol(
    reports: Mapping[str, ResearchReport],
) -> dict[str, str]:
    """Convert reports into the mapping expected by strategy.llm_ranker.rank_stocks()."""
    return {
        symbol.upper(): report.to_text()
        for symbol, report in reports.items()
    }


if __name__ == "__main__":
    raise SystemExit(
        "research_agent.py is a library module; call research_stock(s) from the orchestration layer."
    )
