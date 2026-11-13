"""
Trade Management Engine (Balanced Model):
1. Break-Even Protection at +0.8R (risk-free trade with +0.1R buffer).
2. True 33% Partial Take-Profit at +1.2R (banks cash into balance, removes TP cap).
3. Step-Lock at +1.5R (ratchets stop loss to lock in +0.6R guaranteed profit).
4. Dynamic Structure / Previous Candle Trailing Stop above +1.8R.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple

STATE_FILE = Path(__file__).resolve().parent.parent / "data" / "trade_management_state.json"
DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trading_journal.db"


class TradeManager:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        tm_cfg = self.config.get("trade_management", {})
        self.enabled = tm_cfg.get("enabled", True)
        
        # Thresholds (in R-multiples)
        self.be_trigger_r = tm_cfg.get("breakeven_trigger_r", 0.8)
        self.be_buffer_r = tm_cfg.get("breakeven_buffer_r", 0.1)
        self.partial_tp_trigger_r = tm_cfg.get("partial_tp_trigger_r", 1.2)
        self.partial_tp_vol_pct = tm_cfg.get("partial_tp_volume_pct", 0.33)
        self.step_lock_trigger_r = tm_cfg.get("step_lock_trigger_r", 1.5)
        self.step_lock_guarantee_r = tm_cfg.get("step_lock_guarantee_r", 0.6)
        self.dynamic_trail_trigger_r = tm_cfg.get("dynamic_trail_trigger_r", 1.8)
        self.trail_atr_mult = tm_cfg.get("trail_atr_multiplier", 1.2)
        self.trail_to_candle_structure = tm_cfg.get("trail_to_candle_structure", True)
        
        self.state: Dict[str, Dict[str, Any]] = self._load_state()

    def _load_state(self) -> Dict[str, Dict[str, Any]]:
        """Loads persistently stored trade management state from disk."""
        if STATE_FILE.exists():
            try:
                with open(STATE_FILE, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except Exception:
                pass
        return {}

    def _save_state(self):
        """Saves trade management state to disk."""
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(STATE_FILE, "w") as f:
                json.dump(self.state, f, indent=2)
        except Exception:
            pass

    def prune_closed_positions(self, active_tickets: List[int]):
        """Prunes state records for positions that have closed."""
        active_str = {str(t) for t in active_tickets}
        keys_to_delete = [k for k in self.state if k not in active_str]
        if keys_to_delete:
            for k in keys_to_delete:
                del self.state[k]
            self._save_state()

    def get_initial_risk_dist(self, pos: Dict[str, Any], lower_atr: float) -> float:
        """
        Retrieves true initial risk distance (abs(entry - initial_sl)).
        Queries state cache and trading_journal.db first to preserve accurate R calculations
        even after Stop Loss has moved to Break-Even.
        """
        ticket = pos.get("ticket")
        ticket_str = str(ticket)
        entry = pos.get("entry_price", 0.0)
        sl = pos.get("sl", 0.0)
        direction = pos.get("direction", "BUY")

        # 0. Check if already cached in persistent state
        cached_dist = self.state.get(ticket_str, {}).get("initial_risk_dist", 0.0)
        if cached_dist > 0:
            return cached_dist

        dist = 0.0

        # 1. Query journal database for initial_sl
        if DB_PATH.exists() and ticket:
            try:
                with sqlite3.connect(DB_PATH) as conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT initial_sl FROM trades WHERE ticket = ?", (ticket,))
                    row = cursor.fetchone()
                    if row and row[0] is not None and float(row[0]) > 0:
                        initial_sl = float(row[0])
                        d = abs(entry - initial_sl)
                        if d > 0:
                            dist = d
            except Exception:
                pass

        # 2. Check if current SL is on the loss side
        if dist <= 0 and sl > 0:
            if direction == "BUY" and sl < entry:
                dist = abs(entry - sl)
            elif direction == "SELL" and sl > entry:
                dist = abs(entry - sl)

        # 3. Fallback to standard 1.2 * lower_atr
        if dist <= 0:
            dist = max(0.0001, 1.2 * lower_atr)

        # Cache in state
        if ticket_str in self.state:
            self.state[ticket_str]["initial_risk_dist"] = dist
            self._save_state()

        return dist

    def process_position(
        self,
        pos: Dict[str, Any],
        df_lower: Any,
        mt5_module: Any,
        exec_agent: Any,
        notifier: Any,
        logger_func: Any
    ):
        """
        Executes the 4-step balanced trade management model for a single open position.
        """
        if not self.enabled:
            return

        sym = pos["symbol"]
        ticket = pos["ticket"]
        ticket_str = str(ticket)
        direction = pos["direction"]
        entry = pos["entry_price"]
        sl = pos["sl"]
        tp = pos["tp"]
        current_vol = pos["volume"]

        # Ensure ticket entry exists in state
        if ticket_str not in self.state:
            self.state[ticket_str] = {
                "partial_tp_taken": False,
                "second_partial_taken": False,
                "be_applied": False,
                "step_locked": False,
                "highest_r": 0.0
            }

        # Rollover Shield: Freeze stop modification during 5:00 PM EST rollover spread blowout (21:45 to 22:30 UTC)
        now_utc = datetime.now(timezone.utc)
        curr_mins = now_utc.hour * 60 + now_utc.minute
        if (21 * 60 + 45) <= curr_mins <= (22 * 60 + 30):
            return

        tick = mt5_module.symbol_info_tick(sym)
        if not tick:
            return

        current_price = tick.bid if direction == "BUY" else tick.ask

        sym_info = mt5_module.symbol_info(sym)
        sym_digits = sym_info.digits if (sym_info and sym_info.digits is not None) else 5
        sym_pip = 0.01 if "JPY" in sym else (0.1 if "XAU" in sym else 0.0001)
        lot_step = sym_info.volume_step if (sym_info and sym_info.volume_step > 0) else 0.01
        min_lot = sym_info.volume_min if (sym_info and sym_info.volume_min > 0) else 0.01

        # Calculate lower ATR
        from core.indicators import compute_atr
        lower_atr = compute_atr(df_lower, 14).iloc[-1] if len(df_lower) > 15 else (sym_pip * 15.0)

        risk_dist = self.get_initial_risk_dist(pos, lower_atr)
        if risk_dist <= 0:
            return

        # Calculate current profit in R
        profit_dist = (current_price - entry) if direction == "BUY" else (entry - current_price)
        current_r = round(profit_dist / risk_dist, 2)

        # Track peak R for reporting
        if current_r > self.state[ticket_str].get("highest_r", 0.0):
            self.state[ticket_str]["highest_r"] = current_r
            self._save_state()

        min_step = 2.0 * sym_pip  # Minimum 2 pip change to avoid broker request churn

        # -------------------------------------------------------------
        # STEP 1: BREAK-EVEN PROTECTION (+0.8R)
        # -------------------------------------------------------------
        if current_r >= self.be_trigger_r:
            needs_be = False
            be_target = 0.0

            if direction == "BUY":
                be_target = round(entry + (self.be_buffer_r * risk_dist), sym_digits)
                if sl < be_target:
                    needs_be = True
            elif direction == "SELL":
                be_target = round(entry - (self.be_buffer_r * risk_dist), sym_digits)
                if sl == 0 or sl > be_target:
                    needs_be = True

            if needs_be:
                if exec_agent.modify_stop_loss(ticket, be_target):
                    sl = be_target
                    self.state[ticket_str]["be_applied"] = True
                    self._save_state()
                    logger_func(f"🛡️ [BALANCED MANAGEMENT] {sym} #{ticket} reached +{current_r}R! Stop Loss moved to Break-Even at {be_target:.5f}")
                    notifier.notify_breakeven(sym, be_target)

        # -------------------------------------------------------------
        # STEP 2: 33% TRUE PARTIAL TAKE-PROFIT (+1.2R) + HARD +2.5R RUNNER TP
        # -------------------------------------------------------------
        runner_tp = round(entry + (2.5 * risk_dist), sym_digits) if direction == "BUY" else round(entry - (2.5 * risk_dist), sym_digits)

        # Ensure any post-partial runner that had TP=0 gets its Hard +2.5R Broker TP restored
        if self.state[ticket_str].get("partial_tp_taken", False) and (tp is None or tp == 0.0):
            if exec_agent.modify_sltp(ticket, new_sl=sl, new_tp=runner_tp):
                tp = runner_tp
                logger_func(f"🎯 [RUNNER TP ARMED] {sym} #{ticket}: Set Hard +2.5R Broker Take-Profit at {runner_tp:.5f} for spike protection.")

        if current_r >= self.partial_tp_trigger_r and not self.state[ticket_str].get("partial_tp_taken", False):
            # Calculate 33% volume rounded to broker lot step
            raw_close_vol = current_vol * self.partial_tp_vol_pct
            close_vol = round(round(raw_close_vol / lot_step) * lot_step, 2)
            rem_vol = round(current_vol - close_vol, 2)

            # Only execute if volume is splittable
            if close_vol >= min_lot and rem_vol >= min_lot:
                pnl_usd_est = pos.get("profit", 0.0) * (close_vol / current_vol)
                success = exec_agent.close_partial_position(ticket, close_vol)
                if success:
                    self.state[ticket_str]["partial_tp_taken"] = True
                    self.state[ticket_str]["partial_time"] = datetime.now(timezone.utc).isoformat()
                    self._save_state()

                    # Ensure SL is at Break-Even (+0.1R cushion) or better, and set Hard +2.5R Broker TP for runner
                    be_level = round(entry + (self.be_buffer_r * risk_dist), sym_digits) if direction == "BUY" else round(entry - (self.be_buffer_r * risk_dist), sym_digits)
                    better_sl = (sl > be_level) if direction == "BUY" else (sl < be_level and sl > 0)
                    final_sl = sl if better_sl else be_level

                    exec_agent.modify_sltp(ticket, new_sl=final_sl, new_tp=runner_tp)
                    logger_func(f"💰 [PARTIAL TP] {sym} #{ticket}: Banked 33% profit (+${pnl_usd_est:,.2f}) on {close_vol} lots @ +{current_r}R! Remaining {rem_vol} lots running risk-free (Target TP: {runner_tp:.5f}).")
                    notifier.notify_partial_tp(sym, close_vol, rem_vol, current_price, pnl_usd_est, final_sl)
                    current_vol = rem_vol
                    sl = final_sl
                    tp = runner_tp
            else:
                # Standard account 0.01 minimum lot fallback: lock in +0.25R guaranteed profit on the full lot (leaving 1.25R+ breathing room to target)
                lock_025 = round(entry + (0.25 * risk_dist), sym_digits) if direction == "BUY" else round(entry - (0.25 * risk_dist), sym_digits)
                needs_025 = (sl < lock_025) if direction == "BUY" else (sl == 0 or sl > lock_025)
                if needs_025 and exec_agent.modify_stop_loss(ticket, lock_025):
                    sl = lock_025
                    self.state[ticket_str]["partial_tp_taken"] = True
                    self._save_state()
                    logger_func(f"🔒 [MIN-LOT PROFIT LOCK] {sym} #{ticket} ({current_vol} lot) reached +{current_r}R! Stop Loss secured at +0.25R ({lock_025:.5f}) with ample room to breathe to +2.5R target.")

        # -------------------------------------------------------------
        # STEP 3: STEP-LOCK GUARANTEED PROFIT (+1.5R -> Lock +0.6R)
        # -------------------------------------------------------------
        if current_r >= self.step_lock_trigger_r:
            needs_lock = False
            lock_target = 0.0

            if direction == "BUY":
                lock_target = round(entry + (self.step_lock_guarantee_r * risk_dist), sym_digits)
                if sl < lock_target:
                    needs_lock = True
            elif direction == "SELL":
                lock_target = round(entry - (self.step_lock_guarantee_r * risk_dist), sym_digits)
                if sl == 0 or sl > lock_target:
                    needs_lock = True

            if needs_lock:
                if exec_agent.modify_stop_loss(ticket, lock_target):
                    sl = lock_target
                    self.state[ticket_str]["step_locked"] = True
                    self._save_state()
                    logger_func(f"🔒 [STEP-LOCK] {sym} #{ticket} reached +{current_r}R! Guaranteed profit locked at +{self.step_lock_guarantee_r}R (SL: {lock_target:.5f})")

        # -------------------------------------------------------------
        # STEP 4: 2ND PARTIAL TP (+1.8R) + DYNAMIC TRAILING STOP (+1.8R+)
        # -------------------------------------------------------------
        if current_r >= self.dynamic_trail_trigger_r:
            # 4A. If >= 0.02 lots remain, bank a 2nd Partial (0.01 lot) at +1.8R and lock SL at +1.1R
            if not self.state[ticket_str].get("second_partial_taken", False):
                close_vol2 = round(round((current_vol * 0.5) / lot_step) * lot_step, 2)
                rem_vol2 = round(current_vol - close_vol2, 2)
                if close_vol2 >= min_lot and rem_vol2 >= min_lot:
                    pnl_usd_est2 = pos.get("profit", 0.0) * (close_vol2 / current_vol)
                    if exec_agent.close_partial_position(ticket, close_vol2):
                        self.state[ticket_str]["second_partial_taken"] = True
                        self._save_state()
                        lock_11 = round(entry + (1.1 * risk_dist), sym_digits) if direction == "BUY" else round(entry - (1.1 * risk_dist), sym_digits)
                        better_sl2 = (sl > lock_11) if direction == "BUY" else (sl < lock_11 and sl > 0)
                        final_sl2 = sl if better_sl2 else lock_11
                        exec_agent.modify_sltp(ticket, new_sl=final_sl2, new_tp=runner_tp)
                        logger_func(f"💰 [2ND PARTIAL TP] {sym} #{ticket}: Banked another +${pnl_usd_est2:,.2f} on {close_vol2} lots @ +{current_r}R! Final {rem_vol2} lot locked at +1.1R ({final_sl2:.5f}) targeting {runner_tp:.5f}.")
                        notifier.notify_partial_tp(sym, close_vol2, rem_vol2, current_price, pnl_usd_est2, final_sl2)
                        current_vol = rem_vol2
                        sl = final_sl2
                else:
                    self.state[ticket_str]["second_partial_taken"] = True
                    self._save_state()

            trail_candidate = None

            if direction == "BUY":
                atr_trail = current_price - (self.trail_atr_mult * lower_atr)
                if self.trail_to_candle_structure and len(df_lower) >= 3:
                    prev_low = float(df_lower["low"].iloc[-2]) - (0.2 * lower_atr)
                    trail_candidate = round(max(atr_trail, prev_low), sym_digits)
                else:
                    trail_candidate = round(atr_trail, sym_digits)

                # Ensure candidate ratchets forward and stays in profit
                if trail_candidate > (sl + min_step) and trail_candidate > entry:
                    if exec_agent.modify_stop_loss(ticket, trail_candidate):
                        locked_r = round((trail_candidate - entry) / risk_dist, 2)
                        logger_func(f"📈 [DYNAMIC TRAIL] {sym} BUY #{ticket}: Stop Loss trailed to {trail_candidate:.5f} (Locking in +{locked_r}R profit)")

            elif direction == "SELL":
                atr_trail = current_price + (self.trail_atr_mult * lower_atr)
                if self.trail_to_candle_structure and len(df_lower) >= 3:
                    prev_high = float(df_lower["high"].iloc[-2]) + (0.2 * lower_atr)
                    trail_candidate = round(min(atr_trail, prev_high), sym_digits)
                else:
                    trail_candidate = round(atr_trail, sym_digits)

                # Ensure candidate ratchets down and stays in profit
                if (sl == 0 or trail_candidate < (sl - min_step)) and trail_candidate < entry:
                    if exec_agent.modify_stop_loss(ticket, trail_candidate):
                        locked_r = round((entry - trail_candidate) / risk_dist, 2)
                        logger_func(f"📉 [DYNAMIC TRAIL] {sym} SELL #{ticket}: Stop Loss trailed to {trail_candidate:.5f} (Locking in +{locked_r}R profit)")
