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
        "BTCUSD": ["BTCUSD", "BTCUSDT", "BTC/USD", "XBTUSD", "BTC"],
        "ETHUSD": ["ETHUSD", "ETHUSDT", "ETH/USD", "ETH"],
        "SOLUSD": ["SOLUSD", "SOLUSDT", "SOL/USD", "SOL"],
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


def run_cycle():
    config_path = ROOT_DIR / "config" / "settings.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    init_db()

    exec_agent = ExecutionAgent(config)
    if not exec_agent.connect():
        print("[BOT ERROR] Unable to connect to MetaTrader 5.")
        return

    acct = mt5.account_info()
    if acct is None:
        print("[BOT ERROR] Unable to read account info from MT5.")
        return

    equity = acct.equity
    balance = acct.balance
    timeframe = config.get("data", {}).get("timeframe", "H1").upper()

    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] --- CYCLE START ({timeframe} CANDLES) ---")
    print(f"Account: #{acct.login} | Server: {acct.server} | Balance: ${balance:.2f} | Equity: ${equity:.2f}")

    data_agent = DataAgent(config)
    regime_agent = MarketRegimeAgent(config)
    trend_agent = TrendAgent(config)
    breakout_agent = BreakoutAgent(config)
    mean_rev_agent = MeanReversionAgent(config)
    scorer = SignalScorer(config)
    risk_manager = RiskManager(config)

    # Weekend vs Weekday portfolio selection
    is_weekend = datetime.utcnow().weekday() in (5, 6)  # Saturday=5, Sunday=6
    forex_syms = config.get("symbols", {}).get("forex", [])
    metal_syms = config.get("symbols", {}).get("metals", [])
    crypto_syms = config.get("symbols", {}).get("crypto", [])

    if is_weekend:
        symbols_to_scan = crypto_syms
        print(f"[WEEKEND MODE] Forex & Metals markets closed. Crypto 24/7 scanning active ({', '.join(crypto_syms)}).")
    else:
        symbols_to_scan = forex_syms + metal_syms + crypto_syms
        print(f"[WEEKDAY MODE] Full universe active: {len(symbols_to_scan)} symbols (Forex + Metals + Crypto).")

    open_positions = exec_agent.get_open_positions()
    print(f"Active MT5 Positions: {len(open_positions)}")

    notifier = TelegramNotifier(config)

    # 1. Trailing stops and trade management for open positions
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

                if pos["direction"] == "BUY":
                    profit_dist = current_price - entry
                    if profit_dist > (1.5 * d1_atr) and sl < entry:
                        new_sl = entry + (0.1 * d1_atr)
                        exec_agent.modify_stop_loss(pos["ticket"], new_sl)
                        print(f"[MANAGEMENT] {sym} Stop Loss moved to Break-Even at {new_sl:.5f}")
                        notifier.notify_breakeven(sym, new_sl)

                elif pos["direction"] == "SELL":
                    profit_dist = entry - current_price
                    if profit_dist > (1.5 * d1_atr) and (sl > entry or sl == 0):
                        new_sl = entry - (0.1 * d1_atr)
                        exec_agent.modify_stop_loss(pos["ticket"], new_sl)
                        print(f"[MANAGEMENT] {sym} Stop Loss moved to Break-Even at {new_sl:.5f}")
                        notifier.notify_breakeven(sym, new_sl)

    # 2. Scan market for new signals across all 3 strategy agents
    scanned_count = 0
    for raw_sym in symbols_to_scan:
        actual_sym = resolve_mt5_symbol(raw_sym)
        if not actual_sym:
            # Symbol not present on current broker
            continue

        df_d1, df_lower = data_agent.get_bars(actual_sym, timeframe=timeframe)
        if len(df_d1) < 50 or len(df_lower) < 50:
            continue

        scanned_count += 1
        d1_regimes = regime_agent.calculate_regime_series(df_d1)
        current_regime = d1_regimes.iloc[-1]

        # Collect candidate signals from each agent
        candidate_signals = []
        
        # 1. Trend Agent (D1 Regime + Lower Timeframe Pullback Trigger)
        s_trend = trend_agent.scan_for_signal(actual_sym, df_d1, df_lower, current_regime)
        if s_trend: candidate_signals.append(s_trend)

        # 2. Breakout Agent (D1 Volatility Squeeze Breakout)
        s_break = breakout_agent.scan_for_signal(actual_sym, df_d1, current_regime)
        if s_break: candidate_signals.append(s_break)

        # 3. Mean-Reversion Agent (D1 Ranging Extremes + Lower Timeframe Fade Trigger)
        s_mr = mean_rev_agent.scan_for_signal(actual_sym, df_d1, df_lower, current_regime)
        if s_mr: candidate_signals.append(s_mr)

        for signal in candidate_signals:
            score = scorer.score_signal(signal)
            if scorer.is_eligible(score):
                approved, reason, lots = risk_manager.evaluate_order(
                    signal, equity, balance, open_positions
                )
                signal["status"] = "APPROVED" if approved else "REJECTED_BY_RISK"
                signal["rejection_reason"] = reason
                log_signal(signal)

                if approved:
                    print(f"[SIGNAL ACCEPTED] {actual_sym} {signal['strategy']} {signal['direction']} - Score: {score:.2f} | Lots: {lots}")
                    ticket = exec_agent.place_order(
                        symbol=actual_sym,
                        direction=signal["direction"],
                        lots=lots,
                        stop_loss=signal["stop_loss"],
                        take_profit=signal.get("take_profit"),
                        strategy_name=signal["strategy"]
                    )
                    if ticket:
                        notifier.notify_trade_opened(
                            symbol=actual_sym,
                            direction=signal["direction"],
                            lots=lots,
                            entry=signal["entry"],
                            sl=signal["stop_loss"],
                            tp=signal.get("take_profit"),
                            strategy=signal["strategy"],
                            score=score
                        )
                else:
                    print(f"[RISK REJECTED] {actual_sym} {signal['strategy']} {signal['direction']} - Reason: {reason}")

    print(f"Cycle finished: {scanned_count} active symbols analyzed on {timeframe} candles.")


def main():
    import os
    pid_file = ROOT_DIR / "data" / "bot.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    config_path = ROOT_DIR / "config" / "settings.yaml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)
    timeframe = config.get("data", {}).get("timeframe", "H1").upper()

    print("=" * 65)
    print("    SWING MULTI-AGENT TRADING BOT - LIVE ENGINE ACTIVE")
    print("=" * 65)
    print("Connected to live MetaTrader 5 Terminal.")
    print(f"Scanning frequency: {timeframe} candles (Intraday Pullback Triggers)")
    print("Crypto Weekend Trading: ENABLED (24/7/365)")
    print("Scanning cycle running every 5 minutes...")
    print("Press Ctrl+C in this window at any time to halt the bot.")

    try:
        while True:
            try:
                run_cycle()
            except Exception as e:
                print(f"[ERROR IN CYCLE] {e}")

            time.sleep(300)
    finally:
        if pid_file.exists():
            pid_file.unlink()


if __name__ == "__main__":
    main()
