from datetime import datetime

from pydantic import BaseModel, Field


class RawArticle(BaseModel):
    title: str
    url: str
    source_name: str = ""
    source_domain: str = ""
    published_date: str = ""
    language: str = "en"
    content_snippet: str = ""
    source_type: str = ""  # "gdelt", "rss", "newsapi"
    themes: list[str] = Field(default_factory=list)


class RankedStory(BaseModel):
    rank: int
    category: str  # conflict, diplomacy, economic, security, political
    importance_score: float
    initial_summary: str
    headline_indices: list[int] = Field(default_factory=list)
    articles: list[RawArticle] = Field(default_factory=list)


class ThinkTankReference(BaseModel):
    title: str
    url: str
    source_name: str
    published_date: str = ""
    relevance_snippet: str = ""


class EnrichedStory(BaseModel):
    rank: int
    category: str
    importance_score: float
    initial_summary: str
    articles: list[RawArticle] = Field(default_factory=list)

    situation: str = ""
    historical_context: str = ""
    economic_factors: str = ""
    strategic_implications: str = ""
    outlook: str = ""
    think_tank_references: list[ThinkTankReference] = Field(default_factory=list)


class StoryBrief(BaseModel):
    headline: str
    situation: str
    context_and_analysis: str
    implications: str
    watch_items: list[str] = Field(default_factory=list)


class Briefing(BaseModel):
    date: str
    executive_summary: str = ""
    stories: list[StoryBrief] = Field(default_factory=list)
    looking_ahead: list[str] = Field(default_factory=list)
    html_content: str = ""
    text_content: str = ""
    generation_metadata: dict = Field(default_factory=dict)
