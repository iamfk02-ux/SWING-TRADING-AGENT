# 🤖 Multi-Agent Swing Trading Bot (Forex & Crypto)

An autonomous multi-agent algorithmic swing trading system connecting directly to **MetaTrader 5 (MT5)**, designed for capital preservation, disciplined risk management, and human-in-the-loop macroeconomic news intelligence.

---

## 🏛️ System Architecture

```text
               MARKET DATA (MT5 Feed)
                         ↓
               Market-Regime Agent
        (Trending / Compression / Ranging)
                         ↓
      ┌──────────────────┼──────────────────┐
      ↓                  ↓                  ↓
 Trend Agent       Breakout Agent    Mean-Reversion
      └──────────────────┬──────────────────┘
                         ↓
                   Signal Scorer
     (Blends Technicals + Live Macro News Bias)
                         ↓
                   Risk Manager
      (1% Cap, Cent Account Scaling, 20% Kill-Switch)
                         ↓
                  Execution Agent
                         ↓
              MetaTrader 5 Terminal
                         ↓
              Telegram Alerts to Phone 📱
```

---

## ✨ Key Features

1. **Multi-Agent Decision Pipeline:**
   - **Market-Regime Agent:** Classifies daily market structure using ADX, EMAs, ATR, and Bollinger Band compression.
   - **Trend-Following Agent:** H4 pullback continuation entries with trailing ATR stops.
   - **Donchian Breakout Agent:** Catches explosive momentum moves out of tight consolidation channels.
   - **Mean-Reversion Agent:** Identifies overextended extremes in sideways markets.
   - **Signal Scorer:** Multi-factor scoring engine applying confluence bonuses and conflict resolutions.

2. **Hard-Coded Risk Guardian:**
   - Strictly limits risk to **1% of account equity per trade**.
   - Built-in **cent account support** allowing proper position sizing on accounts as small as $100.
   - Maximum 3 concurrent trades with single-currency exposure limits.
   - **Automated 20% Drawdown Kill Switch** to prevent catastrophic account loss.

3. **Live Human News Desk & AI Assistant:**
   - Real-time institutional financial news feed (FXStreet & ForexFactory calendar).
   - One-click AI sentiment evaluator translating breaking news into structured bias ratings (-2 to +2).
   - Instant 1-click bias activation with automatic expiration timers.

4. **Instant Mobile Alerts via Telegram:**
   - Dispatches real-time notifications for trade executions, break-even stop adjustments, targets, and risk events directly to your phone.

5. **Single-Click UI Dashboard:**
   - Interactive Streamlit desktop application (`OPEN_APP.bat`) with a live pulsing heartbeat indicator, portfolio tracker, news desk, and built-in backtester.

---

## 🚀 Quick Start Guide

### Prerequisites
- **Windows 10/11**
- **Python 3.12+**
- **MetaTrader 5** (logged into your broker demo or live account)

### Setup & Installation
1. Clone this repository:
   ```bash
   git clone https://github.com/YOUR_USERNAME/trading-bot.git
   cd trading-bot
   ```
2. Double-click **`INSTALL.bat`** to install all required dependencies automatically.

3. Open **`config/settings.yaml`** to configure your account preferences and add your Telegram bot token.

4. Launch the application:
   - Double-click **`OPEN_APP.bat`** to start your interactive Control Center.
   - Click **🚀 START BOT** in the sidebar to activate autonomous market scanning.

---

## ⚠️ Disclaimer
*This software is provided for educational, research, and simulation purposes only. Trading financial markets carries significant risk of capital loss. Always test thoroughly on demo accounts before considering live deployment.*
