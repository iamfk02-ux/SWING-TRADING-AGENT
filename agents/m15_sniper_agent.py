"""
M15 Structure Shift Sniper Agent (Top-Down Institutional Entry Engine):
Chains all three timeframes for precision entries without waiting 4 hours for H4 candle close:
1. D1 Regime (The Tide): Must be confirmed TRENDING_UP or TRENDING_DOWN.
2. H4 Value Zone (The Point of Interest):
   - H4 EMA20/EMA50 aligned with D1 trend.
   - M15 price has tapped the H4 Value Zone [EMA20 - 0.25 ATR, EMA50 + 0.40 ATR] within last 3 M15 bars.
   - Passes hard H4 RSI Exhaustion Gate (SELL >= 38, BUY <= 62) & Daily ADR Shield (<= 72% spent).
3. M15 Market Structure Shift (The Sniper Trigger):
   - Confirmed closed M15 displacement candle (body >= 55% of range, close in outer 30%)
   - Breaks the prior 3-bar M15 micro-swing low (SELL) or high (BUY) cleanly back in trend direction
   - Closes back on the trend side of H4 EMA20.
4. Anti-Whipsaw Stop & Target:
   - Structural stop behind the M15 displacement high/low + 0.5 M15 ATR, floored at 20.0 pips
   - Minimum 2.0R–2.5R target.
"""

from typing import Dict, Any, Optional
import pandas as pd
import numpy as np

from core.indicators import compute_ema, compute_atr, compute_rsi, compute_adx
from agents.regime_agent import Regime


class M15SniperAgent:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        cfg = config.get("strategies", {}).get("m15_sniper", {})
        self.enabled = cfg.get("enabled", True)
        self.h4_ema_fast = cfg.get("h4_ema_fast", 20)
        self.h4_ema_slow = cfg.get("h4_ema_slow", 50)
        self.atr_period = cfg.get("atr_period", 14)
        self.rsi_sell_floor = cfg.get("rsi_sell_floor", 38.0)
        self.rsi_buy_ceiling = cfg.get("rsi_buy_ceiling", 62.0)
        self.max_adr_used_pct = cfg.get("max_adr_used_pct", 0.72)
        self.min_body_ratio = cfg.get("min_body_ratio", 0.55)
        self.stop_buffer_m15_atr = cfg.get("stop_buffer_m15_atr", 0.5)
        self.min_stop_pips = cfg.get("min_stop_pips", 20.0)
        self.max_stop_pips = cfg.get("max_stop_pips", 45.0)
        self.target_r = cfg.get("target_r", 2.5)
        self.kill_zones_only = cfg.get("kill_zones_only", True)
        self.excluded_symbols = [s.upper() for s in cfg.get("excluded_symbols", ["CADJPY", "GBPUSD"])]

    def _is_in_kill_zone(self, bar_timestamp) -> bool:
        """
        Institutional Liquidity Kill Zones (backtest-proven PF 2.06 vs 1.06 in dead hours):
        - London Open Kill Zone:       07:00 - 10:30 UTC (08:00 - 11:30 WAT)
        - NY / London Overlap Zone:    12:30 - 16:30 UTC (13:30 - 17:30 WAT)
        """
        try:
            ts = pd.to_datetime(bar_timestamp)
            mins = ts.hour * 60 + ts.minute
            london = (7 * 60 <= mins <= 10 * 60 + 30)
            ny_overlap = (12 * 60 + 30 <= mins <= 16 * 60 + 30)
            return london or ny_overlap
        except Exception:
            return True

    def _compute_adr_exhaustion(self, df_d1: pd.DataFrame, curr_close: float, direction: str) -> float:
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
        df_h4: pd.DataFrame,
        df_m15: pd.DataFrame,
        current_regime: str
    ) -> Optional[Dict[str, Any]]:
        """
        Evaluates the latest CLOSED M15 candle (df_m15.iloc[-2] if live, or df_m15.iloc[-1] if pre-sliced)
        against the D1 regime and H4 Value Zone.
        """
        if not self.enabled:
            return None

        # Pair-Specific Desk Audit Filter: exclude chronic M15 stop-hunt pairs (e.g. CADJPY, GBPUSD)
        sym_clean = symbol.upper().replace(".C", "").replace("M", "").replace(".RAW", "")
        if any(ex in sym_clean for ex in self.excluded_symbols):
            return None

        if current_regime not in (Regime.TRENDING_UP.value, Regime.TRENDING_DOWN.value):
            return None

        if (
            df_d1 is None or len(df_d1) < 20
            or df_h4 is None or len(df_h4) < max(self.h4_ema_slow, self.atr_period) + 5
            or df_m15 is None or len(df_m15) < 30
        ):
            return None

        # Indicators on H4 (closed H4 bars)
        h4_ema20 = float(compute_ema(df_h4["close"], self.h4_ema_fast).iloc[-1])
        h4_ema50 = float(compute_ema(df_h4["close"], self.h4_ema_slow).iloc[-1])
        h4_atr = float(compute_atr(df_h4, self.atr_period).iloc[-1])
        h4_rsi = float(compute_rsi(df_h4["close"], 14).iloc[-1])
        d1_adx = float(compute_adx(df_d1, 14)["adx"].iloc[-1])

        if np.isnan(h4_atr) or h4_atr <= 0 or np.isnan(h4_rsi):
            return None

        # Use the latest CLOSED M15 candle (index -1 assuming caller passes df_m15_closed)
        m15_atr_series = compute_atr(df_m15, self.atr_period)
        curr_idx = len(df_m15) - 1
        bar = df_m15.iloc[curr_idx]

        # Institutional Kill-Zone Gate: strictly avoid dead lunch & late NY chop
        if self.kill_zones_only and not self._is_in_kill_zone(bar["timestamp"]):
            return None

        m15_atr = float(m15_atr_series.iloc[curr_idx])

        c_open = float(bar["open"])
        c_high = float(bar["high"])
        c_low = float(bar["low"])
        c_close = float(bar["close"])
        c_range = c_high - c_low

        if c_range <= 0 or np.isnan(m15_atr) or m15_atr <= 0:
            return None

        prev3 = df_m15.iloc[curr_idx - 3 : curr_idx]
        m15_micro_high = float(prev3["high"].max())
        m15_micro_low = float(prev3["low"].min())

        pip_size = 0.01 if "JPY" in symbol.upper() else 0.0001
        min_stop_dist = self.min_stop_pips * pip_size
        max_stop_dist = self.max_stop_pips * pip_size
        body_ratio = abs(c_close - c_open) / c_range

        # -----------------------------
        # BEARISH M15 STRUCTURE SHIFT SNIPER
        # -----------------------------
        if current_regime == Regime.TRENDING_DOWN.value:
            if h4_ema20 >= h4_ema50:
                return None
            if h4_rsi < self.rsi_sell_floor:
                return None

            adr_used = self._compute_adr_exhaustion(df_d1, c_close, "SELL")
            if adr_used > self.max_adr_used_pct:
                return None

            # Price must be testing/rejecting H4 Value Zone [EMA20 - 0.25 ATR, EMA50 + 0.40 ATR]
            zone_low = h4_ema20 - 0.25 * h4_atr
            zone_high = h4_ema50 + 0.40 * h4_atr
            prev3_high = float(prev3["high"].max())
            in_zone = (zone_low <= c_high <= zone_high) or (zone_low <= prev3_high <= zone_high)
            if not in_zone or c_close >= h4_ema50 + 0.25 * h4_atr:
                return None

            close_from_high = (c_high - c_close) / c_range

            # M15 Market Structure Shift (Bearish displacement closing below 3-bar low & below H4 EMA20)
            is_mss = (
                c_close < m15_micro_low
                and c_close < c_open
                and body_ratio >= self.min_body_ratio
                and close_from_high >= 0.70
                and c_close < h4_ema20
            )
            if not is_mss:
                return None

            raw_sl = max(c_high, prev3_high) + (self.stop_buffer_m15_atr * m15_atr)
            stop_dist = raw_sl - c_close
            if stop_dist < min_stop_dist:
                stop_dist = min_stop_dist
                sl = round(c_close + min_stop_dist, 5)
            else:
                sl = round(raw_sl, 5)

            if stop_dist > max_stop_dist:
                return None

            tp = round(c_close - (self.target_r * stop_dist), 5)

            confidence = 0.65
            reasons = ["H4 Value-Zone Tap", "M15 Bearish MSS Displacement"]
            if d1_adx >= 25:
                confidence += 0.10
                reasons.append(f"D1 ADX {d1_adx:.1f}")
            if 42.0 <= h4_rsi <= 62.0:
                confidence += 0.05
                reasons.append(f"H4 RSI {h4_rsi:.1f}")
            if adr_used <= 0.45:
                confidence += 0.05
                reasons.append(f"Fresh ADR {adr_used*100:.0f}%")

            return {
                "symbol": symbol,
                "strategy": "M15_SNIPER",
                "timeframe": "M15",
                "direction": "SELL",
                "order_type": "MARKET",
                "entry": float(c_close),
                "market_price": float(c_close),
                "stop_loss": float(sl),
                "take_profit": float(tp),
                "confidence": min(1.0, round(confidence, 2)),
                "confluence": reasons,
                "regime": current_regime,
                "reason": f"D1->H4->M15 Sniper SELL ({', '.join(reasons)}) @ {c_close:.5f}",
                "timestamp": str(bar["timestamp"])
            }

        # -----------------------------
        # BULLISH M15 STRUCTURE SHIFT SNIPER
        # -----------------------------
        if current_regime == Regime.TRENDING_UP.value:
            if h4_ema20 <= h4_ema50:
                return None
            if h4_rsi > self.rsi_buy_ceiling:
                return None

            adr_used = self._compute_adr_exhaustion(df_d1, c_close, "BUY")
            if adr_used > self.max_adr_used_pct:
                return None

            zone_high = h4_ema20 + 0.25 * h4_atr
            zone_low = h4_ema50 - 0.40 * h4_atr
            prev3_low = float(prev3["low"].min())
            in_zone = (zone_low <= c_low <= zone_high) or (zone_low <= prev3_low <= zone_high)
            if not in_zone or c_close <= h4_ema50 - 0.25 * h4_atr:
                return None

            close_from_low = (c_close - c_low) / c_range

            # M15 Market Structure Shift (Bullish displacement closing above 3-bar high & above H4 EMA20)
            is_mss = (
                c_close > m15_micro_high
                and c_close > c_open
                and body_ratio >= self.min_body_ratio
                and close_from_low >= 0.70
                and c_close > h4_ema20
            )
            if not is_mss:
                return None

            raw_sl = min(c_low, prev3_low) - (self.stop_buffer_m15_atr * m15_atr)
            stop_dist = c_close - raw_sl
            if stop_dist < min_stop_dist:
                stop_dist = min_stop_dist
                sl = round(c_close - min_stop_dist, 5)
            else:
                sl = round(raw_sl, 5)

            if stop_dist > max_stop_dist:
                return None

            tp = round(c_close + (self.target_r * stop_dist), 5)

            confidence = 0.65
            reasons = ["H4 Value-Zone Tap", "M15 Bullish MSS Displacement"]
            if d1_adx >= 25:
                confidence += 0.10
                reasons.append(f"D1 ADX {d1_adx:.1f}")
            if 38.0 <= h4_rsi <= 58.0:
                confidence += 0.05
                reasons.append(f"H4 RSI {h4_rsi:.1f}")
            if adr_used <= 0.45:
                confidence += 0.05
                reasons.append(f"Fresh ADR {adr_used*100:.0f}%")

            return {
                "symbol": symbol,
                "strategy": "M15_SNIPER",
                "timeframe": "M15",
                "direction": "BUY",
                "order_type": "MARKET",
                "entry": float(c_close),
                "market_price": float(c_close),
                "stop_loss": float(sl),
                "take_profit": float(tp),
                "confidence": min(1.0, round(confidence, 2)),
                "confluence": reasons,
                "regime": current_regime,
                "reason": f"D1->H4->M15 Sniper BUY ({', '.join(reasons)}) @ {c_close:.5f}",
                "timestamp": str(bar["timestamp"])
            }

        return None
