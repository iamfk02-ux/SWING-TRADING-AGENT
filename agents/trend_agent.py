"""
Trend Agent v2.0 (Institutional Value-Zone Pullback Engine):
Eliminates "selling the bottom / buying the top" by enforcing 6 hard institutional entry rules:
1. Fresh Value-Zone Touch: Price must have touched the EMA20-EMA50 value zone within the last 1-2 bars
   (eliminates stale 20-hour lookbacks that entered at exhausted bottoms).
2. Dual Institutional Trigger:
   - Type A (Value-Zone Rejection): Candle wicks into EMA20-EMA50 resistance/support and closes strongly
     back in the trend direction (enters HIGH on shorts / LOW on longs, BEFORE the dump!).
   - Type B (Fresh Continuation): Previous bar touched EMA20 and current bar breaks cleanly without chasing.
3. Hard Anti-Chase Filter: Rejects entries where close is already stretched > 0.75 ATR away from EMA20.
4. Hard RSI Exhaustion Gate: Blocks SELL if RSI < 38 (oversold bottom); blocks BUY if RSI > 62 (overbought top).
5. Daily ADR Exhaustion Shield: Blocks entries if > 72% of the 14-day Average Daily Range is already spent.
6. Structural Room-to-Run Filter: Blocks selling directly into 20-bar support (< 0.75R room) or buying
   directly into 20-bar resistance.
7. Precision 50% Value-Zone Limit Entry: Pulls limit entries 50% back toward EMA20 for tighter stops and higher R:R.
"""

from typing import Dict, Any, Optional, List
import pandas as pd
import numpy as np

from core.indicators import compute_ema, compute_atr, compute_adx, compute_rsi
from agents.regime_agent import Regime


class TrendAgent:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        trend_cfg = config.get("strategies", {}).get("trend", {})
        self.ema_fast_len = trend_cfg.get("ema_fast", 20)
        self.ema_slow_len = trend_cfg.get("ema_slow", 50)
        self.atr_period = trend_cfg.get("atr_period", 14)
        self.pullback_lookback = trend_cfg.get("pullback_lookback", 2)  # Fresh 1-2 bar touch only
        self.max_close_beyond_slow_atr = trend_cfg.get("max_close_below_slow_atr", 0.5)
        self.stop_buffer_atr = trend_cfg.get("stop_buffer_atr", 1.0)    # Tighter structural stop behind pullback
        self.min_stop_atr = trend_cfg.get("min_stop_atr", 1.0)
        self.max_stop_atr = trend_cfg.get("max_stop_atr", 2.8)
        self.target_r = trend_cfg.get("target_r", 2.0)
        self.min_stop_pips = trend_cfg.get("min_stop_pips", 20.0)
        self.strong_adx = trend_cfg.get("strong_adx", 25)

        # Anti-chasing & exhaustion thresholds
        self.max_dist_from_ema_atr = trend_cfg.get("max_dist_from_ema_atr", 0.75)
        self.rsi_sell_floor = trend_cfg.get("rsi_sell_floor", 38.0)
        self.rsi_buy_ceiling = trend_cfg.get("rsi_buy_ceiling", 62.0)
        self.max_adr_used_pct = trend_cfg.get("max_adr_used_pct", 0.72)
        self.min_room_to_structure_r = trend_cfg.get("min_room_to_structure_r", 0.75)

    def _compute_adr_exhaustion(self, df_d1: pd.DataFrame, curr_close: float, direction: str) -> float:
        """
        Computes what fraction of the 14-day Average Daily Range (ADR) has already been spent
        in the trade direction today. Returns 0.0 to 1.5+.
        """
        if df_d1 is None or len(df_d1) < 15:
            return 0.0
        daily_ranges = (df_d1["high"] - df_d1["low"]).tail(15)
        adr_14 = float(daily_ranges.iloc[:-1].mean()) if len(daily_ranges) > 1 else float(daily_ranges.mean())
        if adr_14 <= 0 or np.isnan(adr_14):
            return 0.0

        today_bar = df_d1.iloc[-1]
        if direction == "SELL":
            used = (float(today_bar["high"]) - curr_close) / adr_14
        else:
            used = (curr_close - float(today_bar["low"])) / adr_14
        return max(0.0, used)

    def scan_for_signal(
        self,
        symbol: str,
        df_d1: pd.DataFrame,
        df_lower: pd.DataFrame,
        current_regime: str
    ) -> Optional[Dict[str, Any]]:
        """
        Scans the latest bar of df_lower (H1 or H4) against D1 regime using Institutional Value-Zone Pullback rules.
        """
        if current_regime not in [Regime.TRENDING_UP.value, Regime.TRENDING_DOWN.value]:
            return None

        if len(df_lower) < max(self.ema_slow_len, self.atr_period) + 25:
            return None

        # Detect timeframe (H1 vs H4)
        is_h1 = False
        if len(df_lower) >= 2:
            delta_sec = (pd.to_datetime(df_lower["timestamp"].iloc[-1]) - pd.to_datetime(df_lower["timestamp"].iloc[-2])).total_seconds()
            if delta_sec <= 7200:
                is_h1 = True
        tf_name = "H1" if is_h1 else "H4"

        # Calculate lower timeframe indicators
        lower_ema_fast = compute_ema(df_lower["close"], self.ema_fast_len)
        lower_ema_slow = compute_ema(df_lower["close"], self.ema_slow_len)
        lower_atr = compute_atr(df_lower, self.atr_period)
        lower_rsi = compute_rsi(df_lower["close"], 14)
        d1_adx = float(compute_adx(df_d1, 14)["adx"].iloc[-1])

        curr_idx = len(df_lower) - 1
        curr_bar = df_lower.iloc[curr_idx]
        prev_bar = df_lower.iloc[curr_idx - 1]
        atr_val = float(lower_atr.iloc[curr_idx])
        rsi_val = float(lower_rsi.iloc[curr_idx]) if not np.isnan(lower_rsi.iloc[curr_idx]) else 50.0
        ema20_curr = float(lower_ema_fast.iloc[curr_idx])
        ema50_curr = float(lower_ema_slow.iloc[curr_idx])
        ema20_prev = float(lower_ema_fast.iloc[curr_idx - 1])

        if atr_val <= 0 or np.isnan(atr_val):
            return None

        # Fresh pullback window: strictly last 2 bars (current + previous)
        fresh_lookback = 3 if is_h1 else 2
        window_start = max(0, curr_idx - fresh_lookback)
        pullback_window = df_lower.iloc[window_start:curr_idx + 1]

        # 20-bar structural support/resistance (excluding current bar)
        struct_window = df_lower.iloc[max(0, curr_idx - 20):curr_idx]
        struct_low = float(struct_window["low"].min())
        struct_high = float(struct_window["high"].max())

        bar_range = float(curr_bar["high"] - curr_bar["low"])
        pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
        min_stop_dist = self.min_stop_pips * pip_size

        # -----------------------------
        # LONG SETUP (Trending Up)
        # -----------------------------
        if current_regime == Regime.TRENDING_UP.value:
            # 0. Lower timeframe structure must align with D1 uptrend (EMA20 >= EMA50)
            if ema20_curr < ema50_curr:
                return None

            # 1. Hard RSI Exhaustion Gate: Never buy an overbought top!
            if rsi_val > self.rsi_buy_ceiling:
                return None

            # 2. Hard Daily ADR Exhaustion Shield
            adr_used = self._compute_adr_exhaustion(df_d1, float(curr_bar["close"]), "BUY")
            if adr_used > self.max_adr_used_pct:
                return None

            # 3. Fresh Pullback to Value Zone (EMA20 +/- 0.15 ATR) within last 1-2 bars
            curr_touched = float(curr_bar["low"]) <= ema20_curr + (0.15 * atr_val)
            prev_touched = float(prev_bar["low"]) <= ema20_prev + (0.15 * atr_val)
            if not (curr_touched or prev_touched):
                return None

            # Must not close below EMA50 by more than 0.5 ATR
            violated_slow = (
                pullback_window["close"] < (lower_ema_slow.iloc[window_start:curr_idx + 1] - (self.max_close_beyond_slow_atr * atr_val))
            ).any()
            if violated_slow:
                return None

            # 4. Institutional Trigger: Strict Value-Zone Rejection off EMA20 (Never chase breakouts/extremes)
            close_pos = ((float(curr_bar["close"]) - float(curr_bar["low"])) / bar_range) if bar_range > 0 else 0.5
            is_value_rejection = (
                curr_touched
                and float(curr_bar["close"]) > float(curr_bar["open"])
                and float(curr_bar["close"]) > ema20_curr
                and close_pos >= 0.60
            )
            if not is_value_rejection:
                return None

            # 5. Hard Anti-Chase Filter: Close must not be stretched > 0.75 ATR above EMA20
            dist_from_ema = (float(curr_bar["close"]) - ema20_curr) / atr_val
            if dist_from_ema > self.max_dist_from_ema_atr:
                return None

            # 6. Precision Limit Entry: 50% retracement back toward EMA20 value zone
            limit_price = round(float(curr_bar["close"]) - 0.50 * (float(curr_bar["close"]) - ema20_curr), 5)
            if limit_price >= float(curr_bar["close"]):
                limit_price = round(float(curr_bar["close"]) - 0.10 * atr_val, 5)

            # 7. Structural Stop Loss behind pullback low
            swing_low = float(pullback_window["low"].min())
            sl = swing_low - (self.stop_buffer_atr * atr_val)
            stop_dist = limit_price - sl

            if stop_dist < min_stop_dist:
                stop_dist = min_stop_dist
                sl = round(limit_price - min_stop_dist, 5)

            if stop_dist > self.max_stop_atr * atr_val and stop_dist > (min_stop_dist * 2.5):
                return None

            # 8. Structural Room-to-Run Check: Do not buy directly into 20-bar resistance wall
            if limit_price < struct_high:
                room_to_res = struct_high - limit_price
                if room_to_res < (self.min_room_to_structure_r * stop_dist):
                    return None

            # Multi-Factor Confluence Scoring
            confidence = 0.55
            confluence_reasons = []

            if is_value_rejection:
                confidence += 0.10
                confluence_reasons.append("Value-Zone EMA20 Rejection")
            else:
                confluence_reasons.append("Fresh EMA20 Continuation")

            if d1_adx >= self.strong_adx:
                confidence += 0.10
                confluence_reasons.append(f"D1 ADX {d1_adx:.1f} >= {self.strong_adx}")

            if 38.0 <= rsi_val <= 58.0:
                confidence += 0.10
                confluence_reasons.append(f"Optimal RSI value zone ({rsi_val:.1f})")

            if close_pos >= 0.65:
                confidence += 0.05
                confluence_reasons.append("Strong bullish rejection close")

            if not (pullback_window["close"] < lower_ema_slow.iloc[window_start:curr_idx + 1]).any():
                confidence += 0.05
                confluence_reasons.append("EMA50 cleanly respected")

            if adr_used <= 0.45:
                confidence += 0.05
                confluence_reasons.append(f"Fresh daily range ({adr_used*100:.0f}% ADR)")

            r_dist = abs(limit_price - sl)
            initial_tp = round(limit_price + (self.target_r * r_dist), 5)

            return {
                "symbol": symbol,
                "strategy": "TREND",
                "timeframe": tf_name,
                "direction": "BUY",
                "order_type": "LIMIT",
                "entry": float(limit_price),
                "market_price": float(curr_bar["close"]),
                "stop_loss": float(sl),
                "take_profit": float(initial_tp),
                "confidence": min(1.0, round(confidence, 2)),
                "confluence": confluence_reasons,
                "regime": current_regime,
                "reason": f"D1 uptrend Value-Zone {tf_name} pullback ({', '.join(confluence_reasons)}): Buy Limit @ {limit_price:.5f}",
                "timestamp": str(curr_bar["timestamp"])
            }

        # -----------------------------
        # SHORT SETUP (Trending Down)
        # -----------------------------
        if current_regime == Regime.TRENDING_DOWN.value:
            # 0. Lower timeframe structure must align with D1 downtrend (EMA20 <= EMA50)
            if ema20_curr > ema50_curr:
                return None

            # 1. Hard RSI Exhaustion Gate: Never sell an oversold bottom!
            if rsi_val < self.rsi_sell_floor:
                return None

            # 2. Hard Daily ADR Exhaustion Shield
            adr_used = self._compute_adr_exhaustion(df_d1, float(curr_bar["close"]), "SELL")
            if adr_used > self.max_adr_used_pct:
                return None

            # 3. Fresh Pullback to Value Zone (EMA20 +/- 0.15 ATR) within last 1-2 bars
            curr_touched = float(curr_bar["high"]) >= ema20_curr - (0.15 * atr_val)
            prev_touched = float(prev_bar["high"]) >= ema20_prev - (0.15 * atr_val)
            if not (curr_touched or prev_touched):
                return None

            # Must not close above EMA50 by more than 0.5 ATR
            violated_slow = (
                pullback_window["close"] > (lower_ema_slow.iloc[window_start:curr_idx + 1] + (self.max_close_beyond_slow_atr * atr_val))
            ).any()
            if violated_slow:
                return None

            # 4. Institutional Trigger: Strict Value-Zone Rejection off EMA20 (Never chase breakdowns/bottoms)
            close_pos_bear = ((float(curr_bar["high"]) - float(curr_bar["close"])) / bar_range) if bar_range > 0 else 0.5
            is_value_rejection = (
                curr_touched
                and float(curr_bar["close"]) < float(curr_bar["open"])
                and float(curr_bar["close"]) < ema20_curr
                and close_pos_bear >= 0.60
            )
            if not is_value_rejection:
                return None

            # 5. Hard Anti-Chase Filter: Close must not be stretched > 0.75 ATR below EMA20
            dist_from_ema = (ema20_curr - float(curr_bar["close"])) / atr_val
            if dist_from_ema > self.max_dist_from_ema_atr:
                return None

            # 6. Precision Limit Entry: 50% retracement back toward EMA20 value zone
            limit_price = round(float(curr_bar["close"]) + 0.50 * (ema20_curr - float(curr_bar["close"])), 5)
            if limit_price <= float(curr_bar["close"]):
                limit_price = round(float(curr_bar["close"]) + 0.10 * atr_val, 5)

            # 7. Structural Stop Loss behind pullback high
            swing_high = float(pullback_window["high"].max())
            sl = swing_high + (self.stop_buffer_atr * atr_val)
            stop_dist = sl - limit_price

            if stop_dist < min_stop_dist:
                stop_dist = min_stop_dist
                sl = round(limit_price + min_stop_dist, 5)

            if stop_dist > self.max_stop_atr * atr_val and stop_dist > (min_stop_dist * 2.5):
                return None

            # 8. Structural Room-to-Run Check: Do not sell directly into 20-bar support floor
            if limit_price > struct_low:
                room_to_sup = limit_price - struct_low
                if room_to_sup < (self.min_room_to_structure_r * stop_dist):
                    return None

            # Multi-Factor Confluence Scoring
            confidence = 0.55
            confluence_reasons = []

            if is_value_rejection:
                confidence += 0.10
                confluence_reasons.append("Value-Zone EMA20 Rejection")
            else:
                confluence_reasons.append("Fresh EMA20 Continuation")

            if d1_adx >= self.strong_adx:
                confidence += 0.10
                confluence_reasons.append(f"D1 ADX {d1_adx:.1f} >= {self.strong_adx}")

            if 42.0 <= rsi_val <= 62.0:
                confidence += 0.10
                confluence_reasons.append(f"Optimal RSI value zone ({rsi_val:.1f})")

            if close_pos_bear >= 0.65:
                confidence += 0.05
                confluence_reasons.append("Strong bearish rejection close")

            if not (pullback_window["close"] > lower_ema_slow.iloc[window_start:curr_idx + 1]).any():
                confidence += 0.05
                confluence_reasons.append("EMA50 cleanly respected")

            if adr_used <= 0.45:
                confidence += 0.05
                confluence_reasons.append(f"Fresh daily range ({adr_used*100:.0f}% ADR)")

            r_dist = abs(sl - limit_price)
            initial_tp = round(limit_price - (self.target_r * r_dist), 5)

            return {
                "symbol": symbol,
                "strategy": "TREND",
                "timeframe": tf_name,
                "direction": "SELL",
                "order_type": "LIMIT",
                "entry": float(limit_price),
                "market_price": float(curr_bar["close"]),
                "stop_loss": float(sl),
                "take_profit": float(initial_tp),
                "confidence": min(1.0, round(confidence, 2)),
                "confluence": confluence_reasons,
                "regime": current_regime,
                "reason": f"D1 downtrend Value-Zone {tf_name} pullback ({', '.join(confluence_reasons)}): Sell Limit @ {limit_price:.5f}",
                "timestamp": str(curr_bar["timestamp"])
            }

        return None
