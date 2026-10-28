"""
Economic News & AI Assistant Module:
1. Live Economic Calendar: Automatically fetches this week's scheduled High & Medium impact events
2. News Analyzer / Explainer: Reads raw financial headlines, articles, or numbers you paste,
   explains what it means in plain English, and provides a recommended bias rating (-2 to +2).
"""

import json
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any

CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "economic_calendar_cache.json"


def get_upcoming_economic_events(*args, **kwargs) -> List[Dict[str, Any]]:
    """
    Fetches the global economic calendar from ForexFactory feed or persistent cache.
    Includes Tier-1 super-events (FOMC, NFP, CPI, Rates), High and Medium impact releases.
    Falls back reliably to disk cache to prevent rate-limit 429 drops.
    """
    force_refresh = kwargs.get("force_refresh", False) or (args[0] if len(args) > 0 else False)
    from core.news_guard import NewsGuard
    guard = NewsGuard()

    events = []
    # 1. If cache exists and not forcing refresh, try loading from cache first (fresh up to 6h)
    if not force_refresh and CACHE_FILE.exists():
        try:
            mod_time = datetime.fromtimestamp(CACHE_FILE.stat().st_mtime, tz=timezone.utc)
            now = datetime.now(timezone.utc)
            if (now - mod_time).total_seconds() < 21600:  # 6 hours
                with open(CACHE_FILE, "r") as f:
                    events = json.load(f)
        except Exception:
            pass

    # 2. If no cached events or forcing refresh, fetch from live feed
    if not events:
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as response:
                data = json.loads(response.read().decode("utf-8"))
                if isinstance(data, list) and len(data) > 0:
                    events = data
                    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    with open(CACHE_FILE, "w") as f:
                        json.dump(data, f, indent=2)
        except Exception as e:
            # Fallback to existing disk cache if remote has 429 cooldown or timeout
            if CACHE_FILE.exists():
                try:
                    with open(CACHE_FILE, "r") as f:
                        events = json.load(f)
                except Exception:
                    pass

    # Filter and categorize events
    now = datetime.now(timezone.utc)
    filtered = []
    for e in events:
        impact = str(e.get("impact", "")).capitalize()
        title = e.get("title", "")
        is_tier1 = guard.is_tier1_event(title)

        # Include all High/Medium impact events, plus any Tier-1 events
        if impact in ["High", "Medium"] or is_tier1:
            date_str = e.get("date", "")
            formatted_date = date_str
            status_text = "Scheduled"
            sort_ts = 0.0

            if date_str:
                try:
                    dt = datetime.fromisoformat(date_str).astimezone(timezone.utc)
                    sort_ts = dt.timestamp()
                    formatted_date = dt.strftime("%b %d, %H:%M UTC")
                    diff_m = (dt - now).total_seconds() / 60.0

                    if is_tier1:
                        if 0 <= diff_m <= guard.tier1_blackout_minutes_before:
                            status_text = f"🛡️ Shield Active (In {int(diff_m)}m)"
                        elif -guard.tier1_blackout_minutes_after <= diff_m < 0:
                            status_text = f"🛡️ Shield Active ({int(abs(diff_m))}m ago)"
                        elif diff_m > guard.tier1_blackout_minutes_before:
                            hrs = int(diff_m / 60)
                            status_text = f"In {hrs}h" if hrs > 1 else f"In {int(diff_m)}m"
                        else:
                            hrs_ago = int(abs(diff_m) / 60)
                            status_text = f"Passed ({hrs_ago}h ago)" if hrs_ago > 1 else f"Passed ({int(abs(diff_m))}m ago)"
                    else:
                        if 0 <= diff_m <= guard.blackout_minutes_before:
                            status_text = f"🛡️ Shield Active (In {int(diff_m)}m)"
                        elif -guard.blackout_minutes_after <= diff_m < 0:
                            status_text = f"🛡️ Shield Active ({int(abs(diff_m))}m ago)"
                        elif diff_m > guard.blackout_minutes_before:
                            hrs = int(diff_m / 60)
                            status_text = f"In {hrs}h" if hrs > 1 else f"In {int(diff_m)}m"
                        else:
                            hrs_ago = int(abs(diff_m) / 60)
                            status_text = f"Passed ({hrs_ago}h ago)" if hrs_ago > 1 else f"Passed ({int(abs(diff_m))}m ago)"
                except Exception:
                    pass

            if is_tier1:
                impact_label = "🔥 Tier-1 Super-Shield"
            elif impact == "High":
                impact_label = "🔴 High Impact"
            else:
                impact_label = "🟡 Medium Impact"

            filtered.append({
                "country": e.get("country", ""),
                "title": title,
                "impact": impact_label,
                "raw_impact": impact,
                "is_tier1": is_tier1,
                "date": formatted_date,
                "iso_date": date_str,
                "sort_ts": sort_ts,
                "status": status_text,
                "forecast": e.get("forecast", "-") or "-",
                "previous": e.get("previous", "-") or "-"
            })

    # Sort chronologically by date
    filtered.sort(key=lambda x: x["sort_ts"])
    return filtered


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
