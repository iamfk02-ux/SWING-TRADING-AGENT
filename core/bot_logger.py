"""
Bot Live Logger & State Tracker:
Provides real-time activity streaming, heartbeat persistence, and scanner matrix updates
for the Streamlit Control Center and user visibility.
"""

from pathlib import Path
from datetime import datetime
import json
import sys
from typing import List, Dict, Any, Optional

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LOG_FILE = DATA_DIR / "bot_live.log"
HEARTBEAT_FILE = DATA_DIR / "bot_heartbeat.json"
SCANNER_FILE = DATA_DIR / "scanner_matrix.json"

MAX_LOG_LINES = 1500


def append_live_log(message: str, level: str = "INFO") -> None:
    """
    Appends a timestamped log line to both stdout and data/bot_live.log.
    Keeps the log file trimmed to MAX_LOG_LINES.
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted_msg = f"[{ts}] [{level}] {message}"

    # Print to console/stdout with immediate flush and safe Windows encoding
    try:
        print(formatted_msg, flush=True)
    except (UnicodeEncodeError, Exception):
        try:
            safe_msg = formatted_msg.encode("ascii", errors="replace").decode("ascii")
            print(safe_msg, flush=True)
        except Exception:
            pass

    try:
        lines = []
        if LOG_FILE.exists():
            try:
                lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                lines = []

        lines.append(formatted_msg)
        if len(lines) > MAX_LOG_LINES:
            lines = lines[-MAX_LOG_LINES:]

        LOG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8", errors="ignore")
    except Exception as e:
        print(f"[LOGGER ERROR] Failed to write to {LOG_FILE}: {e}", file=sys.stderr)


def update_heartbeat(
    status: str,
    cycle_count: int,
    last_cycle_time: str,
    next_cycle_time: str,
    seconds_remaining: int,
    scanned_count: int,
    active_positions: int,
    balance: float,
    equity: float,
    timeframe: str = "H1",
    details: str = ""
) -> None:
    """Atomically records current bot heartbeat and countdown to next scan."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    data = {
        "status": status,
        "cycle_count": cycle_count,
        "last_cycle_time": last_cycle_time,
        "next_cycle_time": next_cycle_time,
        "seconds_remaining": max(0, int(seconds_remaining)),
        "scanned_count": scanned_count,
        "active_positions": active_positions,
        "balance": round(balance, 2),
        "equity": round(equity, 2),
        "timeframe": timeframe,
        "details": details,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }
    try:
        tmp_file = DATA_DIR / "bot_heartbeat.tmp"
        tmp_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        tmp_file.replace(HEARTBEAT_FILE)
    except Exception:
        pass


def update_scanner_matrix(rows: List[Dict[str, Any]]) -> None:
    """Saves the latest scan inspection results of all watched pairs."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total_pairs": len(rows),
        "pairs": rows
    }
    try:
        tmp_file = DATA_DIR / "scanner_matrix.tmp"
        tmp_file.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        tmp_file.replace(SCANNER_FILE)
    except Exception:
        pass


def get_live_logs(limit: int = 100) -> str:
    """Returns the most recent log lines as a single string."""
    if not LOG_FILE.exists():
        return "No bot activity logged yet. Start the bot or run an instant scan."
    try:
        lines = LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()
        return "\n".join(lines[-limit:])
    except Exception as e:
        return f"Error reading logs: {e}"


def get_heartbeat() -> Optional[Dict[str, Any]]:
    """Reads the current heartbeat if present."""
    if not HEARTBEAT_FILE.exists():
        return None
    try:
        return json.loads(HEARTBEAT_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def get_scanner_matrix() -> Optional[Dict[str, Any]]:
    """Reads the current scanner matrix."""
    if not SCANNER_FILE.exists():
        return None
    try:
        return json.loads(SCANNER_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None
