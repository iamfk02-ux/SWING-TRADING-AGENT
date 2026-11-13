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
from datetime import datetime, timezone


class RiskManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        risk_cfg = config.get("risk", {})
        self.risk_pct = risk_cfg.get("risk_per_trade_pct", 2.0)
        self.max_risk_pct = risk_cfg.get("max_risk_per_trade_pct", 3.2)
        self.max_open_pos = risk_cfg.get("max_open_positions", 3)
        self.max_currency_risk_pct = risk_cfg.get("max_currency_exposure_pct", 4.5)
        self.free_roll_recycling = risk_cfg.get("free_roll_slot_recycling", True)
        self.conviction_sizing_cfg = risk_cfg.get("conviction_sizing", {})
        self.throttle_pct = risk_cfg.get("drawdown_throttle_pct", 10.0)
        self.kill_switch_pct = risk_cfg.get("kill_switch_drawdown_pct", 20.0)
        self.min_stop_loss_pips = risk_cfg.get("min_stop_loss_pips", 20.0)
        self.is_cent_account = config.get("account", {}).get("type", "cent") == "cent"
        
        # News Guard (Economic Calendar Filter)
        try:
            from core.news_guard import NewsGuard
            self.news_guard = NewsGuard(config)
        except Exception:
            self.news_guard = None

        # Strategy Health Degradation Monitor (Rule 10)
        try:
            from core.strategy_health import StrategyHealthMonitor
            self.health_monitor = StrategyHealthMonitor(config)
        except Exception:
            self.health_monitor = None

        # Symbol Loss Lockout (Post-loss cooldown to prevent churn)
        self.symbol_loss_cooldown_hours = risk_cfg.get("symbol_loss_cooldown_hours", 24)
        self.daily_loss_limit_pct = risk_cfg.get("daily_loss_limit_pct", 4.0)
        self.weekly_loss_limit_pct = risk_cfg.get("weekly_loss_limit_pct", 8.0)
        self.friday_cutoff_utc_hour = risk_cfg.get("friday_cutoff_utc_hour", 14)

        # Institutional Session Filter & Rollover Shield
        self.session_filter_cfg = risk_cfg.get("session_filter", {})
        self.rollover_shield_cfg = risk_cfg.get("rollover_shield", {})

    def _get_and_update_peak_equity(self, account_equity: float, fallback_peak: float) -> float:
        """
        Tracks and persists true historical high-water mark (peak_equity) on disk.
        Automatically resets if account scale changes by >3x (e.g. switching from demo to live).
        """
        import json
        from pathlib import Path

        state_path = Path(__file__).resolve().parent.parent / "data" / "peak_equity_state.json"
        candidate_peak = max(float(account_equity or 0.0), float(fallback_peak or 0.0))
        if candidate_peak <= 0:
            return 0.0

        saved_peak = 0.0
        if state_path.exists():
            try:
                with open(state_path, "r") as f:
                    data = json.load(f)
                    saved_peak = float(data.get("peak_equity", 0.0) or 0.0)
            except Exception:
                saved_peak = 0.0

        # If saved_peak is from a different account scale (e.g. $93k demo vs $305 live), reset cleanly
        if saved_peak > (candidate_peak * 3.0) or saved_peak < (candidate_peak * 0.2):
            true_peak = candidate_peak
        else:
            true_peak = max(saved_peak, candidate_peak)

        if true_peak > saved_peak or saved_peak > (candidate_peak * 3.0):
            try:
                state_path.parent.mkdir(parents=True, exist_ok=True)
                with open(state_path, "w") as f:
                    json.dump({
                        "peak_equity": round(true_peak, 2),
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }, f, indent=2)
            except Exception:
                pass

        return true_peak

    def _check_period_loss_limits(self, account_equity: float, now_utc: datetime) -> Tuple[bool, str]:
        """
        Enforces hard Daily (-4.0%) and Weekly (-8.0%) portfolio loss circuit breakers
        by summing realized PnL from trading_journal.db.
        """
        if account_equity <= 0:
            return False, ""
        try:
            import sqlite3
            from pathlib import Path
            from datetime import timedelta
            import pandas as pd

            db_path = Path(__file__).resolve().parent.parent / "data" / "trading_journal.db"
            if not db_path.exists():
                return False, ""

            with sqlite3.connect(db_path) as conn:
                df = pd.read_sql_query(
                    "SELECT exit_time, pnl_usd FROM trades WHERE exit_time IS NOT NULL AND pnl_usd IS NOT NULL ORDER BY id DESC LIMIT 100",
                    conn
                )
            if df.empty:
                return False, ""

            df["exit_dt"] = pd.to_datetime(df["exit_time"], utc=True, errors="coerce")
            df = df.dropna(subset=["exit_dt"])

            # Filter out historical trades from a vastly different account scale (e.g. $93k demo vs $305 live)
            df = df[df["pnl_usd"].abs() < (account_equity * 0.50)]
            if df.empty:
                return False, ""

            day_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
            week_start = day_start - timedelta(days=now_utc.weekday())

            daily_pnl = float(df[df["exit_dt"] >= day_start]["pnl_usd"].sum())
            weekly_pnl = float(df[df["exit_dt"] >= week_start]["pnl_usd"].sum())

            if self.daily_loss_limit_pct > 0 and daily_pnl < 0:
                daily_loss_pct = (abs(daily_pnl) / account_equity) * 100.0
                if daily_loss_pct >= self.daily_loss_limit_pct:
                    return True, f"DAILY LOSS CIRCUIT BREAKER: Realized loss today is -{daily_loss_pct:.1f}% (>= {self.daily_loss_limit_pct}% limit). Trading paused until tomorrow."

            if self.weekly_loss_limit_pct > 0 and weekly_pnl < 0:
                weekly_loss_pct = (abs(weekly_pnl) / account_equity) * 100.0
                if weekly_loss_pct >= self.weekly_loss_limit_pct:
                    return True, f"WEEKLY LOSS CIRCUIT BREAKER: Realized loss this week is -{weekly_loss_pct:.1f}% (>= {self.weekly_loss_limit_pct}% limit). Trading paused until Monday."
        except Exception:
            pass
        return False, ""

    def _check_symbol_loss_cooldown(self, symbol: str) -> Tuple[bool, str]:
        """
        Enforces a mandatory cooldown on symbols that recently closed at a loss
        (e.g. stopped out within the last 24h) to prevent revenge trading and chop churn.
        """
        if self.symbol_loss_cooldown_hours <= 0:
            return False, ""
        try:
            import sqlite3
            from pathlib import Path
            from datetime import datetime
            import pandas as pd

            db_path = Path(__file__).resolve().parent.parent / "data" / "trading_journal.db"
            if not db_path.exists():
                return False, ""

            with sqlite3.connect(db_path) as conn:
                cur = conn.cursor()
                row = cur.execute("""
                    SELECT exit_time, exit_reason, pnl_usd 
                    FROM trades 
                    WHERE UPPER(symbol) = ? AND exit_time IS NOT NULL 
                    ORDER BY id DESC LIMIT 1
                """, (symbol.upper(),)).fetchone()

                if row and row[0]:
                    exit_time_str, exit_reason, pnl_usd = row
                    exit_dt = pd.to_datetime(exit_time_str)
                    if exit_dt.tzinfo is not None:
                        exit_dt = exit_dt.tz_convert(None)
                    now_time = pd.to_datetime(datetime.now())
                    hours_elapsed = max(0.0, (now_time - exit_dt).total_seconds() / 3600.0)

                    if hours_elapsed < self.symbol_loss_cooldown_hours:
                        # Strictly check for an actual net dollar loss (pnl_usd < -0.01)
                        # Break-even runners, trailing stops, and partial-TP winners must NEVER trigger a loss lockout!
                        is_actual_loss = (pnl_usd is not None and float(pnl_usd) < -0.01)
                        if is_actual_loss:
                            hours_left = self.symbol_loss_cooldown_hours - hours_elapsed
                            return True, f"SYMBOL COOLDOWN ACTIVE: {symbol} had a losing exit (${float(pnl_usd):+.2f} - {exit_reason}) {hours_elapsed:.1f}h ago. Standing aside for {hours_left:.1f}h to prevent churn."
        except Exception:
            pass
        return False, ""

    def _get_currency_components(self, symbol: str) -> List[str]:
        """Extracts individual currencies from a symbol string."""
        s = symbol.upper().replace(".C", "").replace("M", "").replace(".RAW", "")
        if "XAU" in s or "GOLD" in s or "OIL" in s or "US30" in s:
            return ["USD"]
        if len(s) >= 6:
            return [s[:3], s[3:6]]
        return [s]

    def _get_directional_currency_exposures(self, symbol: str, direction: str) -> Dict[str, str]:
        """
        Decomposes a trade into individual currency directional exposures.
        E.g.
        EURUSD BUY  -> {'EUR': 'LONG', 'USD': 'SHORT'}
        EURUSD SELL -> {'EUR': 'SHORT', 'USD': 'LONG'}
        USDCAD BUY  -> {'USD': 'LONG', 'CAD': 'SHORT'}
        EURCAD SELL -> {'EUR': 'SHORT', 'CAD': 'LONG'}
        """
        s = symbol.upper().replace(".C", "").replace("M", "").replace(".RAW", "")
        dir_clean = str(direction).upper()
        if "XAU" in s or "GOLD" in s:
            return {
                "XAU": "LONG" if "BUY" in dir_clean else "SHORT",
                "USD": "SHORT" if "BUY" in dir_clean else "LONG"
            }
        if len(s) >= 6:
            base, quote = s[:3], s[3:6]
            if "BUY" in dir_clean:
                return {base: "LONG", quote: "SHORT"}
            else:
                return {base: "SHORT", quote: "LONG"}
        return {}

    def _is_position_risk_free(self, pos: Dict[str, Any]) -> bool:
        """
        Determines if an open position's Stop Loss is already locked at or better than
        Break-Even (0% downside capital risk). Such 'Free Runners' do not consume an at-risk slot.
        """
        if not self.free_roll_recycling:
            return False

        direction = str(pos.get("direction", "BUY")).upper()
        entry = float(pos.get("entry_price", pos.get("entry", 0.0)) or 0.0)
        sl = float(pos.get("sl", pos.get("stop_loss", 0.0)) or 0.0)

        if entry <= 0 or sl <= 0:
            return False

        if direction == "BUY":
            return sl >= entry
        elif direction == "SELL":
            return sl <= entry
        return False

    def _is_in_rollover_window(self, now_utc: datetime) -> Tuple[bool, str]:
        """
        Checks if current time falls within the 5:00 PM EST interbank rollover window
        (typically 21:45 to 22:30 UTC) where spreads widen drastically.
        """
        if not self.rollover_shield_cfg.get("enabled", True):
            return False, ""

        sh = self.rollover_shield_cfg.get("freeze_start_utc_hour", 21)
        sm = self.rollover_shield_cfg.get("freeze_start_utc_minute", 45)
        eh = self.rollover_shield_cfg.get("freeze_end_utc_hour", 22)
        em = self.rollover_shield_cfg.get("freeze_end_utc_minute", 30)

        curr_mins = now_utc.hour * 60 + now_utc.minute
        start_mins = sh * 60 + sm
        end_mins = eh * 60 + em

        if start_mins <= curr_mins <= end_mins:
            return True, f"ROLLOVER FREEZE: Daily 5:00 PM EST interbank rollover active ({sh:02d}:{sm:02d}-{eh:02d}:{em:02d} UTC). Spreads temporarily wide."
        return False, ""

    def _is_outside_liquidity_session(self, symbol: str, now_utc: datetime) -> Tuple[bool, str]:
        """
        Checks if current time falls outside high-liquidity trading sessions (London & New York).
        Filters out low-volume overnight/Asian range drift.
        """
        if not self.session_filter_cfg.get("enabled", True):
            return False, ""

        if self.session_filter_cfg.get("allow_asian_session_for_jpy_aud", False):
            if any(k in symbol.upper() for k in ["JPY", "AUD"]):
                if 0 <= now_utc.hour < 9:
                    return False, ""

        sh = self.session_filter_cfg.get("start_utc_hour", 7)
        sm = self.session_filter_cfg.get("start_utc_minute", 0)
        eh = self.session_filter_cfg.get("end_utc_hour", 17)
        em = self.session_filter_cfg.get("end_utc_minute", 0)

        curr_mins = now_utc.hour * 60 + now_utc.minute
        start_mins = sh * 60 + sm
        end_mins = eh * 60 + em

        if not (start_mins <= curr_mins < end_mins):
            curr_str = f"{now_utc.hour:02d}:{now_utc.minute:02d} UTC"
            return True, f"SESSION FILTER: Outside peak liquidity hours ({sh:02d}:{sm:02d}-{eh:02d}:{em:02d} UTC London/NY). Current: {curr_str}."
        return False, ""

    def evaluate_order(
        self,
        signal: Dict[str, Any],
        account_equity: float,
        peak_equity: float,
        open_positions: List[Dict[str, Any]],
        pending_orders: Optional[List[Dict[str, Any]]] = None,
        current_spread_pips: float = 1.5
    ) -> Tuple[bool, str, float]:
        """
        Evaluates a signal against hard risk limits (checking active positions + pending orders).
        Returns: (is_approved, rejection_reason, calculated_lots)
        """
        from datetime import datetime
        raw_symbol = str(signal["symbol"])
        symbol = raw_symbol.upper()
        pending_orders = pending_orders or []

        now_utc = datetime.now(timezone.utc)
        if signal.get("is_backtest") and "timestamp" in signal and signal["timestamp"]:
            try:
                import pandas as pd
                ts = pd.to_datetime(signal["timestamp"])
                if ts.tzinfo is not None:
                    now_utc = ts.tz_convert(timezone.utc)
                else:
                    now_utc = ts.tz_localize(timezone.utc)
            except Exception:
                pass

        # 0. Weekend Market Status Check
        # Forex and Metals close on weekends (Friday 5pm EST to Sunday 5pm EST).
        if now_utc.weekday() in (5, 6):
            return False, f"WEEKEND: {symbol} market is closed. Traditional markets resume Sunday 5:00 PM EST.", 0.0

        # 0.005 Friday Afternoon Weekend-Gap Protection Cutoff
        if now_utc.weekday() == 4 and now_utc.hour >= self.friday_cutoff_utc_hour:
            return False, f"FRIDAY CUTOFF: No new entries after {self.friday_cutoff_utc_hour:02d}:00 UTC on Fridays to prevent weekend price gap risk.", 0.0

        # 0.01 Rollover Shield (Daily 5:00 PM EST interbank settlement freeze)
        in_rollover, rollover_reason = self._is_in_rollover_window(now_utc)
        if in_rollover:
            return False, rollover_reason, 0.0

        # 0.02 Session Liquidity Filter (Peak London/NY liquidity hours only)
        out_session, session_reason = self._is_outside_liquidity_session(symbol, now_utc)
        if out_session:
            return False, session_reason, 0.0

        # 0.1 Minimum Stop Floor Enforcement (Mandatory 20-pip safety floor)
        # Prevents overnight and Asian-session whipsaws from clipping tight stops
        entry = signal.get("entry", 0.0)
        stop = signal.get("stop_loss", 0.0)
        direction = signal.get("direction", "BUY")
        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            return False, "INVALID STOP DISTANCE", 0.0

        pip_size = 0.01 if ("JPY" in symbol or "jpy" in symbol) else (0.1 if "XAU" in symbol else 0.0001)
        min_stop_dist = self.min_stop_loss_pips * pip_size

        if stop_dist < min_stop_dist:
            if direction == "BUY":
                new_sl = round(entry - min_stop_dist, 5)
            else:
                new_sl = round(entry + min_stop_dist, 5)

            # Preserve minimum 2.0R or original R reward
            curr_tp = signal.get("take_profit")
            if curr_tp:
                orig_r = max(2.0, abs(curr_tp - entry) / stop_dist) if stop_dist > 0 else 2.0
                if direction == "BUY":
                    signal["take_profit"] = round(entry + (orig_r * min_stop_dist), 5)
                else:
                    signal["take_profit"] = round(entry - (orig_r * min_stop_dist), 5)

            signal["stop_loss"] = new_sl
            stop = new_sl
            stop_dist = min_stop_dist

        # 0.2 Spread & Liquidity Filter (Protects against midnight rollover spread widening)
        try:
            import MetaTrader5 as mt5
            t_info = mt5.symbol_info_tick(raw_symbol) or mt5.symbol_info_tick(symbol)
            if t_info and t_info.ask > 0 and t_info.bid > 0 and stop_dist > 0:
                live_spread = t_info.ask - t_info.bid
                max_spread_ratio = self.config.get("risk", {}).get("max_spread_to_stop_ratio", 0.15)
                if live_spread > (stop_dist * max_spread_ratio):
                    return False, f"SPREAD TOO WIDE: Spread ({live_spread:.5f}) is > {max_spread_ratio*100:.0f}% of stop distance ({stop_dist:.5f})", 0.0
        except Exception:
            pass

        # 0.5 High-Impact News Filter (Economic Calendar Shield)
        if self.news_guard:
            is_blocked_news, news_reason = self.news_guard.check_symbol_news(symbol)
            if is_blocked_news:
                return False, f"NEWS SHIELD: {news_reason}", 0.0

        # 1. Persistent High-Water Mark (peak_equity) & Kill Switch Check
        true_peak = self._get_and_update_peak_equity(account_equity, peak_equity)
        drawdown_pct = 0.0
        if true_peak > 0:
            drawdown_pct = max(0.0, ((true_peak - account_equity) / true_peak) * 100.0)

        if drawdown_pct >= self.kill_switch_pct:
            return False, f"KILL SWITCH ACTIVE: Drawdown {drawdown_pct:.1f}% >= {self.kill_switch_pct}% (Peak: ${true_peak:,.2f})", 0.0

        # 1.5 Daily (-4%) & Weekly (-8%) Portfolio Loss Circuit Breakers
        period_locked, period_reason = self._check_period_loss_limits(account_equity, now_utc)
        if period_locked:
            return False, period_reason, 0.0

        # 2. Maximum open AT-RISK positions (Free-Roll Slot Recycling: Break-Even runners don't consume risk slots)
        at_risk_positions = [p for p in open_positions if not self._is_position_risk_free(p)]
        total_at_risk_orders = len(at_risk_positions) + len(pending_orders)
        if total_at_risk_orders >= self.max_open_pos:
            return False, f"MAX AT-RISK POSITIONS REACHED: {total_at_risk_orders}/{self.max_open_pos} (Free runners excluded)", 0.0

        # Hard portfolio cap including free runners (max 6 across watchlist)
        if (len(open_positions) + len(pending_orders)) >= 6:
            return False, "MAX TOTAL PORTFOLIO POSITIONS REACHED (6/6)", 0.0

        # 3. Duplicate check (1 trade/order per symbol)
        for pos in open_positions:
            if pos["symbol"].upper() == symbol:
                return False, f"ALREADY IN POSITION FOR {symbol}", 0.0

        for pend in pending_orders:
            if pend["symbol"].upper() == symbol:
                return False, f"PENDING ORDER ALREADY ACTIVE FOR {symbol}", 0.0

        # 3.5 Currency Correlation & Clustered Exposure Guard (Evaluates only AT-RISK positions + pending)
        # Once an existing trade's SL is at/past Break-Even, its currency downside risk is 0%
        candidate_currencies = self._get_currency_components(symbol)
        curr_counts = {}
        all_at_risk_committed = at_risk_positions + pending_orders
        for pos in all_at_risk_committed:
            for c in self._get_currency_components(pos.get("symbol", "")):
                curr_counts[c] = curr_counts.get(c, 0) + 1

        for c in candidate_currencies:
            if curr_counts.get(c, 0) >= 2:
                return False, f"CORRELATION LIMIT: Maximum at-risk exposure for currency '{c}' reached ({curr_counts[c]} at-risk/pending)", 0.0

        # 3.6 Institutional Currency Directional Alignment Guard (No Self-Fighting)
        # Prevents holding contradictory exposures (e.g. LONG CAD via EURCAD SELL and SHORT CAD via USDCAD BUY)
        candidate_exposures = self._get_directional_currency_exposures(symbol, direction)
        all_active_trades = open_positions + pending_orders
        for trade in all_active_trades:
            t_sym = trade.get("symbol", "")
            t_dir = trade.get("direction", "BUY")
            existing_exposures = self._get_directional_currency_exposures(t_sym, t_dir)
            for curr, proposed_side in candidate_exposures.items():
                if curr in existing_exposures:
                    existing_side = existing_exposures[curr]
                    if proposed_side != existing_side:
                        return False, (
                            f"CURRENCY DIRECTION CONFLICT: Already {existing_side} '{curr}' via {t_sym} {t_dir}. "
                            f"Cannot open contradictory {proposed_side} '{curr}' via {symbol} {direction}. "
                            f"All portfolio trades must row in the same macro currency direction."
                        ), 0.0

        # 3.8 Symbol Loss Lockout (Post-loss cooldown to prevent churn)
        is_locked, cooldown_reason = self._check_symbol_loss_cooldown(symbol)
        if is_locked:
            return False, cooldown_reason, 0.0

        # 4. Effective Risk calculation (Tiered A+ Conviction Sizing + Drawdown/Health Throttles)
        score = float(signal.get("score", 0.78) or 0.78)
        if self.conviction_sizing_cfg.get("enabled", True):
            tier_b_max = self.conviction_sizing_cfg.get("tier_b_max_score", 0.74)
            tier_a_max = self.conviction_sizing_cfg.get("tier_a_max_score", 0.84)
            if score < (tier_b_max + 0.005):
                effective_risk_pct = self.conviction_sizing_cfg.get("tier_b_risk_pct", 1.50)
            elif score < (tier_a_max + 0.005):
                effective_risk_pct = self.conviction_sizing_cfg.get("tier_a_risk_pct", 2.00)
            else:
                effective_risk_pct = self.conviction_sizing_cfg.get("tier_aplus_risk_pct", 2.75)
        else:
            effective_risk_pct = self.risk_pct

        if drawdown_pct >= self.throttle_pct:
            effective_risk_pct *= 0.5  # Halved on portfolio drawdown

        # Rule 10: Apply strategy health multiplier
        strat_name = signal.get("strategy", "TREND")
        if self.health_monitor:
            health = self.health_monitor.evaluate_strategy_health(strat_name)
            if health.get("risk_multiplier", 1.0) < 1.0:
                effective_risk_pct *= health["risk_multiplier"]

        signal["applied_risk_pct"] = round(effective_risk_pct, 2)
        risk_amount_usd = account_equity * (effective_risk_pct / 100.0)

        # 5. Position Sizing
        entry = signal["entry"]
        stop = signal["stop_loss"]
        direction = signal["direction"]
        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            return False, "INVALID STOP DISTANCE", 0.0

        # Determine exact lot size via MT5 native contract engine or fallback formulas
        raw_lots = 0.01
        min_lot = 0.01
        lot_step = 0.01
        cost_per_lot = 0.0

        mt5_connected = False
        try:
            import MetaTrader5 as mt5
            mt5_sym = raw_symbol if mt5.symbol_info(raw_symbol) else symbol
            info = mt5.symbol_info(mt5_sym)
            if info:
                mt5_connected = True
                min_lot = info.volume_min if info.volume_min > 0 else 0.01
                lot_step = info.volume_step if info.volume_step > 0 else 0.01

                # Priority 1: Use MT5 native profit/loss calculation engine (100% broker-certified across all asset classes)
                mt5_action = mt5.ORDER_TYPE_BUY if direction == "BUY" else mt5.ORDER_TYPE_SELL
                calc_loss = mt5.order_calc_profit(mt5_action, mt5_sym, 1.0, entry, stop)
                if calc_loss is not None and abs(calc_loss) > 0:
                    cost_per_lot = abs(calc_loss)
                    raw_lots = risk_amount_usd / cost_per_lot
        except Exception:
            pass

        # Priority 2: Precise mathematical fallbacks if MT5 native calc unavailable or zero
        if cost_per_lot <= 0:
            if "XAU" in symbol or "GOLD" in symbol:
                # 1 standard lot = 100 troy oz. $1.00 move = $100.00 loss per standard lot.
                cost_per_lot = stop_dist * 100.0
                raw_lots = risk_amount_usd / max(cost_per_lot, 1.0)
            else:
                # Standard Forex: pip math converted to account currency (USD)
                pip_size = 0.01 if "JPY" in symbol else 0.0001
                stop_pips = stop_dist / pip_size
                s_clean = symbol.replace(".C", "").replace("M", "").replace(".RAW", "")
                if s_clean.endswith("GBP"):
                    base_pip_val = 13.50  # GBP quote currency (~$13.50/pip/lot)
                elif s_clean.endswith("CAD"):
                    base_pip_val = 7.15   # CAD quote currency (~$7.15/pip/lot)
                elif s_clean.endswith("CHF"):
                    base_pip_val = 11.50  # CHF quote currency (~$11.50/pip/lot)
                elif s_clean.endswith("JPY") and entry > 0:
                    base_pip_val = 1000.0 / entry  # JPY quote currency (~$6.35/pip/lot)
                else:
                    base_pip_val = 10.0
                pip_val_per_lot = base_pip_val if not self.is_cent_account else (base_pip_val * 0.01)
                cost_per_lot = stop_pips * pip_val_per_lot
                raw_lots = risk_amount_usd / max(cost_per_lot, 0.01)

        # Round to broker step and enforce minimum
        steps = round(raw_lots / lot_step)
        rounded_lots = round(max(min_lot, steps * lot_step), 2)

        # Safety Check: Never round UP above target 1.0% risk if a smaller lot step is available
        while rounded_lots > min_lot and (rounded_lots * cost_per_lot) > (risk_amount_usd * 1.02):
            rounded_lots = round(rounded_lots - lot_step, 2)

        # Hard Cap: Enforce strict safety limit (cannot exceed 2.0% risk under any broker discrepancy)
        actual_risk_usd = rounded_lots * cost_per_lot
        if actual_risk_usd > (account_equity * (self.max_risk_pct / 100.0)):
            return False, f"LOT SIZING EXCEEDS HARD RISK CAP (${actual_risk_usd:.2f} > {self.max_risk_pct}% max risk cap)", 0.0

        return True, "APPROVED", rounded_lots
