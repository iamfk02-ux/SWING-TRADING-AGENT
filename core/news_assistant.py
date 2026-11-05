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


def get_watchlist_shield_status(watchlist_currencies: List[str] = None) -> Dict[str, Any]:
    """
    Evaluates real-time News Shield status across the active watchlist currencies.
    Returns active blackout warnings and the next upcoming major release.
    """
    if watchlist_currencies is None:
        watchlist_currencies = ["USD", "EUR", "GBP", "JPY", "AUD"]

    events = get_upcoming_economic_events(force_refresh=False)
    now_ts = datetime.now(timezone.utc).timestamp()

    active_shields = []
    upcoming_major = None

    for ev in events:
        curr = str(ev.get("country", "")).upper()
        if curr not in watchlist_currencies:
            continue

        status_str = str(ev.get("status", ""))
        is_major = ev.get("is_tier1", False) or ev.get("raw_impact") == "High"

        if "Shield Active" in status_str and is_major:
            active_shields.append(ev)
        elif is_major and ev.get("sort_ts", 0) > now_ts and upcoming_major is None:
            upcoming_major = ev

    return {
        "is_shield_engaged": len(active_shields) > 0,
        "active_shields": active_shields,
        "next_major_event": upcoming_major
    }


def analyze_news_snippet(text: str, currency: str) -> Dict[str, Any]:
    """
    Rule-based financial sentiment analyzer:
    Examines text for hawkish/dovish keywords, rate decisions, inflation, employment, and price momentum.
    Returns: plain-English explanation + suggested bias rating (-1 to +1).
    """
    lower = text.lower()
    score = 0
    reasons = []

    # Bullish / Hawkish indicators
    bullish_terms = [
        "rate hike", "hikes rates", "hawkish", "raise rates", "higher than expected",
        "beat expectations", "beats expectations", "inflation rises", "strong jobs", "tight labor",
        "soars", "surges", "rallies", "climbs", "rebounds", "regains ground", "strengthens",
        "advances", "gains", "upbeat", "optimism", "economic boom", "growth accelerates",
        "gdp beats", "unemployment falls", "holds above", "breaks above", "firm"
    ]
    # Bearish / Dovish indicators
    bearish_terms = [
        "rate cut", "cuts rates", "dovish", "lower rates", "missed expectations",
        "misses expectations", "worse than expected", "inflation cools", "job losses", "recession",
        "slowdown", "weak growth", "gdp contracts", "unemployment rises", "slumps",
        "drops", "falls", "slides", "retreats", "declines", "weakens", "loses ground",
        "under pressure", "pressured", "sell-off", "stalls", "dips"
    ]

    for term in bullish_terms:
        if term in lower:
            score += 1
            reasons.append(f"Bullish signal: '{term}'")

    for term in bearish_terms:
        if term in lower:
            score -= 1
            reasons.append(f"Bearish signal: '{term}'")

    if score >= 2:
        bias = 1
        verdict = "🟢 Strong Bullish (+1)"
        advice = f"Strong economic or monetary tailwind detected for {currency}. Boosts BUY setups and penalizes SELL setups."
    elif score == 1:
        bias = 1
        verdict = "🟢 Bullish (+1)"
        advice = f"Positive sentiment detected for {currency}. BUY signals receive higher confidence."
    elif score <= -2:
        bias = -1
        verdict = "🔴 Strong Bearish (-1)"
        advice = f"Substantial negative or dovish headwind detected for {currency}. Boosts SELL setups and penalizes BUY setups."
    elif score == -1:
        bias = -1
        verdict = "🔴 Bearish (-1)"
        advice = f"Negative sentiment detected for {currency}. SELL signals receive higher confidence."
    else:
        bias = 0
        verdict = "⚪ Neutral (0)"
        advice = f"Balanced or mixed headline for {currency}. Technical consensus remains unaffected."

    return {
        "currency": currency,
        "bias": bias,
        "verdict": verdict,
        "advice": advice,
        "reasons": reasons if reasons else ["No decisive directional keywords detected; appears neutral."]
    }
