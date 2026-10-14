"""
Signal Consensus Engine:
Receives candidate signals from all strategy agents (Trend, Breakout, Mean Reversion) for a symbol.
Performs conflict resolution and multi-strategy agreement:
- Neutralizes conflicting signals (e.g. Trend BUY vs Mean Reversion SELL on same candle).
- Boosts confluence scores when complementary strategies agree on direction.
- Selects the single highest quality validated setup for Risk Manager evaluation.
"""

from typing import List, Dict, Any, Optional, Tuple


class ConsensusEngine:
    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}

    def arbitrate(self, symbol: str, signals: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], str]:
        """
        Arbitrates candidate signals for a single symbol.
        Returns:
            (winning_signal, audit_verdict_str)
        """
        if not signals:
            return None, "No candidate signals generated."

        if len(signals) == 1:
            sig = signals[0]
            return sig, f"Uncontested setup: {sig['strategy']} {sig['direction']} (Confidence: {sig.get('confidence', 0):.2f})"

        # Check for directional conflict
        buys = [s for s in signals if s.get("direction") == "BUY"]
        sells = [s for s in signals if s.get("direction") == "SELL"]

        if buys and sells:
            buy_strats = ", ".join(s["strategy"] for s in buys)
            sell_strats = ", ".join(s["strategy"] for s in sells)
            return None, f"CONFLICT NEUTRALIZED: Strategy conflict on {symbol} (BUY from {buy_strats} vs SELL from {sell_strats}). Standing aside."

        # Agreement across multiple strategies in the same direction!
        chosen_dir = "BUY" if buys else "SELL"
        active_signals = buys if buys else sells
        strategies_agreed = [s["strategy"] for s in active_signals]

        # Pick the signal with highest confidence
        best_sig = max(active_signals, key=lambda s: s.get("confidence", 0.0))
        
        # Confluence bonus: +0.10 confidence when multiple strategies align!
        original_conf = best_sig.get("confidence", 0.6)
        confluence_conf = min(1.0, original_conf + 0.10)
        best_sig["confidence"] = confluence_conf
        best_sig["consensus_notes"] = f"Multi-Agent Agreement ({' + '.join(strategies_agreed)}) in direction {chosen_dir} (+0.10 confluence bonus)"

        return best_sig, f"CONSENSUS REACHED: {chosen_dir} by {' + '.join(strategies_agreed)} (Final Confidence: {confluence_conf:.2f})"
