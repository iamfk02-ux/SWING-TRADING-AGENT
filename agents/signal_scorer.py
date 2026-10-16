"""
Signal Scorer Agent:
Blends strategy confidence with market regime alignment, historical edge, news bias, and carry modifier.
"""

from typing import Dict, Any, Optional


REGIME_FIT_TABLE = {
    "TREND": {
        "TRENDING_UP": 1.0,
        "TRENDING_DOWN": 1.0,
        "COMPRESSION": 0.0,
        "RANGING": 0.0,
        "HIGH_VOLATILITY": 0.5,
        "UNCLEAR": 0.0,
    },
    "BREAKOUT": {
        "TRENDING_UP": 0.5,
        "TRENDING_DOWN": 0.5,
        "COMPRESSION": 1.0,
        "RANGING": 0.3,
        "HIGH_VOLATILITY": 0.5,
        "UNCLEAR": 0.0,
    },
    "MEAN_REVERSION": {
        "TRENDING_UP": 0.0,
        "TRENDING_DOWN": 0.0,
        "COMPRESSION": 0.0,
        "RANGING": 1.0,
        "HIGH_VOLATILITY": 0.0,
        "UNCLEAR": 0.0,
    }
}


class SignalScorer:
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        scorer_cfg = config.get("scorer", {}) if config else {}
        self.min_score_threshold = scorer_cfg.get("min_score_threshold", 0.75)

    def score_signal(
        self,
        signal: Dict[str, Any],
        news_modifier: float = 1.0,
        carry_modifier: float = 1.0,
        edge_factor: float = 1.0
    ) -> float:
        """
        Score = Confidence * RegimeFit * EdgeFactor * NewsModifier * CarryModifier
        """
        strategy = signal.get("strategy", "TREND")
        regime = signal.get("regime", "UNCLEAR")
        confidence = signal.get("confidence", 0.6)

        regime_fit = REGIME_FIT_TABLE.get(strategy, {}).get(regime, 0.0)

        final_score = confidence * regime_fit * edge_factor * news_modifier * carry_modifier
        signal["score"] = round(final_score, 3)
        return signal["score"]

    def is_eligible(self, score: float) -> bool:
        return score >= self.min_score_threshold
