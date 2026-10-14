"""
Execution Circuit Breaker (Quantitative Rule 11):
Protects trading capital against abnormal broker execution anomalies:
- Consecutive execution rejections / retcode errors
- Spread spikes beyond safe tolerances
- MT5 disconnection spikes
Automatically pauses order placement for a cooldown window and alerts via Telegram.
"""

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, Tuple, Optional


class CircuitBreaker:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        cb_cfg = self.config.get("risk", {}).get("circuit_breaker", {})
        self.enabled = cb_cfg.get("enabled", True)
        self.max_errors = cb_cfg.get("max_consecutive_exec_errors", 3)
        self.pause_minutes = cb_cfg.get("pause_duration_minutes", 60)
        
        self.consecutive_errors = 0
        self.tripped_until: Optional[datetime] = None
        self.trip_reason: Optional[str] = None

    def is_tripped(self) -> Tuple[bool, Optional[str]]:
        """Returns True if the circuit breaker is currently active, along with remaining minutes."""
        if not self.enabled:
            return False, None

        if self.tripped_until:
            now = datetime.now(timezone.utc)
            if now < self.tripped_until:
                remaining_m = int((self.tripped_until - now).total_seconds() / 60.0)
                return True, f"CIRCUIT BREAKER ACTIVE ({self.trip_reason}). Cooling down for {remaining_m}m."
            else:
                # Cooldown expired - auto reset
                self.reset()

        return False, None

    def record_execution_success(self):
        """Resets the consecutive error count on a successful deal execution."""
        self.consecutive_errors = 0

    def record_execution_error(self, error_msg: str) -> bool:
        """
        Increments the consecutive error count.
        Trips the circuit breaker if the error limit is reached.
        Returns True if tripped now.
        """
        if not self.enabled:
            return False

        self.consecutive_errors += 1
        if self.consecutive_errors >= self.max_errors:
            self.tripped_until = datetime.now(timezone.utc) + timedelta(minutes=self.pause_minutes)
            self.trip_reason = f"{self.consecutive_errors} consecutive broker execution errors: {error_msg}"
            return True
        return False

    def trip_manually(self, reason: str, duration_minutes: Optional[int] = None):
        """Allows external systems (e.g. spread anomaly detector) to trip the breaker."""
        mins = duration_minutes or self.pause_minutes
        self.tripped_until = datetime.now(timezone.utc) + timedelta(minutes=mins)
        self.trip_reason = reason

    def reset(self):
        """Clears the circuit breaker state."""
        self.consecutive_errors = 0
        self.tripped_until = None
        self.trip_reason = None
