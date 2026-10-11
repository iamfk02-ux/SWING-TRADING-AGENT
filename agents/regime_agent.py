"""
Market-Regime Agent:
Classifies each symbol on Daily (D1) bars into market conditions:
- Trending Up
- Trending Down
- Compression (squeeze, ready for breakout)
- Ranging (sideways, mean-reversion conditions)
- High Volatility (dangerous, half-risk)
- Unclear (stand aside)

Includes anti-flip-flop confirmation filter.
"""

from enum import Enum
from typing import Dict, Any, Optional
import pandas as pd
import numpy as np

from core.indicators import (
    compute_ema,
    compute_atr,
    compute_adx,
    compute_bollinger_bands,
)


class Regime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    COMPRESSION = "COMPRESSION"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    UNCLEAR = "UNCLEAR"


class MarketRegimeAgent:
    def __init__(self, config: Dict[str, Any]):
        regime_cfg = config.get("regime", {})
        self.adx_period = regime_cfg.get("adx_period", 14)
        self.adx_trend_thresh = regime_cfg.get("adx_trend_threshold", 25)
        self.adx_range_thresh = regime_cfg.get("adx_range_threshold", 20)
        self.ema_fast_len = regime_cfg.get("ema_fast", 50)
        self.ema_slow_len = regime_cfg.get("ema_slow", 200)
        self.ema_slope_bars = regime_cfg.get("ema_slope_lookback", 10)
        self.atr_period = regime_cfg.get("atr_period", 14)
        self.high_vol_pct = regime_cfg.get("high_vol_percentile", 0.90)
        self.high_vol_lookback = regime_cfg.get("high_vol_lookback", 252)
        self.bb_period = regime_cfg.get("bb_period", 20)
        self.bb_std = regime_cfg.get("bb_std", 2.0)
        self.comp_pct = regime_cfg.get("compression_percentile", 0.20)
        self.comp_lookback = regime_cfg.get("compression_lookback", 126)
        self.confirm_bars = regime_cfg.get("confirm_bars", 2)

    def calculate_regime_series(self, df_d1: pd.DataFrame) -> pd.Series:
        """
        Calculate regime for every bar in historical D1 dataframe.
        Expects columns: ['open', 'high', 'low', 'close'].
        """
        if len(df_d1) < max(self.high_vol_lookback, self.ema_slow_len):
            return pd.Series(Regime.UNCLEAR.value, index=df_d1.index)

        close = df_d1["close"]
        ema_fast = compute_ema(close, self.ema_fast_len)
        ema_slow = compute_ema(close, self.ema_slow_len)
        ema_fast_slope = ema_fast - ema_fast.shift(self.ema_slope_bars)

        atr = compute_atr(df_d1, self.atr_period)
        atr_rolling_high_vol = atr.rolling(self.high_vol_lookback).quantile(self.high_vol_pct)

        adx_df = compute_adx(df_d1, self.adx_period)
        adx = adx_df["adx"]

        bb_df = compute_bollinger_bands(close, self.bb_period, self.bb_std, self.comp_lookback)
        bb_width_pct = bb_df["bb_width_pct"]

        # 20-day drift of EMA fast in ATR units
        drift_20 = (ema_fast - ema_fast.shift(20)).abs()
        safe_atr = atr.replace(0, np.nan)
        drift_atr = drift_20 / safe_atr

        regimes = []
        n = len(df_d1)

        for i in range(n):
            # 1. High Volatility (checked first)
            if not np.isnan(atr_rolling_high_vol.iloc[i]) and atr.iloc[i] >= atr_rolling_high_vol.iloc[i]:
                regimes.append(Regime.HIGH_VOLATILITY.value)
            # 2. Trending Up
            elif (
                adx.iloc[i] >= self.adx_trend_thresh
                and close.iloc[i] > ema_slow.iloc[i]
                and ema_fast.iloc[i] > ema_slow.iloc[i]
                and ema_fast_slope.iloc[i] > 0
            ):
                regimes.append(Regime.TRENDING_UP.value)
            # 3. Trending Down
            elif (
                adx.iloc[i] >= self.adx_trend_thresh
                and close.iloc[i] < ema_slow.iloc[i]
                and ema_fast.iloc[i] < ema_slow.iloc[i]
                and ema_fast_slope.iloc[i] < 0
            ):
                regimes.append(Regime.TRENDING_DOWN.value)
            # 4. Compression
            elif (
                not np.isnan(bb_width_pct.iloc[i])
                and bb_width_pct.iloc[i] <= self.comp_pct
                and adx.iloc[i] < self.adx_trend_thresh
            ):
                regimes.append(Regime.COMPRESSION.value)
            # 5. Ranging
            elif (
                adx.iloc[i] < self.adx_range_thresh
                and not np.isnan(drift_atr.iloc[i])
                and drift_atr.iloc[i] <= 1.0
            ):
                regimes.append(Regime.RANGING.value)
            # 6. Unclear
            else:
                regimes.append(Regime.UNCLEAR.value)

        raw_series = pd.Series(regimes, index=df_d1.index)

        # Apply anti-flip-flop confirmation filter
        confirmed_series = raw_series.copy()
        current_state = raw_series.iloc[0]
        pending_state = None
        count = 0

        for idx, val in raw_series.items():
            if val == current_state:
                pending_state = None
                count = 0
            else:
                if val == pending_state:
                    count += 1
                    if count >= self.confirm_bars:
                        current_state = val
                        pending_state = None
                        count = 0
                else:
                    pending_state = val
                    count = 1
            confirmed_series[idx] = current_state

        return confirmed_series
