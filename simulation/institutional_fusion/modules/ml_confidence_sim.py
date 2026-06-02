"""
ML_CONFIDENCE_ENGINE_SIM — Synthetic confidence scores.

ML training is deferred; scores are derived from rolling performance
metrics that a real ML model would also use as features.
REVERSIBLE: mock scores replaceable with real model output later.
"""
from __future__ import annotations
import numpy as np
from collections import deque


class MLConfidenceEngineSim:

    def __init__(self, window: int = 20):
        self._window = window
        self._outcomes: deque = deque(maxlen=window)   # 1=win, 0=loss
        self._pnls: deque = deque(maxlen=window)
        self.consecutive_wins = 0
        self.consecutive_losses = 0

    def record(self, pnl: float, win: bool) -> None:
        self._outcomes.append(1 if win else 0)
        self._pnls.append(pnl)
        if win:
            self.consecutive_wins += 1
            self.consecutive_losses = 0
        else:
            self.consecutive_losses += 1
            self.consecutive_wins = 0

    def confidence(self,
                   quality_score: int,
                   smc_score: float,
                   equity_slope: float = 0.5) -> float:
        """
        Compute confidence 0-1 from rolling stats + quality + SMC.
        """
        wr = float(np.mean(list(self._outcomes))) if self._outcomes else 0.387
        recent_pnl = float(np.mean(list(self._pnls))) if self._pnls else 0.0

        # Normalise recent_pnl to 0-1 (baseline exp = 2.61)
        pnl_norm = np.clip(recent_pnl / 20.0, 0, 1)

        wr_component    = np.clip(wr / 0.50, 0, 1) * 0.30
        quality_comp    = (quality_score / 100.0) * 0.30
        smc_comp        = smc_score * 0.20
        equity_comp     = np.clip(equity_slope, 0, 1) * 0.10
        pnl_comp        = pnl_norm * 0.10

        total = wr_component + quality_comp + smc_comp + equity_comp + pnl_comp
        return float(np.clip(total, 0.0, 1.0))

    def label(self, score: float) -> str:
        if score >= 0.80: return "VERY HIGH"
        if score >= 0.60: return "HIGH"
        if score >= 0.40: return "MODERATE"
        if score >= 0.20: return "LOW"
        return "VERY LOW"

    def position_multiplier(self, score: float) -> float:
        """0.5x–1.0x based on confidence."""
        return round(0.5 + score * 0.5, 3)
