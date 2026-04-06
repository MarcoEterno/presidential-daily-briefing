import asyncio
import logging
from urllib.parse import quote

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config.settings import settings
from src.config.sources import GDELT_THEMES
from src.models import RawArticle

logger = logging.getLogger(__name__)

GDELT_DOC_API = "https://api.gdeltproject.org/api/v2/doc/doc"


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=3, min=10, max=60),
    retry=retry_if_exception_type((httpx.HTTPStatusError, httpx.ConnectTimeout, ValueError)),
)
async def _query_gdelt(client: httpx.AsyncClient, query: str, theme_name: str) -> list[dict]:
    params = {
        "query": f"{query} sourcelang:english",
        "mode": "ArtList",
        "maxrecords": str(settings.GDELT_MAX_RECORDS),
        "timespan": settings.GDELT_TIMESPAN,
        "format": "json",
        "sort": "HybridRel",
    }
    response = await client.get(GDELT_DOC_API, params=params, timeout=30)
    if response.status_code == 429:
        raise httpx.HTTPStatusError("Rate limited", request=response.request, response=response)
    response.raise_for_status()
    try:
        data = response.json()
    except Exception:
        raise ValueError(f"Non-JSON response: {response.text[:100]}")
    articles = data.get("articles", [])
    logger.info(f"GDELT [{theme_name}]: {len(articles)} articles")
    return articles


async def collect_gdelt() -> list[RawArticle]:
    """Collect articles from GDELT using a single broad query to minimize rate limiting."""
    articles: list[RawArticle] = []
    # Use a single broad geopolitical query instead of multiple themed queries
    # This avoids GDELT's strict rate limit (1 request per 5 seconds)
    broad_query = (
        "(theme:MILITARY OR theme:ARMED_CONFLICT OR theme:TERROR "
        "OR theme:DIPLOMACY OR theme:NEGOTIATIONS OR theme:ALLIANCE "
        "OR theme:SANCTIONS OR theme:ECON_TRADE "
        "OR theme:ELECTION OR theme:COUP OR theme:PROTEST)"
    )
    async with httpx.AsyncClient() as client:
        try:
            raw = await _query_gdelt(client, broad_query, "geopolitics")
            for item in raw:
                articles.append(RawArticle(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    source_name=item.get("domain", ""),
                    source_domain=item.get("domain", ""),
                    published_date=item.get("seendate", ""),
                    language=item.get("language", "English"),
                    source_type="gdelt",
                ))
        except Exception as e:
            logger.error(f"GDELT collection failed after retries: {e}")

    logger.info(f"GDELT total: {len(articles)} articles")
    return articles
