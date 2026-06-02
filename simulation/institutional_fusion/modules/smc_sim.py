"""
SMC_ENGINE_SIM — Smart Money Concepts confluence detection.

Maps existing zone/event/narrative fields to SMC structure labels.
Does NOT parse raw price bars (no historical OHLCV available per session);
uses semantic trade metadata already computed by the production engine.
REVERSIBLE: parallel detection, no side effects.
"""
from __future__ import annotations
from typing import Dict, Tuple


class SMCEngineSim:

    # GIBBZ event → SMC label mapping
    _EVENT_TO_SMC = {
        "INTENTO":    {"sweep": False, "bos": True,  "ob": False},
        "AGOTAMIENTO":{"sweep": True,  "bos": False, "ob": True},
        "ACUMULACIÓN":{"sweep": False, "bos": False, "ob": True},
        "FALLO":      {"sweep": True,  "bos": False, "ob": False},
    }

    _ZONE_FVG = {
        "AT_VAH": True,
        "AT_VAL": True,
        "AT_POC": False,
        "ABOVE_VAH": True,
        "BELOW_VAL": True,
        "IN_VALUE_AREA": False,
        "OUTSIDE_RANGE": False,
    }

    def analyse_trade(self, trade: dict) -> Tuple[float, Dict]:
        """
        Return SMC confluence score (0-1) from trade metadata.
        """
        event = trade.get("event", "INTENTO")
        zone = trade.get("zone", "IN_VALUE_AREA")
        narrative = trade.get("narrative", "")
        was_trap = int(trade.get("was_trap", 0) or 0)

        smc = self._EVENT_TO_SMC.get(event, {"sweep": False, "bos": False, "ob": False})

        sweep = smc["sweep"] or was_trap == 1
        bos   = smc["bos"]
        ob    = smc["ob"]
        fvg   = self._ZONE_FVG.get(zone, False)

        active = sum([sweep, bos, ob, fvg])
        score = active / 4.0

        return score, {
            "sweep": sweep,
            "bos": bos,
            "ob": ob,
            "fvg": fvg,
            "active_signals": active,
        }
