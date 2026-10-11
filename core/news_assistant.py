"""
Economic News & AI Assistant Module:
1. Live Economic Calendar: Automatically fetches this week's scheduled High & Medium impact events
2. News Analyzer / Explainer: Reads raw financial headlines, articles, or numbers you paste,
   explains what it means in plain English, and provides a recommended bias rating (-2 to +2).
"""

import json
import urllib.request
from datetime import datetime
from typing import List, Dict, Any


def get_upcoming_economic_events() -> List[Dict[str, Any]]:
    """
    Fetches the live global economic calendar (ForexFactory feed)
    Filtered down to High and Medium impact events.
    """
    url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            events = json.loads(response.read().decode("utf-8"))
            
            # Filter for meaningful market-moving events
            filtered = []
            for e in events:
                impact = e.get("impact", "")
                if impact in ["High", "Medium"]:
                    filtered.append({
                        "country": e.get("country", ""),
                        "title": e.get("title", ""),
                        "impact": impact,
                        "date": e.get("date", ""),
                        "forecast": e.get("forecast", "-"),
                        "previous": e.get("previous", "-")
                    })
            return filtered
    except Exception as e:
        print(f"[CALENDAR ERROR] Could not fetch economic calendar: {e}")
        return []


def analyze_news_snippet(text: str, currency: str) -> Dict[str, Any]:
    """
    Rule-based financial sentiment analyzer:
    Examines text for hawkish/dovish keywords, rate decisions, inflation, and employment indicators.
    Returns: plain-English explanation + suggested bias rating (-2 to +2).
    """
    lower = text.lower()
    score = 0
    reasons = []

    # Bullish / Hawkish indicators
    bullish_terms = [
        "rate hike", "hikes rates", "hawkish", "raise rates", "higher than expected",
        "beat expectations", "inflation rises", "strong jobs", "tight labor", "soars",
        "economic boom", "growth accelerates", "gdp beats", "unemployment falls"
    ]
    # Bearish / Dovish indicators
    bearish_terms = [
        "rate cut", "cuts rates", "dovish", "lower rates", "missed expectations",
        "worse than expected", "inflation cools", "job losses", "recession",
        "slowdown", "weak growth", "gdp contracts", "unemployment rises", "slumps"
    ]

    for term in bullish_terms:
        if term in lower:
            score += 1
            reasons.append(f"Bullish signal: found '{term}'")

    for term in bearish_terms:
        if term in lower:
            score -= 1
            reasons.append(f"Bearish signal: found '{term}'")

    if score >= 2:
        bias = 2
        verdict = "Strong Bullish (+2)"
        advice = f"Strong economic or monetary tailwind detected for {currency}. The bot will boost buy setups and block sell setups."
    elif score == 1:
        bias = 1
        verdict = "Bullish (+1)"
        advice = f"Mild positive sentiment for {currency}. Buy signals receive higher confidence."
    elif score <= -2:
        bias = -2
        verdict = "Strong Bearish (-2)"
        advice = f"Substantial negative or dovish headwind detected for {currency}. The bot will prioritize short setups and cancel long setups."
    elif score == -1:
        bias = -1
        verdict = "Bearish (-1)"
        advice = f"Mild negative sentiment for {currency}. Sell signals receive higher confidence."
    else:
        bias = 0
        verdict = "Neutral (0)"
        advice = f"The news appears balanced or mixed. Keeping {currency} at Neutral (0) lets the technical indicators trade normally."

    return {
        "currency": currency,
        "bias": bias,
        "verdict": verdict,
        "advice": advice,
        "reasons": reasons if reasons else ["No decisive directional keywords detected; appears neutral."]
    }
