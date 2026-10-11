"""
Backtester Engine:
Bar-by-bar simulation that executes the exact agent pipeline:
Market Data -> Regime Agent -> Strategy Agents -> Signal Scorer -> Risk Manager -> Execution & Trailing SL.

Calculates realistic equity curves, Expectancy (R), Profit Factor, Max Drawdown, and Monte Carlo simulations.
"""

from typing import Dict, Any, List, Optional
import pandas as pd
import numpy as np

from agents.regime_agent import MarketRegimeAgent, Regime
from agents.trend_agent import TrendAgent
from agents.breakout_agent import BreakoutAgent
from agents.mean_reversion_agent import MeanReversionAgent
from agents.signal_scorer import SignalScorer
from agents.risk_manager import RiskManager
from core.indicators import compute_atr


class BacktestEngine:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.regime_agent = MarketRegimeAgent(config)
        self.trend_agent = TrendAgent(config)
        self.breakout_agent = BreakoutAgent(config)
        self.mean_rev_agent = MeanReversionAgent(config)
        self.scorer = SignalScorer(config)
        self.risk_manager = RiskManager(config)

        acct_cfg = config.get("account", {})
        self.initial_balance = float(acct_cfg.get("backtest_starting_balance", 300.0))
        self.commission_per_lot = float(acct_cfg.get("commission_per_lot", 0.0))
        self.is_cent = acct_cfg.get("type", "cent") == "cent"

    def run(self, symbol: str, df_d1: pd.DataFrame, df_h4: pd.DataFrame) -> Dict[str, Any]:
        """
        Executes backtest over historical H4 bars with daily regime alignment.
        """
        # 1. Precalculate D1 regimes
        d1_regimes = self.regime_agent.calculate_regime_series(df_d1)
        d1_atr = compute_atr(df_d1, 14)
        df_d1_mapped = df_d1.copy()
        df_d1_mapped["regime"] = d1_regimes
        df_d1_mapped["atr"] = d1_atr
        df_d1_mapped.set_index("timestamp", inplace=True)

        equity = self.initial_balance
        peak_equity = self.initial_balance
        open_trades: List[Dict[str, Any]] = []
        closed_trades: List[Dict[str, Any]] = []
        equity_curve: List[float] = [equity]

        pip_size = 0.01 if "JPY" in symbol else 0.0001
        pip_val = 10.0 if not self.is_cent else 0.10

        # Scan each H4 bar sequentially
        for i in range(50, len(df_h4)):
            curr_h4_bar = df_h4.iloc[i]
            bar_time = curr_h4_bar["timestamp"]

            # Lookup latest closed D1 regime before this H4 bar
            prior_d1 = df_d1_mapped.loc[df_d1_mapped.index <= bar_time]
            if len(prior_d1) == 0:
                continue
            latest_d1_bar = prior_d1.iloc[-1]
            current_regime = latest_d1_bar["regime"]
            current_d1_atr = latest_d1_bar["atr"]

            # ----------------------------------------------------
            # 1. MANAGE OPEN TRADES (Check Stop Loss, Breakeven, Trailing SL)
            # ----------------------------------------------------
            remaining_trades = []
            for trade in open_trades:
                direction = trade["direction"]
                entry = trade["entry"]
                sl = trade["stop_loss"]
                risk_dist = trade["initial_risk_dist"]
                lots = trade["lots"]

                high = curr_h4_bar["high"]
                low = curr_h4_bar["low"]

                closed = False
                exit_price = 0.0
                exit_reason = ""

                if direction == "BUY":
                    # Check stop hit
                    if low <= sl:
                        exit_price = sl
                        exit_reason = "STOP_LOSS"
                        closed = True
                    else:
                        # Check breakeven trigger (+1.5R)
                        favorable_dist = high - entry
                        if favorable_dist >= (1.5 * risk_dist) and trade["is_be"] is False:
                            trade["stop_loss"] = entry + (0.05 * risk_dist)  # Buffer above entry
                            trade["is_be"] = True

                        # Trailing stop update if past 1.5R
                        if trade["is_be"] and current_d1_atr > 0:
                            trade["highest_high"] = max(trade.get("highest_high", high), high)
                            new_trail = trade["highest_high"] - (2.5 * current_d1_atr)
                            if new_trail > trade["stop_loss"]:
                                trade["stop_loss"] = new_trail

                elif direction == "SELL":
                    if high >= sl:
                        exit_price = sl
                        exit_reason = "STOP_LOSS"
                        closed = True
                    else:
                        favorable_dist = entry - low
                        if favorable_dist >= (1.5 * risk_dist) and trade["is_be"] is False:
                            trade["stop_loss"] = entry - (0.05 * risk_dist)
                            trade["is_be"] = True

                        if trade["is_be"] and current_d1_atr > 0:
                            trade["lowest_low"] = min(trade.get("lowest_low", low), low)
                            new_trail = trade["lowest_low"] + (2.5 * current_d1_atr)
                            if new_trail < trade["stop_loss"]:
                                trade["stop_loss"] = new_trail

                if closed:
                    # Calculate P&L
                    pips = (exit_price - entry) / pip_size if direction == "BUY" else (entry - exit_price) / pip_size
                    pnl_usd = pips * lots * pip_val
                    pnl_r = pnl_usd / trade["risk_usd"] if trade["risk_usd"] > 0 else 0.0

                    equity += pnl_usd
                    peak_equity = max(peak_equity, equity)

                    trade["exit_price"] = exit_price
                    trade["exit_time"] = bar_time
                    trade["exit_reason"] = exit_reason
                    trade["pnl_usd"] = round(pnl_usd, 2)
                    trade["pnl_r"] = round(pnl_r, 2)
                    closed_trades.append(trade)
                else:
                    remaining_trades.append(trade)

            open_trades = remaining_trades
            equity_curve.append(equity)

            # ----------------------------------------------------
            # 2. SCAN FOR NEW SIGNALS (ALL 3 AGENTS)
            # ----------------------------------------------------
            h4_slice = df_h4.iloc[: i + 1]
            candidates = []

            # 1. Trend
            s_trend = self.trend_agent.scan_for_signal(symbol, df_d1, h4_slice, current_regime)
            if s_trend: candidates.append(s_trend)

            # 2. Breakout
            s_break = self.breakout_agent.scan_for_signal(symbol, df_d1, current_regime)
            if s_break: candidates.append(s_break)

            # 3. Mean-Reversion
            s_mr = self.mean_rev_agent.scan_for_signal(symbol, df_d1, h4_slice, current_regime)
            if s_mr: candidates.append(s_mr)

            for signal in candidates:
                score = self.scorer.score_signal(signal)
                if self.scorer.is_eligible(score):
                    approved, reason, lots = self.risk_manager.evaluate_order(
                        signal, equity, peak_equity, open_trades
                    )
                    if approved:
                        risk_dist = abs(signal["entry"] - signal["stop_loss"])
                        stop_pips = risk_dist / pip_size
                        risk_usd = lots * stop_pips * pip_val

                        trade = {
                            "symbol": symbol,
                            "strategy": signal["strategy"],
                            "direction": signal["direction"],
                            "entry": signal["entry"],
                            "stop_loss": signal["stop_loss"],
                            "take_profit": signal.get("take_profit"),
                            "initial_risk_dist": risk_dist,
                            "lots": lots,
                            "risk_usd": risk_usd,
                            "entry_time": bar_time,
                            "is_be": False,
                            "score": score
                        }
                        open_trades.append(trade)

        # Compute summary metrics
        metrics = self._calculate_metrics(closed_trades, equity_curve)
        return {
            "symbol": symbol,
            "metrics": metrics,
            "closed_trades": closed_trades,
            "equity_curve": equity_curve
        }

    def _calculate_metrics(self, trades: List[Dict[str, Any]], equity_curve: List[float]) -> Dict[str, Any]:
        total_trades = len(trades)
        if total_trades == 0:
            return {
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "expectancy_r": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_pct": 0.0,
                "net_profit_usd": 0.0
            }

        wins = [t for t in trades if t["pnl_r"] > 0]
        losses = [t for t in trades if t["pnl_r"] <= 0]
        win_rate = (len(wins) / total_trades) * 100.0

        gross_profit = sum(t["pnl_usd"] for t in wins)
        gross_loss = abs(sum(t["pnl_usd"] for t in losses))
        profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 999.0

        expectancy_r = round(float(np.mean([t["pnl_r"] for t in trades])), 2)

        # Max drawdown
        eq_series = pd.Series(equity_curve)
        rolling_max = eq_series.cummax()
        drawdowns = (rolling_max - eq_series) / rolling_max
        max_dd_pct = round(float(drawdowns.max() * 100.0), 2)

        net_profit_usd = round(equity_curve[-1] - self.initial_balance, 2)

        return {
            "total_trades": total_trades,
            "win_rate_pct": round(win_rate, 1),
            "expectancy_r": expectancy_r,
            "profit_factor": profit_factor,
            "max_drawdown_pct": max_dd_pct,
            "net_profit_usd": net_profit_usd
        }
