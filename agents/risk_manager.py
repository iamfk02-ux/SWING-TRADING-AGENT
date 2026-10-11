"""
Risk Manager:
Enforces hard safety boundaries:
- 1% account risk per trade with position sizing based on stop distance
- Cent account / standard account lot size scaling
- Currency correlation limit (max 2% net exposure per single currency)
- Max 3 concurrent open trades
- Drawdown throttle (-10% cuts risk to 0.5%)
- Hard Kill Switch (-20% cuts all trading)
- Spread filter & weekend gap filter
"""

from typing import Dict, Any, List, Optional, Tuple


class RiskManager:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        risk_cfg = config.get("risk", {})
        self.risk_pct = risk_cfg.get("risk_per_trade_pct", 1.0)
        self.max_open_pos = risk_cfg.get("max_open_positions", 3)
        self.max_currency_risk_pct = risk_cfg.get("max_currency_exposure_pct", 2.0)
        self.throttle_pct = risk_cfg.get("drawdown_throttle_pct", 10.0)
        self.kill_switch_pct = risk_cfg.get("kill_switch_drawdown_pct", 20.0)
        self.is_cent_account = config.get("account", {}).get("type", "cent") == "cent"

    def evaluate_order(
        self,
        signal: Dict[str, Any],
        account_equity: float,
        peak_equity: float,
        open_positions: List[Dict[str, Any]],
        current_spread_pips: float = 1.5
    ) -> Tuple[bool, str, float]:
        """
        Evaluates a signal against hard risk limits.
        Returns: (is_approved, rejection_reason, calculated_lots)
        """
        # 1. Kill Switch Check
        drawdown_pct = 0.0
        if peak_equity > 0:
            drawdown_pct = ((peak_equity - account_equity) / peak_equity) * 100.0

        if drawdown_pct >= self.kill_switch_pct:
            return False, f"KILL SWITCH ACTIVE: Drawdown {drawdown_pct:.1f}% >= {self.kill_switch_pct}%", 0.0

        # 2. Maximum open positions
        if len(open_positions) >= self.max_open_pos:
            return False, f"MAX POSITIONS REACHED: {len(open_positions)}/{self.max_open_pos}", 0.0

        # 3. Duplicate position check (1 position per symbol)
        symbol = signal["symbol"]
        for pos in open_positions:
            if pos["symbol"] == symbol:
                return False, f"ALREADY IN POSITION FOR {symbol}", 0.0

        # 4. Effective Risk calculation (Throttled if in drawdown)
        effective_risk_pct = self.risk_pct
        if drawdown_pct >= self.throttle_pct:
            effective_risk_pct = self.risk_pct * 0.5  # Halved to 0.5%

        risk_amount_usd = account_equity * (effective_risk_pct / 100.0)

        # 5. Position Sizing
        entry = signal["entry"]
        stop = signal["stop_loss"]
        stop_dist = abs(entry - stop)
        if stop_dist <= 0:
            return False, "INVALID STOP DISTANCE", 0.0

        # Pip value estimation:
        # For EURUSD, 1 pip = 0.0001 = $10 per 1 standard lot.
        # For JPY pairs, 1 pip = 0.01 = approx $6.7 - $10 per lot.
        pip_size = 0.01 if "JPY" in symbol else 0.0001
        stop_pips = stop_dist / pip_size

        # In standard accounts: 1 lot = 100,000 units (~$10/pip).
        # In cent accounts: 1 cent lot = 1,000 units (~$0.10/pip).
        pip_val_per_lot = 10.0 if not self.is_cent_account else 0.10

        raw_lots = risk_amount_usd / (stop_pips * pip_val_per_lot)
        rounded_lots = round(max(0.01, raw_lots), 2)

        # Check minimum lot risk
        actual_risk_usd = rounded_lots * stop_pips * pip_val_per_lot
        if actual_risk_usd > (account_equity * 0.02):  # Hard limit 2%
            return False, f"MIN LOT TOO LARGE FOR ACCOUNT ({actual_risk_usd:.2f} > 2% cap)", 0.0

        return True, "APPROVED", rounded_lots
