import json
import logging

import anthropic
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config.settings import settings
from src.models import RawArticle, RankedStory

logger = logging.getLogger(__name__)

RANKING_SYSTEM_PROMPT = """\
You are a senior intelligence analyst preparing the Presidential Daily Briefing. \
Your job is to identify the most geopolitically significant events from a list of headlines \
and provide initial assessment."""

RANKING_USER_PROMPT = """\
Below are {count} news headlines from the last 24 hours with their source domains.

Your task:
1. Group headlines that cover the same event
2. Rank the unique events by geopolitical importance
3. For each top event, provide a 2-sentence summary

Criteria for importance:
- Direct impact on international relations or security
- Scale of affected population
- Potential for escalation or cascading effects
- Strategic significance for major powers
- Economic implications at regional or global scale

Headlines:
{headlines}

Respond in JSON format ONLY (no markdown code fences):
{{
  "stories": [
    {{
      "rank": 1,
      "headline_indices": [3, 17, 42],
      "category": "conflict|diplomacy|economic|security|political",
      "importance_score": 9.5,
      "summary": "Two sentences describing the event and its significance."
    }}
  ]
}}

Select the top {max_stories} most important stories. \
Exclude celebrity news, sports, entertainment, and purely domestic stories \
unless they have clear international implications."""


def _format_headlines(articles: list[RawArticle]) -> str:
    lines = []
    for i, a in enumerate(articles):
        lines.append(f"{i}. [{a.source_name}] {a.title}")
    return "\n".join(lines)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError)),
)
async def _call_claude_ranking(headlines: str, count: int) -> list[dict]:
    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    prompt = RANKING_USER_PROMPT.format(
        count=count,
        headlines=headlines,
        max_stories=settings.MAX_STORIES,
    )

    response = await client.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=4096,
        system=RANKING_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    data = json.loads(text)
    return data["stories"]


async def rank_stories(articles: list[RawArticle]) -> list[RankedStory]:
    if not articles:
        logger.warning("No articles to rank")
        return []

    logger.info(f"Ranking {len(articles)} articles...")
    headlines = _format_headlines(articles)
    raw_stories = await _call_claude_ranking(headlines, len(articles))

    ranked: list[RankedStory] = []
    for story_data in raw_stories:
        story_articles = []
        for idx in story_data.get("headline_indices", []):
            if 0 <= idx < len(articles):
                story_articles.append(articles[idx])

        ranked.append(RankedStory(
            rank=story_data["rank"],
            category=story_data.get("category", "political"),
            importance_score=story_data.get("importance_score", 5.0),
            initial_summary=story_data.get("summary", ""),
            headline_indices=story_data.get("headline_indices", []),
            articles=story_articles,
        ))

    ranked.sort(key=lambda s: s.rank)
    logger.info(f"Ranked: {len(ranked)} top stories selected")
    return ranked
