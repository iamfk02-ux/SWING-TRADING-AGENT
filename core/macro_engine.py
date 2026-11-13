"""
Institutional Macro & Fundamental Engine:
Adopts the institutional trading philosophy:
"Fundamentals determine the direction (The Tide); Technicals determine the entry (The Wave)."

Tracks:
1. Central Bank Monetary Policy & Benchmark Interest Rates (8 Major Currencies + Gold)
2. Interest Rate Differentials (Carry advantage)
3. Macroeconomic Health / Economic Divergence (Inflation, Labor, Growth/PMI, Commodities)
4. Dynamic News Sentiment Layer (FXStreet breaking wires & economic calendar results)
5. Directional Macro Bias & Institutional Confluence Filter:
   - Forbids counter-fundamental entries (never fight the central bank trend)
   - Boosts setups with strong fundamental alignment (Institutional Confluence)
"""

import json
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
MACRO_STATE_FILE = DATA_DIR / "macro_state.json"
MACRO_NEWS_FILE = DATA_DIR / "macro_news_state.json"


# ---------------------------------------------------------------------
# CENTRAL BANK & MACROECONOMIC BASELINE MATRIX
# Updated to current macroeconomic landscape (Fed, ECB, BoE, BoJ, BoC, RBA, RBNZ, SNB)
# ---------------------------------------------------------------------
CENTRAL_BANK_PROFILES: Dict[str, Dict[str, Any]] = {
    "USD": {
        "name": "United States Dollar",
        "central_bank": "Federal Reserve (Fed)",
        "policy_rate": 4.75,
        "policy_stance": "NEUTRAL_HOLD",
        "stance_score": 1.0,         # +1.0 = High yield, data-dependent, resilient labor
        "cpi_trend": "Sticky (~2.7%)",
        "growth_pmi": "Resilient (Services > 52)",
        "commodity_tier": "Global Reserve / Safe-Haven Carry",
        "base_macro_score": 2.0,     # Strong macroeconomic baseline
        "narrative": "US economic resilience and high policy yields (4.75%) maintain structural dollar demand. Fed is easing gradually, but dollar retains dominant carry advantage over EUR, JPY, and CHF."
    },
    "EUR": {
        "name": "Euro",
        "central_bank": "European Central Bank (ECB)",
        "policy_rate": 3.25,
        "policy_stance": "EASING_CYCLE",
        "stance_score": -1.5,        # -1.5 = Successive 25bp cuts, sluggish growth
        "cpi_trend": "Cooling towards 2.0%",
        "growth_pmi": "Weak (Manufacturing < 46, German stagnation)",
        "commodity_tier": "Net Energy Importer",
        "base_macro_score": -2.0,    # Structurally weak macro baseline
        "narrative": "ECB is cutting rates aggressively due to rapid disinflation and Eurozone economic weakness (especially German industrial slump). Structural macro headwind against USD, GBP, and CAD."
    },
    "GBP": {
        "name": "British Pound",
        "central_bank": "Bank of England (BoE)",
        "policy_rate": 4.75,
        "policy_stance": "GRADUAL_CUTS",
        "stance_score": 0.5,         # +0.5 = High yield, but cautious gradual easing
        "cpi_trend": "Persistent Services Inflation (~5%)",
        "growth_pmi": "Modest (~50.5)",
        "commodity_tier": "Service-Driven / Fiscal Deficit",
        "base_macro_score": 0.8,     # Neutral to slightly positive due to high yield
        "narrative": "BoE is cutting rates at a slower, quarterly pace compared to the ECB because UK services inflation and wage growth remain sticky. High yield offers support against EUR, but fiscal/tax drag caps upside against USD."
    },
    "JPY": {
        "name": "Japanese Yen",
        "central_bank": "Bank of Japan (BoJ)",
        "policy_rate": 0.25,
        "policy_stance": "NORMALIZING_HIKE",
        "stance_score": -1.2,        # -1.2 = Hiking gradually, but lowest yield globally
        "cpi_trend": "Moderate (~2.5%)",
        "growth_pmi": "Sluggish (~49.5)",
        "commodity_tier": "Prime Global Funding Currency / Risk-Off Safe Haven",
        "base_macro_score": -1.5,    # Vulnerable to carry trades during calm markets
        "narrative": "BoJ is normalizing rates upward, but Japan's 0.25% yield creates a massive negative carry deficit against G10 peers (USD +4.50%, CAD +3.50%). JPY remains a funding currency, surging primarily during equity sell-offs."
    },
    "CAD": {
        "name": "Canadian Dollar",
        "central_bank": "Bank of Canada (BoC)",
        "policy_rate": 3.75,
        "policy_stance": "EASING_CYCLE",
        "stance_score": 0.0,         # 0.0 = Neutral: cutting to protect mortgages, but supported by US trade & energy
        "cpi_trend": "Near Target (~2.0%)",
        "growth_pmi": "Moderate (~50.0)",
        "commodity_tier": "Major Oil Exporter (WTI Crude)",
        "base_macro_score": 0.5,     # Solid baseline supported by US proximity & energy
        "narrative": "BoC has front-loaded rate cuts to relieve household mortgage debt, but Canadian fundamentals are buffered by robust US trade demand and stable crude oil prices. Outperforms weaker European currencies."
    },
    "AUD": {
        "name": "Australian Dollar",
        "central_bank": "Reserve Bank of Australia (RBA)",
        "policy_rate": 4.35,
        "policy_stance": "HAWKISH_HOLD",
        "stance_score": 1.2,         # +1.2 = Reluctant to cut, strong labor market
        "cpi_trend": "Sticky Trimmed Mean (~3.5%)",
        "growth_pmi": "Modest (~50.0)",
        "commodity_tier": "Iron Ore, Coal, Metals / China Growth Proxy",
        "base_macro_score": 1.2,     # High yield & hawkish central bank
        "narrative": "RBA remains one of the most hawkish G10 central banks, holding rates at 4.35% due to sticky domestic inflation and tight employment. Constrained primarily by Chinese macroeconomic stimulus uncertainty."
    },
    "NZD": {
        "name": "New Zealand Dollar",
        "central_bank": "Reserve Bank of New Zealand (RBNZ)",
        "policy_rate": 4.75,
        "policy_stance": "AGGRESSIVE_EASING",
        "stance_score": -1.5,        # -1.5 = Fast 50bp rate cutting cycle
        "cpi_trend": "Back within target (~2.2%)",
        "growth_pmi": "Contracting (Recessionary pressure)",
        "commodity_tier": "Dairy & Agricultural Exports",
        "base_macro_score": -1.8,    # Weak domestic economy, fast rate cuts
        "narrative": "RBNZ shifted from ultra-hawkish to aggressively dovish, cutting 50bp as New Zealand entered technical recession. Rapidly eroding yield advantage creates persistent structural headwind against USD and AUD."
    },
    "CHF": {
        "name": "Swiss Franc",
        "central_bank": "Swiss National Bank (SNB)",
        "policy_rate": 1.00,
        "policy_stance": "EASING_CYCLE",
        "stance_score": -0.8,        # -0.8 = Low yield, preemptive cuts to curb CHF strength
        "cpi_trend": "Very Low (~1.0%)",
        "growth_pmi": "Subdued (~49.0)",
        "commodity_tier": "Ultimate Geopolitical Safe-Haven",
        "base_macro_score": -0.5,    # Low yield, but unmatched crisis demand
        "narrative": "SNB is easing rates toward 0.75%-1.00% to discourage excessive Swiss Franc strength and deflation. Yield is low, meaning CHF acts purely as a geopolitical and systemic crisis hedge."
    },
    "XAU": {
        "name": "Gold (Spot)",
        "central_bank": "Global Central Banks (Reserve Asset)",
        "policy_rate": 0.0,
        "policy_stance": "STRUCTURAL_ACCUMULATION",
        "stance_score": 1.5,
        "cpi_trend": "Inflation Hedge",
        "growth_pmi": "Inverse Real Yields",
        "commodity_tier": "Monetary Metal / Geopolitical Hedge",
        "base_macro_score": 2.0,
        "narrative": "Supported by secular central bank gold purchases (de-dollarization), ballooning Western sovereign debt deficits, and ongoing Middle East/Eastern European geopolitical friction."
    }
}


class MacroEngine:
    """
    Central Macroeconomic & Fundamental Analysis Engine.
    Combines central bank policy divergence, interest rate differentials, and breaking news.
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.profiles = dict(CENTRAL_BANK_PROFILES)
        self._load_dynamic_news_overlay()

    def _load_dynamic_news_overlay(self):
        """Loads breaking news sentiment adjustments with 24-hour decay."""
        if not MACRO_NEWS_FILE.exists():
            return
        try:
            with open(MACRO_NEWS_FILE, "r", encoding="utf-8") as f:
                news_state = json.load(f)
            now_ts = datetime.now(timezone.utc).timestamp()
            # Prune records older than 24 hours (86,400s)
            cleaned = {}
            for curr, data in news_state.items():
                ts = data.get("timestamp", 0)
                if (now_ts - ts) < 86400:
                    cleaned[curr] = data
            if len(cleaned) != len(news_state):
                with open(MACRO_NEWS_FILE, "w", encoding="utf-8") as f:
                    json.dump(cleaned, f, indent=2)
        except Exception:
            pass

    def get_currency_macro_score(self, currency: str) -> float:
        """
        Calculates composite macro strength score (-5.0 to +5.0) for a currency,
        combining central bank baseline + dynamic news sentiment.
        """
        curr = currency.upper().strip()
        profile = self.profiles.get(curr, {})
        base_score = float(profile.get("base_macro_score", 0.0))

        # Dynamic news overlay
        news_adj = 0.0
        if MACRO_NEWS_FILE.exists():
            try:
                with open(MACRO_NEWS_FILE, "r", encoding="utf-8") as f:
                    news_data = json.load(f)
                if curr in news_data:
                    news_adj = float(news_data[curr].get("score_adjustment", 0.0))
            except Exception:
                pass

        total_score = round(max(-5.0, min(5.0, base_score + news_adj)), 2)
        return total_score

    def extract_currencies(self, symbol: str) -> Tuple[str, str]:
        """Extracts base and quote currency from any broker symbol string."""
        s = symbol.upper().replace(".C", "").replace("M", "").replace(".RAW", "")
        if "XAU" in s or "GOLD" in s:
            return "XAU", "USD"
        if len(s) >= 6:
            return s[:3], s[3:6]
        return s, "USD"

    def evaluate_pair_macro(self, symbol: str) -> Dict[str, Any]:
        """
        Conducts full institutional macroeconomic evaluation of a currency pair:
        - Interest Rate Differential (Carry)
        - Macroeconomic Strength Divergence
        - Directional Fundamental Bias (STRONG_BULLISH, BULLISH, NEUTRAL, BEARISH, STRONG_BEARISH)
        - Institutional Narrative
        """
        base_curr, quote_curr = self.extract_currencies(symbol)

        base_prof = self.profiles.get(base_curr, {
            "name": base_curr, "central_bank": "Unknown", "policy_rate": 2.50,
            "policy_stance": "NEUTRAL", "base_macro_score": 0.0, "narrative": "Neutral"
        })
        quote_prof = self.profiles.get(quote_curr, {
            "name": quote_curr, "central_bank": "Unknown", "policy_rate": 2.50,
            "policy_stance": "NEUTRAL", "base_macro_score": 0.0, "narrative": "Neutral"
        })

        base_rate = float(base_prof.get("policy_rate", 2.50))
        quote_rate = float(quote_prof.get("policy_rate", 2.50))
        rate_differential = round(base_rate - quote_rate, 2)

        base_score = self.get_currency_macro_score(base_curr)
        quote_score = self.get_currency_macro_score(quote_curr)
        divergence = round(base_score - quote_score, 2)

        # Factor in rate differential (+40% carry weighting in G10)
        # 1.0% positive interest rate gap adds +0.5 to divergence
        carry_contribution = round(rate_differential * 0.50, 2)
        total_divergence = round(divergence + (carry_contribution * 0.40), 2)

        # Determine Macro Bias
        if total_divergence >= 2.0:
            bias = "STRONG_BULLISH"
            bias_badge = "🟢🟢 STRONG BULLISH (LONGS ONLY)"
            preferred_dir = "BUY"
            color_emoji = "🟢"
        elif total_divergence >= 0.75:
            bias = "BULLISH"
            bias_badge = "🟢 BULLISH (FAVOR LONGS)"
            preferred_dir = "BUY"
            color_emoji = "🟢"
        elif total_divergence <= -2.0:
            bias = "STRONG_BEARISH"
            bias_badge = "🔴🔴 STRONG BEARISH (SHORTS ONLY)"
            preferred_dir = "SELL"
            color_emoji = "🔴"
        elif total_divergence <= -0.75:
            bias = "BEARISH"
            bias_badge = "🔴 BEARISH (FAVOR SHORTS)"
            preferred_dir = "SELL"
            color_emoji = "🔴"
        else:
            bias = "NEUTRAL"
            bias_badge = "⚪ BALANCED / NEUTRAL"
            preferred_dir = "ANY"
            color_emoji = "⚪"

        # Generate narrative
        narrative_parts = []
        if rate_differential > 1.0:
            narrative_parts.append(f"+{rate_differential:.2f}% positive carry advantage for {base_curr}")
        elif rate_differential < -1.0:
            narrative_parts.append(f"{rate_differential:.2f}% negative carry drag for {base_curr}")

        base_stance = base_prof.get("policy_stance", "NEUTRAL").replace("_", " ").title()
        quote_stance = quote_prof.get("policy_stance", "NEUTRAL").replace("_", " ").title()
        narrative_parts.append(f"{base_curr} ({base_prof.get('central_bank')}: {base_stance}) vs {quote_curr} ({quote_prof.get('central_bank')}: {quote_stance})")

        return {
            "symbol": symbol,
            "base_currency": base_curr,
            "quote_currency": quote_curr,
            "base_rate": base_rate,
            "quote_rate": quote_rate,
            "rate_differential": rate_differential,
            "base_score": base_score,
            "quote_score": quote_score,
            "divergence": total_divergence,
            "bias": bias,
            "bias_badge": bias_badge,
            "preferred_direction": preferred_dir,
            "color_emoji": color_emoji,
            "narrative": " | ".join(narrative_parts),
            "base_profile": base_prof,
            "quote_profile": quote_prof
        }

    def filter_and_score_signal(
        self,
        symbol: str,
        technical_direction: str,
        technical_score: float
    ) -> Tuple[bool, float, str, Dict[str, Any]]:
        """
        The Institutional Confluence Gatekeeper:
        1. Never fight strong fundamental tides (Hard reject on counter-trend setups)
        2. Soft penalty on mild counter-macro signals (forces technicals to be exceptional)
        3. Multiplier boost (e.g. +25%) on Macro-Aligned setups (Institutional Confluence)

        Returns: (is_allowed: bool, macro_modifier: float, reason: str, macro_details: dict)
        """
        macro = self.evaluate_pair_macro(symbol)
        macro_cfg = self.config.get("macro", {})
        if not macro_cfg.get("enabled", True):
            return True, 1.00, "Macro engine disabled via settings", macro

        bias = macro["bias"]
        tech_dir = technical_direction.upper()
        block_counter = macro_cfg.get("block_counter_macro", True)
        boost_mult = round(1.0 + (macro_cfg.get("confluence_boost_pct", 25.0) / 100.0), 2)
        penalty_mult = round(max(0.2, 1.0 - (macro_cfg.get("counter_macro_penalty_pct", 35.0) / 100.0)), 2)

        # Hard Rule 1: STRONG_BEARISH forbids BUY
        if block_counter and bias == "STRONG_BEARISH" and tech_dir == "BUY":
            return (
                False,
                0.0,
                f"COUNTER-MACRO HARD BLOCK: {macro['base_currency']}/{macro['quote_currency']} is STRONG BEARISH ({macro['divergence']:+.2f}). Longs forbidden against central bank tide.",
                macro
            )

        # Hard Rule 2: STRONG_BULLISH forbids SELL
        if block_counter and bias == "STRONG_BULLISH" and tech_dir == "SELL":
            return (
                False,
                0.0,
                f"COUNTER-MACRO HARD BLOCK: {macro['base_currency']}/{macro['quote_currency']} is STRONG BULLISH ({macro['divergence']:+.2f}). Shorts forbidden against central bank tide.",
                macro
            )

        # Confluence Rule 3: Perfect Alignment (Institutional Confluence Boost)
        if (bias in ("BULLISH", "STRONG_BULLISH") and tech_dir == "BUY") or \
           (bias in ("BEARISH", "STRONG_BEARISH") and tech_dir == "SELL"):
            reason = f"INSTITUTIONAL CONFLUENCE (+{int((boost_mult-1)*100)}% Boost): Technical {tech_dir} fully aligned with {bias} macro tide ({macro['divergence']:+.2f})."
            return True, boost_mult, reason, macro

        # Rule 4: Mild Counter-Macro Drag (Penalize)
        if (bias == "BEARISH" and tech_dir == "BUY") or (bias == "BULLISH" and tech_dir == "SELL"):
            reason = f"COUNTER-MACRO DRAG (-{int((1-penalty_mult)*100)}% Penalty): Technical {tech_dir} faces opposing {bias} fundamental headwind ({macro['divergence']:+.2f})."
            return True, penalty_mult, reason, macro

        # Rule 5: Neutral Macro
        modifier = 1.00
        reason = f"MACRO NEUTRAL (1.0x): {macro['base_currency']}/{macro['quote_currency']} fundamental divergence is balanced ({macro['divergence']:+.2f}). Technicals drive execution."
        return True, modifier, reason, macro
        reason = f"MACRO NEUTRAL (1.0x): {macro['base_currency']}/{macro['quote_currency']} fundamental divergence is balanced ({macro['divergence']:+.2f}). Technicals drive execution."
        return True, modifier, reason, macro

    def update_news_sentiment(self, currency: str, headline: str, score_delta: float):
        """
        Allows the breaking news desk to register high-impact macro headlines
        and adjust currency scores dynamically with timestamps.
        """
        curr = currency.upper().strip()
        data = {}
        if MACRO_NEWS_FILE.exists():
            try:
                with open(MACRO_NEWS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}

        now_ts = datetime.now(timezone.utc).timestamp()
        current_adj = data.get(curr, {}).get("score_adjustment", 0.0)
        new_adj = round(max(-2.0, min(2.0, current_adj + score_delta)), 2)

        data[curr] = {
            "score_adjustment": new_adj,
            "latest_headline": headline[:140],
            "timestamp": now_ts,
            "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        }

        DATA_DIR.mkdir(parents=True, exist_ok=True)
        with open(MACRO_NEWS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    def format_telegram_macro_summary(self, symbols: List[str]) -> str:
        """
        Generates a clean, comprehensive Macroeconomic & Fundamentals report
        formatted for mobile reading on Telegram.
        """
        now_str = datetime.now(timezone.utc).strftime("%b %d, %H:%M UTC")

        # 1. Currency Strength Rankings
        ranked_currs = sorted(
            ["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "NZD", "CHF"],
            key=lambda c: self.get_currency_macro_score(c),
            reverse=True
        )

        rank_lines = []
        for i, c in enumerate(ranked_currs, 1):
            score = self.get_currency_macro_score(c)
            p = self.profiles[c]
            rate = p.get("policy_rate", 0.0)
            stance = p.get("policy_stance", "NEUTRAL").replace("_", " ").title()
            rank_lines.append(f"{i}. *{c}*: `{score:+.1f}` (Rate: `{rate:.2f}%` | {stance})")

        # 2. Watchlist Pair Biases
        pair_lines = []
        for s in symbols:
            m = self.evaluate_pair_macro(s)
            clean_s = s.replace(".C", "").replace("m", "").replace(".raw", "").upper()
            pair_lines.append(
                f"{m['color_emoji']} *{clean_s}*: *{m['bias']}* (`{m['divergence']:+.1f}`)\n"
                f"   ↳ Diff: `{m['rate_differential']:+.2f}%` | {m['preferred_direction']} Bias"
            )

        msg = (
            f"🌐 *INSTITUTIONAL MACRO DESK*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🕒 *As of:* `{now_str}`\n"
            f"💡 *Philosophy:* _Fundamentals drive the tide; Technicals time the wave._\n\n"
            f"📊 *Currency Strength Rankings (G8):*\n" +
            "\n".join(rank_lines) +
            f"\n\n🧭 *Watchlist Macro Directional Biases:*\n" +
            "\n".join(pair_lines) +
            f"\n━━━━━━━━━━━━━━━━━━\n"
            f"🛡️ *Rule:* Counter-fundamental setups are penalized or blocked to prevent trading against Central Banks."
        )
        return msg
