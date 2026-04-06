import logging

import httpx

from src.config.settings import settings
from src.config.sources import NEWS_KEYWORDS
from src.models import RawArticle

logger = logging.getLogger(__name__)

NEWSAPI_URL = "https://newsapi.org/v2/everything"


async def collect_newsapi() -> list[RawArticle]:
    if not settings.NEWSAPI_KEY:
        logger.info("NewsAPI: skipped (no API key)")
        return []

    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(
                NEWSAPI_URL,
                params={
                    "q": NEWS_KEYWORDS,
                    "language": "en",
                    "sortBy": "relevancy",
                    "pageSize": "50",
                    "apiKey": settings.NEWSAPI_KEY,
                },
                timeout=15,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as e:
            logger.error(f"NewsAPI request failed: {e}")
            return []

    articles: list[RawArticle] = []
    for item in data.get("articles", []):
        articles.append(RawArticle(
            title=item.get("title", ""),
            url=item.get("url", ""),
            source_name=item.get("source", {}).get("name", ""),
            source_domain=item.get("source", {}).get("name", ""),
            published_date=item.get("publishedAt", ""),
            content_snippet=item.get("description", "")[:500],
            source_type="newsapi",
        ))

    logger.info(f"NewsAPI: {len(articles)} articles")
    return articles
