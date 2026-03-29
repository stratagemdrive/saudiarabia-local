"""
saudiarabia_news.py
Fetches Saudi Arabia-focused news from English-language RSS feeds, categorizes
stories, and writes them to docs/saudiarabia_news.json — capped at 20 per
category, max age 7 days, oldest entries replaced first.
No external APIs are used. All sources publish in English.
"""

import json
import os
import re
import time
import logging
from datetime import datetime, timezone, timedelta
from dateutil import parser as dateparser
import feedparser

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

OUTPUT_DIR = "docs"
OUTPUT_FILE = os.path.join(OUTPUT_DIR, "saudiarabia_news.json")
MAX_PER_CATEGORY = 20
MAX_AGE_DAYS = 7
CATEGORIES = ["Diplomacy", "Military", "Energy", "Economy", "Local Events"]

# RSS feeds — all free, English-language, Saudi Arabia-focused, no APIs
FEEDS = [
    # Arab News — Saudi Arabia's largest English-language newspaper (est. 1975)
    {"source": "Arab News", "url": "https://www.arabnews.com/rss"},
    {"source": "Arab News", "url": "https://www.arabnews.com/saudiarabia"},
    {"source": "Arab News", "url": "https://www.arabnews.com/economy"},
    {"source": "Arab News", "url": "https://www.arabnews.com/main-category/energy"},
    {"source": "Arab News", "url": "https://www.arabnews.com/middleeast"},
    # Saudi Gazette — leading English daily in KSA (est. 1976)
    {"source": "Saudi Gazette", "url": "https://saudigazette.com.sa/rssFeed/74"},
    {"source": "Saudi Gazette", "url": "https://saudigazette.com.sa/rss"},
    # Al Arabiya English — Saudi-owned pan-Arab broadcaster, English service
    {"source": "Al Arabiya", "url": "https://english.alarabiya.net/rss.xml"},
    {"source": "Al Arabiya", "url": "https://english.alarabiya.net/business/economy.rss"},
    {"source": "Al Arabiya", "url": "https://english.alarabiya.net/business/energy.rss"},
    # The Guardian — Saudi Arabia section
    {"source": "The Guardian", "url": "https://www.theguardian.com/world/saudi-arabia/rss"},
    # BBC News — Middle East section (strong Saudi coverage)
    {"source": "BBC News", "url": "https://feeds.bbci.co.uk/news/world/middle_east/rss.xml"},
]

# ---------------------------------------------------------------------------
# Category keyword mapping (Saudi Arabia-contextualised)
# ---------------------------------------------------------------------------

CATEGORY_KEYWORDS = {
    "Diplomacy": [
        "diplomacy", "diplomatic", "foreign policy", "embassy", "ambassador",
        "treaty", "bilateral", "multilateral", "united nations", "arab league",
        "foreign minister", "foreign affairs", "summit", "sanctions",
        "international relations", "geopolitical", "opec", "gcc",
        "trade deal", "g20", "accord", "alliance", "envoy", "consul",
        "prince salman", "mbs", "mohammed bin salman", "crown prince",
        "king salman", "saudi foreign", "riyadh", "saudi and us",
        "saudi and china", "saudi and iran", "saudi and russia",
        "normalization", "abraham accords", "saudi and israel",
        "middle east peace", "two-state", "saudi diplomat",
        "faisal bin farhan", "prince faisal",
    ],
    "Military": [
        "military", "army", "navy", "air force", "royal saudi",
        "defence", "defense", "troops", "soldier", "weapons",
        "missile", "drone", "armed forces", "war", "combat",
        "conflict", "bomb", "houthi", "yemen", "operation storm",
        "coalition forces", "saudi-led coalition", "ballistic missile",
        "air defense", "patriot missile", "intelligence", "general",
        "security forces", "national guard", "interior ministry",
        "terrorism", "terrorist", "counterterrorism", "isis",
        "attack", "strike", "airbase",
    ],
    "Energy": [
        "energy", "oil", "gas", "petroleum", "aramco", "saudi aramco",
        "opec", "opec plus", "opec+", "crude", "barrel", "brent",
        "renewable", "solar", "wind", "electricity", "power grid",
        "net zero", "carbon", "climate", "fossil fuel", "emissions",
        "neom energy", "hydrogen", "green hydrogen", "refinery",
        "lng", "pipeline", "energy transition", "power plant",
        "energy price", "oil price", "production cut", "output cut",
        "sabic", "vision 2030 energy", "national renewable",
        "desalination", "water energy",
    ],
    "Economy": [
        "economy", "economic", "gdp", "inflation", "interest rate",
        "sama", "saudi central bank", "budget", "finance minister",
        "treasury", "tax", "vat", "unemployment", "jobs", "recession",
        "growth", "trade", "riyal", "sar", "tadawul", "sabic",
        "fiscal", "spending", "debt", "deficit", "wage",
        "cost of living", "investment", "business", "exports",
        "imports", "vision 2030", "neom", "giga project",
        "red sea project", "qiddiya", "diriyah", "amaala",
        "saudi economy", "privatization", "aramco ipo",
        "public investment fund", "pif", "sovereign wealth",
        "tourism", "entertainment", "retail", "manufacturing",
        "mohammed al-jadaan", "ministry of finance",
    ],
    "Local Events": [
        "local", "region", "province", "municipality", "city",
        "town", "community", "hospital", "school", "university",
        "crime", "police", "court", "flood", "fire", "transport",
        "strike", "housing", "riyadh", "jeddah", "mecca", "medina",
        "dammam", "khobar", "dhahran", "tabuk", "abha", "taif",
        "holy mosque", "hajj", "umrah", "pilgrimage", "kaaba",
        "women rights", "driving ban", "social reform",
        "entertainment", "cinema", "concert", "cultural",
        "saudi society", "expat", "worker", "kafala",
        "human rights", "death penalty", "execution",
        "saudi court", "arrest", "detained", "imprisoned",
        "flood relief", "disaster", "ngo",
    ],
}


def classify(title: str, description: str):
    """Return the best-matching category for a story, or None if no match."""
    text = (title + " " + (description or "")).lower()
    scores = {cat: 0 for cat in CATEGORIES}
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if re.search(r'\b' + re.escape(kw) + r'\b', text):
                scores[cat] += 1
    best_cat = max(scores, key=scores.get)
    return best_cat if scores[best_cat] > 0 else None


def strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    return re.sub(r"<[^>]+>", "", text or "").strip()


def parse_date(entry):
    """Parse a feed entry's published date into a UTC-aware datetime."""
    raw = entry.get("published") or entry.get("updated") or entry.get("created")
    if not raw:
        struct = entry.get("published_parsed") or entry.get("updated_parsed")
        if struct:
            return datetime(*struct[:6], tzinfo=timezone.utc)
        return None
    try:
        dt = dateparser.parse(raw)
        if dt and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc) if dt else None
    except Exception:
        return None


def fetch_feed(feed_cfg: dict) -> list:
    """Fetch a single RSS feed and return a list of story dicts."""
    source = feed_cfg["source"]
    url = feed_cfg["url"]
    stories = []
    try:
        parsed = feedparser.parse(url)
        if parsed.bozo and not parsed.entries:
            log.warning("Bozo feed (%s): %s", source, url)
            return stories
        cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
        for entry in parsed.entries:
            pub_date = parse_date(entry)
            if pub_date and pub_date < cutoff:
                continue
            title = strip_html(entry.get("title", "")).strip()
            desc = strip_html(entry.get("summary", "")).strip()
            if not title:
                continue
            category = classify(title, desc)
            if not category:
                continue
            story = {
                "title": title,
                "source": source,
                "url": entry.get("link", ""),
                "published_date": pub_date.isoformat() if pub_date else None,
                "category": category,
            }
            stories.append(story)
    except Exception as exc:
        log.error("Failed to fetch %s (%s): %s", source, url, exc)
    return stories


def load_existing() -> dict:
    """Load the current JSON file, grouped by category."""
    if not os.path.exists(OUTPUT_FILE):
        return {cat: [] for cat in CATEGORIES}
    try:
        with open(OUTPUT_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (json.JSONDecodeError, OSError):
        return {cat: [] for cat in CATEGORIES}

    grouped = {cat: [] for cat in CATEGORIES}
    stories = data.get("stories", data) if isinstance(data, dict) else data
    if isinstance(stories, list):
        for story in stories:
            cat = story.get("category")
            if cat in grouped:
                grouped[cat].append(story)
    return grouped


def merge(existing: dict, fresh: list) -> dict:
    """
    Merge fresh stories into the existing pool.
    - De-duplicate by URL.
    - Discard stories older than MAX_AGE_DAYS.
    - Replace oldest entries first when over MAX_PER_CATEGORY.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)

    existing_urls = set()
    for stories in existing.values():
        for s in stories:
            if s.get("url"):
                existing_urls.add(s["url"])

    for story in fresh:
        cat = story.get("category")
        if cat not in existing:
            continue
        if story["url"] in existing_urls:
            continue
        existing[cat].append(story)
        existing_urls.add(story["url"])

    for cat in CATEGORIES:
        pool = existing[cat]
        # Drop expired stories
        pool = [
            s for s in pool
            if s.get("published_date") and
               dateparser.parse(s["published_date"]).astimezone(timezone.utc) >= cutoff
        ]
        # Sort newest-first, cap at limit (oldest replaced first)
        pool.sort(key=lambda s: s.get("published_date") or "", reverse=True)
        existing[cat] = pool[:MAX_PER_CATEGORY]

    return existing


def write_output(grouped: dict) -> None:
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    flat = []
    for stories in grouped.values():
        flat.extend(stories)
    output = {
        "country": "Saudi Arabia",
        "last_updated": datetime.now(timezone.utc).isoformat(),
        "story_count": len(flat),
        "stories": flat,
    }
    with open(OUTPUT_FILE, "w", encoding="utf-8") as fh:
        json.dump(output, fh, ensure_ascii=False, indent=2)
    log.info("Wrote %d stories to %s", len(flat), OUTPUT_FILE)


def main():
    log.info("Loading existing data ...")
    existing = load_existing()

    log.info("Fetching %d RSS feeds ...", len(FEEDS))
    fresh = []
    for cfg in FEEDS:
        results = fetch_feed(cfg)
        log.info("  %s — %d stories from %s", cfg["source"], len(results), cfg["url"])
        fresh.extend(results)
        time.sleep(0.5)  # polite crawl delay

    log.info("Merging %d fresh stories ...", len(fresh))
    merged = merge(existing, fresh)

    counts = {cat: len(merged[cat]) for cat in CATEGORIES}
    log.info("Category totals: %s", counts)

    write_output(merged)


if __name__ == "__main__":
    main()
