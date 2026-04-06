import asyncio
import logging

from src.collectors.gdelt import collect_gdelt
from src.collectors.rss_feeds import collect_rss_feeds
from src.collectors.news_api import collect_newsapi
from src.models import RawArticle
from src.utils import normalize_url

logger = logging.getLogger(__name__)


async def collect_all() -> list[RawArticle]:
    results = await asyncio.gather(
        collect_gdelt(),
        collect_rss_feeds(),
        collect_newsapi(),
        return_exceptions=True,
    )

    all_articles: list[RawArticle] = []
    source_names = ["GDELT", "RSS Feeds", "NewsAPI"]
    for name, result in zip(source_names, results):
        if isinstance(result, Exception):
            logger.error(f"{name} collection failed: {result}")
            continue
        logger.info(f"{name}: collected {len(result)} articles")
        all_articles.extend(result)

    # Deduplicate by normalized URL
    seen_urls: set[str] = set()
    unique: list[RawArticle] = []
    for article in all_articles:
        norm = normalize_url(article.url)
        if norm not in seen_urls:
            seen_urls.add(norm)
            unique.append(article)

    logger.info(f"Total unique articles: {len(unique)} (from {len(all_articles)} raw)")
    return unique
