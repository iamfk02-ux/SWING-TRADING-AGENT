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
    print(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] --- CYCLE START ---")
    print(f"Account: #{acct.login} | Balance: ${balance:.2f} | Equity: ${equity:.2f}")

    data_agent = DataAgent(config)
    regime_agent = MarketRegimeAgent(config)
    trend_agent = TrendAgent(config)
    breakout_agent = BreakoutAgent(config)
    mean_rev_agent = MeanReversionAgent(config)
    scorer = SignalScorer(config)
    risk_manager = RiskManager(config)

    symbols = config.get("symbols", {}).get("forex", [])
    open_positions = exec_agent.get_open_positions()
    print(f"Active MT5 Positions: {len(open_positions)}")

    notifier = TelegramNotifier(config)

    # 1. Trailing stops and trade management for open positions
    for pos in open_positions:
        sym = pos["symbol"]
        df_d1, df_h4 = data_agent.get_bars(sym)
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
    for sym in symbols:
        df_d1, df_h4 = data_agent.get_bars(sym)
        if len(df_d1) < 50 or len(df_h4) < 50:
            continue

        d1_regimes = regime_agent.calculate_regime_series(df_d1)
        current_regime = d1_regimes.iloc[-1]

        # Collect candidate signals from each agent
        candidate_signals = []
        
        # 1. Trend Agent
        s_trend = trend_agent.scan_for_signal(sym, df_d1, df_h4, current_regime)
        if s_trend: candidate_signals.append(s_trend)

        # 2. Breakout Agent
        s_break = breakout_agent.scan_for_signal(sym, df_d1, current_regime)
        if s_break: candidate_signals.append(s_break)

        # 3. Mean-Reversion Agent
        s_mr = mean_rev_agent.scan_for_signal(sym, df_d1, df_h4, current_regime)
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
                    print(f"[SIGNAL ACCEPTED] {sym} {signal['strategy']} {signal['direction']} - Score: {score:.2f} | Lots: {lots}")
                    ticket = exec_agent.place_order(
                        symbol=sym,
                        direction=signal["direction"],
                        lots=lots,
                        stop_loss=signal["stop_loss"],
                        take_profit=signal.get("take_profit"),
                        strategy_name=signal["strategy"]
                    )
                    if ticket:
                        notifier.notify_trade_opened(
                            symbol=sym,
                            direction=signal["direction"],
                            lots=lots,
                            entry=signal["entry"],
                            sl=signal["stop_loss"],
                            tp=signal.get("take_profit"),
                            strategy=signal["strategy"],
                            score=score
                        )
                else:
                    print(f"[RISK REJECTED] {sym} {signal['strategy']} {signal['direction']} - Reason: {reason}")


def main():
    import os
    pid_file = ROOT_DIR / "data" / "bot.pid"
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(str(os.getpid()))

    print("=" * 65)
    print("    SWING MULTI-AGENT TRADING BOT - LIVE ENGINE ACTIVE")
    print("=" * 65)
    print("Connected to live MetaTrader 5 Terminal.")
    print("Executing cycle now, then monitoring every H4 candle close...")
    print("Press Ctrl+C in this window at any time to halt the bot.")

    try:
        while True:
            try:
                run_cycle()
            except Exception as e:
                print(f"[ERROR IN CYCLE] {e}")

            time.sleep(900)
    finally:
        if pid_file.exists():
            pid_file.unlink()


if __name__ == "__main__":
    main()
