import asyncio
import logging
from datetime import datetime, timedelta, timezone

import feedparser
import httpx

from src.config.sources import THINK_TANK_FEEDS
from src.models import RawArticle

logger = logging.getLogger(__name__)

FEED_TIMEOUT = 10
LOOKBACK_HOURS = 168  # 7 days — think tank content is less frequent


async def _fetch_feed(client: httpx.AsyncClient, source) -> list[RawArticle]:
    try:
        response = await client.get(source.url, timeout=FEED_TIMEOUT, follow_redirects=True)
        response.raise_for_status()
    except Exception as e:
        logger.warning(f"RSS [{source.name}] fetch failed: {e}")
        return []

    feed = await asyncio.to_thread(feedparser.parse, response.text)
    if feed.bozo and not feed.entries:
        logger.warning(f"RSS [{source.name}] parse error: {feed.bozo_exception}")
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=LOOKBACK_HOURS)
    articles: list[RawArticle] = []

    for entry in feed.entries:
        # Parse date
        published = ""
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            try:
                dt = datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
                if dt < cutoff:
                    continue
                published = dt.isoformat()
            except Exception:
                pass
        elif hasattr(entry, "updated_parsed") and entry.updated_parsed:
            try:
                dt = datetime(*entry.updated_parsed[:6], tzinfo=timezone.utc)
                if dt < cutoff:
                    continue
                published = dt.isoformat()
            except Exception:
                pass

        summary = ""
        if hasattr(entry, "summary"):
            summary = entry.summary[:500]

        articles.append(RawArticle(
            title=entry.get("title", ""),
            url=entry.get("link", ""),
            source_name=source.name,
            source_domain=source.url,
            published_date=published,
            content_snippet=summary,
            source_type="rss",
        ))

    logger.info(f"RSS [{source.name}]: {len(articles)} articles (last {LOOKBACK_HOURS}h)")
    return articles


async def collect_rss_feeds() -> list[RawArticle]:
    all_articles: list[RawArticle] = []
    async with httpx.AsyncClient(headers={"User-Agent": "PDB-Bot/1.0"}) as client:
        tasks = [_fetch_feed(client, source) for source in THINK_TANK_FEEDS]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"RSS feed task failed: {result}")
                continue
            all_articles.extend(result)

    logger.info(f"RSS total: {len(all_articles)} articles from {len(THINK_TANK_FEEDS)} feeds")
    return all_articles
