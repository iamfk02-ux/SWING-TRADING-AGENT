"""
2-Way Interactive Telegram Mobile Controller:
Allows the user to monitor and control live MT5 trades and bot state directly from Telegram:
- Slash commands (/status, /positions, /scanner, /breakeven, /pause, /resume, /closeall, /menu)
- Persistent Mobile Bottom Reply Keyboard for 1-tap phone control
- Per-trade Inline Action Buttons (Move SL to Break-Even, Lock +0.5R, Close 50%, Close 100%, Cancel Pending)
- Strict Chat-ID authentication (only responds to the authorized account owner)
"""

import json
import socket
import threading
import time
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, Optional, List, Tuple
import requests
import urllib3.util.connection as urllib3_cn

# Force IPv4 for Telegram Bot API to prevent 21-second Windows IPv6 SYN timeouts
urllib3_cn.allowed_gai_family = lambda: socket.AF_INET

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None

from core.config_loader import load_config
from core.bot_logger import append_live_log, get_heartbeat, get_scanner_matrix
from agents.execution_agent import ExecutionAgent

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CONTROL_STATE_FILE = DATA_DIR / "bot_control_state.json"
PEAK_EQUITY_FILE = DATA_DIR / "peak_equity_state.json"
DB_FILE = DATA_DIR / "trading_journal.db"


def get_bot_control_state() -> Dict[str, Any]:
    """Reads persistent bot control state (e.g. pause new entries)."""
    if CONTROL_STATE_FILE.exists():
        try:
            return json.loads(CONTROL_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"entries_paused": False, "updated_at": None}


def set_entries_paused(paused: bool) -> Dict[str, Any]:
    """Persists whether new trade entries are paused via Telegram."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    state = {
        "entries_paused": bool(paused),
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    try:
        CONTROL_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except Exception:
        pass
    return state


def is_entries_paused() -> bool:
    """Returns True if the user paused new trade entries from Telegram."""
    return bool(get_bot_control_state().get("entries_paused", False))


class TelegramController:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or load_config()
        tg_cfg = self.config.get("telegram", {})
        self.enabled = bool(tg_cfg.get("enabled", False))
        self.bot_token = str(tg_cfg.get("bot_token", "")).strip()
        self.chat_id = str(tg_cfg.get("chat_id", "")).strip()
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"
        self.last_update_id = 0
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def get_main_reply_keyboard(self) -> Dict[str, Any]:
        """Returns the persistent bottom phone keypad for 1-tap mobile control."""
        paused = is_entries_paused()
        pause_btn = "▶️ Resume Entries" if paused else "⏸️ Pause Entries"
        return {
            "keyboard": [
                [{"text": "📊 Status"}, {"text": "💼 Positions"}, {"text": "🔍 Scanner"}],
                [{"text": "🛡️ All Break-Even"}, {"text": pause_btn}, {"text": "🚨 Panic Close"}]
            ],
            "resize_keyboard": True,
            "is_persistent": True
        }

    def send_msg(
        self,
        text: str,
        reply_markup: Optional[Dict[str, Any]] = None,
        use_main_keyboard: bool = False
    ) -> bool:
        if not self.enabled or not self.bot_token or not self.chat_id:
            return False
        try:
            payload: Dict[str, Any] = {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "Markdown"
            }
            if reply_markup is not None:
                payload["reply_markup"] = reply_markup
            elif use_main_keyboard:
                payload["reply_markup"] = self.get_main_keyboard_or_default()
            res = requests.post(f"{self.base_url}/sendMessage", json=payload, timeout=15)
            return res.status_code == 200
        except Exception as e:
            print(f"[TG CONTROLLER] send_msg error: {e}")
            return False

    def get_main_keyboard_or_default(self) -> Dict[str, Any]:
        return self.get_main_reply_keyboard()

    def answer_callback(self, callback_id: str, text: str = "", show_alert: bool = False):
        try:
            requests.post(
                f"{self.base_url}/answerCallbackQuery",
                json={
                    "callback_query_id": callback_id,
                    "text": text[:190] if text else "Done",
                    "show_alert": show_alert
                },
                timeout=10
            )
        except Exception:
            pass

    def register_commands(self):
        """Registers the '/' command menu in the user's Telegram client."""
        if not self.enabled or not self.bot_token:
            return
        commands = [
            {"command": "status", "description": "📊 Live balance, equity, PnL & bot state"},
            {"command": "positions", "description": "💼 Manage open trades & pending orders"},
            {"command": "scanner", "description": "🔍 Live 6-pair regime & pullback radar"},
            {"command": "breakeven", "description": "🛡️ Move all profitable trades to Break-Even"},
            {"command": "pause", "description": "⏸️ Pause opening new trades"},
            {"command": "resume", "description": "▶️ Resume opening new trades"},
            {"command": "closeall", "description": "🚨 Emergency close all trades & orders"},
            {"command": "menu", "description": "📱 Show mobile quick-control keypad"}
        ]
        try:
            requests.post(
                f"{self.base_url}/setMyCommands",
                json={"commands": commands},
                timeout=10
            )
        except Exception:
            pass

    def _prime_offset(self):
        """Skips old queued Telegram messages on startup so old commands aren't re-executed."""
        try:
            res = requests.get(f"{self.base_url}/getUpdates", params={"timeout": 1, "offset": -1}, timeout=10)
            if res.status_code == 200:
                data = res.json()
                results = data.get("result", [])
                if results:
                    self.last_update_id = results[-1]["update_id"] + 1
        except Exception:
            pass

    def start_background_listener(self, announce_startup: bool = False):
        """Starts the 2-way Telegram polling listener in a daemon thread."""
        if not self.enabled or not self.bot_token or not self.chat_id:
            return
        if self._running:
            return
        self._running = True
        self.register_commands()
        self._prime_offset()
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="TelegramControllerThread")
        self._thread.start()
        append_live_log("📱 2-Way Telegram Mobile Controller ONLINE (Listening silently for phone commands & button taps).")

    def stop(self):
        self._running = False

    def _poll_loop(self):
        while self._running:
            try:
                res = requests.get(
                    f"{self.base_url}/getUpdates",
                    params={"offset": self.last_update_id, "timeout": 15},
                    timeout=20
                )
                if res.status_code == 200:
                    data = res.json()
                    for upd in data.get("result", []):
                        self.last_update_id = upd["update_id"] + 1
                        self._handle_update(upd)
            except Exception:
                time.sleep(3)
            time.sleep(0.5)

    def _handle_update(self, upd: Dict[str, Any]):
        # 1. Handle Inline Button Callback Queries
        if "callback_query" in upd:
            cb = upd["callback_query"]
            cb_id = cb.get("id", "")
            msg = cb.get("message", {})
            sender_chat_id = str(msg.get("chat", {}).get("id", ""))
            if sender_chat_id != self.chat_id:
                self.answer_callback(cb_id, "Unauthorized", show_alert=True)
                return
            data = str(cb.get("data", ""))
            self._handle_callback_action(cb_id, data)
            return

        # 2. Handle Text / Slash Commands / Reply Keyboard Buttons
        msg = upd.get("message", {})
        sender_chat_id = str(msg.get("chat", {}).get("id", ""))
        if sender_chat_id != self.chat_id:
            return

        text = str(msg.get("text", "")).strip()
        if not text:
            return

        low = text.lower()
        if low in ("/start", "/menu", "/help", "📱 menu"):
            self._cmd_menu()
        elif low.startswith("/status") or "status" in low:
            self._cmd_status()
        elif low.startswith("/positions") or low.startswith("/trades") or "positions" in low:
            self._cmd_positions()
        elif low.startswith("/scanner") or "scanner" in low:
            self._cmd_scanner()
        elif low.startswith("/breakeven") or "break-even" in low:
            self._cmd_breakeven_all()
        elif low.startswith("/pause") or "pause entries" in low:
            self._cmd_pause(True)
        elif low.startswith("/resume") or "resume entries" in low:
            self._cmd_pause(False)
        elif low.startswith("/closeall") or low.startswith("/panic") or "panic close" in low:
            self._cmd_panic_prompt()
        else:
            self.send_msg(
                "🤖 Unknown command. Tap a button below or use `/status`, `/positions`, `/scanner`, `/breakeven`, `/pause`, `/resume`, or `/closeall`.",
                reply_markup=self.get_main_reply_keyboard()
            )

    def _handle_callback_action(self, cb_id: str, data: str):
        if data == "cmd:status":
            self.answer_callback(cb_id, "Refreshing status...")
            self._cmd_status()
        elif data == "cmd:positions":
            self.answer_callback(cb_id, "Loading active positions...")
            self._cmd_positions()
        elif data == "cmd:scanner":
            self.answer_callback(cb_id, "Loading live scanner...")
            self._cmd_scanner()
        elif data == "cmd:be_all":
            self.answer_callback(cb_id, "Moving winning trades to Break-Even...")
            self._cmd_breakeven_all()
        elif data == "cmd:panic_confirm":
            self.answer_callback(cb_id, "Executing emergency close all!")
            self._execute_close_all()
        elif data == "cmd:panic_cancel":
            self.answer_callback(cb_id, "Cancelled.")
            self.send_msg("✅ Emergency close cancelled. Your trades remain active.", reply_markup=self.get_main_reply_keyboard())
        elif data.startswith("be:"):
            ticket = int(data.split(":")[1])
            self._action_breakeven_single(cb_id, ticket)
        elif data.startswith("lock05:"):
            ticket = int(data.split(":")[1])
            self._action_lock_half_r(cb_id, ticket)
        elif data.startswith("half:"):
            ticket = int(data.split(":")[1])
            self._action_close_half(cb_id, ticket)
        elif data.startswith("close:"):
            ticket = int(data.split(":")[1])
            self._action_close_full(cb_id, ticket)
        elif data.startswith("cancel:"):
            ticket = int(data.split(":")[1])
            self._action_cancel_pending(cb_id, ticket)
        else:
            self.answer_callback(cb_id, "Action received")

    def _cmd_menu(self):
        paused = is_entries_paused()
        state_str = "⏸️ *PAUSED (Managing Open Trades Only)*" if paused else "🟢 *ACTIVE (Scanning & Trading)*"
        self.send_msg(
            f"📱 *TELEGRAM MOBILE CONTROL CENTER*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Engine State: {state_str}\n\n"
            f"*Quick Commands:*\n"
            f"• `/status` — Account Balance, Equity & Drawdown\n"
            f"• `/positions` — Inspect & Control Open Trades\n"
            f"• `/scanner` — Live 6-Pair Regime & Pullback Matrix\n"
            f"• `/breakeven` — Lock all winning trades to Break-Even\n"
            f"• `/pause` / `/resume` — Freeze/Resume new entries\n"
            f"• `/closeall` — Emergency close all trades & orders",
            reply_markup=self.get_main_reply_keyboard()
        )

    def _cmd_status(self):
        exec_agent = ExecutionAgent(self.config)
        if not exec_agent.connect():
            self.send_msg("⚠️ *MT5 Terminal Disconnected* — Check that MetaTrader 5 is open on your PC.")
            return

        acct = mt5.account_info()
        open_pos = exec_agent.get_open_positions()
        pend_ord = exec_agent.get_pending_orders()

        balance = float(acct.balance) if acct else 0.0
        equity = float(acct.equity) if acct else 0.0
        floating_pnl = round(equity - balance, 2)
        login_id = acct.login if acct else "N/A"
        server = acct.server if acct else "Exness"

        # Peak equity & drawdown
        peak = max(balance, equity)
        if PEAK_EQUITY_FILE.exists():
            try:
                peak = max(peak, float(json.loads(PEAK_EQUITY_FILE.read_text()).get("peak_equity", peak)))
            except Exception:
                pass
        dd_pct = max(0.0, ((peak - equity) / peak) * 100.0) if peak > 0 else 0.0

        # Count free-roll risk-free positions
        free_roll_count = 0
        for p in open_pos:
            d = p["direction"]
            ep = float(p["entry_price"])
            sl = float(p["sl"] or 0.0)
            if sl > 0 and ((d == "BUY" and sl >= ep) or (d == "SELL" and sl <= ep)):
                free_roll_count += 1

        # Today's realized PnL
        today_realized = 0.0
        try:
            if DB_FILE.exists():
                today_prefix = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                with sqlite3.connect(DB_FILE) as conn:
                    row = conn.execute(
                        "SELECT COALESCE(SUM(pnl_usd), 0.0) FROM trades WHERE exit_time LIKE ?",
                        (f"{today_prefix}%",)
                    ).fetchone()
                    if row:
                        today_realized = float(row[0] or 0.0)
        except Exception:
            pass

        paused = is_entries_paused()
        state_badge = "⏸️ PAUSED (Defense Only)" if paused else "🟢 ACTIVE (Live Scanning)"
        hb = get_heartbeat() or {}
        next_scan_sec = hb.get("seconds_remaining", 0)
        mins, secs = divmod(int(next_scan_sec), 60)

        float_emoji = "🟢" if floating_pnl >= 0 else "🔴"
        msg = (
            f"📊 *LIVE ACCOUNT & BOT STATUS*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🏦 *Account:* `#{login_id}` ({server})\n"
            f"🤖 *Engine:* *{state_badge}*\n"
            f"⏱️ *Next Scan:* `{mins}m {secs:02d}s`\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 *Balance:* `${balance:,.2f}`\n"
            f"📈 *Equity:* `${equity:,.2f}`\n"
            f"{float_emoji} *Floating PnL:* `${floating_pnl:+,.2f}`\n"
            f"💵 *Today Banked:* `${today_realized:+,.2f}`\n"
            f"🏔️ *High-Water Mark:* `${peak:,.2f}` (DD: `{dd_pct:.2f}%`)\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💼 *Open Positions:* `{len(open_pos)}` (`{free_roll_count}` Risk-Free 🛡️)\n"
            f"⏳ *Pending Limits:* `{len(pend_ord)}`"
        )
        inline = {
            "inline_keyboard": [
                [
                    {"text": "🔄 Refresh", "callback_data": "cmd:status"},
                    {"text": f"💼 Positions ({len(open_pos)})", "callback_data": "cmd:positions"},
                    {"text": "🔍 Scanner", "callback_data": "cmd:scanner"}
                ]
            ]
        }
        self.send_msg(msg, reply_markup=inline)

    def _cmd_positions(self):
        exec_agent = ExecutionAgent(self.config)
        if not exec_agent.connect():
            self.send_msg("⚠️ *MT5 Terminal Disconnected*")
            return

        open_pos = exec_agent.get_open_positions()
        pend_ord = exec_agent.get_pending_orders()

        if not open_pos and not pend_ord:
            self.send_msg(
                "💼 *NO OPEN POSITIONS OR PENDING ORDERS*\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "Your portfolio currently has 0 active trades. The bot is scanning all 6 pairs every 5 minutes.",
                reply_markup=self.get_main_reply_keyboard()
            )
            return

        # 1. Send an interactive card for each open position
        for p in open_pos:
            ticket = p["ticket"]
            sym = p["symbol"]
            d = p["direction"]
            vol = float(p["volume"])
            entry = float(p["entry_price"])
            curr = float(p.get("current_price", entry))
            sl = float(p["sl"] or 0.0)
            tp = float(p["tp"] or 0.0)
            pnl = round(float(p["profit"]) + float(p.get("swap", 0.0)), 2)

            pip_size = 0.01 if "JPY" in sym else (0.1 if "XAU" in sym else 0.0001)
            pips = ((curr - entry) / pip_size) if d == "BUY" else ((entry - curr) / pip_size)

            # Compute initial risk distance from DB or current SL
            init_sl = sl
            try:
                if DB_FILE.exists():
                    with sqlite3.connect(DB_FILE) as conn:
                        row = conn.execute("SELECT initial_sl FROM trades WHERE ticket = ?", (ticket,)).fetchone()
                        if row and row[0]:
                            init_sl = float(row[0])
            except Exception:
                pass
            risk_dist = abs(entry - init_sl) if init_sl > 0 else (20.0 * pip_size)
            profit_dist = (curr - entry) if d == "BUY" else (entry - curr)
            r_mult = profit_dist / risk_dist if risk_dist > 0 else 0.0

            is_risk_free = sl > 0 and ((d == "BUY" and sl >= entry) or (d == "SELL" and sl <= entry))
            lock_badge = "🛡️ *RISK-FREE (SL Locked)*" if is_risk_free else "⚡ *ACTIVE RISK*"
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"

            text = (
                f"💼 *POSITION #{ticket} — {sym}*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🔄 *Type:* *{d}* (`{vol:.2f} lots`)\n"
                f"📌 *State:* {lock_badge}\n"
                f"💰 *Entry:* `{entry:.5f}`  ➡️  *Now:* `{curr:.5f}`\n"
                f"🛑 *Stop Loss:* `{sl:.5f}`\n"
                f"🏆 *Take Profit:* `{tp:.5f}`\n"
                f"{pnl_emoji} *Live PnL:* *${pnl:+,.2f}* (`{pips:+.1f} pips` | `{r_mult:+.2f}R`)\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"👇 *Tap below to control this trade:*"
            )
            inline = {
                "inline_keyboard": [
                    [
                        {"text": "🛡️ Move SL to BE", "callback_data": f"be:{ticket}"},
                        {"text": "🔒 Lock +0.5R", "callback_data": f"lock05:{ticket}"}
                    ],
                    [
                        {"text": "💰 Close 50%", "callback_data": f"half:{ticket}"},
                        {"text": "❌ Close 100%", "callback_data": f"close:{ticket}"}
                    ]
                ]
            }
            self.send_msg(text, reply_markup=inline)

        # 2. Send cards for any pending limit/stop orders
        for o in pend_ord:
            oticket = o["ticket"]
            osym = o["symbol"]
            odir = o["direction"]
            otype = o["order_type"]
            ovol = float(o["volume"])
            oprice = float(o["price"])
            osl = float(o["sl"] or 0.0)
            otp = float(o["tp"] or 0.0)

            otext = (
                f"⏳ *PENDING {otype} #{oticket} — {osym}*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🔄 *Order:* *{odir} {otype}* (`{ovol:.2f} lots`)\n"
                f"🎯 *Limit Price:* `{oprice:.5f}`\n"
                f"🛑 *Stop Loss:* `{osl:.5f}` | 🏆 *TP:* `{otp:.5f}`"
            )
            oinline = {
                "inline_keyboard": [
                    [{"text": f"🚫 Cancel Pending #{oticket}", "callback_data": f"cancel:{oticket}"}]
                ]
            }
            self.send_msg(otext, reply_markup=oinline)

    def _cmd_scanner(self):
        matrix = get_scanner_matrix()
        if not matrix or not matrix.get("pairs"):
            self.send_msg("🔍 Scanner matrix is warming up. Try again in a few seconds.")
            return

        lines = [
            "🔍 *LIVE 6-PAIR MARKET SCANNER*",
            f"🕒 Updated: `{matrix.get('timestamp', 'Just now')}`",
            "━━━━━━━━━━━━━━━━━━"
        ]
        for row in matrix.get("pairs", []):
            sym = row.get("symbol", "")
            price = row.get("price", 0.0)
            regime = row.get("regime", "UNCLEAR")
            badge = row.get("badge", "WATCHING")
            details = row.get("details", "")
            lines.append(f"• *{sym}* (`{price:.5f}`) — `{regime}`\n  └ *{badge}:* {details}")

        inline = {
            "inline_keyboard": [
                [
                    {"text": "🔄 Refresh Scanner", "callback_data": "cmd:scanner"},
                    {"text": "📊 Account Status", "callback_data": "cmd:status"}
                ]
            ]
        }
        self.send_msg("\n".join(lines), reply_markup=inline)

    def _action_breakeven_single(self, cb_id: str, ticket: int):
        exec_agent = ExecutionAgent(self.config)
        if not exec_agent.connect():
            self.answer_callback(cb_id, "MT5 not connected", show_alert=True)
            return

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            self.answer_callback(cb_id, f"Position #{ticket} is no longer open.", show_alert=True)
            return

        pos = positions[0]
        sym = pos.symbol
        is_buy = pos.type == mt5.ORDER_TYPE_BUY
        entry = float(pos.price_open)
        tick = mt5.symbol_info_tick(sym)
        if not tick:
            self.answer_callback(cb_id, "Could not read live price.", show_alert=True)
            return

        curr = float(tick.bid if is_buy else tick.ask)
        pip_size = 0.01 if "JPY" in sym else (0.1 if "XAU" in sym else 0.0001)
        profit_pips = ((curr - entry) / pip_size) if is_buy else ((entry - curr) / pip_size)

        if profit_pips < 1.0:
            self.answer_callback(
                cb_id,
                f"Cannot move SL to Break-Even yet: {sym} is currently at {profit_pips:+.1f} pips (needs >= +1.0 pip in profit).",
                show_alert=True
            )
            return

        # Lock entry + 0.5 pip spread buffer (or exact entry if < 2 pips)
        buffer_pips = 0.8 if profit_pips >= 2.5 else 0.1
        new_sl = (entry + buffer_pips * pip_size) if is_buy else (entry - buffer_pips * pip_size)

        if exec_agent.modify_stop_loss(ticket, new_sl):
            self.answer_callback(cb_id, f"SL moved to Break-Even ({new_sl:.5f})!")
            append_live_log(f"📱 [TELEGRAM CONTROL] User moved #{ticket} ({sym}) SL to Break-Even @ {new_sl:.5f}.")
            self.send_msg(
                f"🛡️ *BREAK-EVEN LOCKED (#{ticket})*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📈 *Symbol:* `{sym}`\n"
                f"💰 *Entry:* `{entry:.5f}`\n"
                f"🛑 *New Stop-Loss:* `{new_sl:.5f}`\n"
                f"✅ This trade is now *100% Risk-Free*!"
            )
        else:
            self.answer_callback(cb_id, "MT5 rejected SL modification (too close to market price).", show_alert=True)

    def _action_lock_half_r(self, cb_id: str, ticket: int):
        exec_agent = ExecutionAgent(self.config)
        if not exec_agent.connect():
            self.answer_callback(cb_id, "MT5 not connected", show_alert=True)
            return

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            self.answer_callback(cb_id, f"Position #{ticket} not found.", show_alert=True)
            return

        pos = positions[0]
        sym = pos.symbol
        is_buy = pos.type == mt5.ORDER_TYPE_BUY
        entry = float(pos.price_open)
        tick = mt5.symbol_info_tick(sym)
        if not tick:
            return
        curr = float(tick.bid if is_buy else tick.ask)
        pip_size = 0.01 if "JPY" in sym else (0.1 if "XAU" in sym else 0.0001)

        init_sl = float(pos.sl or 0.0)
        try:
            if DB_FILE.exists():
                with sqlite3.connect(DB_FILE) as conn:
                    row = conn.execute("SELECT initial_sl FROM trades WHERE ticket = ?", (ticket,)).fetchone()
                    if row and row[0]:
                        init_sl = float(row[0])
        except Exception:
            pass
        risk_dist = abs(entry - init_sl) if init_sl > 0 else (20.0 * pip_size)
        target_sl = (entry + 0.5 * risk_dist) if is_buy else (entry - 0.5 * risk_dist)

        if (is_buy and curr <= target_sl + 1.5 * pip_size) or (not is_buy and curr >= target_sl - 1.5 * pip_size):
            self.answer_callback(
                cb_id,
                f"Price ({curr:.5f}) hasn't cleared +0.5R ({target_sl:.5f}) yet!",
                show_alert=True
            )
            return

        if exec_agent.modify_stop_loss(ticket, target_sl):
            self.answer_callback(cb_id, f"Locked +0.5R profit at {target_sl:.5f}!")
            append_live_log(f"📱 [TELEGRAM CONTROL] User locked +0.5R on #{ticket} ({sym}) @ {target_sl:.5f}.")
            self.send_msg(
                f"🔒 *+0.5R PROFIT LOCKED (#{ticket})*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📈 *Symbol:* `{sym}`\n"
                f"🛑 *New Stop-Loss:* `{target_sl:.5f}` (+0.5R guaranteed)"
            )
        else:
            self.answer_callback(cb_id, "MT5 rejected SL modification.", show_alert=True)

    def _action_close_half(self, cb_id: str, ticket: int):
        exec_agent = ExecutionAgent(self.config)
        if not exec_agent.connect():
            self.answer_callback(cb_id, "MT5 not connected", show_alert=True)
            return

        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            self.answer_callback(cb_id, f"Position #{ticket} not found.", show_alert=True)
            return

        pos = positions[0]
        sym = pos.symbol
        vol = float(pos.volume)
        if vol <= 0.01:
            self.answer_callback(
                cb_id,
                "Position is already at minimum 0.01 lot (cannot split in half). Use Break-Even or Close 100%.",
                show_alert=True
            )
            return

        half_vol = max(0.01, round(vol / 2.0, 2))
        if exec_agent.close_partial_position(ticket, half_vol):
            self.answer_callback(cb_id, f"Closed {half_vol:.2f} lots on {sym}!")
            append_live_log(f"📱 [TELEGRAM CONTROL] User closed 50% ({half_vol:.2f} lots) on #{ticket} ({sym}).")
            self.send_msg(
                f"💰 *50% PARTIAL CLOSED (#{ticket})*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📈 *Symbol:* `{sym}`\n"
                f"💵 *Closed Volume:* `{half_vol:.2f} lots`\n"
                f"🏃 *Remaining Runner:* `{vol - half_vol:.2f} lots`"
            )
        else:
            self.answer_callback(cb_id, "Partial close failed in MT5.", show_alert=True)

    def _action_close_full(self, cb_id: str, ticket: int):
        exec_agent = ExecutionAgent(self.config)
        ok, pnl, msg = exec_agent.close_position(ticket, reason_comment="TG_PHONE_CLOSE")
        if ok:
            self.answer_callback(cb_id, f"Position #{ticket} closed!")
            append_live_log(f"📱 [TELEGRAM CONTROL] User closed full position #{ticket}: {msg} (PnL: ${pnl:+,.2f})")
            emoji = "🟢" if pnl >= 0 else "🔴"
            self.send_msg(
                f"{emoji} *POSITION CLOSED FROM PHONE (#{ticket})*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"📋 *Details:* {msg}\n"
                f"💵 *Realized PnL:* *${pnl:+,.2f}*"
            )
        else:
            self.answer_callback(cb_id, msg, show_alert=True)

    def _action_cancel_pending(self, cb_id: str, ticket: int):
        exec_agent = ExecutionAgent(self.config)
        if exec_agent.cancel_pending_order(ticket):
            self.answer_callback(cb_id, f"Pending order #{ticket} cancelled!")
            append_live_log(f"📱 [TELEGRAM CONTROL] User cancelled pending order #{ticket} from phone.")
            self.send_msg(f"🚫 *Pending Order `#{ticket}` Cancelled* in MetaTrader 5.")
        else:
            self.answer_callback(cb_id, f"Could not cancel #{ticket} (already filled or removed).", show_alert=True)

    def _cmd_breakeven_all(self):
        exec_agent = ExecutionAgent(self.config)
        if not exec_agent.connect():
            self.send_msg("⚠️ *MT5 Terminal Disconnected*")
            return

        open_pos = exec_agent.get_open_positions()
        if not open_pos:
            self.send_msg("🛡️ No open positions right now.", reply_markup=self.get_main_reply_keyboard())
            return

        locked = []
        skipped = []
        for p in open_pos:
            ticket = p["ticket"]
            sym = p["symbol"]
            is_buy = p["direction"] == "BUY"
            entry = float(p["entry_price"])
            curr = float(p.get("current_price", entry))
            pip_size = 0.01 if "JPY" in sym else (0.1 if "XAU" in sym else 0.0001)
            profit_pips = ((curr - entry) / pip_size) if is_buy else ((entry - curr) / pip_size)

            if profit_pips >= 1.0:
                buf = 0.8 if profit_pips >= 2.5 else 0.1
                new_sl = (entry + buf * pip_size) if is_buy else (entry - buf * pip_size)
                if exec_agent.modify_stop_loss(ticket, new_sl):
                    locked.append(f"• `{sym}` (#{ticket}) ➡️ SL `{new_sl:.5f}`")
                else:
                    skipped.append(f"• `{sym}` (#{ticket}): Broker rejected SL change")
            else:
                skipped.append(f"• `{sym}` (#{ticket}): Currently `{profit_pips:+.1f} pips` (not in profit yet)")

        lines = ["🛡️ *BULK BREAK-EVEN SWEEP*", "━━━━━━━━━━━━━━━━━━"]
        if locked:
            lines.append("*Locked Risk-Free:*")
            lines.extend(locked)
        if skipped:
            lines.append("\n*Unchanged:*")
            lines.extend(skipped)

        self.send_msg("\n".join(lines), reply_markup=self.get_main_reply_keyboard())

    def _cmd_pause(self, pause: bool):
        set_entries_paused(pause)
        if pause:
            append_live_log("⏸️ [TELEGRAM CONTROL] User PAUSED new trade entries from phone.")
            self.send_msg(
                "⏸️ *NEW ENTRIES PAUSED*\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "The bot will *NOT* open any new trades until you tap *▶️ Resume Entries* or send `/resume`.\n\n"
                "🛡️ *Active Defense Still Running:* All open positions continue to get automatic Break-Even, Partial TP, and Trailing Stop protection!",
                reply_markup=self.get_main_reply_keyboard()
            )
        else:
            append_live_log("▶️ [TELEGRAM CONTROL] User RESUMED new trade entries from phone.")
            self.send_msg(
                "▶️ *NEW ENTRIES RESUMED*\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "Full 6-pair scanning and automated trade execution are back *ONLINE*! 🚀",
                reply_markup=self.get_main_reply_keyboard()
            )

    def _cmd_panic_prompt(self):
        exec_agent = ExecutionAgent(self.config)
        open_pos = exec_agent.get_open_positions() if exec_agent.connect() else []
        pend_ord = exec_agent.get_pending_orders() if exec_agent.is_connected else []

        if not open_pos and not pend_ord:
            self.send_msg("🚨 *Panic Close:* You have `0` open positions and `0` pending orders right now.")
            return

        total_pnl = sum(float(p["profit"]) + float(p.get("swap", 0.0)) for p in open_pos)
        inline = {
            "inline_keyboard": [
                [
                    {"text": "⚠️ YES, CLOSE EVERYTHING", "callback_data": "cmd:panic_confirm"},
                    {"text": "❎ Cancel", "callback_data": "cmd:panic_cancel"}
                ]
            ]
        }
        self.send_msg(
            f"🚨 *CONFIRM EMERGENCY PANIC CLOSE?*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"This will immediately:\n"
            f"• Close **`{len(open_pos)}` open position(s)** at market (Net PnL: `${total_pnl:+,.2f}`)\n"
            f"• Cancel **`{len(pend_ord)}` pending order(s)**\n"
            f"• Pause new entries until you resume\n\n"
            f"👇 *Are you sure?*",
            reply_markup=inline
        )

    def _execute_close_all(self):
        exec_agent = ExecutionAgent(self.config)
        if not exec_agent.connect():
            self.send_msg("⚠️ *MT5 Terminal Disconnected*")
            return

        open_pos = exec_agent.get_open_positions()
        pend_ord = exec_agent.get_pending_orders()

        closed_count = 0
        total_pnl = 0.0
        for p in open_pos:
            ok, pnl, _ = exec_agent.close_position(p["ticket"], reason_comment="TG_PANIC_CLOSE")
            if ok:
                closed_count += 1
                total_pnl += pnl

        cancelled_count = 0
        for o in pend_ord:
            if exec_agent.cancel_pending_order(o["ticket"]):
                cancelled_count += 1

        set_entries_paused(True)
        append_live_log(
            f"🚨 [TELEGRAM PANIC CLOSE] Closed {closed_count} positions (${total_pnl:+,.2f}), "
            f"cancelled {cancelled_count} orders, and paused new entries."
        )
        self.send_msg(
            f"🚨 *EMERGENCY FLATTEN COMPLETE*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"✅ *Positions Closed:* `{closed_count}` (Net PnL: `${total_pnl:+,.2f}`)\n"
            f"🚫 *Pending Orders Cancelled:* `{cancelled_count}`\n"
            f"⏸️ *Engine State:* Paused new entries (tap *▶️ Resume Entries* when ready).",
            reply_markup=self.get_main_reply_keyboard()
        )
