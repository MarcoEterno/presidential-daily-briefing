from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

import anthropic
from jinja2 import Environment, FileSystemLoader
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from src.config.settings import settings
from src.models import EnrichedStory, Briefing, StoryBrief, SourceRef
from src.utils import extract_article_text, truncate_text

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Stage 1: Generate structured briefing (no inline citations — those come later)
# ---------------------------------------------------------------------------

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
      "implications": "What this means for international order and key actors (2-3 sentences)"
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
        source_names = ", ".join(a.source_name for a in story.articles[:3])
        tt_names = ", ".join(
            f"{r.source_name}: {r.title}" for r in story.think_tank_references[:2]
        ) or "None"
        parts.append(
            f"--- BRIEF {i} (Category: {story.category}, "
            f"Importance: {story.importance_score}) ---\n"
            f"Summary: {story.initial_summary}\n"
            f"Situation: {story.situation}\n"
            f"Historical Context: {story.historical_context}\n"
            f"Economic Factors: {story.economic_factors}\n"
            f"Strategic Implications: {story.strategic_implications}\n"
            f"Outlook: {story.outlook}\n"
            f"Sources: {source_names}\n"
            f"Think Tank References: {tt_names}"
        )
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


# ---------------------------------------------------------------------------
# Stage 2: Ground every story with the Anthropic Citations API
#
# For each story we:
#   1. Fetch the actual source article text (trafilatura)
#   2. Pass the texts as `document` content blocks
#   3. Ask Claude to reproduce the briefing text with citations enabled
#   4. Parse the response into text + [N] markers verified by the API
# ---------------------------------------------------------------------------

GROUNDING_SYSTEM = """\
You rewrite intelligence briefing text, grounding it in provided source documents. \
Rules: \
1. Output ONLY the rewritten text — no commentary, no preamble, no meta-discussion. \
2. Keep the three section headers exactly: SITUATION: / CONTEXT AND ANALYSIS: / IMPLICATIONS: \
3. Keep the SAME facts, structure, and topic as the input. Do NOT change the story's topic. \
4. If a fact is supported by the source documents, include it. If not, still include it without a citation — that's fine. \
5. Do NOT say things like "the document does not contain" or "I should clarify". \
6. CRITICALLY IMPORTANT: For each fact, cite EVERY source document that supports it, not just one. \
If three documents report the same event, cite all three. Write claims so they draw from \
ALL relevant documents, not just the first one."""

GROUNDING_PROMPT = """\
Rewrite this briefing grounded in the source documents. Output ONLY the three sections, nothing else. \
For each fact, cite ALL source documents that contain supporting evidence — not just one.

{story_text}"""


async def _extract_source_docs(
    enriched: EnrichedStory,
) -> tuple[list[dict], list[SourceRef], list[str]]:
    """Extract article text and build document blocks for the citations API.
    Returns (document_blocks, source_map, raw_texts) — raw_texts used for cross-citation."""
    documents: list[dict] = []
    source_map: list[SourceRef] = []
    raw_texts: list[str] = []

    all_sources = []
    for a in enriched.articles[:4]:
        all_sources.append((a.title, a.url, a.source_name))
    for r in enriched.think_tank_references[:3]:
        all_sources.append((r.title, r.url, r.source_name))

    async def _extract(title, url, source_name):
        text = await asyncio.to_thread(extract_article_text, url)
        return title, url, source_name, text

    results = await asyncio.gather(
        *[_extract(t, u, s) for t, u, s in all_sources],
        return_exceptions=True,
    )

    for result in results:
        if isinstance(result, Exception):
            continue
        title, url, source_name, text = result
        if not text:
            continue
        truncated = truncate_text(text, 4000)
        documents.append({
            "type": "document",
            "source": {
                "type": "text",
                "media_type": "text/plain",
                "data": truncated,
            },
            "title": f"{source_name}: {title}",
            "citations": {"enabled": True},
        })
        source_map.append(SourceRef(title=title, url=url, source_name=source_name))
        raw_texts.append(truncated.lower())

    return documents, source_map, raw_texts


def _parse_citation_response(
    content_blocks: list,
    source_map: list[SourceRef],
    raw_texts: list[str],
) -> tuple[str, list[SourceRef]]:
    """
    Walk the response content blocks produced by the citations API.
    Build a single string with [N] markers inserted after every cited span.

    Cross-citation: when the API cites document X, also check whether the
    cited_text appears in documents Y, Z and add those citations too.
    This ensures facts reported by multiple outlets get multi-source tags.
    """
    result = ""

    for block in content_blocks:
        if not hasattr(block, "text"):
            continue
        result += block.text
        citations = getattr(block, "citations", None) or []
        if citations:
            all_indices: set[int] = set()
            for c in citations:
                if not hasattr(c, "document_index"):
                    continue
                all_indices.add(c.document_index)
                # Cross-match: check other docs for the same cited text
                cited_text = getattr(c, "cited_text", "")
                if cited_text and len(cited_text) > 20:
                    snippet = cited_text[:80].lower()
                    for other_idx, doc_text in enumerate(raw_texts):
                        if other_idx != c.document_index and snippet in doc_text:
                            all_indices.add(other_idx)

            markers = "".join(
                f"[{idx + 1}]"
                for idx in sorted(all_indices)
                if idx < len(source_map)
            )
            if markers:
                result += f" {markers}"

    return result, source_map


def _split_sections(text: str) -> dict[str, str]:
    """Split grounded text back into situation / context / implications."""
    mapping = {
        "SITUATION:": "situation",
        "CONTEXT AND ANALYSIS:": "context_and_analysis",
        "IMPLICATIONS:": "implications",
    }
    sections: dict[str, str] = {}
    current_key: Optional[str] = None
    buf: list[str] = []

    for line in text.split("\n"):
        stripped = line.strip()
        matched = False
        for header, key in mapping.items():
            if stripped.upper().startswith(header.upper()):
                if current_key is not None:
                    sections[current_key] = "\n".join(buf).strip()
                current_key = key
                rest = stripped[len(header):].strip()
                buf = [rest] if rest else []
                matched = True
                break
        if not matched:
            buf.append(line)

    if current_key is not None:
        sections[current_key] = "\n".join(buf).strip()

    return sections


async def _ground_single_story(
    client: anthropic.AsyncAnthropic,
    story: StoryBrief,
    enriched: EnrichedStory,
) -> StoryBrief:
    """Ground one story against its source documents using the citations API."""
    documents, source_map, raw_texts = await _extract_source_docs(enriched)
    logger.info(f"Extracted {len(documents)} source docs for '{story.headline[:50]}...'")
    if not documents:
        logger.warning(f"No source text extracted for '{story.headline}' — skipping grounding")
        return story

    story_text = (
        f"SITUATION:\n{story.situation}\n\n"
        f"CONTEXT AND ANALYSIS:\n{story.context_and_analysis}\n\n"
        f"IMPLICATIONS:\n{story.implications}"
    )
    user_content = documents + [{
        "type": "text",
        "text": GROUNDING_PROMPT.format(story_text=story_text),
    }]

    try:
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=2048,
            system=GROUNDING_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as e:
        logger.error(f"Citations API call failed for '{story.headline}': {e}")
        return story

    # Count citations in response
    total_cites = sum(
        len(getattr(b, "citations", []) or [])
        for b in response.content
    )
    logger.info(
        f"Grounded '{story.headline[:50]}...': "
        f"{len(documents)} docs, {total_cites} citations, "
        f"{len(response.content)} blocks"
    )

    cited_text, cited_sources = _parse_citation_response(response.content, source_map, raw_texts)
    sections = _split_sections(cited_text)

    if sections.get("situation"):
        story.situation = sections["situation"]
    if sections.get("context_and_analysis"):
        story.context_and_analysis = sections["context_and_analysis"]
    if sections.get("implications"):
        story.implications = sections["implications"]

    # Update source lists to the ones we actually have text for
    story.source_articles = cited_sources
    story.think_tank_refs = []  # merged into source_articles above

    return story


async def _ground_with_citations(
    briefing: Briefing,
    enriched_stories: list[EnrichedStory],
) -> Briefing:
    """Ground every story in the briefing against real source documents."""
    logger.info("Grounding stories with citations API...")
    client = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

    async def _passthrough(s: StoryBrief) -> StoryBrief:
        return s

    tasks = []
    for i, story in enumerate(briefing.stories):
        if i < len(enriched_stories):
            tasks.append(_ground_single_story(client, story, enriched_stories[i]))
        else:
            tasks.append(_passthrough(story))

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            logger.error(f"Grounding failed for story {i}: {result}")
        else:
            briefing.stories[i] = result

    cited_count = sum(
        s.situation.count("[") + s.context_and_analysis.count("[") + s.implications.count("[")
        for s in briefing.stories
    )
    logger.info(f"Grounding complete: {cited_count} citations across {len(briefing.stories)} stories")
    return briefing


# ---------------------------------------------------------------------------
# Rendering helpers
# ---------------------------------------------------------------------------

def _build_story_contexts(briefing: Briefing) -> list[str]:
    """Pre-serialize story context JSON for embedding in HTML (Q&A grounding)."""
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
        lines.extend(["=" * 60, "LOOKING AHEAD", "-" * 40])
        for item in briefing.looking_ahead:
            lines.append(f"  - {item}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def generate_briefing(enriched_stories: list[EnrichedStory]) -> Briefing:
    date = datetime.now().strftime("%B %d, %Y")
    logger.info(f"Generating briefing for {date}...")

    # Step 1: Generate structured briefing (no citations yet)
    report_data = await _generate_report_content(enriched_stories, date)

    story_briefs = []
    for i, s in enumerate(report_data.get("stories", [])):
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

    # Step 2: Ground citations against real source text
    briefing = await _ground_with_citations(briefing, enriched_stories)

    # Step 3: Render HTML and plain text
    try:
        briefing.html_content = _render_html(briefing, "briefing_web.html")
    except Exception as e:
        logger.error(f"HTML rendering failed: {e}")
        briefing.html_content = f"<pre>{_render_text(briefing)}</pre>"

    briefing.text_content = _render_text(briefing)

    logger.info(f"Briefing generated: {len(story_briefs)} stories")
    return briefing
