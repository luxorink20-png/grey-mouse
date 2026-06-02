"""
ORDERFLOW_ENGINE_SIM — Simulated order-flow imbalance scoring.

Uses existing bar fields (delta, ask_volume, bid_volume) if available,
otherwise derives imbalance from OHLCV.  Future: replace with real
CME Level-2 feed (Rithmic).  REVERSIBLE: pure simulation.
"""
from __future__ import annotations
import numpy as np
from collections import deque
from typing import Dict


class OrderFlowEngineSim:

    def __init__(self, lookback: int = 20):
        self.lookback = lookback
        self._imbalance_history: deque = deque(maxlen=lookback)

    def score_from_bar(self, bar: dict) -> Dict:
        """
        Derive order-flow metrics from a bar dict.
        Accepts real delta fields if present; otherwise synthesises from price.
        """
        high = float(bar.get("high", bar.get("entry_price", 0)) or 0)
        low  = float(bar.get("low",  bar.get("stop", 0)) or 0)
        close = float(bar.get("close", bar.get("entry_price", 0)) or 0)
        volume = float(bar.get("volume", bar.get("bars_held", 10)) or 10)
        delta = float(bar.get("delta", 0) or 0)

        price_range = high - low if high != low else 1.0
        close_pos = (close - low) / price_range  # 0–1

        if delta != 0:
            # Real delta available
            imbalance = np.clip(delta / max(volume, 1), -1.0, 1.0)
        else:
            # Synthetic: infer from close position
            imbalance = float(np.clip((close_pos - 0.5) * 2, -1.0, 1.0))

        self._imbalance_history.append(imbalance)
        avg_imb = float(np.mean(list(self._imbalance_history)))

        confidence = min(1.0, abs(imbalance) * 1.5)

        return {
            "imbalance": imbalance,
            "imbalance_avg": avg_imb,
            "confidence": confidence,
            "direction": "bullish" if imbalance > 0 else "bearish",
        }

    def is_confluent(self, metrics: Dict, trade_direction: str) -> bool:
        """True when order flow aligns with trade direction."""
        if trade_direction == "LONG":
            return metrics["imbalance"] > 0.05
        if trade_direction == "SHORT":
            return metrics["imbalance"] < -0.05
        return False
