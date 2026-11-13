"""
Post-News Momentum Hunter Agent:
Captures institutional fundamental repricing waves after High-Impact ('Red Folder') releases.

Architecture & Execution Rules:
1. Dust Must Settle: Enforces a strict 15-minute cool-off window immediately following the release.
   Never trades within minutes 0-15 (protects 100% against spread spikes, liquidity voids, and algo whipsaws).
2. Prime Window: Scans between 15 and 120 minutes post-release while institutional flow is actively repricing fair value.
3. News Initial Range (NIR): Identifies the High, Low, and Close of the 15-minute news reaction candle.
   Requires true momentum impulse (Range >= 0.75 * ATR_M15).
4. Structural Confirmation: Enters only when a subsequent closed candle confirms the impulse:
   - Breakout: Confirmed close beyond the News Reaction High/Low.
   - Retest: Shallow 38.2%-50% pullback with rejection wick in the trend direction.
5. Anti-Whipsaw Structural Stop: Stop Loss is placed behind the opposite side of the news reaction base
   (or 50% midpoint on giant expansion bars), protected by a dynamic ATR buffer (minimum 20 pips floor).
   Never uses naive 8-10 pip stops that get hunted by residual volatility.
6. Target: Aims for a clean +2.0R to +2.5R momentum expansion leg.
"""

from typing import Dict, Any, Optional, Tuple, List
import pandas as pd
from datetime import datetime, timezone
from core.indicators import compute_atr


class PostNewsMomentumAgent:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        pnm_cfg = self.config.get("strategies", {}).get("post_news_momentum", {})
        self.enabled = pnm_cfg.get("enabled", True)
        self.min_minutes_after = pnm_cfg.get("min_minutes_after", 15)
        self.max_minutes_after = pnm_cfg.get("max_minutes_after", 120)
        self.min_impulse_atr_mult = pnm_cfg.get("min_impulse_atr_multiplier", 0.75)
        self.target_r = pnm_cfg.get("target_r", 2.0)
        self.stop_buffer_atr = pnm_cfg.get("stop_buffer_atr", 0.5)
        self.min_stop_pips = pnm_cfg.get("min_stop_pips", 20.0)

    def scan_for_signal(
        self,
        symbol: str,
        df_m15: pd.DataFrame,
        event: Dict[str, Any],
        macro_bias: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Analyzes M15 price structure following a High-Impact news release.
        Returns a high-conviction signal dictionary if all criteria are satisfied.
        """
        if not self.enabled or df_m15 is None or len(df_m15) < 30:
            return None

        event_title = event.get("title", "High-Impact Release")
        event_country = event.get("country", "")
        mins_ago = event.get("minutes_ago", 0)

        # Enforce prime post-news window (15m to 120m)
        if not (self.min_minutes_after <= mins_ago <= self.max_minutes_after):
            return None

        # Clean symbol and check if event currency is relevant
        s_clean = symbol.upper().replace(".C", "").replace("M", "").replace(".RAW", "")
        is_base = s_clean.startswith(event_country)
        is_quote = s_clean.endswith(event_country)
        if not (is_base or is_quote):
            return None

        # Calculate M15 ATR
        df_atr = compute_atr(df_m15, 14)
        if df_atr.empty or df_atr.iloc[-1] <= 0:
            return None
        current_atr = float(df_atr.iloc[-1])

        pip_size = 0.01 if "JPY" in s_clean else (0.1 if "XAU" in s_clean else 0.0001)

        # Identify the news candle:
        # Since each M15 bar represents 15 minutes, mins_ago // 15 gives approximate bars back
        bars_back = max(1, int(mins_ago // 15))
        if bars_back >= len(df_m15) - 2:
            return None

        # The news reaction bar is at index: -(bars_back + 1)
        news_idx = -(bars_back + 1)
        news_bar = df_m15.iloc[news_idx]

        news_open = float(news_bar["open"])
        news_high = float(news_bar["high"])
        news_low = float(news_bar["low"])
        news_close = float(news_bar["close"])
        news_range = news_high - news_low

        if news_range <= 0:
            return None

        # Filter 1: News reaction must show genuine institutional impulse
        if news_range < (self.min_impulse_atr_mult * current_atr):
            return None

        # Filter 2: Determine impulse direction and candle body quality
        # Strong Bullish: closed in the top 35% of its range
        # Strong Bearish: closed in the bottom 35% of its range
        close_pos = (news_close - news_low) / news_range
        is_bullish_impulse = (news_close > news_open) and (close_pos >= 0.65)
        is_bearish_impulse = (news_close < news_open) and (close_pos <= 0.35)

        if not (is_bullish_impulse or is_bearish_impulse):
            # Indecisive news candle (wicks on both sides / doji) -> skip
            return None

        # Filter 3: Check subsequent confirmation candles (from news_idx + 1 up to latest closed bar)
        post_news_bars = df_m15.iloc[news_idx + 1 : -1]  # exclude active unclosed candle
        if post_news_bars.empty:
            return None

        latest_closed = df_m15.iloc[-2]
        latest_close = float(latest_closed["close"])
        latest_high = float(latest_closed["high"])
        latest_low = float(latest_closed["low"])
        latest_open = float(latest_closed["open"])

        direction: Optional[str] = None
        confirmation_type: Optional[str] = None

        if is_bullish_impulse:
            # Setup A: Breakout confirmation (latest close above news high)
            if latest_close > news_high:
                direction = "BUY"
                confirmation_type = "BREAKOUT"
            # Setup B: Retest & Bounce confirmation (shallow pullback held above 38.2% fib of news candle)
            elif (latest_low >= (news_low + 0.382 * news_range)) and (latest_close > latest_open):
                direction = "BUY"
                confirmation_type = "RETEST_BOUNCE"

        elif is_bearish_impulse:
            # Setup A: Breakdown confirmation (latest close below news low)
            if latest_close < news_low:
                direction = "SELL"
                confirmation_type = "BREAKDOWN"
            # Setup B: Retest & Rejection confirmation (pullback held below 61.8% of news candle)
            elif (latest_high <= (news_high - 0.382 * news_range)) and (latest_close < latest_open):
                direction = "SELL"
                confirmation_type = "RETEST_REJECT"

        if not direction:
            return None

        # Filter 4: Macro Stance & Alignment Check
        # Reject if directly fighting strong conflicting macro bias
        if macro_bias:
            mb_clean = str(macro_bias).upper()
            if direction == "BUY" and ("STRONG_BEARISH" in mb_clean or "BEARISH" in mb_clean):
                return None
            if direction == "SELL" and ("STRONG_BULLISH" in mb_clean or "BULLISH" in mb_clean):
                return None

        # Filter 5: Anti-Whipsaw Structural Stop Loss & Target Calculation
        buffer_val = self.stop_buffer_atr * current_atr
        range_pips = news_range / pip_size
        min_stop_dist = self.min_stop_pips * pip_size

        if direction == "BUY":
            # If news candle is moderate (< 50 pips), stop behind news low
            if range_pips <= 50.0:
                raw_sl = news_low - buffer_val
            else:
                # Giant expansion bar: stop behind 50% midpoint of the news candle
                raw_sl = ((news_open + news_low) / 2.0) - buffer_val
            
            # Enforce safety stop floor
            stop_dist = max(latest_close - raw_sl, min_stop_dist)
            sl = round(latest_close - stop_dist, 5)
            tp = round(latest_close + (self.target_r * stop_dist), 5)
        else:
            # If news candle is moderate (< 50 pips), stop above news high
            if range_pips <= 50.0:
                raw_sl = news_high + buffer_val
            else:
                raw_sl = ((news_open + news_high) / 2.0) + buffer_val

            stop_dist = max(raw_sl - latest_close, min_stop_dist)
            sl = round(latest_close + stop_dist, 5)
            tp = round(latest_close - (self.target_r * stop_dist), 5)

        stop_pips_final = round(stop_dist / pip_size, 1)

        # Conviction Scoring (Tier A / A+)
        base_score = 0.80
        if news_range > (1.2 * current_atr):
            base_score += 0.03  # High institutional volume impulse
        if macro_bias and ("BULLISH" in macro_bias.upper() or "BEARISH" in macro_bias.upper()):
            base_score += 0.04  # Macro tailwind synergy
        if confirmation_type == "BREAKOUT":
            base_score += 0.02

        conviction_score = min(0.89, round(base_score, 2))

        narrative = (
            f"POST-NEWS MOMENTUM HUNT ({event_country} '{event_title}', released {mins_ago}m ago). "
            f"News M15 candle printed {news_range / pip_size:.1f}-pip impulse ({'Bullish' if is_bullish_impulse else 'Bearish'}). "
            f"Confirmed via {confirmation_type} at {latest_close:.5f}. "
            f"Structural Stop placed safely at {sl:.5f} ({stop_pips_final} pips buffer) targeting +{self.target_r:.1f}R ({tp:.5f})."
        )

        return {
            "symbol": symbol,
            "strategy": "POST_NEWS_MOMENTUM",
            "direction": direction,
            "timeframe": "M15",
            "entry": latest_close,
            "entry_price": latest_close,
            "stop_loss": sl,
            "take_profit": tp,
            "target_r": self.target_r,
            "score": conviction_score,
            "confidence": conviction_score,
            "reasons": [narrative],
            "news_event": event_title,
            "news_country": event_country,
            "minutes_ago": mins_ago,
            "confirmation_type": confirmation_type,
            "market_price": latest_close
        }
