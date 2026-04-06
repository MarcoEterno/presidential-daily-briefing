from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Union
from urllib.parse import urlparse, urlunparse

import trafilatura

from src.config.settings import settings

logger = logging.getLogger(__name__)


def get_output_dir(date: Optional[str] = None) -> Path:
    date = date or datetime.now().strftime("%Y-%m-%d")
    path = Path(settings.OUTPUT_DIR) / date
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_intermediate(stage_name: str, data: Union[list, dict], date: Optional[str] = None) -> Path:
    output_dir = get_output_dir(date)
    filepath = output_dir / f"{stage_name}.json"
    if isinstance(data, list):
        serialized = [item.model_dump() if hasattr(item, "model_dump") else item for item in data]
    elif hasattr(data, "model_dump"):
        serialized = data.model_dump()
    else:
        serialized = data
    filepath.write_text(json.dumps(serialized, indent=2, default=str))
    logger.info(f"Saved {stage_name} to {filepath}")
    return filepath


def load_intermediate(stage_name: str, date: Optional[str] = None) -> Union[list, dict]:
    output_dir = get_output_dir(date)
    filepath = output_dir / f"{stage_name}.json"
    return json.loads(filepath.read_text())


def normalize_url(url: str) -> str:
    parsed = urlparse(url)
    return urlunparse((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def extract_article_text(url: str, timeout: int = 15) -> str:
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return ""
        text = trafilatura.extract(downloaded, include_comments=False, include_tables=False)
        return text or ""
    except Exception as e:
        logger.warning(f"Failed to extract text from {url}: {e}")
        return ""


def truncate_text(text: str, max_chars: int = 2000) -> str:
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last_period = truncated.rfind(".")
    if last_period > max_chars * 0.5:
        return truncated[: last_period + 1]
    return truncated + "..."
