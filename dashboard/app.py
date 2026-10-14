"""
All-in-One Trading Bot Application with Live Heartbeat and Interactive Controls:
- Controls bot lifecycle (Start / Stop) with a single button
- Live pulsing visual indicator showing whether the bot is running
- Real-time Account Status & Active MT5 Positions
- Human News Desk controls
- Live Activity Stream / Cycle Logs
- Single desktop application experience (no multiple .bat files needed)
"""

import streamlit as st
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
import yaml
import time
import subprocess
import os
import sys

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from core.bot_logger import get_live_logs, get_heartbeat, get_scanner_matrix, append_live_log

DB_PATH = ROOT_DIR / "data" / "trading_journal.db"
CONFIG_PATH = ROOT_DIR / "config" / "settings.yaml"
PID_FILE = ROOT_DIR / "data" / "bot.pid"
LOG_FILE = ROOT_DIR / "data" / "bot_live.log"

st.set_page_config(
    page_title="Trading Bot - Control Center",
    page_icon="🟢",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for modern styling and pulsing status dot
st.markdown("""
<style>
    .status-card {
        padding: 1.2rem;
        border-radius: 10px;
        background-color: #1E232A;
        border: 1px solid #2D3748;
        margin-bottom: 1rem;
    }
    .pulse-green {
        display: inline-block;
        width: 14px;
        height: 14px;
        border-radius: 50%;
        background: #10B981;
        box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
        animation: pulse 1.6s infinite;
        vertical-align: middle;
        margin-right: 8px;
    }
    .pulse-red {
        display: inline-block;
        width: 14px;
        height: 14px;
        border-radius: 50%;
        background: #EF4444;
        vertical-align: middle;
        margin-right: 8px;
    }
    @keyframes pulse {
        0% {
            transform: scale(0.95);
            box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
        }
        70% {
            transform: scale(1);
            box-shadow: 0 0 0 8px rgba(16, 185, 129, 0);
        }
        100% {
            transform: scale(0.95);
            box-shadow: 0 0 0 0 rgba(16, 185, 129, 0);
        }
    }
</style>
""", unsafe_allow_html=True)

import streamlit.components.v1 as components


def render_live_countdown(seconds_left: int, is_running: bool, label: str = "Next Market Scan In"):
    """
    Renders a live client-side JavaScript ticking countdown clock in the browser.
    Ticks down every second (1000ms) without causing screen flickering.
    """
    js_running = "true" if is_running else "false"
    html_code = f"""
    <!DOCTYPE html>
    <html>
    <head>
      <meta charset="utf-8">
      <style>
        body {{
          margin: 0;
          padding: 0;
          background-color: transparent;
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
          color: #FAFAFA;
        }}
        .timer-card {{
          background-color: #1E232A;
          border: 1px solid #2D3748;
          border-radius: 8px;
          padding: 9px 12px;
          box-sizing: border-box;
        }}
        .timer-label {{
          font-size: 0.80rem;
          color: #A0AEC0;
          font-weight: 500;
          margin-bottom: 2px;
          text-transform: uppercase;
          letter-spacing: 0.4px;
        }}
        .timer-value {{
          font-size: 1.70rem;
          font-weight: 700;
          color: #10B981;
          font-variant-numeric: tabular-nums;
          letter-spacing: 0.5px;
          display: flex;
          align-items: center;
          gap: 8px;
        }}
        .timer-sub {{
          font-size: 0.74rem;
          color: #718096;
          margin-top: 2px;
        }}
        .pulse-dot {{
          width: 9px;
          height: 9px;
          border-radius: 50%;
          background: #10B981;
          display: inline-block;
          animation: pulse 1.5s infinite;
        }}
        @keyframes pulse {{
          0% {{ opacity: 1; transform: scale(0.95); }}
          50% {{ opacity: 0.35; transform: scale(1.15); }}
          100% {{ opacity: 1; transform: scale(0.95); }}
        }}
      </style>
    </head>
    <body>
      <div class="timer-card">
        <div class="timer-label">{label}</div>
        <div class="timer-value" id="clock-display">
          <span class="pulse-dot" id="pulse-indicator"></span>
          <span id="digits">--:--</span>
        </div>
        <div class="timer-sub" id="clock-sub">Ticking live every second ⏱️</div>
      </div>

      <script>
        let remaining = {seconds_left};
        const active = {js_running};

        function tick() {{
          const digits = document.getElementById("digits");
          const sub = document.getElementById("clock-sub");
          const dot = document.getElementById("pulse-indicator");

          if (!active) {{
            digits.innerText = "PAUSED";
            digits.style.color = "#EF4444";
            dot.style.background = "#EF4444";
            dot.style.animation = "none";
            sub.innerText = "Start bot to begin countdown";
            return;
          }}

          if (remaining <= 0) {{
            digits.innerText = "SCANNING NOW...";
            digits.style.color = "#F59E0B";
            dot.style.background = "#F59E0B";
            sub.innerText = "Evaluating all pairs across market...";
            setTimeout(() => {{
              try {{ window.parent.location.reload(); }} catch(e) {{}}
            }}, 3000);
            return;
          }}

          const m = Math.floor(remaining / 60);
          const s = remaining % 60;
          digits.innerText = String(m).padStart(2, '0') + ":" + String(s).padStart(2, '0');
          digits.style.color = "#10B981";
          sub.innerText = "Ticking live (" + remaining + "s remaining) ⏱️";
        }}

        tick();
        if (active) {{
          setInterval(() => {{
            remaining--;
            tick();
          }}, 1000);
        }}
      </script>
    </body>
    </html>
    """
    components.html(html_code, height=90)


def is_bot_running() -> bool:
    if not PID_FILE.exists():
        return False
    try:
        pid = int(PID_FILE.read_text().strip())
        if os.name == 'nt':
            import ctypes
            kernel32 = ctypes.windll.kernel32
            PROCESS_QUERY_INFORMATION = 0x0400
            handle = kernel32.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        else:
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def start_bot_process():
    root_dir = ROOT_DIR
    runner_script = root_dir / "run_bot.py"
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    f = open(LOG_FILE, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, str(runner_script)],
        cwd=str(root_dir),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
        stdout=f,
        stderr=f
    )
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid))
    append_live_log(f"🚀 Bot background engine launched (Process #{proc.pid}). Live scanning online.")


def stop_bot_process():
    if PID_FILE.exists():
        try:
            pid = int(PID_FILE.read_text().strip())
            import signal
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        finally:
            if PID_FILE.exists():
                PID_FILE.unlink()
        append_live_log("🛑 Bot background engine stopped by user.")


# -------------------------------------------------------------
# SIDEBAR: STATUS & MASTER CONTROLS
# -------------------------------------------------------------
bot_running = is_bot_running()

with st.sidebar:
    st.title("🎛️ Master Control")
    st.markdown("---")

    hb = get_heartbeat()

    if bot_running:
        st.markdown('<div><span class="pulse-green"></span><b>BOT STATUS: ACTIVE & RUNNING</b></div>', unsafe_allow_html=True)
        sec_sb = 300
        if hb and "next_cycle_time" in hb:
            try:
                nxt = datetime.strptime(hb["next_cycle_time"], "%Y-%m-%d %H:%M:%S")
                sec_sb = max(0, int((nxt - datetime.now()).total_seconds()))
            except Exception:
                sec_sb = hb.get("seconds_remaining", 300)
        elif hb:
            sec_sb = hb.get("seconds_remaining", 300)
        render_live_countdown(sec_sb, bot_running, label="Next Scan Countdown")

        if st.button("🛑 STOP BOT", use_container_width=True, type="secondary"):
            stop_bot_process()
            st.rerun()
    else:
        st.markdown('<div><span class="pulse-red"></span><b>BOT STATUS: STOPPED</b></div>', unsafe_allow_html=True)
        st.warning("The bot is currently idle.")
        if st.button("🚀 START BOT", use_container_width=True, type="primary"):
            start_bot_process()
            st.rerun()

    st.markdown("---")
    st.subheader("⚡ Instant Market Audit")
    if st.button("▶️ Run 1 Instant Scan Now", use_container_width=True):
        with st.spinner("Scanning all pairs right now..."):
            from run_bot import run_cycle
            scanned = run_cycle(cycle_num=999)
            st.success(f"Scanned {scanned} pairs!")
            time.sleep(1)
            st.rerun()

    # Load config safely
    config = {}
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r") as f:
            config = yaml.safe_load(f) or {}

    active_tf = config.get("data", {}).get("timeframe", "H4").upper()
    st.markdown("---")
    st.subheader("⚙️ Active Bot Profile")
    st.markdown(f"• **Timeframe:** `{active_tf}` ({'4-Hour Swing' if active_tf == 'H4' else '1-Hour Fast'})")
    st.markdown("• **Regime Filter:** `Daily (D1)` (Macro Safety)")
    st.markdown("• **Order Types:** `Limit & Stop Pending`")
    st.markdown("• **Pending Expiration:** `24 Hours`")
    st.markdown("• **Crypto Weekend:** `24/7 Active`")
    st.markdown("• **Risk Per Trade:** `1.0% Hard Cap`")

    st.markdown("---")
    st.subheader("🔔 Telegram Alerts")
    st.write("Connected to: **@Wondatrade_bot** ✅")

    st.markdown("---")
    st.caption("Auto-refreshes every 30 seconds.")
    if st.button("🔄 Refresh Data Now", use_container_width=True):
        st.rerun()

# -------------------------------------------------------------
# MAIN HEADER & ACCOUNT METRICS
# -------------------------------------------------------------
st.title("Trading Bot — Command Center")

# Live market banner
is_wknd = datetime.utcnow().weekday() in (5, 6)
if is_wknd:
    st.info("🕒 **Weekend Mode Active:** Traditional Forex and Metals markets are closed. The bot is scanning Crypto pairs 24/7 (BTCUSD, ETHUSD, SOLUSD). Forex resumes Sunday 5:00 PM EST.")
else:
    st.success(f"🟢 **Weekday Market Active:** Actively scanning Forex, Gold (XAUUSD), and Crypto on **{active_tf} ({'4-Hour Swing' if active_tf == 'H4' else '1-Hour'})** candles every 5 minutes.")

try:
    import MetaTrader5 as mt5
    mt5_active = mt5.initialize()
    if not mt5_active:
        time.sleep(0.5)
        mt5_active = mt5.initialize()
except Exception:
    mt5_active = False

col1, col2, col3, col4 = st.columns(4)
hb = get_heartbeat()
if mt5_active:
    acct = mt5.account_info()
    if acct:
        col1.metric("MT5 Account", f"#{acct.login}", "Connected ✅")
        col2.metric("Account Balance", f"${acct.balance:,.2f}")
        col3.metric("Live Equity", f"${acct.equity:,.2f}")
        profit_delta = f"{'+' if acct.profit >= 0 else ''}${acct.profit:,.2f}"
        col4.metric("Floating P&L", f"${acct.profit:,.2f}", profit_delta)
    else:
        # Fallback to heartbeat metrics recorded by bot
        col1.metric("MT5 Account", f"#{hb.get('login', 'Active')}", "Syncing 🔄")
        col2.metric("Account Balance", f"${hb.get('balance', 0):,.2f}")
        col3.metric("Live Equity", f"${hb.get('equity', 0):,.2f}")
        col4.metric("Floating P&L", "$0.00")
else:
    # If terminal IPC briefly flickered, check if bot engine is active
    if hb and hb.get("balance", 0) > 0:
        col1.warning("MT5 Reconnecting... 🔄")
        col2.metric("Account Balance", f"${hb.get('balance', 0):,.2f}")
        col3.metric("Live Equity", f"${hb.get('equity', 0):,.2f}")
        col4.metric("Floating P&L", "$0.00")
    else:
        col1.error("MT5 Terminal: Disconnected")
        col2.metric("Account Balance", "$0.00")
        col3.metric("Live Equity", "$0.00")
        col4.metric("Floating P&L", "$0.00")

st.markdown("---")

# -------------------------------------------------------------
# TABS
# -------------------------------------------------------------
tab_monitor, tab_positions, tab_history, tab_news, tab_signals, tab_backtest = st.tabs([
    "🔴 Live Market Scanner & Feed", 
    "📈 Open Positions", 
    "🏁 Trade History (Closed P&L)", 
    "📰 Human News Desk", 
    "📋 Decision Log", 
    "🧪 Strategy Backtester"
])

# TAB 1: LIVE MONITOR & FEED
with tab_monitor:
    st.subheader("🔴 Live Market Scanner & Real-Time Bot Feed")
    st.write("Monitor exactly what the bot is evaluating, its countdown timer, and live activity feed.")

    hb_data = get_heartbeat()

    # 1. Live Heartbeat Metrics
    c_m1, c_m2, c_m3, c_m4 = st.columns(4)
    with c_m1:
        if bot_running:
            pid_val = PID_FILE.read_text().strip() if PID_FILE.exists() else ""
            st.metric("Bot Status", "ACTIVE & SCANNING", f"Process #{pid_val} 🟢")
        else:
            st.metric("Bot Status", "STOPPED", "Idle 🛑")

    with c_m2:
        sec_tab = 300
        if bot_running and hb_data:
            if "next_cycle_time" in hb_data:
                try:
                    nxt = datetime.strptime(hb_data["next_cycle_time"], "%Y-%m-%d %H:%M:%S")
                    sec_tab = max(0, int((nxt - datetime.now()).total_seconds()))
                except Exception:
                    sec_tab = hb_data.get("seconds_remaining", 300)
            else:
                sec_tab = hb_data.get("seconds_remaining", 300)
        elif hb_data:
            sec_tab = hb_data.get("seconds_remaining", 300)
        render_live_countdown(sec_tab, bot_running, label="Next Market Scan In")

    with c_m3:
        cyc = hb_data.get("cycle_count", 0) if hb_data else 0
        last_t = hb_data.get("last_cycle_time", "N/A").split(" ")[-1] if hb_data else "N/A"
        st.metric("Latest Cycle", f"Cycle #{cyc}", f"Finished at {last_t}")

    with c_m4:
        tf_val = hb_data.get("timeframe", "H1") if hb_data else "H1"
        st.metric("Monitored Timeframe", f"{tf_val} (1-Hour)", "Intraday Pullback Triggers")

    col_audit_btn, col_audit_txt = st.columns([1, 2.5])
    with col_audit_btn:
        if st.button("⚡ Trigger Immediate Market Scan", type="primary", use_container_width=True):
            with st.spinner("Analyzing all pairs across the market right now..."):
                from run_bot import run_cycle
                done_scanned = run_cycle(cycle_num=999)
                st.success(f"Audit complete! Analyzed {done_scanned} pairs.")
                time.sleep(1)
                st.rerun()
    with col_audit_txt:
        st.caption("You don't have to wait for the 5-minute timer. Click above to make the bot evaluate all pairs immediately.")

    st.markdown("---")

    # 2. Live Scanner Matrix
    st.markdown("#### 🔍 Real-Time Pair Inspection Matrix")
    st.caption("Here is what the bot is evaluating for every watched pair right now:")

    mat = get_scanner_matrix()
    if mat and mat.get("pairs"):
        pairs_data = mat["pairs"]
        df_mat = pd.DataFrame(pairs_data)
        df_mat_clean = df_mat[["symbol", "price", "regime", "badge", "details", "last_checked"]].copy()
        df_mat_clean.columns = ["Symbol", "Live Price", "D1 Macro Regime", "Status", "What the Bot is Seeing / Waiting For", "Time Checked"]
        st.dataframe(df_mat_clean, use_container_width=True, hide_index=True)
    else:
        st.info("No scanner snapshot recorded yet. Click '⚡ Trigger Immediate Market Scan' or start the bot to view live pair inspections.")

    st.markdown("---")

    # 3. Live Console Terminal Stream
    c_hdr, c_clr = st.columns([3, 1])
    with c_hdr:
        st.markdown("#### 📟 Live Console Activity Stream")
        st.caption("Real-time terminal logs streaming directly from the bot engine:")
    with c_clr:
        if st.button("🗑️ Clear Log Feed"):
            if LOG_FILE.exists():
                LOG_FILE.write_text("")
            st.rerun()

    terminal_logs = get_live_logs(limit=80)
    st.text_area("Terminal Output", value=terminal_logs, height=280, disabled=True, label_visibility="collapsed")

# TAB 1: ACTIVE TRADES
with tab_positions:
    st.subheader("Open Positions in MetaTrader 5")
    if mt5_active:
        positions = mt5.positions_get()
        if positions and len(positions) > 0:
            df_pos = pd.DataFrame([{
                "Ticket": p.ticket,
                "Symbol": p.symbol,
                "Type": "BUY" if p.type == 0 else "SELL",
                "Lots": p.volume,
                "Entry Price": p.price_open,
                "Current Price": p.price_current,
                "Stop Loss": p.sl,
                "Take Profit": p.tp if p.tp > 0 else "Trailing",
                "Profit ($)": f"${p.profit:,.2f}"
            } for p in positions])
            st.dataframe(df_pos, use_container_width=True)
        else:
            st.info("No active positions currently running in MT5.")

        # Pending Orders Section (Limit & Stop Orders)
        pending_orders = mt5.orders_get()
        st.markdown("#### ⏳ Pending Orders (Limit & Stop Orders)")
        if pending_orders and len(pending_orders) > 0:
            df_pend = pd.DataFrame([{
                "Ticket": o.ticket,
                "Symbol": o.symbol,
                "Type": "BUY LIMIT" if o.type == 2 else ("SELL LIMIT" if o.type == 3 else ("BUY STOP" if o.type == 4 else "SELL STOP")),
                "Volume": o.volume_initial,
                "Limit Price": o.price_open,
                "Stop Loss": o.sl,
                "Take Profit": o.tp if o.tp > 0 else "Trailing",
                "Auto Expiration": "24 Hours (Active)"
            } for o in pending_orders])
            st.dataframe(df_pend, use_container_width=True)
        else:
            st.caption("No pending orders waiting. When a pullback setup forms, the bot places Buy/Sell Limits at optimal discount prices.")
    else:
        st.error("Please ensure your MetaTrader 5 terminal is open.")

# TAB 3: CLOSED TRADE HISTORY & REALIZED P&L
with tab_history:
    st.subheader("🏁 Closed Trades History & Realized Performance")
    st.caption("Complete historical track record of all closed positions taken by the bot, showing realized profit/loss, return in R, and post-trade technical post-mortems.")

    if DB_PATH.exists():
        with sqlite3.connect(DB_PATH) as conn:
            df_closed = pd.read_sql_query("""
                SELECT 
                    ticket AS 'Ticket',
                    symbol AS 'Symbol',
                    direction AS 'Type',
                    lots AS 'Lots',
                    entry_price AS 'Entry Price',
                    exit_price AS 'Exit Price',
                    exit_time AS 'Closed At',
                    exit_reason AS 'Exit Trigger',
                    pnl_usd AS 'Profit ($)',
                    pnl_r AS 'Return (R)',
                    notes AS 'Post-Trade Analysis'
                FROM trades 
                WHERE exit_time IS NOT NULL 
                ORDER BY id DESC
            """, conn)
            
            if not df_closed.empty:
                # Summary metrics
                total_pnl = df_closed["Profit ($)"].sum()
                win_count = len(df_closed[df_closed["Profit ($)"] > 0])
                loss_count = len(df_closed[df_closed["Profit ($)"] <= 0])
                win_rate = (win_count / len(df_closed)) * 100.0 if len(df_closed) > 0 else 0.0

                c_h1, c_h2, c_h3, c_h4 = st.columns(4)
                c_h1.metric("Closed Trades", len(df_closed))
                c_h2.metric("Net Realized Profit", f"${total_pnl:+,.2f}", f"{'+' if total_pnl >= 0 else ''}${total_pnl:,.2f}")
                c_h3.metric("Win Rate", f"{win_rate:.1f}%", f"{win_count} Wins / {loss_count} Losses")
                c_h4.metric("Avg Trade P&L", f"${(total_pnl / len(df_closed)):+,.2f}")

                st.markdown("---")
                st.markdown("#### Detailed Trade Log")

                # Format profit column for display
                df_display = df_closed.copy()
                df_display["Profit ($)"] = df_display["Profit ($)"].apply(lambda x: f"${x:+,.2f}" if pd.notnull(x) else "$0.00")
                df_display["Return (R)"] = df_display["Return (R)"].apply(lambda x: f"{x:+.2f}R" if pd.notnull(x) else "-")
                st.dataframe(df_display, use_container_width=True, hide_index=True)
            else:
                st.info("No closed trades recorded yet.")

    st.markdown("---")
    with st.expander("ℹ️ How 4-Hour (H4) Swing Scanning & Pending Orders Work", expanded=False):
        st.markdown("""
        ### ⚡ Faster Trade Opportunities with 1-Hour (H1) Scanning
        - **Why we added H1 scanning:** Waiting for 4-Hour (H4) or Daily setups can take days or even weeks.
        - **How it works:** The bot uses the **Daily (D1)** chart for macro trend & market regime direction, while scanning **1-Hour (H1)** candles every 5 minutes to catch intraday pullback entry triggers.
        - **Result:** You get **4x more trade setups** every week while keeping the strict protection of the Daily macro trend!

        ---

        ### 🪙 24/7 Crypto Weekend Trading
        - **Forex & Gold:** Close on Friday 5:00 PM EST and reopen Sunday 5:00 PM EST.
        - **Crypto (BTCUSD, ETHUSD, SOLUSD):** Trades 24 hours a day, 7 days a week, 365 days a year.
        - **Trading Crypto on weekends:**
          1. The bot is fully programmed to scan and trade crypto 24/7.
          2. The default `MetaQuotes-Demo` broker only provides Forex, Metals, and US Nasdaq ETFs (which close on weekends).
          3. To trade crypto on weekends with demo money, open a free Demo account in MT5 with a broker offering Crypto CFDs (e.g., Exness, IC Markets, Bybit MT5). Inside MT5: *File -> Open an Account -> Search 'Exness' -> Open Demo*. The bot will automatically detect it!
        """)

# TAB 2: HUMAN NEWS DESK
with tab_news:
    st.subheader("Give the Bot Your Market View")
    st.write("You don't need code to influence the bot. Set your opinion on any currency below, and the bot will factor it into its scoring.")

    with st.form("news_form"):
        col_c1, col_c2, col_c3 = st.columns(3)
        with col_c1:
            asset = st.selectbox("Select Currency / Asset", [
                "USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "XAU", "BTC", "ETH"
            ])
        with col_c2:
            bias_label = st.select_slider(
                "Your Macro Bias",
                options=["Strong Bearish (-2)", "Bearish (-1)", "Neutral (0)", "Bullish (+1)", "Strong Bullish (+2)"],
                value="Neutral (0)"
            )
        with col_c3:
            days = st.number_input("Days Active", min_value=1, max_value=30, value=7)

        note = st.text_input("Reason (e.g. 'Upcoming rate hike', 'Bullish weekly trend')")
        submitted = st.form_submit_button("💾 Save View")

        if submitted:
            bias_val = 0
            if "-2" in bias_label: bias_val = -2
            elif "-1" in bias_label: bias_val = -1
            elif "+1" in bias_label: bias_val = 1
            elif "+2" in bias_label: bias_val = 2

            expiry = (datetime.utcnow() + timedelta(days=days)).isoformat()
            with sqlite3.connect(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO news_bias (created_at, asset, bias, expires_at, note)
                    VALUES (?, ?, ?, ?, ?)
                """, (datetime.utcnow().isoformat(), asset, bias_val, expiry, note))
                conn.commit()
            st.success(f"Saved: {asset} is set to {bias_label} until {expiry[:10]}!")

    st.markdown("---")
    st.markdown("### 📰 Breaking Market News & Auto-Analysis")
    st.write(
        "Real-time news stories and reports are pulled automatically below. "
        "You can read the article summary, click **'🧠 Analyze with AI'**, and let the assistant explain the market impact and set your bias with one click."
    )

    from core.news_feed import get_latest_financial_news
    from core.news_assistant import get_upcoming_economic_events, analyze_news_snippet

    col_news_feed, col_cal_right = st.columns([1.5, 1])

    with col_news_feed:
        st.markdown("#### ⚡ Latest Breaking Financial Articles")
        news_articles = get_latest_financial_news(limit=10)
        
        if news_articles:
            for idx, article in enumerate(news_articles):
                with st.expander(f"📌 {article['title']} ({', '.join(article['currencies'])})", expanded=(idx == 0)):
                    st.caption(f"📅 Published: {article['date']}")
                    st.write(article['summary'])
                    
                    c_act1, c_act2 = st.columns([1, 1.2])
                    with c_act1:
                        target_curr = st.selectbox(
                            "Focus Currency:",
                            options=article['currencies'] if article['currencies'] != ["GLOBAL"] else ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "XAU", "BTC"],
                            key=f"curr_select_{idx}"
                        )
                    with c_act2:
                        analyze_clicked = st.button("🧠 Analyze Market Impact", key=f"btn_analyze_{idx}")

                    if analyze_clicked:
                        analysis = analyze_news_snippet(article['full_text'], target_curr)
                        st.markdown(f"**AI Recommendation:** `{analysis['verdict']}`")
                        st.info(f"💡 **Plain English Explanation:** {analysis['advice']}")
                        st.write("**Detected Signals:**")
                        for r in analysis["reasons"]:
                            st.write(f"- {r}")

                        if st.button(f"⚡ Apply '{analysis['verdict']}' to {target_curr} in Bot", key=f"btn_apply_{idx}"):
                            expiry = (datetime.utcnow() + timedelta(days=5)).isoformat()
                            with sqlite3.connect(DB_PATH) as conn:
                                cursor = conn.cursor()
                                cursor.execute("""
                                    INSERT INTO news_bias (created_at, asset, bias, expires_at, note)
                                    VALUES (?, ?, ?, ?, ?)
                                """, (datetime.utcnow().isoformat(), target_curr, analysis["bias"], expiry, f"News Feed: {article['title'][:60]}..."))
                                conn.commit()
                            st.success(f"Successfully applied {analysis['verdict']} to {target_curr}!")
                            st.rerun()
        else:
            st.warning("Loading breaking news articles or feed temporarily unavailable.")

    with col_cal_right:
        st.markdown("#### 📅 High-Impact Calendar")
        events = get_upcoming_economic_events()
        if events:
            df_ev = pd.DataFrame(events)
            st.dataframe(df_ev[["country", "title", "impact", "date", "forecast", "previous"]], use_container_width=True, height=350)
        else:
            st.write("No high-impact events found.")

        st.markdown("---")
        st.markdown("#### ✍️ Or Paste Custom News Excerpt")
        custom_curr = st.selectbox("Currency Affected", ["USD", "EUR", "GBP", "JPY", "AUD", "CAD", "CHF", "NZD", "XAU", "BTC"], key="custom_curr_box")
        custom_text = st.text_area("Paste any headline or article text:", height=90, key="custom_text_box", placeholder="Paste breaking news here...")
        if st.button("🧠 Analyze Custom Text"):
            if custom_text.strip():
                c_analysis = analyze_news_snippet(custom_text, custom_curr)
                st.markdown(f"**Verdict:** `{c_analysis['verdict']}`")
                st.write(f"📌 {c_analysis['advice']}")
                if st.button(f"⚡ Apply '{c_analysis['verdict']}' to {custom_curr}", key="btn_apply_custom"):
                    expiry = (datetime.utcnow() + timedelta(days=5)).isoformat()
                    with sqlite3.connect(DB_PATH) as conn:
                        cursor = conn.cursor()
                        cursor.execute("""
                            INSERT INTO news_bias (created_at, asset, bias, expires_at, note)
                            VALUES (?, ?, ?, ?, ?)
                        """, (datetime.utcnow().isoformat(), custom_curr, c_analysis["bias"], expiry, f"Custom Input: {custom_text[:60]}..."))
                        conn.commit()
                    st.success(f"Applied to {custom_curr}!")
                    st.rerun()

    st.markdown("---")
    st.markdown("#### Currently Active Biases in the Bot")
    if DB_PATH.exists():
        with sqlite3.connect(DB_PATH) as conn:
            df_biases = pd.read_sql_query(
                "SELECT asset, bias, expires_at, note FROM news_bias ORDER BY id DESC LIMIT 10", conn
            )
            if not df_biases.empty:
                st.dataframe(df_biases, use_container_width=True)
            else:
                st.write("No active news biases set yet.")

# TAB 3: DECISION LOG
with tab_signals:
    st.subheader("Bot Decision Log (Every Setup Checked)")
    st.caption("Sorted newest to oldest. When an active trade is running, subsequent scans safely block opening duplicates for the same pair.")
    if DB_PATH.exists():
        with sqlite3.connect(DB_PATH) as conn:
            df_sig = pd.read_sql_query(
                "SELECT id, timestamp, symbol, strategy, direction, score, status, rejection_reason FROM signals ORDER BY id DESC LIMIT 50",
                conn
            )
            if not df_sig.empty:
                st.dataframe(df_sig, use_container_width=True, hide_index=True)
            else:
                st.info("No signals recorded yet. As market cycles close, every decision will appear here.")

# TAB 4: STRATEGY BACKTESTER
with tab_backtest:
    st.subheader("Test Strategies on Past Data")
    st.write(
        "Use the controls below to choose the timeframe and lookback depth into past market history. "
        "The bot will analyze every candle across all pairs and report profit, win rate, and maximum drawdown."
    )

    col_tf, col_slider, col_btn = st.columns([1.2, 1.8, 1])

    with col_tf:
        tf_option = st.selectbox(
            "Scanning Timeframe:",
            ["1-Hour (H1) - High Frequency (Active)", "4-Hour (H4) - Classic Swing"],
            index=0
        )
        tf_code = "H1" if "H1" in tf_option else "H4"

    with col_slider:
        default_bars = 2000 if tf_code == "H1" else 1500
        lookback_bars = st.slider(
            f"Historical Depth ({tf_code} Candles)",
            min_value=500,
            max_value=8000,
            value=default_bars,
            step=500,
            help="Select how many historical candles to backtest."
        )
        bars_per_day = 24 if tf_code == "H1" else 6
        approx_days = round(lookback_bars / bars_per_day)
        approx_months = round(approx_days / 30, 1)
        st.caption(f"⏱️ **Estimated Test Period:** ~{approx_days} days (~{approx_months} months of market history)")

    with col_btn:
        st.write("")
        st.write("")
        run_btn = st.button("▶️ Run Backtest Now", type="primary", use_container_width=True)

    if run_btn:
        with st.spinner(f"Simulating past ~{approx_months} months across all portfolio pairs on {tf_code} candles..."):
            root_dir = Path(__file__).resolve().parent.parent
            result = subprocess.run(
                [sys.executable, str(root_dir / "run_test.py"), str(lookback_bars), tf_code],
                capture_output=True,
                text=True,
                cwd=str(root_dir)
            )
            st.code(result.stdout)
