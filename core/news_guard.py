"""
Economic Calendar & News Guard:
Fetches weekly economic news releases from ForexFactory.
Detects upcoming and recently passed High-Impact ('Red Folder') events.
Blocks new trade entries within a safety blackout window (e.g., 30 mins before & after).
"""

import json
import urllib.request
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple

NEWS_CACHE_FILE = Path(__file__).resolve().parent.parent / "data" / "economic_calendar_cache.json"
NEWS_ALERTED_FILE = Path(__file__).resolve().parent.parent / "data" / "news_alerted_history.json"
CACHE_EXPIRY_HOURS = 12


class NewsGuard:
    TIER1_KEYWORDS = [
        "FOMC",
        "FEDERAL FUNDS",
        "FED FUNDS",
        "INTEREST RATE",
        "POLICY RATE",
        "CASH RATE",
        "OVERNIGHT RATE",
        "REFINANCING RATE",
        "RATE STATEMENT",
        "RATE DECISION",
        "MONETARY POLICY",
        "PRESS CONFERENCE",
        "NON-FARM",
        "NONFARM",
        "NFP",
        "CPI",
        "PCE PRICE INDEX",
        "INFLATION",
        "GDP",
        "POWELL",
        "LAGARDE",
        "BAILEY",
        "UEDA",
    ]

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        news_cfg = self.config.get("news", {})
        self.enabled = news_cfg.get("enabled", True)
        self.blackout_minutes_before = news_cfg.get("blackout_minutes_before", 60)
        self.blackout_minutes_after = news_cfg.get("blackout_minutes_after", 60)
        self.tier1_blackout_minutes_before = news_cfg.get("tier1_blackout_minutes_before", 120)
        self.tier1_blackout_minutes_after = news_cfg.get("tier1_blackout_minutes_after", 120)
        self.advance_alert_minutes = news_cfg.get("advance_alert_minutes", 60)
        self.events: List[Dict[str, Any]] = []
        self.alerted_events: Dict[str, float] = self._load_alerted_history()
        self._load_or_fetch_events()

    def is_tier1_event(self, title: str) -> bool:
        """Determines if a release is an ultra-high-volatility Tier-1 central bank or macroeconomic shock event."""
        if not title:
            return False
        t_upper = title.upper()
        return any(kw in t_upper for kw in self.TIER1_KEYWORDS)

    def _load_alerted_history(self) -> Dict[str, float]:
        """Loads persistently stored alert IDs from disk and prunes items older than 48 hours."""
        if NEWS_ALERTED_FILE.exists():
            try:
                with open(NEWS_ALERTED_FILE, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        now_ts = datetime.now(timezone.utc).timestamp()
                        return {k: v for k, v in data.items() if (now_ts - v) < 172800}
            except Exception:
                pass
        return {}

    def _save_alerted_history(self):
        """Saves alerted event IDs to disk to prevent duplicate notifications across scan cycles."""
        try:
            NEWS_ALERTED_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(NEWS_ALERTED_FILE, "w") as f:
                json.dump(self.alerted_events, f, indent=2)
        except Exception:
            pass

    def _load_or_fetch_events(self):
        """Loads events from disk cache or fetches fresh JSON from ForexFactory feed."""
        if not self.enabled:
            return

        now = datetime.now(timezone.utc)
        if NEWS_CACHE_FILE.exists():
            try:
                mod_time = datetime.fromtimestamp(NEWS_CACHE_FILE.stat().st_mtime, tz=timezone.utc)
                if (now - mod_time).total_seconds() < (CACHE_EXPIRY_HOURS * 3600):
                    with open(NEWS_CACHE_FILE, "r") as f:
                        self.events = json.load(f)
                    if self.events:
                        return
            except Exception:
                pass

        # Fetch fresh events
        self._fetch_calendar()

    def _fetch_calendar(self):
        """Downloads current week's economic events with polite cache fallback."""
        url = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        try:
            with urllib.request.urlopen(req, timeout=10) as res:
                data = json.loads(res.read().decode("utf-8"))
                if isinstance(data, list) and len(data) > 0:
                    self.events = data
                    NEWS_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                    with open(NEWS_CACHE_FILE, "w") as f:
                        json.dump(data, f, indent=2)
                    return
        except Exception:
            pass

        # Fallback to existing disk cache if remote has 429 cooldown
        if not self.events and NEWS_CACHE_FILE.exists():
            try:
                with open(NEWS_CACHE_FILE, "r") as f:
                    self.events = json.load(f)
            except Exception:
                self.events = []

    def check_symbol_news(self, symbol: str) -> Tuple[bool, Optional[str]]:
        """
        Checks if a trade on `symbol` falls inside a High-Impact news blackout window.
        Returns:
            (is_blocked, reason_string)
        """
        if not self.enabled or not self.events:
            return False, None

        sym_clean = symbol.upper().replace(".C", "").replace("M", "").replace(".RAW", "")
        
        # Extract base and quote currency (e.g. EURUSD -> ['EUR', 'USD'], XAUUSD -> ['USD'])
        currencies_to_check = []
        if "XAU" in sym_clean or "GOLD" in sym_clean or "OIL" in sym_clean or "US30" in sym_clean or "SPX" in sym_clean:
            currencies_to_check.append("USD")
        elif len(sym_clean) >= 6:
            currencies_to_check.append(sym_clean[:3])
            currencies_to_check.append(sym_clean[3:6])
        else:
            currencies_to_check.append(sym_clean)

        now = datetime.now(timezone.utc)

        for ev in self.events:
            if ev.get("impact") != "High":
                continue

            event_currency = str(ev.get("country", "")).upper()
            if event_currency not in currencies_to_check:
                continue

            # Parse event ISO date (e.g. 2026-10-14T08:30:00-04:00)
            date_str = ev.get("date", "")
            if not date_str:
                continue

            try:
                ev_time = datetime.fromisoformat(date_str)
                ev_time_utc = ev_time.astimezone(timezone.utc)
            except Exception:
                continue

            time_diff_mins = (ev_time_utc - now).total_seconds() / 60.0

            title = str(ev.get("title", ""))
            is_tier1 = self.is_tier1_event(title)
            limit_before = self.tier1_blackout_minutes_before if is_tier1 else self.blackout_minutes_before
            limit_after = self.tier1_blackout_minutes_after if is_tier1 else self.blackout_minutes_after
            tier_tag = "TIER-1 SUPER-SHIELD" if is_tier1 else "HIGH-IMPACT NEWS"

            # If event is upcoming within the safety buffer:
            if 0 <= time_diff_mins <= limit_before:
                mins = int(time_diff_mins)
                return True, f"{tier_tag}: '{title}' ({event_currency}) in {mins}m ({limit_before}m blackout)"

            # If event occurred within the safety buffer:
            if -limit_after <= time_diff_mins < 0:
                mins_ago = int(abs(time_diff_mins))
                return True, f"{tier_tag}: '{title}' ({event_currency}) released {mins_ago}m ago ({limit_after}m blackout)"

        return False, None

    def get_upcoming_high_impact_events(self, window_minutes: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Returns all scheduled High-Impact events occurring within the next `window_minutes`.
        """
        if not self.enabled or not self.events:
            return []

        base_window = window_minutes or self.advance_alert_minutes
        now = datetime.now(timezone.utc)
        upcoming = []

        for ev in self.events:
            if ev.get("impact") != "High":
                continue

            date_str = ev.get("date", "")
            if not date_str:
                continue

            try:
                ev_time = datetime.fromisoformat(date_str)
                ev_time_utc = ev_time.astimezone(timezone.utc)
            except Exception:
                continue

            time_diff_mins = (ev_time_utc - now).total_seconds() / 60.0

            title = str(ev.get("title", ""))
            is_tier1 = self.is_tier1_event(title)
            effective_window = max(base_window, self.tier1_blackout_minutes_before) if is_tier1 else base_window

            if 0 <= time_diff_mins <= effective_window:
                country = str(ev.get("country", "")).upper()
                event_id = f"{country}_{title}_{date_str}"
                upcoming.append({
                    "id": event_id,
                    "title": title,
                    "country": country,
                    "date": date_str,
                    "minutes_until": max(1, int(time_diff_mins)),
                    "forecast": ev.get("forecast", "-"),
                    "previous": ev.get("previous", "-"),
                    "is_tier1": is_tier1
                })

        return upcoming

    def is_event_alerted(self, event_id: str) -> bool:
        return event_id in self.alerted_events

    def mark_event_alerted(self, event_id: str):
        now_ts = datetime.now(timezone.utc).timestamp()
        self.alerted_events[event_id] = now_ts
        self._save_alerted_history()
