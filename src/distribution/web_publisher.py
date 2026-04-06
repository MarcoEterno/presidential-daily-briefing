import logging
import subprocess
from datetime import datetime
from pathlib import Path

from src.models import Briefing

logger = logging.getLogger(__name__)

DOCS_DIR = Path("docs")


def _write_archive_index():
    archive_dir = DOCS_DIR / "archive"
    archive_files = sorted(archive_dir.glob("*.html"), reverse=True)
    # Exclude index.html itself
    archive_files = [f for f in archive_files if f.name != "index.html"]

    entries = []
    for f in archive_files:
        date_str = f.stem  # e.g., "2026-04-05"
        entries.append(f'<li><a href="{f.name}">{date_str}</a></li>')

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Briefing Archive</title>
    <style>
        body {{ font-family: Georgia, serif; max-width: 600px; margin: 40px auto; padding: 0 20px; color: #1a1a1a; background: #f5f2eb; }}
        h1 {{ color: #1a3a5c; font-size: 22px; letter-spacing: 2px; text-transform: uppercase; border-bottom: 3px double #1a3a5c; padding-bottom: 12px; }}
        ul {{ list-style: none; padding: 0; }}
        li {{ padding: 8px 0; border-bottom: 1px solid #e0e0e0; }}
        a {{ color: #1a3a5c; text-decoration: none; font-size: 16px; }}
        a:hover {{ text-decoration: underline; }}
        .back {{ margin-top: 24px; font-size: 14px; }}
    </style>
</head>
<body>
    <h1>Briefing Archive</h1>
    <ul>
        {"".join(entries) if entries else "<li>No archived briefings yet.</li>"}
    </ul>
    <p class="back"><a href="../">Latest Briefing</a></p>
</body>
</html>"""

    (archive_dir / "index.html").write_text(html)


def publish_to_web(briefing: Briefing) -> bool:
    try:
        DOCS_DIR.mkdir(parents=True, exist_ok=True)
        (DOCS_DIR / "archive").mkdir(exist_ok=True)

        # Write latest briefing
        (DOCS_DIR / "index.html").write_text(briefing.html_content)

        # Write dated archive copy
        date_str = datetime.now().strftime("%Y-%m-%d")
        (DOCS_DIR / "archive" / f"{date_str}.html").write_text(briefing.html_content)

        # Update archive index
        _write_archive_index()

        logger.info(f"Web: published to {DOCS_DIR}/index.html and archive/{date_str}.html")

        # Git commit and push (if in a git repo with a remote)
        _git_push(date_str)
        return True
    except Exception as e:
        logger.error(f"Web publishing failed: {e}")
        return False


def _git_push(date_str: str):
    try:
        # Check if we're in a git repo
        subprocess.run(["git", "rev-parse", "--git-dir"], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        logger.info("Web: not a git repo, skipping push")
        return

    try:
        subprocess.run(["git", "add", "docs/"], capture_output=True, check=True)

        result = subprocess.run(["git", "status", "--porcelain", "docs/"], capture_output=True, text=True)
        if not result.stdout.strip():
            logger.info("Web: no changes to commit")
            return

        subprocess.run(
            ["git", "commit", "-m", f"Briefing {date_str}"],
            capture_output=True,
            check=True,
        )
        subprocess.run(["git", "push"], capture_output=True, check=True)
        logger.info(f"Web: pushed briefing {date_str} to remote")
    except subprocess.CalledProcessError as e:
        logger.warning(f"Web: git push failed: {e.stderr}")
