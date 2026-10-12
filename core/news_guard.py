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
CACHE_EXPIRY_HOURS = 12


class NewsGuard:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        news_cfg = self.config.get("news", {})
        self.enabled = news_cfg.get("enabled", True)
        self.blackout_minutes_before = news_cfg.get("blackout_minutes_before", 30)
        self.blackout_minutes_after = news_cfg.get("blackout_minutes_after", 30)
        self.events: List[Dict[str, Any]] = []
        self._load_or_fetch_events()

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
        
        # Crypto symbols trade 24/7 and are not direct central bank currency releases
        if any(c in sym_clean for c in ["BTC", "ETH", "SOL", "LTC", "XRP"]):
            return False, None

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

            # If event is upcoming within the next X minutes:
            if 0 <= time_diff_mins <= self.blackout_minutes_before:
                mins = int(time_diff_mins)
                return True, f"HIGH-IMPACT NEWS: '{ev.get('title')}' ({event_currency}) in {mins}m"

            # If event occurred within the past Y minutes:
            if -self.blackout_minutes_after <= time_diff_mins < 0:
                mins_ago = int(abs(time_diff_mins))
                return True, f"HIGH-IMPACT NEWS: '{ev.get('title')}' ({event_currency}) released {mins_ago}m ago"

        return False, None
