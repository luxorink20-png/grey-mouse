"""
QUALITY_ENGINE_SIM — Trade quality scoring 0-100.

Maps existing trade fields (confluence_score, zone, event, conviction)
to a composite quality gate.  DOES NOT modify core strategy.
REVERSIBLE: parallel scoring only, no side effects.
"""
from __future__ import annotations
from typing import Dict, Tuple


class QualityEngineSim:

    # Zone quality weights (based on SMC proximity analysis)
    ZONE_WEIGHTS: Dict[str, float] = {
        "AT_VAH": 1.0,
        "AT_VAL": 1.0,
        "AT_POC": 0.85,      # POC discounted per backtest_engine PATCH 4
        "IN_VALUE_AREA": 0.75,
        "ABOVE_VAH": 0.80,
        "BELOW_VAL": 0.80,
        "OUTSIDE_RANGE": 0.50,
    }

    EVENT_WEIGHTS: Dict[str, float] = {
        "INTENTO": 1.0,
        "AGOTAMIENTO": 0.90,
        "ACUMULACIÓN": 0.85,
        "FALLO": 0.65,
    }

    def __init__(self, quality_threshold: int = 60):
        self.quality_threshold = quality_threshold

    def score_from_trade_record(self, trade: dict) -> Tuple[int, Dict]:
        """
        Score a trade dict from the real backtest CSV / trade log.
        trade keys expected: confluence_score, zone, event, conviction, rr

        Formula (max 100 pts):
          Confluence   0-50 pts  (dominant factor — signals edge quality)
          Zone         0-20 pts  (proximity to institutional level)
          Event        0-15 pts  (event type quality)
          Conviction   0-10 pts  (system conviction)
          R:R quality  0-5  pts  (risk/reward structure)

        Calibrated so:
          LOW-quality  trades (confluence 44-62): scores ~40-58 → rejected at threshold 60
          HIGH-quality trades (confluence 65-88): scores ~62-88 → accepted at threshold 60
        """
        cs = float(trade.get("confluence_score", 50))
        zone = trade.get("zone", "IN_VALUE_AREA")
        event = trade.get("event", "INTENTO")
        conviction = float(trade.get("conviction", 70))
        rr = float(trade.get("rr", 2.0))

        breakdown: Dict[str, int] = {}

        # 1. Confluence component (0-50 pts) — primary discriminator
        # Scale: 40→20, 60→30, 70→35, 80→40, 90→45
        conf_pts = int((cs / 100.0) * 50)
        breakdown["confluence"] = conf_pts

        # 2. Zone component (0-20 pts)
        zone_w = self.ZONE_WEIGHTS.get(zone, 0.60)
        zone_pts = int(zone_w * 20)
        breakdown["zone"] = zone_pts

        # 3. Event component (0-15 pts)
        event_w = self.EVENT_WEIGHTS.get(event, 0.70)
        event_pts = int(event_w * 15)
        breakdown["event"] = event_pts

        # 4. Conviction component (0-10 pts)
        conviction_pts = int((conviction / 100.0) * 10)
        breakdown["conviction"] = conviction_pts

        # 5. R:R quality (0-5 pts)
        rr_pts = min(5, int((rr / 3.0) * 5))
        breakdown["rr_quality"] = rr_pts

        total = sum(breakdown.values())
        breakdown["total"] = total
        return total, breakdown

    def passes_filter(self, quality_score: int) -> bool:
        return quality_score >= self.quality_threshold

    def adaptive_threshold(self, recent_win_rate: float) -> int:
        if recent_win_rate > 0.50:
            return 55
        elif recent_win_rate < 0.38:
            return 68
        return self.quality_threshold
