"""
Strategy Health & Adaptive Performance Monitor (Quantitative Rule 10):
Continuously audits the rolling performance of each strategy (TREND, BREAKOUT, MEAN_REVERSION).
Detects performance decay (consecutive loss streaks or degraded win rates).
Dynamically throttles risk allocation for struggling strategies while keeping winning strategies at full power.
"""

import sqlite3
from pathlib import Path
from typing import Dict, Any, Tuple, Optional

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trading_journal.db"


class StrategyHealthMonitor:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        health_cfg = self.config.get("risk", {}).get("strategy_health", {})
        self.enabled = health_cfg.get("enabled", True)
        self.min_lookback_trades = health_cfg.get("min_lookback_trades", 5)
        self.max_consecutive_losses = health_cfg.get("max_consecutive_losses", 3)
        self.min_win_rate_pct = health_cfg.get("min_win_rate_pct", 35.0)

    def evaluate_strategy_health(self, strategy_name: str) -> Dict[str, Any]:
        """
        Queries closed trade records in SQLite for this strategy.
        Returns:
            {
                "status": "HEALTHY" | "THROTTLED" | "INSUFFICIENT_DATA",
                "risk_multiplier": float (1.0 or 0.5),
                "consecutive_losses": int,
                "win_rate_pct": float,
                "reason": str
            }
        """
        default_res = {
            "status": "HEALTHY",
            "risk_multiplier": 1.0,
            "consecutive_losses": 0,
            "win_rate_pct": 100.0,
            "reason": "Normal operational parameters"
        }

        if not self.enabled or not DB_PATH.exists():
            return default_res

        try:
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                # Fetch recent closed trades for this strategy (most recent first)
                rows = cursor.execute("""
                    SELECT pnl_usd 
                    FROM trades 
                    WHERE strategy = ? AND exit_time IS NOT NULL 
                    ORDER BY id DESC 
                    LIMIT 20
                """, (strategy_name.upper(),)).fetchall()

                if len(rows) < self.min_lookback_trades:
                    return {
                        "status": "HEALTHY",
                        "risk_multiplier": 1.0,
                        "consecutive_losses": 0,
                        "win_rate_pct": 0.0,
                        "reason": f"Insufficient sample size ({len(rows)}/{self.min_lookback_trades} trades). Full risk authorized."
                    }

                # Count consecutive losses from newest trades backwards
                consecutive_losses = 0
                for r in rows:
                    pnl = r[0] or 0.0
                    if pnl < 0:
                        consecutive_losses += 1
                    else:
                        break

                # Calculate win rate across lookback window
                wins = sum(1 for r in rows if (r[0] or 0.0) > 0)
                win_rate = (wins / len(rows)) * 100.0

                # Degradation Condition 1: Consecutive loss streak
                if consecutive_losses >= self.max_consecutive_losses:
                    return {
                        "status": "THROTTLED",
                        "risk_multiplier": 0.5,
                        "consecutive_losses": consecutive_losses,
                        "win_rate_pct": win_rate,
                        "reason": f"STRATEGY DEGRADATION: {consecutive_losses} consecutive losses. Risk throttled to 50%."
                    }

                # Degradation Condition 2: Depressed rolling win rate
                if win_rate < self.min_win_rate_pct:
                    return {
                        "status": "THROTTLED",
                        "risk_multiplier": 0.5,
                        "consecutive_losses": consecutive_losses,
                        "win_rate_pct": win_rate,
                        "reason": f"STRATEGY DEGRADATION: Rolling win rate {win_rate:.1f}% < {self.min_win_rate_pct}%. Risk throttled to 50%."
                    }

                return {
                    "status": "HEALTHY",
                    "risk_multiplier": 1.0,
                    "consecutive_losses": consecutive_losses,
                    "win_rate_pct": win_rate,
                    "reason": f"Strategy healthy (Win Rate: {win_rate:.1f}%, Streak: {consecutive_losses}L)"
                }

        except Exception as e:
            return default_res
