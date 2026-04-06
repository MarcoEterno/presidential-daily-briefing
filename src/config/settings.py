from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

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
