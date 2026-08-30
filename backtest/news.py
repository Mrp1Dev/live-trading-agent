from alpaca.data.historical.news import NewsClient
from alpaca.data.requests import NewsRequest
from datetime import datetime

# Initialize client
client = NewsClient(
    api_key="YOUR_API_KEY",
    secret_key="YOUR_API_SECRET"
)

# Get latest 10 news articles for AAPL
request = NewsRequest(
    symbols="AAPL",
    limit=10
)
news = client.get_news(request)

for article in news.news:
    print(f"\n{'='*60}")
    print(f"  ID:         {article.id}")
    print(f"  Headline:   {article.headline}")
    print(f"  Summary:    {article.summary[:100]}...")
    print(f"  Author:     {article.author}")
    print(f"  Source:     {article.source}")
    print(f"  Created:    {article.created_at}")
    print(f"  URL:        {article.url}")
    print(f"  Symbols:    {article.symbols}")
    print(f"  Images:     {len(article.images)} image(s)")

# Get news for multiple symbols over a date range
request = NewsRequest(
    symbols=["AAPL", "TSLA", "MSFT"],
    start=datetime(2024, 1, 1),
    end=datetime(2024, 1, 31),
    limit=50,
    sort="desc"  # newest first
)
news = client.get_news(request)

for article in news.news:
    print(f"[{article.created_at}] [{article.source}] {article.headline}")
    print(f"  Symbols: {', '.join(article.symbols)}")
    print()

def get_all_news(client, symbols, start, end, include_content=False):
    """Fetch all news articles with automatic pagination."""
    all_articles = []
    page_token = None

    while True:
        request = NewsRequest(
            symbols=symbols,
            start=start,
            end=end,
            limit=50,  # max per page
            sort="asc",
            include_content=include_content,
            page_token=page_token
        )
        response = client.get_news(request)

        all_articles.extend(response.news)
        print(f"  Fetched {len(response.news)} articles (Total: {len(all_articles)})")

        # Check for more pages
        page_token = response.next_page_token
        if not page_token:
            break

    return all_articles


# Usage
articles = get_all_news(
    client=client,
    symbols=["AAPL", "TSLA"],
    start=datetime(2024, 1, 1),
    end=datetime(2024, 3, 31),
    include_content=True
)

print(f"\nTotal articles retrieved: {len(articles)}")