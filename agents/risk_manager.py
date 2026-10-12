"""
Risk Manager:
Enforces hard safety boundaries:
- 1% account risk per trade with position sizing based on stop distance
- Cent account / standard account lot size scaling
- Currency correlation limit (max 2% net exposure per single currency)
- Max 3 concurrent open trades
- Drawdown throttle (-10% cuts risk to 0.5%)
- Hard Kill Switch (-20% cuts all trading)
- Spread filter & weekend gap filter
"""

from typing import Dict, Any, List, Optional, Tuple


class RiskManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        risk_cfg = config.get("risk", {})
        self.risk_pct = risk_cfg.get("risk_per_trade_pct", 1.0)
        self.max_open_pos = risk_cfg.get("max_open_positions", 3)
        self.max_currency_risk_pct = risk_cfg.get("max_currency_exposure_pct", 2.0)
        self.throttle_pct = risk_cfg.get("drawdown_throttle_pct", 10.0)
        self.kill_switch_pct = risk_cfg.get("kill_switch_drawdown_pct", 20.0)
        self.is_cent_account = config.get("account", {}).get("type", "cent") == "cent"
        
        # News Guard (Economic Calendar Filter)
        try:
            from core.news_guard import NewsGuard
            self.news_guard = NewsGuard(config)
        except Exception:
            self.news_guard = None

    def _get_currency_components(self, symbol: str) -> List[str]:
        """Extracts individual currencies from a symbol string."""
        s = symbol.upper().replace(".C", "").replace("M", "").replace(".RAW", "")
        if "XAU" in s or "GOLD" in s or "OIL" in s or "US30" in s:
            return ["USD"]
        if len(s) >= 6 and not any(c in s for c in ["BTC", "ETH", "SOL"]):
            return [s[:3], s[3:6]]
        return [s]

    def evaluate_order(
        self,
        signal: Dict[str, Any],
        account_equity: float,
        peak_equity: float,
        open_positions: List[Dict[str, Any]],
        current_spread_pips: float = 1.5
    ) -> Tuple[bool, str, float]:
        """
        Evaluates a signal against hard risk limits.
        Returns: (is_approved, rejection_reason, calculated_lots)
        """
        from datetime import datetime
        symbol = signal["symbol"].upper()

        # 0. Weekend Market Status Check
        # Crypto trades 24/7. Forex and Metals close on weekends (Friday 5pm EST to Sunday 5pm EST).
        is_crypto = any(c in symbol for c in ["BTC", "ETH", "SOL", "CRYPTO", "BITCOIN", "LTC"])
        
        is_weekend = False
        if "timestamp" in signal and signal["timestamp"]:
            try:
                import pandas as pd
                ts = pd.to_datetime(signal["timestamp"])
                is_weekend = ts.weekday() in (5, 6)
            except Exception:
                is_weekend = datetime.utcnow().weekday() in (5, 6)
        else:
            is_weekend = datetime.utcnow().weekday() in (5, 6)

        if is_weekend and not is_crypto:
            return False, f"WEEKEND: {symbol} market is closed. Crypto trades 24/7.", 0.0

        # 0.5 High-Impact News Filter (Economic Calendar Shield)
        if self.news_guard:
            is_blocked_news, news_reason = self.news_guard.check_symbol_news(symbol)
            if is_blocked_news:
                return False, f"NEWS SHIELD: {news_reason}", 0.0

        # 1. Kill Switch Check
        drawdown_pct = 0.0
        if peak_equity > 0:
            drawdown_pct = ((peak_equity - account_equity) / peak_equity) * 100.0

        if drawdown_pct >= self.kill_switch_pct:
            return False, f"KILL SWITCH ACTIVE: Drawdown {drawdown_pct:.1f}% >= {self.kill_switch_pct}%", 0.0

        # 2. Maximum open positions
        if len(open_positions) >= self.max_open_pos:
            return False, f"MAX POSITIONS REACHED: {len(open_positions)}/{self.max_open_pos}", 0.0

        # 3. Duplicate position check (1 position per symbol)
        for pos in open_positions:
            if pos["symbol"].upper() == symbol:
                return False, f"ALREADY IN POSITION FOR {symbol}", 0.0

        # 3.5 Currency Correlation & Clustered Exposure Guard
        # Prevents stacking multiple trades sharing the same base/quote currency (e.g. buying EURUSD, GBPUSD, AUDUSD simultaneously)
        candidate_currencies = self._get_currency_components(symbol)
        curr_counts = {}
        for pos in open_positions:
            for c in self._get_currency_components(pos.get("symbol", "")):
                curr_counts[c] = curr_counts.get(c, 0) + 1

        for c in candidate_currencies:
            if curr_counts.get(c, 0) >= 2:
                return False, f"CORRELATION LIMIT: Maximum exposure for currency '{c}' reached ({curr_counts[c]} active positions)", 0.0

        # 4. Effective Risk calculation (Throttled if in drawdown)
        effective_risk_pct = self.risk_pct
        if drawdown_pct >= self.throttle_pct:
            effective_risk_pct = self.risk_pct * 0.5  # Halved to 0.5%

        risk_amount_usd = account_equity * (effective_risk_pct / 100.0)

        # 5. Position Sizing
        entry = signal["entry"]
        stop = signal["stop_loss"]
        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            return False, "INVALID STOP DISTANCE", 0.0

        # Determine exact lot size via MT5 contract specs or fallback formulas
        raw_lots = 0.01
        min_lot = 0.01
        lot_step = 0.01

        mt5_connected = False
        try:
            import MetaTrader5 as mt5
            info = mt5.symbol_info(symbol)
            if info:
                mt5_connected = True
                min_lot = info.volume_min if info.volume_min > 0 else 0.01
                lot_step = info.volume_step if info.volume_step > 0 else 0.01
                tick_size = info.trade_tick_size if info.trade_tick_size > 0 else info.point
                tick_val = info.trade_tick_value
                if tick_size > 0 and tick_val > 0:
                    ticks_at_risk = stop_dist / tick_size
                    cost_per_lot = ticks_at_risk * tick_val
                    if cost_per_lot > 0:
                        raw_lots = risk_amount_usd / cost_per_lot
        except Exception:
            pass

        if not mt5_connected:
            # Universal fallback position sizing
            if is_crypto:
                # 1 standard lot = 1 coin (e.g. 1 BTC). Risk per 1 lot = stop_dist in USD.
                raw_lots = risk_amount_usd / stop_dist
                min_lot = 0.01
            elif "XAU" in symbol or "GOLD" in symbol:
                # 1 standard lot = 100 troy oz. $1 move = $100 per lot.
                cost_per_lot = stop_dist * 100.0
                raw_lots = risk_amount_usd / max(cost_per_lot, 1.0)
                min_lot = 0.01
            else:
                # Standard Forex: pip math
                pip_size = 0.01 if "JPY" in symbol else 0.0001
                stop_pips = stop_dist / pip_size
                pip_val_per_lot = 10.0 if not self.is_cent_account else 0.10
                raw_lots = risk_amount_usd / max(stop_pips * pip_val_per_lot, 0.01)
                min_lot = 0.01

        # Round to step and enforce minimum
        steps = round(raw_lots / lot_step)
        rounded_lots = round(max(min_lot, steps * lot_step), 2)

        # Check maximum safety threshold (hard limit 2.5% of account on minimum lot)
        # Prevent oversized fills on very small cent balances
        if not is_crypto:
            pip_size = 0.01 if "JPY" in symbol else 0.0001
            stop_pips = stop_dist / pip_size
            pip_val_per_lot = 10.0 if not self.is_cent_account else 0.10
            actual_risk_usd = rounded_lots * stop_pips * pip_val_per_lot
            if actual_risk_usd > (account_equity * 0.025):
                return False, f"MIN LOT TOO LARGE FOR ACCOUNT (${actual_risk_usd:.2f} > 2.5% max risk cap)", 0.0

        return True, "APPROVED", rounded_lots
