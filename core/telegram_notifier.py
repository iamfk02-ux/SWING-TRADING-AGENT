"""
Telegram Notifier:
Sends alerts directly to your phone via Telegram Bot:
- Trade entries (symbol, direction, lots, SL, TP, reason)
- Stop loss updates (moves to break-even, trailing stops)
- Trade exits (PnL in $ and R)
- Risk alarms & Kill Switch warnings
"""

import requests
from typing import Dict, Any, Optional
import yaml
from pathlib import Path

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"


class TelegramNotifier:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        if config is None and CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r") as f:
                config = yaml.safe_load(f)
        self.config = config or {}
        tg_cfg = self.config.get("telegram", {})
        self.enabled = tg_cfg.get("enabled", False)
        self.bot_token = tg_cfg.get("bot_token", "")
        self.chat_id = tg_cfg.get("chat_id", "")

    def send_message(self, text: str) -> bool:
        if not self.enabled or not self.bot_token or not self.chat_id:
            return False
        try:
            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown"
            }
            res = requests.post(url, json=payload, timeout=20)
            return res.status_code == 200
        except Exception as e:
            print(f"[TELEGRAM ERROR] Could not dispatch alert: {e}")
            return False

    def notify_trade_opened(self, symbol: str, direction: str, lots: float, entry: float, sl: float, tp: Optional[float], strategy: str, score: float):
        msg = (
            f"🚀 *TRADE OPENED*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📈 *Symbol:* `{symbol}`\n"
            f"🎯 *Strategy:* `{strategy}`\n"
            f"🔄 *Direction:* *{direction}*\n"
            f"📦 *Volume:* `{lots:.2f} lots`\n"
            f"💰 *Entry:* `{entry:.5f}`\n"
            f"🛑 *Stop Loss:* `{sl:.5f}`\n"
            f"🏆 *Target:* `{f'{tp:.5f}' if tp else 'Trailing Stop'}`\n"
            f"⭐ *Score:* `{score:.2f}`\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        self.send_message(msg)

    def notify_breakeven(self, symbol: str, new_sl: float):
        msg = (
            f"🛡️ *RISK DEFENSE ACTIVATED*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Symbol: `{symbol}`\n"
            f"Action: Stop-Loss moved to *Break-Even* at `{new_sl:.5f}`\n"
            f"Status: Trade is now *risk-free*! ✅\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        self.send_message(msg)

    def notify_partial_tp(self, symbol: str, closed_lots: float, remaining_lots: float, price: float, profit_usd: float, new_sl: float):
        msg = (
            f"💰 *PARTIAL TAKE PROFIT HIT!*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📈 *Symbol:* `{symbol}`\n"
            f"💵 *Cash Banked:* *+${profit_usd:,.2f}* (Closed {closed_lots:.2f} lots @ `{price:.5f}`)\n"
            f"🛡️ *Stop-Loss:* Moved to *Break-Even* at `{new_sl:.5f}`\n"
            f"🏃 *Runner Active:* `{remaining_lots:.2f} lots` running *100% risk-free* with trailing stop!\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        self.send_message(msg)

    def notify_kill_switch(self, current_dd: float, threshold: float):
        msg = (
            f"🚨 *KILL SWITCH TRIGGERED* 🚨\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Account Drawdown reached `{current_dd:.1f}%` (Limit: `{threshold:.1f}%`).\n"
            f"All automated trading is *STOPPED* immediately.\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        self.send_message(msg)

    def notify_trade_closed(
        self,
        symbol: str,
        direction: str,
        lots: float,
        entry_price: float,
        exit_price: float,
        profit_usd: float,
        exit_reason: str,
        analysis_explanation: str
    ):
        pnl_emoji = "🟢" if profit_usd >= 0 else "🔴"
        pnl_label = "PROFIT" if profit_usd >= 0 else "LOSS"
        msg = (
            f"{pnl_emoji} *TRADE CLOSED REPORT: {symbol}*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📊 *Result:* {pnl_emoji} *{pnl_label} of ${profit_usd:+,.2f}*\n"
            f"📈 *Direction:* {direction} ({lots:.2f} lots)\n"
            f"💰 *Entry Price:* `{entry_price:.5f}`\n"
            f"🏁 *Exit Price:* `{exit_price:.5f}`\n"
            f"🛑 *Exit Trigger:* `{exit_reason}`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🧠 *Post-Trade Analysis & Why:*\n"
            f"{analysis_explanation}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        self.send_message(msg)

    def notify_news_shield_warning(
        self,
        event_title: str,
        currency: str,
        minutes_until: int,
        affected_positions: list,
        shield_action: str
    ):
        pos_text = ", ".join(affected_positions) if affected_positions else "None (No active exposure)"
        msg = (
            f"⚠️ *HIGH-IMPACT NEWS SHIELD ALERT*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📅 *Event:* {event_title}\n"
            f"🌍 *Currency:* `{currency}`\n"
            f"⏳ *Time Until Release:* ~{minutes_until} Minutes\n"
            f"⚡ *Expected Volatility:* High / Spread Spikes\n"
            f"🎯 *Active Positions Affected:* `{pos_text}`\n"
            f"🛡️ *Action Taken:* {shield_action}\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        self.send_message(msg)

    def notify_news_shield_protected(
        self,
        symbol: str,
        event_title: str,
        new_sl: float
    ):
        msg = (
            f"🔒 *PRE-NEWS POSITION SECURED*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📈 *Symbol:* `{symbol}`\n"
            f"📅 *Approaching Event:* {event_title}\n"
            f"🛡️ *Stop-Loss Locked:* Moved to *Break-Even* at `{new_sl:.5f}`\n"
            f"✅ *Status:* 100% Risk-Free against upcoming news whipsaw!\n"
            f"━━━━━━━━━━━━━━━━━━"
        )
        self.send_message(msg)

