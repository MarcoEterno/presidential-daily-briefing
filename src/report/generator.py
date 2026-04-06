import json
import logging
from datetime import datetime
from pathlib import Path

import anthropic
from jinja2 import Environment, FileSystemLoader
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config.settings import settings
from src.models import EnrichedStory, Briefing, StoryBrief

logger = logging.getLogger(__name__)

REPORT_SYSTEM_PROMPT = """\
You are the editor of the Presidential Daily Briefing. \
Write in an authoritative, concise style. Every sentence should convey actionable intelligence. \
No hedging, no caveats. Be specific with names, dates, and figures."""

REPORT_USER_PROMPT = """\
Compile the following intelligence briefs into a cohesive daily briefing.

DATE: {date}

INDIVIDUAL BRIEFS:
{briefs}

Generate a response in JSON format ONLY (no markdown code fences):
{{
  "executive_summary": "One paragraph (4-6 sentences). Start with the single most important development. Note interconnections between stories.",
  "stories": [
    {{
      "headline": "Concise, factual headline",
      "situation": "What happened and current status (2-3 sentences)",
      "context_and_analysis": "Historical context, economic drivers, and strategic significance (3-4 sentences)",
      "implications": "What this means for international order and key actors (2-3 sentences)",
      "watch_items": ["What to monitor in next 48-72h", "Key variable or decision point", "Potential escalation trigger"]
    }}
  ],
  "looking_ahead": [
    "Key event or deadline expected in next 48-72 hours",
    "Development to monitor across multiple stories",
    "Emerging trend that could reshape the landscape"
  ]
}}

Produce {story_count} story briefs, ordered by importance. \
The executive summary should synthesize themes across stories, not merely list them."""


def _format_briefs(stories: list[EnrichedStory]) -> str:
    parts = []
    for i, story in enumerate(stories, 1):
        parts.append(f"""--- BRIEF {i} (Category: {story.category}, Importance: {story.importance_score}) ---
Summary: {story.initial_summary}
Situation: {story.situation}
Historical Context: {story.historical_context}
Economic Factors: {story.economic_factors}
Strategic Implications: {story.strategic_implications}
Outlook: {story.outlook}
Sources: {', '.join(a.source_name for a in story.articles[:3])}
Think Tank References: {', '.join(r.source_name + ': ' + r.title for r in story.think_tank_references[:2]) or 'None'}""")
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


def _render_html(briefing: Briefing, template_name: str) -> str:
    templates_dir = Path(__file__).parent.parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)
    template = env.get_template(template_name)
    return template.render(briefing=briefing)


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
            "WATCH ITEMS:",
        ])
        for item in story.watch_items:
            lines.append(f"  - {item}")
        lines.append("")

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
    for s in report_data.get("stories", []):
        story_briefs.append(StoryBrief(
            headline=s.get("headline", ""),
            situation=s.get("situation", ""),
            context_and_analysis=s.get("context_and_analysis", ""),
            implications=s.get("implications", ""),
            watch_items=s.get("watch_items", []),
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
