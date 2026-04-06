from dataclasses import dataclass


@dataclass
class FeedSource:
    name: str
    url: str
    category: str  # "think_tank" or "news"


THINK_TANK_FEEDS: list[FeedSource] = [
    # Verified working feeds (as of 2026-04)
    FeedSource("ECFR", "https://ecfr.eu/feed/", "think_tank"),
    FeedSource("ECFR Publications", "https://ecfr.eu/feed/?post_type=publication", "think_tank"),
    FeedSource("FPRI", "https://www.fpri.org/feed", "think_tank"),
    FeedSource("Foreign Policy In Focus", "https://fpif.org/feed", "think_tank"),
    FeedSource("War on the Rocks", "https://warontherocks.com/feed/", "think_tank"),
    FeedSource("Foreign Affairs", "https://www.foreignaffairs.com/rss.xml", "think_tank"),
    FeedSource("Stimson Center", "https://www.stimson.org/feed/", "think_tank"),
    FeedSource("Atlantic Council", "https://www.atlanticcouncil.org/feed/", "think_tank"),
    FeedSource("Crisis Group", "https://www.crisisgroup.org/rss.xml", "think_tank"),
]

# GDELT theme clusters for geopolitical queries
GDELT_THEMES: dict[str, str] = {
    "conflict": "theme:MILITARY OR theme:ARMED_CONFLICT OR theme:TERROR",
    "diplomacy": "theme:DIPLOMACY OR theme:NEGOTIATIONS OR theme:ALLIANCE",
    "economic": "theme:SANCTIONS OR theme:ECON_TRADE OR theme:TAX_FNCACT",
    "political": "theme:ELECTION OR theme:COUP OR theme:PROTEST",
}

NEWS_KEYWORDS = (
    "(military OR conflict OR diplomacy OR sanctions OR "
    '"foreign policy" OR geopolitical OR "national security" '
    "OR war OR treaty OR nuclear OR NATO OR \"United Nations\")"
)
