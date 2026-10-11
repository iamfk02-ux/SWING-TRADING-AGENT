"""
Breakout Agent:
Implements volatility-squeeze and Donchian range breakout logic:
- Active when Regime = COMPRESSION (full size) or TRENDING (with-trend only)
- Range: D1 Donchian 20-day high/low (excluding current day)
- Trigger (D1): Daily close breaks out beyond 20-day extreme by >= 0.2 * ATR(D1), candle body >= 50% of range
- Stops & Exits: Stop at breakout level - 1.0 ATR, failed breakout cutoff, trailing stop after +2R
"""

from typing import Dict, Any, Optional
import pandas as pd
import numpy as np

from core.indicators import compute_donchian, compute_atr, compute_bollinger_bands, compute_ema
from agents.regime_agent import Regime


class BreakoutAgent:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        bo_cfg = config.get("strategies", {}).get("breakout", {})
        self.donchian_period = bo_cfg.get("donchian_period", 20)
        self.atr_period = bo_cfg.get("atr_period", 14)
        self.breakout_buffer_atr = bo_cfg.get("breakout_buffer_atr", 0.2)
        self.stop_buffer_atr = bo_cfg.get("stop_buffer_atr", 1.0)
        self.min_body_ratio = bo_cfg.get("min_body_ratio", 0.5)

    def scan_for_signal(
        self,
        symbol: str,
        df_d1: pd.DataFrame,
        current_regime: str
    ) -> Optional[Dict[str, Any]]:
        """
        Scans latest closed D1 bar for breakout setups.
        """
        if current_regime not in [Regime.COMPRESSION.value, Regime.TRENDING_UP.value, Regime.TRENDING_DOWN.value]:
            return None

        if len(df_d1) < max(self.donchian_period, self.atr_period) + 10:
            return None

        donchian = compute_donchian(df_d1, self.donchian_period)
        atr_series = compute_atr(df_d1, self.atr_period)
        ema200 = compute_ema(df_d1["close"], 200)

        curr_idx = len(df_d1) - 1
        curr_bar = df_d1.iloc[curr_idx]
        atr_val = atr_series.iloc[curr_idx]
        d_high = donchian["donchian_high"].iloc[curr_idx]
        d_low = donchian["donchian_low"].iloc[curr_idx]

        if np.isnan(atr_val) or atr_val <= 0 or np.isnan(d_high) or np.isnan(d_low):
            return None

        candle_range = curr_bar["high"] - curr_bar["low"]
        candle_body = abs(curr_bar["close"] - curr_bar["open"])
        body_ratio = candle_body / candle_range if candle_range > 0 else 0.0

        # -----------------------------
        # BULLISH BREAKOUT
        # -----------------------------
        if (curr_bar["close"] >= d_high + (self.breakout_buffer_atr * atr_val)) and (body_ratio >= self.min_body_ratio):
            # Only allowed if Compression or Trending Up
            if current_regime in [Regime.COMPRESSION.value, Regime.TRENDING_UP.value]:
                sl = d_high - (self.stop_buffer_atr * atr_val)
                stop_dist = curr_bar["close"] - sl

                if 1.0 * atr_val <= stop_dist <= 2.5 * atr_val:
                    confidence = 0.6
                    if curr_bar["close"] > ema200.iloc[curr_idx]:
                        confidence += 0.1
                    if candle_range >= 1.2 * atr_val:
                        confidence += 0.1

                    return {
                        "symbol": symbol,
                        "strategy": "BREAKOUT",
                        "direction": "BUY",
                        "entry": float(curr_bar["close"]),
                        "stop_loss": float(sl),
                        "take_profit": None,
                        "confidence": min(1.0, confidence),
                        "regime": current_regime,
                        "reason": f"D1 Bullish Donchian breakout above {d_high:.5f} with strong candle body",
                        "timestamp": str(curr_bar["timestamp"])
                    }

        # -----------------------------
        # BEARISH BREAKOUT
        # -----------------------------
        if (curr_bar["close"] <= d_low - (self.breakout_buffer_atr * atr_val)) and (body_ratio >= self.min_body_ratio):
            if current_regime in [Regime.COMPRESSION.value, Regime.TRENDING_DOWN.value]:
                sl = d_low + (self.stop_buffer_atr * atr_val)
                stop_dist = sl - curr_bar["close"]

                if 1.0 * atr_val <= stop_dist <= 2.5 * atr_val:
                    confidence = 0.6
                    if curr_bar["close"] < ema200.iloc[curr_idx]:
                        confidence += 0.1
                    if candle_range >= 1.2 * atr_val:
                        confidence += 0.1

                    return {
                        "symbol": symbol,
                        "strategy": "BREAKOUT",
                        "direction": "SELL",
                        "entry": float(curr_bar["close"]),
                        "stop_loss": float(sl),
                        "take_profit": None,
                        "confidence": min(1.0, confidence),
                        "regime": current_regime,
                        "reason": f"D1 Bearish Donchian breakout below {d_low:.5f} with strong candle body",
                        "timestamp": str(curr_bar["timestamp"])
                    }

        return None
