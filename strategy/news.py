from alpaca_client.client import get_news_client
from alpaca.data.requests import NewsRequest

from google import genai
import os
import json
from dotenv import load_dotenv
load_dotenv()
from pydantic import BaseModel

folder_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'shared_folder'))


class ArticleAnalysis(BaseModel):
    headline: str
    relevance: float
    sentiment: str
    importance: str
    reason: str

class NewsAnalysis(BaseModel):
    symbol: str
    signal: str
    confidence: float
    bullish_points: list[str]
    bearish_points: list[str]
    catalysts: list[str]
    risks: list[str]
    research_summary: str

client = genai.Client(
    api_key=os.getenv("GEMINI_API_KEY")
)

def get_news(symbol, limit=10):
    news_client = get_news_client()
    request_params = NewsRequest(
        symbols=symbol,
        limit=limit,
        include_content=True,
        exclude_contentless=True
    )
    news_set = news_client.get_news(request_params)
    
    # NewsSet is iterable and yields tuples of ('data', {...})
    # We need to extract the actual news items
    news_items = []
    for key, value in news_set:
        if key == 'data' and isinstance(value, dict) and 'news' in value:
            news_items.extend(value['news'])
    
    return news_items

def analyze_news(symbol, news) -> NewsAnalysis:

    # news is a list (might be dicts or objects) returned from get_news
    # Convert to simple text format for LLM
    articles = []
    for item in news:
        # Handle both dict and object formats
        if isinstance(item, dict):
            headline = item.get('headline', 'N/A')
            source = item.get('source', 'N/A')
            published_at = str(item.get('created_at', 'N/A'))
            url = item.get('url', 'N/A')
            summary = item.get('summary', '')
        else:
            # Handle object format (News object from Alpaca)
            headline = getattr(item, 'headline', 'N/A')
            source = getattr(item, 'source', 'N/A')
            published_at = str(getattr(item, 'created_at', 'N/A'))
            url = getattr(item, 'url', 'N/A')
            summary = getattr(item, 'summary', '')
        
        articles.append({
            "headline": headline,
            "source": source,
            "published_at": published_at,
            "url": url,
            "summary": summary,
        })

    if not articles:
        # Return neutral analysis if no articles
        return NewsAnalysis(
            symbol=symbol,
            signal="neutral",
            confidence=0.0,
            bullish_points=[],
            bearish_points=[],
            catalysts=[],
            risks=[],
            research_summary="No news articles to analyze"
        )

    prompt = f"""
You are a financial news research assistant.

Analyze the following recent news about {symbol}.

Your job is NOT to make a final trading decision.
Instead, produce research that another synthesis agent can use.

For each article determine:
- relevance to {symbol}: 0 to 1
- sentiment: bullish, bearish, or neutral
- importance: low, medium, or high
- one-sentence explanation

Then provide:
- overall sentiment
- key bullish factors
- key bearish factors
- whether the news provides a meaningful short-term catalyst
- a concise research takeaway

Do not invent facts that are not present in the articles.

NEWS:
{json.dumps(articles, indent=2)}
"""

    response = client.models.generate_content(
        model="gemini-2.0-flash-lite",
        contents=prompt,
        config={
            "response_mime_type": "application/json",
            "response_schema": NewsAnalysis,
        }
    )

    # Parse the JSON response and return NewsAnalysis object
    try:
        analysis_json = json.loads(response.text)
        return NewsAnalysis(**analysis_json)
    except (json.JSONDecodeError, ValueError) as e:
        # If parsing fails, return a neutral analysis
        return NewsAnalysis(
            symbol=symbol,
            signal="neutral",
            confidence=0.0,
            bullish_points=[],
            bearish_points=[],
            catalysts=[],
            risks=["Failed to analyze news"],
            research_summary=f"News analysis failed: {str(e)}"
        )

if __name__ == "__main__":
    symbol = "NVDA"
    news = get_news(symbol, limit=5)
    print("\n========== NEWS ==========")
    print(news)
    print("\n========== NEWS RESEARCH ==========")
    analysis = analyze_news(symbol, news)
    print(analysis.model_dump_json(indent=2))