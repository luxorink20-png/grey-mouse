"""
confidence_engine.py — Institutional Fusion Wave 1
Rolling confidence scoring 0-1 based on recent trade performance.

Position multiplier range: 0.5x (low confidence) → 1.0x (high confidence).
This is a multiplicative overlay on top of RiskEngine's position_size —
it never increases beyond the base size (max multiplier = 1.0).

NOTE: ML model training is deferred to Wave 3. Scores are derived from
rolling performance metrics that a real ML model would also use as features.
"""
from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from typing import Optional
from log_config import get_logger

_log = get_logger("confidence_engine")


@dataclass
class ConfidenceResult:
    score:      float   # 0.0–1.0
    label:      str     # VERY LOW / LOW / MODERATE / HIGH / VERY HIGH
    multiplier: float   # position_size *= multiplier (0.5–1.0)
    trades_in_window: int

    def __str__(self) -> str:
        return (
            f"CONFIDENCE {self.label} "
            f"score={self.score:.2f} "
            f"mult={self.multiplier:.2f} "
            f"n={self.trades_in_window}"
        )


class ConfidenceEngine:
    """
    Computes trade-entry confidence from a rolling 20-trade window.

    Components:
      Win Rate         30% weight — primary performance signal
      Quality score    30% weight — current setup quality
      Drawdown         20% weight — reduces confidence in drawdown
      Momentum         20% weight — consecutive wins boost confidence

    Call `register_outcome()` after each completed trade to keep the
    rolling window current.  Call `score()` before each new trade open.
    """

    WINDOW = 20
    MIN_TRADES_FOR_SCALING = 5   # below this use neutral confidence

    def __init__(self) -> None:
        self._outcomes: deque[bool]  = deque(maxlen=self.WINDOW)
        self._pnls:     deque[float] = deque(maxlen=self.WINDOW)
        self._consecutive_wins  = 0
        self._consecutive_losses = 0
        self._peak_equity = 0.0
        self._equity      = 0.0

    # ── Public API ────────────────────────────────────────────────────

    def register_outcome(self, win: bool, pnl_pts: float) -> None:
        """Call once per completed trade (from engine.py closed_trade handler)."""
        self._outcomes.append(win)
        self._pnls.append(pnl_pts)
        self._equity += pnl_pts
        self._peak_equity = max(self._peak_equity, self._equity)

        if win:
            self._consecutive_wins  += 1
            self._consecutive_losses = 0
        else:
            self._consecutive_losses += 1
            self._consecutive_wins   = 0

    def score(self, quality_score: int) -> ConfidenceResult:
        """
        Compute confidence for the next trade entry.

        Args:
            quality_score: 0-100 from QualityEngine
        """
        n = len(self._outcomes)

        # Not enough history — return neutral (0.5 multiplier)
        if n < self.MIN_TRADES_FOR_SCALING:
            raw = 0.5
            return self._build_result(raw, n)

        win_rate = sum(self._outcomes) / n

        # Win-rate component (0-0.30)
        wr_comp = min(0.30, (win_rate / 0.50) * 0.30)

        # Quality component (0-0.30)
        q_comp = (quality_score / 100.0) * 0.30

        # Drawdown component (0-0.20): penalise if in drawdown
        dd = self._equity - self._peak_equity   # <= 0
        dd_pct = abs(dd) / max(abs(self._peak_equity) + 1.0, 1.0)
        dd_comp = max(0.0, 0.20 - dd_pct * 0.40)

        # Momentum component (0-0.20): consecutive wins boost
        momentum = min(self._consecutive_wins / 5.0, 1.0)
        mom_comp  = momentum * 0.20

        raw = wr_comp + q_comp + dd_comp + mom_comp

        _log.debug(
            "CONFIDENCE wr=%.0f%% wr_c=%.2f q_c=%.2f dd_c=%.2f mom_c=%.2f raw=%.2f",
            win_rate * 100, wr_comp, q_comp, dd_comp, mom_comp, raw,
        )

        return self._build_result(raw, n)

    # ── Internals ─────────────────────────────────────────────────────

    @staticmethod
    def _build_result(raw: float, n: int) -> ConfidenceResult:
        score = max(0.0, min(1.0, raw))
        # Multiplier: 0.5x at score 0.0, 1.0x at score 1.0
        mult  = round(0.5 + score * 0.5, 3)
        if   score >= 0.80: label = "VERY HIGH"
        elif score >= 0.60: label = "HIGH"
        elif score >= 0.40: label = "MODERATE"
        elif score >= 0.20: label = "LOW"
        else:               label = "VERY LOW"
        return ConfidenceResult(
            score             = round(score, 3),
            label             = label,
            multiplier        = mult,
            trades_in_window  = n,
        )
