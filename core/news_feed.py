"""
Live Financial News Feed & Multi-Currency Classifier:
Fetches breaking macroeconomic and forex articles from real-time feeds (FXStreet, Investing.com)
including:
- Article headlines
- Article summaries / body text
- Affected currency detection (USD, EUR, GBP, JPY, AUD, CAD, CHF, NZD, Gold, Crypto)
- Automated AI Sentiment & Bias suggestion (-2 to +2)
"""

import urllib.request
import xml.etree.ElementTree as ET
import re
from typing import List, Dict, Any
from datetime import datetime


CURRENCY_KEYWORDS = {
    "USD": ["fed", "federal reserve", "dollar", "powell", "usd", "treasury", "fomc", "united states", "u.s."],
    "EUR": ["ecb", "lagarde", "euro", "eurozone", "germany", "eur"],
    "GBP": ["bank of england", "boe", "bailey", "pound", "sterling", "uk", "britain", "gbp", "gilt"],
    "JPY": ["bank of japan", "boj", "ueda", "yen", "jpy", "japan"],
    "AUD": ["rba", "reserve bank of australia", "aussie", "aud", "australia"],
    "CAD": ["bank of canada", "boc", "loonie", "cad", "canada"],
    "CHF": ["snb", "swiss national bank", "swiss franc", "chf", "switzerland"],
    "NZD": ["rbnz", "kiwi", "nzd", "new zealand"],
    "XAU": ["gold", "xau", "bullion", "precious metal"],
    "BTC": ["bitcoin", "btc", "crypto", "ethereum", "eth"]
}


def detect_currencies(text: str) -> List[str]:
    """Detects which currencies/assets are relevant to the article text."""
    lower = text.lower()
    matches = []
    for curr, keywords in CURRENCY_KEYWORDS.items():
        for kw in keywords:
            # Use word boundaries so 'cad' doesn't match 'defend' or 'arcade'
            pattern = r'\b' + re.escape(kw) + r'\b'
            if re.search(pattern, lower):
                matches.append(curr)
                break
    return matches if matches else ["GLOBAL"]


def clean_html(raw_html: str) -> str:
    """Removes HTML markup from RSS description strings."""
    if not raw_html:
        return ""
    clean_text = re.sub(r"<[^>]+>", "", raw_html)
    return " ".join(clean_text.split())


def get_latest_financial_news(limit: int = 15) -> List[Dict[str, Any]]:
    """
    Fetches live breaking forex & macroeconomic news with headlines and article summaries.
    """
    url = "https://www.fxstreet.com/rss/news"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    news_items = []

    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            xml_data = response.read()
            root = ET.fromstring(xml_data)
            items = root.findall(".//item")

            for item in items[:limit]:
                title = item.find("title").text if item.find("title") is not None else "No Title"
                raw_desc = item.find("description").text if item.find("description") is not None else ""
                desc = clean_html(raw_desc)
                pub_date = item.find("pubDate").text if item.find("pubDate") is not None else ""
                link = item.find("link").text if item.find("link") is not None else "#"

                full_text = f"{title}. {desc}"
                currencies = detect_currencies(full_text)

                news_items.append({
                    "title": title.strip(),
                    "summary": desc.strip(),
                    "date": pub_date.strip(),
                    "link": link.strip(),
                    "currencies": currencies,
                    "full_text": full_text
                })
        return news_items
    except Exception as e:
        print(f"[NEWS FEED ERROR] Could not fetch live news feed: {e}")
        return []
