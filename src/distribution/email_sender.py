import asyncio
import json
import logging
from pathlib import Path

import resend
from jinja2 import Environment, FileSystemLoader

from src.config.settings import settings
from src.models import Briefing

logger = logging.getLogger(__name__)


def _render_email_html(briefing: Briefing) -> str:
    templates_dir = Path(__file__).parent.parent.parent / "templates"
    env = Environment(loader=FileSystemLoader(str(templates_dir)), autoescape=True)
    template = env.get_template("briefing_email.html")
    return template.render(briefing=briefing)


def _load_subscribers() -> list[dict]:
    path = Path(settings.SUBSCRIBERS_FILE)
    if not path.exists():
        logger.warning(f"Subscribers file not found: {path}")
        return []
    data = json.loads(path.read_text())
    return [s for s in data if s.get("active", True)]


async def send_briefing_email(briefing: Briefing) -> dict[str, bool]:
    if not settings.RESEND_API_KEY:
        logger.info("Email: skipped (no RESEND_API_KEY)")
        return {}

    resend.api_key = settings.RESEND_API_KEY
    subscribers = _load_subscribers()
    if not subscribers:
        logger.warning("Email: no active subscribers")
        return {}

    html = _render_email_html(briefing)
    subject = f"Presidential Daily Briefing — {briefing.date}"
    results: dict[str, bool] = {}

    for subscriber in subscribers:
        email = subscriber["email"]
        try:
            await asyncio.to_thread(
                resend.Emails.send,
                {
                    "from": settings.RESEND_FROM_EMAIL,
                    "to": [email],
                    "subject": subject,
                    "html": html,
                },
            )
            results[email] = True
            logger.info(f"Email sent to {email}")
        except Exception as e:
            results[email] = False
            logger.error(f"Email failed for {email}: {e}")
        # Rate limit: max 2 per second
        await asyncio.sleep(0.5)

    sent = sum(1 for v in results.values() if v)
    logger.info(f"Email: {sent}/{len(results)} delivered")
    return results
