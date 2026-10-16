"""
Database layer for trade logging, signals, news desk, and performance tracking.
Uses SQLite for zero-configuration, robust local persistence.
"""

import sqlite3
from pathlib import Path
from typing import Dict, Any, List, Optional
from datetime import datetime


DB_PATH = Path(__file__).resolve().parent.parent / "data" / "trading_journal.db"


def init_db(db_path: Path = DB_PATH) -> None:
    """Initialize SQLite database and create tables if they do not exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()

        # 1. Signals table (logs all generated signals, accepted or rejected)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                direction TEXT NOT NULL,
                entry REAL NOT NULL,
                stop_loss REAL NOT NULL,
                take_profit REAL,
                confidence REAL NOT NULL,
                regime TEXT NOT NULL,
                score REAL NOT NULL,
                status TEXT NOT NULL,  -- 'APPROVED', 'REJECTED_BY_RISK', 'REJECTED_BY_SCORE'
                rejection_reason TEXT,
                details_json TEXT
            )
        """)

        # 2. Trades table (orders placed, fills, exits, P&L in USD and R)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticket INTEGER,
                symbol TEXT NOT NULL,
                strategy TEXT NOT NULL,
                regime TEXT NOT NULL,
                direction TEXT NOT NULL,
                lots REAL NOT NULL,
                entry_time TEXT NOT NULL,
                entry_price REAL NOT NULL,
                initial_sl REAL NOT NULL,
                initial_tp REAL,
                risk_usd REAL NOT NULL,
                exit_time TEXT,
                exit_price REAL,
                exit_reason TEXT,  -- 'SL', 'TP', 'TRAILING_SL', 'REGIME_CHANGE', 'TIME_EXIT'
                pnl_usd REAL,
                pnl_r REAL,
                swap_paid REAL DEFAULT 0.0,
                commission_paid REAL DEFAULT 0.0,
                notes TEXT
            )
        """)

        # 3. News Desk table (human inputs)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS news_bias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                asset TEXT NOT NULL, -- e.g. 'USD', 'EUR', 'XAU', 'GLOBAL'
                bias INTEGER NOT NULL, -- -2 (Strong Bearish) to +2 (Strong Bullish)
                expires_at TEXT NOT NULL,
                note TEXT
            )
        """)

        # 4. Blackouts & Emergency halt table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS blackout_windows (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                asset TEXT NOT NULL,
                start_time TEXT NOT NULL,
                end_time TEXT NOT NULL,
                event_name TEXT
            )
        """)

        # 5. Account equity snapshots (for drawdown tracking)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS equity_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                balance REAL NOT NULL,
                equity REAL NOT NULL,
                margin REAL NOT NULL,
                free_margin REAL NOT NULL,
                drawdown_pct REAL NOT NULL
            )
        """)
        conn.commit()


def log_signal(signal: Dict[str, Any], db_path: Path = DB_PATH) -> int:
    """Insert a generated signal into the database."""
    init_db(db_path)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO signals (
                timestamp, symbol, strategy, direction, entry, stop_loss,
                take_profit, confidence, regime, score, status, rejection_reason, details_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal.get('timestamp', datetime.utcnow().isoformat()),
            signal['symbol'],
            signal['strategy'],
            signal['direction'],
            signal['entry'],
            signal['stop_loss'],
            signal.get('take_profit'),
            signal.get('confidence', 0.0),
            signal.get('regime', 'UNKNOWN'),
            signal.get('score', 0.0),
            signal.get('status', 'PENDING'),
            signal.get('rejection_reason', ''),
            str(signal)
        ))
        conn.commit()
        return cursor.lastrowid


def get_symbol_news_modifier(symbol: str, direction: str, db_path: Path = DB_PATH) -> tuple:
    """
    Computes a news multiplier (0.70 to 1.30) based on active human news biases.
    Returns: (multiplier: float, summary_note: str)
    """
    if not db_path.exists():
        return 1.0, "Neutral"
    try:
        now_iso = datetime.utcnow().isoformat()
        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            rows = cur.execute("""
                SELECT asset, bias, note FROM news_bias 
                WHERE expires_at > ? 
                ORDER BY id DESC
            """, (now_iso,)).fetchall()

        if not rows:
            return 1.0, "Neutral"

        s = symbol.upper().replace(".C", "").replace("M", "").replace(".RAW", "")
        base = s[:3] if len(s) >= 6 else s
        quote = s[3:6] if len(s) >= 6 else ""
        if "XAU" in s or "GOLD" in s:
            base, quote = "XAU", "USD"

        multiplier = 1.0
        notes = []

        for asset, bias, note in rows:
            asset = asset.upper()
            if asset == base:
                # If Long base currency and Bullish bias: boost score!
                mult = 1.0 + (bias * 0.10) if direction == "BUY" else 1.0 - (bias * 0.10)
                multiplier *= mult
                notes.append(f"{base} bias ({bias:+d})")

            elif asset == quote:
                # If Long base currency and Bullish quote currency: penalize score!
                mult = 1.0 - (bias * 0.10) if direction == "BUY" else 1.0 + (bias * 0.10)
                multiplier *= mult
                notes.append(f"{quote} bias ({bias:+d})")

        final_mult = round(max(0.60, min(1.40, multiplier)), 2)
        summary = ", ".join(notes) if notes else "Neutral"
        return final_mult, summary
    except Exception:
        return 1.0, "Neutral"


def delete_news_bias(bias_id: int, db_path: Path = DB_PATH) -> bool:
    """Removes a specific bias record by ID."""
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM news_bias WHERE id = ?", (bias_id,))
            conn.commit()
            return True
    except Exception:
        return False


def clear_all_news_biases(db_path: Path = DB_PATH) -> bool:
    """Clears all human news biases."""
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(db_path) as conn:
            cur = conn.cursor()
            cur.execute("DELETE FROM news_bias")
            conn.commit()
            return True
    except Exception:
        return False
