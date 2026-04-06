from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config.settings import settings
from src.models import RankedStory, EnrichedStory, RawArticle, ThinkTankReference
from src.utils import extract_article_text, truncate_text

logger = logging.getLogger(__name__)

RESEARCH_SYSTEM_PROMPT = """\
You are a senior intelligence analyst providing deep context for the Presidential Daily Briefing. \
Your analysis must be grounded in the provided source material. Be specific with names, dates, and figures. \
Write as if briefing a head of state. No hedging language."""

RESEARCH_USER_PROMPT = """\
STORY TO ANALYZE:
{summary}

SOURCE ARTICLES:
{source_articles}

{think_tank_section}

Provide a structured intelligence brief with these exact sections:

1. SITUATION (3-4 sentences): What happened, who is involved, current status
2. HISTORICAL_CONTEXT (2-3 sentences): How this connects to prior events and trends
3. ECONOMIC_FACTORS (2-3 sentences): Economic drivers, trade implications, resource competition
4. STRATEGIC_IMPLICATIONS (3-4 sentences): What this means for major power dynamics, regional stability, alliance structures
5. OUTLOOK (2-3 sentences): Most likely trajectory and key variables to watch

Respond in JSON format ONLY (no markdown code fences):
{{
  "situation": "...",
  "historical_context": "...",
  "economic_factors": "...",
  "strategic_implications": "...",
  "outlook": "..."
}}"""


def _find_think_tank_refs(story: RankedStory, all_rss_articles: list[RawArticle]) -> list[RawArticle]:
    """Find think tank articles related to this story by keyword matching."""
    if not all_rss_articles:
        return []

    story_words = set()
    for article in story.articles:
        for word in article.title.lower().split():
            if len(word) > 4:
                story_words.add(word)
    for word in story.initial_summary.lower().split():
        if len(word) > 4:
            story_words.add(word)

    scored: list[tuple[int, RawArticle]] = []
    for rss_article in all_rss_articles:
        title_words = set(rss_article.title.lower().split())
        snippet_words = set(rss_article.content_snippet.lower().split())
        overlap = len(story_words & (title_words | snippet_words))
        if overlap >= 2:
            scored.append((overlap, rss_article))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [article for _, article in scored[:3]]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError)),
)
async def _research_single_story(
    story: RankedStory,
    think_tank_refs: list[RawArticle],
) -> EnrichedStory:
    # Extract text from top source articles (max 2)
    source_texts = []
    for article in story.articles[:2]:
        text = await asyncio.to_thread(extract_article_text, article.url)
        if text:
            source_texts.append(f"[{article.source_name}] {article.title}\n{truncate_text(text)}")
        else:
            source_texts.append(f"[{article.source_name}] {article.title}\n{article.content_snippet}")

    # Extract think tank text
    think_tank_texts = []
    tt_references: list[ThinkTankReference] = []
    for ref_article in think_tank_refs:
        text = await asyncio.to_thread(extract_article_text, ref_article.url)
        if text:
            think_tank_texts.append(
                f"[{ref_article.source_name}] {ref_article.title}\n{truncate_text(text)}"
            )
        tt_references.append(ThinkTankReference(
            title=ref_article.title,
            url=ref_article.url,
            source_name=ref_article.source_name,
            published_date=ref_article.published_date,
            relevance_snippet=ref_article.content_snippet[:200],
        ))

    think_tank_section = ""
    if think_tank_texts:
        think_tank_section = "THINK TANK ANALYSIS:\n" + "\n\n---\n\n".join(think_tank_texts)
    else:
        think_tank_section = "THINK TANK ANALYSIS: No directly relevant think tank publications found for this story."

    prompt = RESEARCH_USER_PROMPT.format(
        summary=story.initial_summary,
        source_articles="\n\n---\n\n".join(source_texts) if source_texts else "No article text available.",
        think_tank_section=think_tank_section,
    )

    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    response = await client.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=2048,
        system=RESEARCH_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    data = json.loads(text)

    return EnrichedStory(
        rank=story.rank,
        category=story.category,
        importance_score=story.importance_score,
        initial_summary=story.initial_summary,
        articles=story.articles,
        situation=data.get("situation", ""),
        historical_context=data.get("historical_context", ""),
        economic_factors=data.get("economic_factors", ""),
        strategic_implications=data.get("strategic_implications", ""),
        outlook=data.get("outlook", ""),
        think_tank_references=tt_references,
    )


async def research_stories(
    ranked_stories: list[RankedStory],
    all_rss_articles: Optional[list[RawArticle]] = None,
) -> list[EnrichedStory]:
    if not ranked_stories:
        return []

    all_rss = all_rss_articles or []
    logger.info(f"Researching {len(ranked_stories)} stories...")

    tasks = []
    for story in ranked_stories:
        refs = _find_think_tank_refs(story, all_rss)
        tasks.append(_research_single_story(story, refs))

    results = await asyncio.gather(*tasks, return_exceptions=True)

    enriched: list[EnrichedStory] = []
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Research failed for story {i + 1}: {result}")
            # Fallback: create enriched story with just the summary
            story = ranked_stories[i]
            enriched.append(EnrichedStory(
                rank=story.rank,
                category=story.category,
                importance_score=story.importance_score,
                initial_summary=story.initial_summary,
                articles=story.articles,
                situation=story.initial_summary,
            ))
        else:
            enriched.append(result)

    enriched.sort(key=lambda s: s.rank)
    logger.info(f"Research complete: {len(enriched)} enriched stories")
    return enriched
