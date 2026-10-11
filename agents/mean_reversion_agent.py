"""
Mean-Reversion Agent:
Fades Bollinger Band extremes inside confirmed RANGING markets:
- Active ONLY when Regime = RANGING
- Setup (D1): Daily close at or beyond Bollinger Band, RSI(14) <= 35 (long) or >= 65 (short)
- Trigger (H4): Bullish/bearish reversal candle closing beyond previous candle's extreme
- Take Profit: Middle Bollinger Band (20-day mean), 100% exit
- Stop Loss: Lowest low / highest high + buffer
"""

from typing import Dict, Any, Optional
import pandas as pd
import numpy as np

from core.indicators import compute_bollinger_bands, compute_rsi, compute_atr, compute_adx
from agents.regime_agent import Regime


class MeanReversionAgent:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        mr_cfg = config.get("strategies", {}).get("mean_reversion", {})
        self.bb_period = mr_cfg.get("bb_period", 20)
        self.bb_std = mr_cfg.get("bb_std", 2.0)
        self.rsi_period = mr_cfg.get("rsi_period", 14)
        self.rsi_oversold = mr_cfg.get("rsi_oversold", 35)
        self.rsi_overbought = mr_cfg.get("rsi_overbought", 65)
        self.stop_buffer_atr = mr_cfg.get("stop_buffer_atr", 0.75)

    def scan_for_signal(
        self,
        symbol: str,
        df_d1: pd.DataFrame,
        df_lower: pd.DataFrame,
        current_regime: str
    ) -> Optional[Dict[str, Any]]:
        """
        Scans for Mean Reversion signal in ranging markets using df_lower (H1 or H4) reversal triggers.
        """
        # Strict rule: Only active in confirmed Ranging regimes
        if current_regime != Regime.RANGING.value:
            return None

        if len(df_d1) < max(self.bb_period, self.rsi_period) + 10 or len(df_lower) < 10:
            return None

        bb_df = compute_bollinger_bands(df_d1["close"], self.bb_period, self.bb_std)
        rsi_series = compute_rsi(df_d1["close"], self.rsi_period)
        d1_atr = compute_atr(df_d1, 14).iloc[-1]
        adx_val = compute_adx(df_d1, 14)["adx"].iloc[-1]

        last_d1_close = df_d1["close"].iloc[-1]
        bb_upper = bb_df["bb_upper"].iloc[-1]
        bb_lower = bb_df["bb_lower"].iloc[-1]
        bb_mid = bb_df["bb_mid"].iloc[-1]
        rsi_val = rsi_series.iloc[-1]

        curr_bar = df_lower.iloc[-1]
        prev_bar = df_lower.iloc[-2]

        # -----------------------------
        # LONG MEAN REVERSION
        # -----------------------------
        if last_d1_close <= bb_lower and rsi_val <= self.rsi_oversold:
            # Reversal Trigger: Bullish close above previous candle high
            if curr_bar["close"] > prev_bar["high"]:
                sl = min(curr_bar["low"], prev_bar["low"]) - (self.stop_buffer_atr * d1_atr)
                tp = bb_mid
                stop_dist = curr_bar["close"] - sl
                tp_dist = tp - curr_bar["close"]

                # Minimum reward ratio check (0.8R)
                if tp_dist >= (0.8 * stop_dist) and stop_dist > 0:
                    confidence = 0.6
                    if rsi_val <= 25:
                        confidence += 0.1
                    if adx_val < 15:
                        confidence += 0.1

                    return {
                        "symbol": symbol,
                        "strategy": "MEAN_REVERSION",
                        "direction": "BUY",
                        "entry": float(curr_bar["close"]),
                        "stop_loss": float(sl),
                        "take_profit": float(tp),
                        "confidence": min(1.0, confidence),
                        "regime": current_regime,
                        "reason": f"D1 Lower BB & RSI({rsi_val:.1f}) bounce targeting mean {tp:.5f}",
                        "timestamp": str(curr_bar["timestamp"])
                    }

        # -----------------------------
        # SHORT MEAN REVERSION
        # -----------------------------
        if last_d1_close >= bb_upper and rsi_val >= self.rsi_overbought:
            # Reversal Trigger: Bearish close below previous candle low
            if curr_bar["close"] < prev_bar["low"]:
                sl = max(curr_bar["high"], prev_bar["high"]) + (self.stop_buffer_atr * d1_atr)
                tp = bb_mid
                stop_dist = sl - curr_bar["close"]
                tp_dist = curr_bar["close"] - tp

                if tp_dist >= (0.8 * stop_dist) and stop_dist > 0:
                    confidence = 0.6
                    if rsi_val >= 75:
                        confidence += 0.1
                    if adx_val < 15:
                        confidence += 0.1

                    return {
                        "symbol": symbol,
                        "strategy": "MEAN_REVERSION",
                        "direction": "SELL",
                        "entry": float(curr_bar["close"]),
                        "stop_loss": float(sl),
                        "take_profit": float(tp),
                        "confidence": min(1.0, confidence),
                        "regime": current_regime,
                        "reason": f"D1 Upper BB & RSI({rsi_val:.1f}) exhaustion targeting mean {tp:.5f}",
                        "timestamp": str(curr_bar["timestamp"])
                    }

        return None
