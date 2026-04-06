from __future__ import annotations

import argparse
import asyncio
import logging
import time
from datetime import datetime
from typing import Optional

from src.collectors import collect_all
from src.analysis.ranker import rank_stories
from src.analysis.researcher import research_stories
from src.report.generator import generate_briefing
from src.distribution.email_sender import send_briefing_email
from src.distribution.web_publisher import publish_to_web
from src.models import RawArticle, RankedStory, EnrichedStory
from src.utils import save_intermediate, load_intermediate

logger = logging.getLogger(__name__)


def setup_logging(verbose: bool = False):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


async def run_pipeline(
    from_stage: str = "collect",
    dry_run: bool = False,
    date: Optional[str] = None,
    verbose: bool = False,
):
    setup_logging(verbose)
    start = time.time()
    date_str = date or datetime.now().strftime("%Y-%m-%d")
    logger.info(f"Starting pipeline for {date_str} (from_stage={from_stage}, dry_run={dry_run})")

    stages = ["collect", "rank", "research", "report", "distribute"]
    start_idx = stages.index(from_stage) if from_stage in stages else 0

    # Stage 1: Collect
    if start_idx <= 0:
        logger.info("=== STAGE: Collect ===")
        raw_articles = await collect_all()
        save_intermediate("raw_articles", raw_articles, date_str)
    else:
        data = load_intermediate("raw_articles", date_str)
        raw_articles = [RawArticle(**a) for a in data]
        logger.info(f"Loaded {len(raw_articles)} raw articles from cache")

    # Separate RSS articles for think tank matching in research stage
    rss_articles = [a for a in raw_articles if a.source_type == "rss"]

    # Stage 2: Rank
    if start_idx <= 1:
        logger.info("=== STAGE: Rank ===")
        ranked_stories = await rank_stories(raw_articles)
        save_intermediate("ranked_stories", ranked_stories, date_str)
    else:
        data = load_intermediate("ranked_stories", date_str)
        ranked_stories = [RankedStory(**s) for s in data]
        logger.info(f"Loaded {len(ranked_stories)} ranked stories from cache")

    # Stage 3: Research
    if start_idx <= 2:
        logger.info("=== STAGE: Research ===")
        enriched_stories = await research_stories(ranked_stories, rss_articles)
        save_intermediate("enriched_stories", enriched_stories, date_str)
    else:
        data = load_intermediate("enriched_stories", date_str)
        enriched_stories = [EnrichedStory(**s) for s in data]
        logger.info(f"Loaded {len(enriched_stories)} enriched stories from cache")

    # Stage 4: Report
    if start_idx <= 3:
        logger.info("=== STAGE: Report ===")
        briefing = await generate_briefing(enriched_stories)
        save_intermediate("briefing", briefing, date_str)
    else:
        data = load_intermediate("briefing", date_str)
        from src.models import Briefing
        briefing = Briefing(**data)
        logger.info("Loaded briefing from cache")

    # Stage 5: Distribute
    if start_idx <= 4 and not dry_run:
        logger.info("=== STAGE: Distribute ===")
        email_results = await send_briefing_email(briefing)
        web_ok = publish_to_web(briefing)
        logger.info(f"Distribution: email={email_results}, web={'ok' if web_ok else 'failed'}")
    elif dry_run:
        logger.info("=== STAGE: Distribute (skipped — dry run) ===")
        # Still write HTML locally for inspection
        from pathlib import Path
        output_dir = Path("output") / date_str
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "briefing.html").write_text(briefing.html_content)
        (output_dir / "briefing.txt").write_text(briefing.text_content)
        logger.info(f"Dry run: saved HTML/text to {output_dir}")

    elapsed = time.time() - start
    logger.info(f"Pipeline complete in {elapsed:.1f}s — {len(enriched_stories)} stories")


def main():
    parser = argparse.ArgumentParser(description="Presidential Daily Briefing Generator")
    parser.add_argument("--from-stage", default="collect", choices=["collect", "rank", "research", "report", "distribute"])
    parser.add_argument("--dry-run", action="store_true", help="Skip email/web distribution")
    parser.add_argument("--date", default=None, help="Date to process (YYYY-MM-DD)")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    asyncio.run(run_pipeline(
        from_stage=args.from_stage,
        dry_run=args.dry_run,
        date=args.date,
        verbose=args.verbose,
    ))


if __name__ == "__main__":
    main()
