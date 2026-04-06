import json
import logging
from datetime import datetime
from pathlib import Path

import anthropic
from jinja2 import Environment, FileSystemLoader
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config.settings import settings
from src.models import EnrichedStory, Briefing, StoryBrief, SourceRef

logger = logging.getLogger(__name__)

REPORT_SYSTEM_PROMPT = """\
You are the editor of the Presidential Daily Briefing. \
Write in an authoritative, concise style. Every sentence should convey actionable intelligence. \
No hedging, no caveats. Be specific with names, dates, and figures.

CITATION RULES: You MUST cite sources inline using [N] notation where N is the 1-based index \
from the SOURCES list provided with each brief. Every factual claim (numbers, dates, quotes, \
events) must have at least one citation. Place the citation immediately after the fact it supports. \
Example: "Brent crude surged 59% [1] while Iran maintained control of the Strait [2][3]." \
Use the source indices exactly as numbered in each brief's source list."""

REPORT_USER_PROMPT = """\
Compile the following intelligence briefs into a cohesive daily briefing.

DATE: {date}

INDIVIDUAL BRIEFS:
{briefs}

Generate a response in JSON format ONLY (no markdown code fences):
{{
  "executive_summary": "One paragraph (4-6 sentences). Start with the single most important development. Note interconnections between stories. Include [N] citations.",
  "stories": [
    {{
      "headline": "Concise, factual headline",
      "situation": "What happened and current status (2-3 sentences). Include [N] citations for every fact.",
      "context_and_analysis": "Historical context, economic drivers, and strategic significance (3-4 sentences). Include [N] citations.",
      "implications": "What this means for international order and key actors (2-3 sentences). Include [N] citations."
    }}
  ],
  "looking_ahead": [
    "Key event or deadline expected in next 48-72 hours",
    "Development to monitor across multiple stories",
    "Emerging trend that could reshape the landscape"
  ]
}}

IMPORTANT: Every factual claim MUST have an inline [N] citation referencing the source index from that brief's SOURCES list.

Produce {story_count} story briefs, ordered by importance. \
The executive summary should synthesize themes across stories, not merely list them."""


def _build_source_list(story: EnrichedStory) -> list[dict]:
    """Build a numbered source list combining articles and think tank refs."""
    sources = []
    for a in story.articles[:5]:
        sources.append({"title": a.title, "url": a.url, "source_name": a.source_name})
    for r in story.think_tank_references:
        sources.append({"title": r.title, "url": r.url, "source_name": r.source_name})
    return sources


def _format_briefs(stories: list[EnrichedStory]) -> str:
    parts = []
    for i, story in enumerate(stories, 1):
        sources = _build_source_list(story)
        source_lines = "\n".join(
            f"  [{j}] {s['source_name']}: {s['title']}" for j, s in enumerate(sources, 1)
        )
        parts.append(f"""--- BRIEF {i} (Category: {story.category}, Importance: {story.importance_score}) ---
Summary: {story.initial_summary}
Situation: {story.situation}
Historical Context: {story.historical_context}
Economic Factors: {story.economic_factors}
Strategic Implications: {story.strategic_implications}
Outlook: {story.outlook}

SOURCES (cite these inline using [N]):
{source_lines}""")
    return "\n\n".join(parts)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    retry=retry_if_exception_type((anthropic.RateLimitError, anthropic.APIStatusError)),
)
async def _generate_report_content(stories: list[EnrichedStory], date: str) -> dict:
    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
    prompt = REPORT_USER_PROMPT.format(
        date=date,
        briefs=_format_briefs(stories),
        story_count=len(stories),
    )

    response = await client.messages.create(
        model=settings.CLAUDE_MODEL,
        max_tokens=8192,
        system=REPORT_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )

    text = response.content[0].text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

    return json.loads(text)


def _build_story_contexts(briefing: Briefing) -> list[str]:
    """Pre-serialize story context JSON for embedding in HTML."""
    contexts = []
    for story in briefing.stories:
        ctx = {
            "headline": story.headline,
            "situation": story.situation,
            "context_and_analysis": story.context_and_analysis,
            "implications": story.implications,
            "deep_context": story.deep_context,
            "source_articles": [s.model_dump() for s in story.source_articles],
            "think_tank_refs": [s.model_dump() for s in story.think_tank_refs],
        }
        contexts.append(json.dumps(ctx))
    return contexts


def _render_html(briefing: Briefing, template_name: str) -> str:
    templates_dir = Path(__file__).parent.parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)
    template = env.get_template(template_name)
    story_contexts = _build_story_contexts(briefing)
    return template.render(briefing=briefing, story_contexts=story_contexts)


def _render_text(briefing: Briefing) -> str:
    lines = [
        f"PRESIDENTIAL DAILY BRIEFING — {briefing.date}",
        "=" * 60,
        "",
        "EXECUTIVE SUMMARY",
        "-" * 40,
        briefing.executive_summary,
        "",
    ]

    for story in briefing.stories:
        lines.extend([
            "=" * 60,
            story.headline.upper(),
            "-" * 40,
            "",
            "SITUATION:",
            story.situation,
            "",
            "CONTEXT & ANALYSIS:",
            story.context_and_analysis,
            "",
            "IMPLICATIONS:",
            story.implications,
            "",
        ])

    if briefing.looking_ahead:
        lines.extend([
            "=" * 60,
            "LOOKING AHEAD",
            "-" * 40,
        ])
        for item in briefing.looking_ahead:
            lines.append(f"  - {item}")

    return "\n".join(lines)


async def generate_briefing(enriched_stories: list[EnrichedStory]) -> Briefing:
    date = datetime.now().strftime("%B %d, %Y")
    logger.info(f"Generating briefing for {date}...")

    report_data = await _generate_report_content(enriched_stories, date)

    story_briefs = []
    for i, s in enumerate(report_data.get("stories", [])):
        # Re-attach source data from enriched stories for Q&A grounding
        source_articles = []
        think_tank_refs = []
        deep_context = ""
        if i < len(enriched_stories):
            es = enriched_stories[i]
            source_articles = [
                SourceRef(title=a.title, url=a.url, source_name=a.source_name)
                for a in es.articles[:5]
            ]
            think_tank_refs = [
                SourceRef(title=r.title, url=r.url, source_name=r.source_name)
                for r in es.think_tank_references
            ]
            deep_context = (
                f"Situation: {es.situation}\n"
                f"Historical Context: {es.historical_context}\n"
                f"Economic Factors: {es.economic_factors}\n"
                f"Strategic Implications: {es.strategic_implications}\n"
                f"Outlook: {es.outlook}"
            )

        story_briefs.append(StoryBrief(
            headline=s.get("headline", ""),
            situation=s.get("situation", ""),
            context_and_analysis=s.get("context_and_analysis", ""),
            implications=s.get("implications", ""),
            source_articles=source_articles,
            think_tank_refs=think_tank_refs,
            deep_context=deep_context,
        ))

    briefing = Briefing(
        date=date,
        executive_summary=report_data.get("executive_summary", ""),
        stories=story_briefs,
        looking_ahead=report_data.get("looking_ahead", []),
        generation_metadata={
            "model": settings.CLAUDE_MODEL,
            "story_count": len(story_briefs),
            "generated_at": datetime.now().isoformat(),
        },
    )

    # Render HTML and text
    try:
        briefing.html_content = _render_html(briefing, "briefing_web.html")
    except Exception as e:
        logger.error(f"HTML rendering failed: {e}")
        briefing.html_content = f"<pre>{_render_text(briefing)}</pre>"

    briefing.text_content = _render_text(briefing)

    logger.info(f"Briefing generated: {len(story_briefs)} stories")
    return briefing
