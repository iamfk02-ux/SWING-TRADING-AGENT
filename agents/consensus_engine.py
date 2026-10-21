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
            tf_txt = f" [{sig.get('timeframe')}]" if sig.get('timeframe') else ""
            return sig, f"Uncontested setup: {sig['strategy']} {sig['direction']}{tf_txt} (Confidence: {sig.get('confidence', 0):.2f})"

        # Check for directional conflict
        buys = [s for s in signals if s.get("direction") == "BUY"]
        sells = [s for s in signals if s.get("direction") == "SELL"]

        if buys and sells:
            buy_desc = ", ".join(f"{s['strategy']}({s.get('timeframe', 'D1')})" for s in buys)
            sell_desc = ", ".join(f"{s['strategy']}({s.get('timeframe', 'D1')})" for s in sells)
            return None, f"CONFLICT NEUTRALIZED on {symbol}: Directional disagreement (BUY from {buy_desc} vs SELL from {sell_desc}). Standing aside."

        # Agreement across multiple strategies/timeframes in the same direction!
        chosen_dir = "BUY" if buys else "SELL"
        active_signals = buys if buys else sells
        signals_desc = [f"{s['strategy']}({s.get('timeframe', 'D1')})" for s in active_signals]

        # Multi-Horizon Arbitration:
        # Priority 1: Highest confidence score wins.
        # Priority 2: Tiebreaker -> H4 trumps H1 (weight: H4=2, H1=1, other=0).
        def _arbitration_rank(s: Dict[str, Any]):
            conf = float(s.get("confidence", 0.0))
            tf = str(s.get("timeframe", "")).upper()
            tf_weight = 2 if tf == "H4" else (1 if tf == "H1" else 0)
            return (conf, tf_weight)

        best_sig = max(active_signals, key=_arbitration_rank)

        # Multi-Timeframe / Multi-Agent Confluence Bonus
        timeframes_present = set(str(s.get("timeframe", "")).upper() for s in active_signals if s.get("timeframe"))
        bonus = 0.0
        confluence_labels = []

        if len(active_signals) > 1:
            bonus += 0.05
            confluence_labels.append("Multi-Strategy Agreement")

        if len(timeframes_present) > 1:
            bonus += 0.10
            confluence_labels.append("Dual-Horizon Alignment (H4 + H1)")

        original_conf = float(best_sig.get("confidence", 0.6))
        confluence_conf = min(1.0, round(original_conf + bonus, 2))
        best_sig["confidence"] = confluence_conf

        tf_tag = best_sig.get("timeframe", "H4")
        bonus_info = f" (+{bonus:.2f} bonus for {', '.join(confluence_labels)})" if bonus > 0 else ""
        best_sig["consensus_notes"] = f"Winner: {best_sig['strategy']} on {tf_tag}{bonus_info}. Aligned with: {', '.join(signals_desc)}"

        return best_sig, f"CONSENSUS REACHED: {chosen_dir} via {best_sig['strategy']}({tf_tag}){bonus_info} (Final Confidence: {confluence_conf:.2f})"
