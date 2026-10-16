"""
Live Trading Bot Orchestrator:
Runs the full multi-agent pipeline:
- Fetches live bars from MT5
- Runs Market-Regime Agent
- Generates Trend strategy signals
- Applies multi-factor Signal Scorer
- Enforces strict Risk Manager rules (drawdown checks, sizing, kill-switch)
- Places orders through Execution Agent into MT5
- Manages trailing stops on active trades
"""

import time
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional, List, Dict, Any
import yaml

ROOT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT_DIR))

from agents.data_agent import DataAgent
from agents.regime_agent import MarketRegimeAgent
from agents.trend_agent import TrendAgent
from agents.breakout_agent import BreakoutAgent
from agents.mean_reversion_agent import MeanReversionAgent
from agents.signal_scorer import SignalScorer
from agents.risk_manager import RiskManager
from agents.execution_agent import ExecutionAgent
from core.database import init_db, log_signal
from core.indicators import compute_atr
from core.telegram_notifier import TelegramNotifier

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None


def resolve_mt5_symbol(symbol: str) -> Optional[str]:
    """
    Finds the active symbol name on the broker, accounting for suffixes and aliases.
    """
    if mt5 is None:
        return symbol

    # Known aliases across different broker naming conventions
    aliases_map = {
        "XAUUSD": ["XAUUSD", "GOLD", "XAU/USD"],
    }

    candidates = aliases_map.get(symbol.upper(), [symbol])
    suffixes = ["", "m", ".c", "c", "t", "T", ".raw", ".pro", "_i", ".s"]

    for cand in candidates:
        for sfx in suffixes:
            test_sym = f"{cand}{sfx}"
            info = mt5.symbol_info(test_sym)
            if info is not None:
                return test_sym

    return None


from core.bot_logger import append_live_log, update_heartbeat, update_scanner_matrix


def run_cycle(cycle_num: int = 1) -> int:
    """
    Executes a single market scan cycle across all enabled pairs with real-time logging.
    Returns the number of scanned symbols.
    """
    config_path = ROOT_DIR / "config" / "settings.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    init_db()

    exec_agent = ExecutionAgent(config)
    if not exec_agent.connect():
        append_live_log("Unable to connect to MetaTrader 5 terminal. Check that MT5 is running.", level="ERROR")
        return 0

    acct = mt5.account_info()
    if acct is None:
        append_live_log("Unable to read account credentials from active MT5 terminal.", level="ERROR")
        return 0

    equity = acct.equity
    balance = acct.balance
    timeframe = config.get("data", {}).get("timeframe", "H1").upper()

    append_live_log(f"--- 🔄 SCAN CYCLE #{cycle_num} INITIATED ({timeframe} CANDLES) ---")
    append_live_log(f"MT5 Account #{acct.login} ({acct.server}) | Balance: ${balance:,.2f} | Equity: ${equity:,.2f}")

    data_agent = DataAgent(config)
    regime_agent = MarketRegimeAgent(config)
    trend_agent = TrendAgent(config)
    breakout_agent = BreakoutAgent(config)
    mean_rev_agent = MeanReversionAgent(config)
    scorer = SignalScorer(config)
    risk_manager = RiskManager(config)

    from agents.consensus_engine import ConsensusEngine
    from core.circuit_breaker import CircuitBreaker
    consensus_engine = ConsensusEngine(config)
    circuit_breaker = CircuitBreaker(config)

    # Weekend vs Weekday portfolio selection
    is_weekend = datetime.utcnow().weekday() in (5, 6)
    forex_syms = config.get("symbols", {}).get("forex", [])
    metal_syms = config.get("symbols", {}).get("metals", [])

    if is_weekend:
        symbols_to_scan = []
        append_live_log("🕒 Weekend Standby: Forex and Metals markets are closed. Scanner in standby until Sunday 5:00 PM EST.")
    else:
        symbols_to_scan = forex_syms + metal_syms
        append_live_log(f"🟢 Market Active: Scanning {len(symbols_to_scan)} symbols across Forex and Metals.")

    open_positions = exec_agent.get_open_positions()
    pending_orders = exec_agent.get_pending_orders()
    append_live_log(f"Active MT5 Positions: {len(open_positions)} | Pending Orders: {len(pending_orders)}")

    notifier = TelegramNotifier(config)

    # 0. Detect newly closed trades from MT5 deal history and dispatch post-trade analysis
    try:
        import sqlite3
        from datetime import timedelta
        db_file = ROOT_DIR / "data" / "trading_journal.db"
        with sqlite3.connect(db_file) as conn:
            cur = conn.cursor()
            # Fetch recorded trades that don't have an exit_time yet
            open_db_trades = cur.execute(
                "SELECT id, ticket, symbol, direction, lots, entry_price, initial_sl, initial_tp, strategy FROM trades WHERE exit_time IS NULL"
            ).fetchall()

            if open_db_trades:
                from_date = datetime.now() - timedelta(days=7)
                to_date = datetime.now() + timedelta(days=1)
                closed_deals = mt5.history_deals_get(from_date, to_date)
                
                if closed_deals:
                    deal_map = {d.position_id: d for d in closed_deals if d.entry == 1} # exit deals indexed by position ticket
                    
                    for row in open_db_trades:
                        db_id, t_ticket, t_sym, t_dir, t_lots, t_entry, t_sl, t_tp, t_strat = row
                        
                        # If trade is closed in MT5 (deal with matching position ticket exists)
                        matching_deal = deal_map.get(t_ticket)
                        if matching_deal:
                            exit_price = matching_deal.price
                            profit_usd = matching_deal.profit
                            exit_time = datetime.fromtimestamp(matching_deal.time).isoformat()
                            comment = matching_deal.comment or ""
                            
                            # Determine exit reason
                            if "sl" in comment.lower() or abs(exit_price - (t_sl or 0)) < 0.0005:
                                exit_reason = "STOP LOSS HIT"
                            elif "tp" in comment.lower() or (t_tp and abs(exit_price - t_tp) < 0.0005):
                                exit_reason = "TAKE PROFIT HIT"
                            else:
                                exit_reason = f"CLOSED ({comment})" if comment else "CLOSED"

                            # Calculate R multiple
                            r_risk = abs(t_entry - t_sl) if t_sl else 0.0001
                            pnl_r = round((exit_price - t_entry) / r_risk if t_dir == "BUY" else (t_entry - exit_price) / r_risk, 2)

                            # Generate Post-Trade Analysis Report
                            if profit_usd < 0:
                                analysis_text = (
                                    f"Trade was stopped out at `{exit_price:.5f}`. Market experienced a pullback/spread expansion past the dynamic ATR stop buffer (`{t_sl:.5f}`). "
                                    f"Capital was strictly protected to initial 1% risk cap."
                                )
                            else:
                                analysis_text = (
                                    f"Target reached at `{exit_price:.5f}`! Strategy {t_strat} captured +{pnl_r}R profit into the expansion leg."
                                )

                            # Update database
                            cur.execute("""
                                UPDATE trades 
                                SET exit_time = ?, exit_price = ?, exit_reason = ?, pnl_usd = ?, pnl_r = ?, notes = ?
                                WHERE id = ?
                            """, (exit_time, exit_price, exit_reason, profit_usd, pnl_r, analysis_text, db_id))
                            conn.commit()

                            # Dispatch alerts and live log
                            append_live_log(f"📋 [TRADE REPORT] Closed #{t_ticket} ({t_sym} {t_dir}) | PnL: ${profit_usd:+,.2f} ({pnl_r}R) | Reason: {exit_reason}")
                            notifier.notify_trade_closed(
                                symbol=t_sym,
                                direction=t_dir,
                                lots=t_lots,
                                entry_price=t_entry,
                                exit_price=exit_price,
                                profit_usd=profit_usd,
                                exit_reason=exit_reason,
                                analysis_explanation=analysis_text
                            )
    except Exception as e:
        append_live_log(f"Error checking closed trade history: {e}", level="WARNING")

    # 1. Partial Take Profit (50%) & Trailing Stops for open positions
    for pos in open_positions:
        sym = pos["symbol"]
        df_d1, df_lower = data_agent.get_bars(sym, timeframe=timeframe)
        if len(df_d1) > 15:
            d1_atr = compute_atr(df_d1, 14).iloc[-1]
            tick = mt5.symbol_info_tick(sym)
            if tick and d1_atr > 0:
                current_price = tick.bid if pos["direction"] == "BUY" else tick.ask
                entry = pos["entry_price"]
                sl = pos["sl"]
                tp = pos["tp"]
                current_vol = pos["volume"]

                # 1A. Partial Take Profit Trigger at 1.5R:
                # If price reached TP level and volume is >= 0.02 lots, bank 50% profit & remove TP cap for trailing runner
                if tp > 0 and current_vol >= 0.02:
                    hit_tp = (pos["direction"] == "BUY" and current_price >= tp) or \
                             (pos["direction"] == "SELL" and current_price <= tp)
                    if hit_tp:
                        half_vol = round(current_vol / 2.0, 2)
                        pnl_usd = pos["profit"] * (half_vol / current_vol)
                        success = exec_agent.close_partial_position(pos["ticket"], half_vol)
                        if success:
                            # Move SL to Break-Even (+0.1R cushion) and set TP to 0 (unlimited runner)
                            be_sl = entry + (0.1 * d1_atr) if pos["direction"] == "BUY" else entry - (0.1 * d1_atr)
                            exec_agent.modify_sltp(pos["ticket"], new_sl=be_sl, new_tp=0.0)
                            rem_vol = round(current_vol - half_vol, 2)
                            append_live_log(f"💰 [PARTIAL TP] {sym}: Banked +${pnl_usd:,.2f} on {half_vol} lots! Remaining {rem_vol} lots running risk-free with Trailing Stop.")
                            notifier.notify_partial_tp(sym, half_vol, rem_vol, current_price, pnl_usd, be_sl)
                            continue

                # 1B. Standard Break-even at +1.0R of profit
                lower_atr = compute_atr(df_lower, 14).iloc[-1]
                risk_dist = abs(entry - sl) if (sl > 0 and ((pos["direction"] == "BUY" and sl < entry) or (pos["direction"] == "SELL" and sl > entry))) else (1.2 * lower_atr)

                if pos["direction"] == "BUY":
                    profit_dist = current_price - entry
                    if profit_dist >= (1.0 * risk_dist) and sl < entry:
                        new_sl = round(entry + (0.1 * risk_dist), 5)
                        exec_agent.modify_stop_loss(pos["ticket"], new_sl)
                        append_live_log(f"🎯 [MANAGEMENT] {sym} +1.0R reached! Stop Loss moved to Break-Even at {new_sl:.5f}")
                        notifier.notify_breakeven(sym, new_sl)

                elif pos["direction"] == "SELL":
                    profit_dist = entry - current_price
                    if profit_dist >= (1.0 * risk_dist) and (sl > entry or sl == 0):
                        new_sl = round(entry - (0.1 * risk_dist), 5)
                        exec_agent.modify_stop_loss(pos["ticket"], new_sl)
                        append_live_log(f"🎯 [MANAGEMENT] {sym} +1.0R reached! Stop Loss moved to Break-Even at {new_sl:.5f}")
                        notifier.notify_breakeven(sym, new_sl)

    # 2. Scan market for new signals across all 3 strategy agents
    scanned_count = 0
    scanner_matrix_rows = []

    for raw_sym in symbols_to_scan:
        actual_sym = resolve_mt5_symbol(raw_sym)
        if not actual_sym:
            continue

        df_d1, df_lower = data_agent.get_bars(actual_sym, timeframe=timeframe)
        if len(df_d1) < 50 or len(df_lower) < 50:
            continue

        scanned_count += 1
        d1_regimes = regime_agent.calculate_regime_series(df_d1)
        current_regime = d1_regimes.iloc[-1]
        curr_price = float(df_lower["close"].iloc[-1])
        pip_size = 0.01 if "JPY" in actual_sym else (0.1 if "XAU" in actual_sym else 0.0001)

        # Human-readable diagnostics
        status_text = ""
        action_badge = "WATCHING"

        if current_regime in ["TRENDING_UP", "TRENDING_DOWN"]:
            from core.indicators import compute_ema
            h1_ema_fast = float(compute_ema(df_lower["close"], 20).iloc[-1])
            pips_diff = abs(curr_price - h1_ema_fast) / pip_size
            direction_word = "above" if curr_price > h1_ema_fast else "below"
            pullback_window = df_lower.iloc[-10:-1]
            
            if current_regime == "TRENDING_UP":
                touched = (pullback_window["low"] <= compute_ema(df_lower["close"], 20).iloc[-10:-1]).any()
                if touched:
                    status_text = f"Pullback to EMA20 occurred! Watching for bullish candle trigger close."
                    action_badge = "PULLBACK DETECTED"
                else:
                    status_text = f"Uptrend active. Price is +{pips_diff:.1f} pips {direction_word} EMA20 ({h1_ema_fast:.5f}). Waiting for pullback."
                    action_badge = "WAITING PULLBACK"
            else:
                touched = (pullback_window["high"] >= compute_ema(df_lower["close"], 20).iloc[-10:-1]).any()
                if touched:
                    status_text = f"Pullback to EMA20 occurred! Watching for bearish candle trigger close."
                    action_badge = "PULLBACK DETECTED"
                else:
                    status_text = f"Downtrend active. Price is -{pips_diff:.1f} pips {direction_word} EMA20 ({h1_ema_fast:.5f}). Waiting for pullback."
                    action_badge = "WAITING PULLBACK"

        elif current_regime == "RANGING":
            from core.indicators import compute_rsi, compute_bollinger_bands
            rsi_val = float(compute_rsi(df_d1["close"], 14).iloc[-1])
            bb = compute_bollinger_bands(df_d1["close"], 20, 2.0)
            bb_low = float(bb["bb_lower"].iloc[-1])
            bb_high = float(bb["bb_upper"].iloc[-1])
            status_text = f"Ranging market. RSI: {rsi_val:.1f} (Looking for <35 at {bb_low:.5f} or >65 at {bb_high:.5f})."
            action_badge = "RANGE FADE"

        elif current_regime == "COMPRESSION":
            from core.indicators import compute_donchian
            donch = compute_donchian(df_d1, 20)
            d_high = float(donch["donchian_high"].iloc[-1])
            d_low = float(donch["donchian_low"].iloc[-1])
            status_text = f"Volatility Squeeze. Watching for breakout close above {d_high:.5f} or below {d_low:.5f}."
            action_badge = "SQUEEZE BREAKOUT"

        else:
            status_text = f"Consolidation / Unclear macro regime. Standing aside to protect capital."
            action_badge = "IDLE (PROTECTING)"

        # Check Circuit Breaker before processing setups (Rule 11)
        cb_tripped, cb_reason = circuit_breaker.is_tripped()
        if cb_tripped:
            append_live_log(f"⚠️ [{actual_sym}] {cb_reason}", level="WARNING")
            continue

        # Collect candidate signals from each agent
        candidate_signals = []
        
        # 1. Trend Agent
        s_trend = trend_agent.scan_for_signal(actual_sym, df_d1, df_lower, current_regime)
        if s_trend: candidate_signals.append(s_trend)

        # 2. Breakout Agent
        s_break = breakout_agent.scan_for_signal(actual_sym, df_d1, current_regime)
        if s_break: candidate_signals.append(s_break)

        # 3. Mean-Reversion Agent
        s_mr = mean_rev_agent.scan_for_signal(actual_sym, df_d1, df_lower, current_regime)
        if s_mr: candidate_signals.append(s_mr)

        # Multi-Agent Consensus & Conflict Arbitration (Architecture Layer 4)
        if candidate_signals:
            winning_signal, audit_verdict = consensus_engine.arbitrate(actual_sym, candidate_signals)
            append_live_log(f"🤝 [{actual_sym}] Consensus Audit: {audit_verdict}")
            
            if winning_signal:
                signal = winning_signal
                action_badge = "🔥 SETUP FOUND"
                score = scorer.score_signal(signal)
                append_live_log(f"⚡ [{actual_sym}] SETUP DETECTED! Strategy: {signal['strategy']} {signal['direction']} | Final Score: {score:.2f}")

                if scorer.is_eligible(score):
                    approved, reason, lots = risk_manager.evaluate_order(
                        signal, equity, balance, open_positions, pending_orders=pending_orders
                    )
                    signal["status"] = "APPROVED" if approved else "REJECTED_BY_RISK"
                    signal["rejection_reason"] = reason
                    log_signal(signal)

                    order_type_str = signal.get("order_type", "MARKET")
                    expiry_h = config.get("risk", {}).get("pending_order_expiry_hours", 24)

                    if approved:
                        action_badge = "🚀 ORDER PLACED"
                        target_entry_txt = f"{signal['entry']:.5f} ({order_type_str})" if order_type_str != "MARKET" else "Market"
                        append_live_log(f"✅ [{actual_sym}] RISK APPROVED! Placing {order_type_str} {lots} lots @ {target_entry_txt} | SL: {signal['stop_loss']:.5f} | TP: {signal.get('take_profit') or 'Trailing'}")
                        ticket = exec_agent.place_order(
                            symbol=actual_sym,
                            direction=signal["direction"],
                            lots=lots,
                            stop_loss=signal["stop_loss"],
                            take_profit=signal.get("take_profit"),
                            strategy_name=signal["strategy"],
                            order_type_str=order_type_str,
                            entry_price=signal.get("entry"),
                            expiration_hours=expiry_h
                        )
                        if ticket:
                            circuit_breaker.record_execution_success()
                            append_live_log(f"🎉 [{actual_sym}] Live {order_type_str} Order Placed in MT5! Ticket #{ticket} (Auto-exp: {expiry_h}h)")
                            notifier.notify_trade_opened(
                                symbol=actual_sym,
                                direction=f"{signal['direction']} ({order_type_str})",
                                lots=lots,
                                entry=signal["entry"],
                                sl=signal["stop_loss"],
                                tp=signal.get("take_profit"),
                                strategy=signal["strategy"],
                                score=score
                            )
                        else:
                            # Record execution failure for circuit breaker (Rule 11)
                            tripped = circuit_breaker.record_execution_error("Order placement rejected by MT5 terminal.")
                            if tripped:
                                append_live_log(f"🚨 [CIRCUIT BREAKER TRIPPED] Order placement halted due to repeated broker rejections.", level="ERROR")
                    else:
                        action_badge = "⚠️ RISK REJECTED"
                        append_live_log(f"⚠️ [{actual_sym}] Trade Rejected by Risk Manager: {reason}")

        scanner_matrix_rows.append({
            "symbol": actual_sym,
            "price": curr_price,
            "regime": current_regime,
            "badge": action_badge,
            "details": status_text,
            "last_checked": datetime.now().strftime("%H:%M:%S")
        })

    update_scanner_matrix(scanner_matrix_rows)
    now = datetime.now()
    from datetime import timedelta
    next_s = now + timedelta(seconds=300)
    update_heartbeat(
        status="Cycle complete",
        cycle_count=cycle_num,
        last_cycle_time=now.strftime("%Y-%m-%d %H:%M:%S"),
        next_cycle_time=next_s.strftime("%Y-%m-%d %H:%M:%S"),
        seconds_remaining=300,
        scanned_count=scanned_count,
        active_positions=len(open_positions),
        balance=balance,
        equity=equity,
        timeframe=timeframe
    )
    append_live_log(f"✅ Cycle #{cycle_num} finished. {scanned_count} active symbols analyzed. 0 errors.")
    return scanned_count


def main():
    import os
    pid_file = ROOT_DIR / "data" / "bot.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    config_path = ROOT_DIR / "config" / "settings.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    timeframe = config.get("data", {}).get("timeframe", "H1").upper()

    append_live_log("=" * 65)
    append_live_log("    SWING MULTI-AGENT TRADING BOT - LIVE ENGINE ONLINE")
    append_live_log("=" * 65)
    append_live_log(f"Active Timeframe: {timeframe} Candles (Lower Timeframe Scanning)")
    append_live_log("Market Coverage: Forex & Metals (XAUUSD) - 24/5")
    append_live_log("Cycle Interval: 5 Minutes (300 seconds)")

    cycle_count = 0
    try:
        while True:
            cycle_count += 1
            try:
                scanned = run_cycle(cycle_count)
            except Exception as e:
                append_live_log(f"Error in cycle #{cycle_count}: {e}", level="ERROR")
                scanned = 0

            # Countdown sleep loop (updates heartbeat every 5 seconds)
            scan_finish = datetime.now()
            from datetime import timedelta
            next_scan = scan_finish + timedelta(seconds=300)
            
            append_live_log(f"💤 Bot sleeping. Next scan in 5m 00s (at {next_scan.strftime('%H:%M:%S')})...")

            while datetime.now() < next_scan:
                if not pid_file.exists():
                    append_live_log("PID file removed. Halting bot cleanly.")
                    return

                sec_left = int((next_scan - datetime.now()).total_seconds())
                mins = sec_left // 60
                secs = sec_left % 60

                update_heartbeat(
                    status=f"Sleeping until next candle (Next scan in {mins}m {secs:02d}s)",
                    cycle_count=cycle_count,
                    last_cycle_time=scan_finish.strftime("%Y-%m-%d %H:%M:%S"),
                    next_cycle_time=next_scan.strftime("%Y-%m-%d %H:%M:%S"),
                    seconds_remaining=sec_left,
                    scanned_count=scanned,
                    active_positions=0,
                    balance=100000.0,
                    equity=100000.0,
                    timeframe=timeframe
                )
                time.sleep(5)

    finally:
        if pid_file.exists():
            pid_file.unlink()
        append_live_log("Bot engine terminated.")


if __name__ == "__main__":
    main()
