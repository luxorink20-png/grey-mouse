"""
ADAPTIVE_RISK_ENGINE_SIM — Dynamic position sizing overlay.

Multiplies the baseline 1-contract result by a confidence-based
multiplier.  DOES NOT change entry/exit logic or stop/target levels.
REVERSIBLE: purely multiplicative overlay on top of existing risk_engine.
"""
from __future__ import annotations
import numpy as np
from typing import Dict, Tuple


class AdaptiveRiskEngineSim:

    MAX_MULTIPLIER = 1.5
    MIN_MULTIPLIER = 0.5

    def __init__(self, base_risk_pct: float = 2.0, max_daily_dd_pct: float = 0.20):
        self.base_risk_pct = base_risk_pct
        self.max_daily_dd_pct = max_daily_dd_pct

    def size_multiplier(self,
                        confidence: float,
                        current_drawdown_pct: float,
                        recent_win_rate: float) -> Tuple[float, Dict]:
        """
        Return a multiplier (MIN–MAX) for position sizing.
        Production system uses 1 contract baseline; this multiplier
        is applied to the trade P&L in simulation to model adaptive sizing.
        """
        # Confidence: 0.5x at 0.0 confidence, 1.0x at 1.0 confidence
        conf_mult = 0.5 + confidence * 0.5

        # Drawdown scaling: reduce size linearly if in drawdown > 5%
        if current_drawdown_pct > 0.05:
            dd_factor = max(0.40, 1.0 - current_drawdown_pct * 2)
        else:
            dd_factor = 1.0

        # Win-rate tilt
        if recent_win_rate > 0.52:
            wr_factor = 1.10
        elif recent_win_rate < 0.38:
            wr_factor = 0.85
        else:
            wr_factor = 1.0

        raw = conf_mult * dd_factor * wr_factor
        final = float(np.clip(raw, self.MIN_MULTIPLIER, self.MAX_MULTIPLIER))

        return final, {
            "conf_mult": round(conf_mult, 3),
            "dd_factor": round(dd_factor, 3),
            "wr_factor": round(wr_factor, 3),
            "raw": round(raw, 3),
            "final": final,
        }

    def kill_switch(self,
                    current_drawdown_pct: float,
                    session_trade_count: int,
                    max_trades: int = 10) -> Dict:
        """Hard stop checks mirroring production risk_engine limits."""
        if current_drawdown_pct > self.max_daily_dd_pct:
            return {"allow": False, "reason": f"DD {current_drawdown_pct:.1%} > {self.max_daily_dd_pct:.1%} limit"}
        if session_trade_count >= max_trades:
            return {"allow": False, "reason": f"Daily trade limit ({max_trades}) reached"}
        return {"allow": True, "reason": "OK"}
