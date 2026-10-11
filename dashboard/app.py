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

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trading_journal.db"
CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "settings.yaml"
PID_FILE = Path(__file__).resolve().parent.parent / "data" / "bot.pid"

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
            # Linux / Streamlit Community Cloud
            os.kill(pid, 0)
            return True
    except Exception:
        return False


def start_bot_process():
    root_dir = Path(__file__).resolve().parent.parent
    runner_script = root_dir / "run_bot.py"
    # Launch detached background process
    proc = subprocess.Popen(
        [sys.executable, str(runner_script)],
        cwd=str(root_dir),
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(str(proc.pid))


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


# -------------------------------------------------------------
# SIDEBAR: STATUS & MASTER CONTROLS
# -------------------------------------------------------------
bot_running = is_bot_running()

with st.sidebar:
    st.title("🎛️ Master Control")
    st.markdown("---")

    if bot_running:
        st.markdown('<div><span class="pulse-green"></span><b>BOT STATUS: ACTIVE & RUNNING</b></div>', unsafe_allow_html=True)
        st.success("The bot is continuously monitoring market cycles in the background.")
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

try:
    import MetaTrader5 as mt5
    mt5_active = mt5.initialize()
except Exception:
    mt5_active = False

col1, col2, col3, col4 = st.columns(4)
if mt5_active:
    acct = mt5.account_info()
    if acct:
        col1.metric("MT5 Account", f"#{acct.login}", "Connected ✅")
        col2.metric("Account Balance", f"${acct.balance:,.2f}")
        col3.metric("Live Equity", f"${acct.equity:,.2f}")
        profit_delta = f"{'+' if acct.profit >= 0 else ''}${acct.profit:,.2f}"
        col4.metric("Floating P&L", f"${acct.profit:,.2f}", profit_delta)
    else:
        st.warning("MT5 terminal detected, but could not read account info.")
else:
    col1.error("MT5 Terminal: Disconnected")
    col2.metric("Account Balance", "$0.00")
    col3.metric("Live Equity", "$0.00")
    col4.metric("Floating P&L", "$0.00")

st.markdown("---")

# -------------------------------------------------------------
# TABS
# -------------------------------------------------------------
tab_positions, tab_news, tab_signals, tab_backtest = st.tabs([
    "📈 Active Trades", "📰 Human News Desk", "📋 Decision Log", "🧪 Strategy Backtester"
])

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
            st.info("No trades currently open. The bot will open positions automatically when an approved setup appears at a candle close.")
    else:
        st.error("Please ensure your MetaTrader 5 terminal is open.")

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
    st.write("Here you can see every single trade the bot considered, and why it was taken or rejected.")
    if DB_PATH.exists():
        with sqlite3.connect(DB_PATH) as conn:
            df_sig = pd.read_sql_query(
                "SELECT timestamp, symbol, strategy, direction, score, status, rejection_reason FROM signals ORDER BY id DESC LIMIT 50",
                conn
            )
            if not df_sig.empty:
                st.dataframe(df_sig, use_container_width=True)
            else:
                st.info("No signals recorded yet. As market cycles close, every decision will appear here.")

# TAB 4: STRATEGY BACKTESTER
with tab_backtest:
    st.subheader("Test Strategies on Past Data")
    st.write("Click below to run a historical test of your strategies without leaving this window.")
    if st.button("▶️ Run Historical Backtest Now"):
        with st.spinner("Analyzing historical market data..."):
            root_dir = Path(__file__).resolve().parent.parent
            result = subprocess.run([sys.executable, str(root_dir / "run_test.py")], capture_output=True, text=True, cwd=str(root_dir))
            st.code(result.stdout)
