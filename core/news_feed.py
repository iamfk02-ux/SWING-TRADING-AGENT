"""
Live Financial News Feed & Multi-Currency Classifier:
Fetches breaking macroeconomic and forex articles from real-time feeds (FXStreet)
including:
- Article headlines & summaries
- Affected currency detection focused on active watchlist (USD, EUR, GBP, JPY, AUD)
- Automated AI Sentiment & Bias suggestion (-1 to +1)
- Persistent disk caching for instant dashboard performance
"""

import json
import urllib.request
import xml.etree.ElementTree as ET
import re
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime, timezone

BREAKING_CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "breaking_news_cache.json"
WATCHLIST_CURRENCIES = ["USD", "EUR", "GBP", "JPY", "AUD"]

CURRENCY_KEYWORDS = {
    "USD": ["fed", "federal reserve", "dollar", "powell", "usd", "treasury", "fomc", "united states", "u.s.", "nfp", "wall street"],
    "EUR": ["ecb", "lagarde", "euro", "eurozone", "germany", "eur", "bundesbank"],
    "GBP": ["bank of england", "boe", "bailey", "pound", "sterling", "uk", "britain", "gbp", "gilt"],
    "JPY": ["bank of japan", "boj", "ueda", "yen", "jpy", "japan", "tokyo"],
    "AUD": ["rba", "reserve bank of australia", "aussie", "aud", "australia"],
    "CAD": ["bank of canada", "boc", "loonie", "cad", "canada"],
    "CHF": ["snb", "swiss national bank", "swiss franc", "chf", "switzerland"],
    "NZD": ["rbnz", "kiwi", "nzd", "new zealand"]
}


def detect_currencies(text: str) -> List[str]:
    """Detects which currencies/assets are relevant to the article text, prioritizing watchlist currencies."""
    lower = text.lower()
    matches = []
    for curr, keywords in CURRENCY_KEYWORDS.items():
        for kw in keywords:
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, lower):
                matches.append(curr)
                break
    return matches if matches else ["USD"]


def clean_html(raw_html: str) -> str:
    """Removes HTML markup from RSS description strings."""
    if not raw_html:
        return ""
    clean_text = re.sub(r"<[^>]+>", "", raw_html)
    return " ".join(clean_text.split())


def get_latest_financial_news(limit: int = 12, force_refresh: bool = False, watchlist_only: bool = True) -> List[Dict[str, Any]]:
    """
    Fetches live breaking forex & macroeconomic news with headlines, summaries,
    and automated sentiment classification. Uses 15-minute disk cache unless force_refresh=True.
    """
    from core.news_assistant import analyze_news_snippet

    raw_items = []

    # 1. Try disk cache first (15-minute TTL)
    if not force_refresh and BREAKING_CACHE_FILE.exists():
        try:
            mod_time = datetime.fromtimestamp(BREAKING_CACHE_FILE.stat().st_mtime, tz=timezone.utc)
            if (datetime.now(timezone.utc) - mod_time).total_seconds() < 900:
                with open(BREAKING_CACHE_FILE, "r", encoding="utf-8") as f:
                    raw_items = json.load(f)
        except Exception:
            pass

    # 2. Fetch fresh from RSS if cache expired or force_refresh
    if not raw_items:
        url = "https://www.fxstreet.com/rss/news"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=8) as response:
                xml_data = response.read()
                root = ET.fromstring(xml_data)
                items = root.findall(".//item")

                for item in items[:30]:
                    title = item.find("title").text if item.find("title") is not None else "No Title"
                    raw_desc = item.find("description").text if item.find("description") is not None else ""
                    desc = clean_html(raw_desc)
                    pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                    link = item.find("link").text if item.find("link") is not None else "#"

                    full_text = f"{title}. {desc}"
                    currencies = detect_currencies(full_text)

                    # Pick primary currency (prioritize active watchlist)
                    watchlist_hits = [c for c in currencies if c in WATCHLIST_CURRENCIES]
                    primary_curr = watchlist_hits[0] if watchlist_hits else currencies[0]

                    ai_eval = analyze_news_snippet(full_text, primary_curr)

                    raw_items.append({
                        "title": title.strip(),
                        "summary": desc.strip()[:240] + ("..." if len(desc.strip()) > 240 else ""),
                        "date": pub_date.strip()[:22],
                        "link": link.strip(),
                        "currencies": currencies,
                        "primary_currency": primary_curr,
                        "is_watchlist": len(watchlist_hits) > 0,
                        "bias": ai_eval["bias"],
                        "verdict": ai_eval["verdict"],
                        "advice": ai_eval["advice"]
                    })

                if raw_items:
                    BREAKING_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    with open(BREAKING_CACHE_FILE, "w", encoding="utf-8") as f:
                        json.dump(raw_items, f, indent=2)
        except Exception:
            if BREAKING_CACHE_FILE.exists():
                try:
                    with open(BREAKING_CACHE_FILE, "r", encoding="utf-8") as f:
                        raw_items = json.load(f)
                except Exception:
                    raw_items = []

    if watchlist_only:
        filtered = [it for it in raw_items if it.get("is_watchlist", True)]
        return filtered[:limit]

    return raw_items[:limit]
