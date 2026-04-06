from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env before Settings reads os.environ
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
load_dotenv(_PROJECT_ROOT / ".env", override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    ANTHROPIC_API_KEY: str = ""
    NEWSAPI_KEY: str = ""
    RESEND_API_KEY: str = ""
    RESEND_FROM_EMAIL: str = "briefing@yourdomain.com"
    GITHUB_REPO: str = ""
    GITHUB_TOKEN: str = ""

    MAX_STORIES: int = 8
    MIN_STORIES: int = 5
    GDELT_TIMESPAN: str = "24h"
    GDELT_MAX_RECORDS: int = 250
    CLAUDE_MODEL: str = "claude-sonnet-4-20250514"

    OUTPUT_DIR: str = "output"
    SUBSCRIBERS_FILE: str = "config/subscribers.json"


settings = Settings()
