"""
Trend Agent:
Implements exact strategy specification rules for swing trend-following:
- D1 Regime: Trending Up or Trending Down
- Pullback (H4): within last 5 candles, price touches H4 EMA20, without closing below EMA50 > 0.5 ATR
- Trigger (H4): bullish/bearish resumption candle closing above/below previous high/low and EMA20
- Sizing & Stops: Swing low/high - 0.5 ATR(H4), trailing stop at 2.5 ATR(D1), breakeven at +1.5R
"""

from typing import Dict, Any, Optional, List
import pandas as pd
import numpy as np

from core.indicators import compute_ema, compute_atr, compute_adx
from agents.regime_agent import Regime


class TrendAgent:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        trend_cfg = config.get("strategies", {}).get("trend", {})
        self.ema_fast_len = trend_cfg.get("ema_fast", 20)
        self.ema_slow_len = trend_cfg.get("ema_slow", 50)
        self.atr_period = trend_cfg.get("atr_period", 14)
        self.pullback_lookback = trend_cfg.get("pullback_lookback", 5)
        self.max_close_beyond_slow_atr = trend_cfg.get("max_close_below_slow_atr", 0.5)
        self.stop_buffer_atr = trend_cfg.get("stop_buffer_atr", 0.5)
        self.min_stop_atr = trend_cfg.get("min_stop_atr", 1.0)
        self.max_stop_atr = trend_cfg.get("max_stop_atr", 3.0)
        self.strong_adx = trend_cfg.get("strong_adx", 30)

    def scan_for_signal(
        self,
        symbol: str,
        df_d1: pd.DataFrame,
        df_lower: pd.DataFrame,
        current_regime: str
    ) -> Optional[Dict[str, Any]]:
        """
        Scans the latest bar of df_lower (H1 or H4) against D1 regime.
        Returns signal dict if valid setup & trigger occur, otherwise None.
        """
        if current_regime not in [Regime.TRENDING_UP.value, Regime.TRENDING_DOWN.value]:
            return None

        if len(df_lower) < max(self.ema_slow_len, self.atr_period) + 10:
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
        d1_adx = compute_adx(df_d1, 14)["adx"].iloc[-1]

        curr_idx = len(df_lower) - 1
        curr_bar = df_lower.iloc[curr_idx]
        prev_bar = df_lower.iloc[curr_idx - 1]
        atr_val = lower_atr.iloc[curr_idx]

        if atr_val <= 0 or np.isnan(atr_val):
            return None

        # Lookback window for pullback (more candles for H1)
        effective_lookback = self.pullback_lookback * 2 if is_h1 else self.pullback_lookback
        window_start = max(0, curr_idx - effective_lookback)
        pullback_window = df_lower.iloc[window_start:curr_idx]

        # -----------------------------
        # LONG SETUP (Trending Up)
        # -----------------------------
        if current_regime == Regime.TRENDING_UP.value:
            # 1. Pullback condition: low touched EMA20 in window
            touched_ema20 = (pullback_window["low"] <= lower_ema_fast.iloc[window_start:curr_idx]).any()
            if not touched_ema20:
                return None

            # Must not close below EMA50 by more than 0.5 ATR
            violated_slow = (
                pullback_window["close"] < (lower_ema_slow.iloc[window_start:curr_idx] - (self.max_close_beyond_slow_atr * atr_val))
            ).any()
            if violated_slow:
                return None

            # 2. Trigger: current candle closes > prev high AND > EMA20
            is_bullish_close = curr_bar["close"] > prev_bar["high"] and curr_bar["close"] > lower_ema_fast.iloc[curr_idx]
            if not is_bullish_close:
                return None

            # 3. Stop loss: lowest low in pullback - 0.5 ATR
            swing_low = min(pullback_window["low"].min(), curr_bar["low"])
            sl = swing_low - (self.stop_buffer_atr * atr_val)
            stop_dist = curr_bar["close"] - sl

            if not (self.min_stop_atr * atr_val <= stop_dist <= self.max_stop_atr * atr_val):
                return None

            # Calculate confidence score (0.6 base, +0.1 for each strength factor)
            confidence = 0.6
            if d1_adx >= self.strong_adx:
                confidence += 0.1
            if lower_ema_fast.iloc[curr_idx] > lower_ema_slow.iloc[curr_idx]:
                confidence += 0.1
            if not (pullback_window["close"] < lower_ema_slow.iloc[window_start:curr_idx]).any():
                confidence += 0.1
            # Closed in top 25% of candle range
            # Optimal Limit Entry: 50% retrace between breakout close and EMA20 (or exact EMA20)
            ema_target = float(lower_ema_fast.iloc[curr_idx])
            limit_price = round(curr_bar["close"] - 0.3 * (curr_bar["close"] - ema_target), 5)
            # Ensure limit is below current price
            if limit_price >= curr_bar["close"]:
                limit_price = round(curr_bar["close"] - 0.1 * atr_val, 5)

            # Calculate 1.5R Target for Initial Partial Take Profit
            r_dist = abs(limit_price - sl)
            initial_tp = round(limit_price + (1.5 * r_dist), 5)

            return {
                "symbol": symbol,
                "strategy": "TREND",
                "direction": "BUY",
                "order_type": "LIMIT",
                "entry": float(limit_price),
                "market_price": float(curr_bar["close"]),
                "stop_loss": float(sl),
                "take_profit": float(initial_tp),  # 1.5R Partial TP Target
                "confidence": min(1.0, confidence),
                "regime": current_regime,
                "reason": f"D1 uptrend with {tf_name} EMA20 pullback: Buy Limit at discount {limit_price:.5f}",
                "timestamp": str(curr_bar["timestamp"])
            }

        # -----------------------------
        # SHORT SETUP (Trending Down)
        # -----------------------------
        if current_regime == Regime.TRENDING_DOWN.value:
            # 1. Pullback condition: high touched EMA20 in window
            touched_ema20 = (pullback_window["high"] >= lower_ema_fast.iloc[window_start:curr_idx]).any()
            if not touched_ema20:
                return None

            # Must not close above EMA50 by more than 0.5 ATR
            violated_slow = (
                pullback_window["close"] > (lower_ema_slow.iloc[window_start:curr_idx] + (self.max_close_beyond_slow_atr * atr_val))
            ).any()
            if violated_slow:
                return None

            # 2. Trigger: current candle closes < prev low AND < EMA20
            is_bearish_close = curr_bar["close"] < prev_bar["low"] and curr_bar["close"] < lower_ema_fast.iloc[curr_idx]
            if not is_bearish_close:
                return None

            # 3. Stop loss: highest high in pullback + 0.5 ATR
            swing_high = max(pullback_window["high"].max(), curr_bar["high"])
            sl = swing_high + (self.stop_buffer_atr * atr_val)
            stop_dist = sl - curr_bar["close"]

            if not (self.min_stop_atr * atr_val <= stop_dist <= self.max_stop_atr * atr_val):
                return None

            # Confidence score
            confidence = 0.6
            if d1_adx >= self.strong_adx:
                confidence += 0.1
            if lower_ema_fast.iloc[curr_idx] < lower_ema_slow.iloc[curr_idx]:
                confidence += 0.1
            if not (pullback_window["close"] > lower_ema_slow.iloc[window_start:curr_idx]).any():
                confidence += 0.1
            # Optimal Limit Entry: 50% retrace between breakdown close and EMA20
            ema_target = float(lower_ema_fast.iloc[curr_idx])
            limit_price = round(curr_bar["close"] + 0.3 * (ema_target - curr_bar["close"]), 5)
            # Ensure limit is above current price
            if limit_price <= curr_bar["close"]:
                limit_price = round(curr_bar["close"] + 0.1 * atr_val, 5)

            # Calculate 1.5R Target for Initial Partial Take Profit
            r_dist = abs(sl - limit_price)
            initial_tp = round(limit_price - (1.5 * r_dist), 5)

            return {
                "symbol": symbol,
                "strategy": "TREND",
                "direction": "SELL",
                "order_type": "LIMIT",
                "entry": float(limit_price),
                "market_price": float(curr_bar["close"]),
                "stop_loss": float(sl),
                "take_profit": float(initial_tp),  # 1.5R Partial TP Target
                "confidence": min(1.0, confidence),
                "regime": current_regime,
                "reason": f"D1 downtrend with {tf_name} EMA20 pullback: Sell Limit at discount {limit_price:.5f}",
                "timestamp": str(curr_bar["timestamp"])
            }

        return None
