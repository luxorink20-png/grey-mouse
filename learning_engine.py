# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — learning_engine.py
#  Adaptive Learning Engine v2.0
#
#  CAMBIOS v2.0:
#  ─ Aprende por régimen de sesión (TREND/RANGE/etc)
#  ─ Aprende qué breakout_types funcionan en cada régimen
#  ─ Aprende qué continuation setups funcionan en TREND
#  ─ Mínimo 30 muestras antes de modificar pesos (era 20)
#  ─ Ajuste conservador: máximo ±10 por setup (era ±8)
#  ─ JSON persistente por sesión
# ╚══════════════════════════════════════════════════════════════════╝

import json
import os
import shutil
from collections import defaultdict
from log_config import get_logger

_log = get_logger("learning_engine")
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LearningAdjustment:
    adjustment:  float = 0.0
    n_samples:   int   = 0
    win_rate:    float = 0.0
    confidence:  float = 0.0


class LearningEngine:
    """
    Motor de aprendizaje adaptativo v2.0 — OBSERVER ROLE

    Registra trades y aprende qué combinaciones funcionan.
    No modifica pesos hasta tener 30 muestras por setup.

    Observer contract:
      - register() is called on every closed trade (engine.py, replay_feed.py)
      - get_adjustment() returns a score delta (±10 pts max) once ≥30 samples
      - The adjustment is NOT currently wired into confluence/validator in the
        live engine — it is available for future integration (Wave 2/3).
      - force_analyze() prints a summary report on session shutdown.
    """

    MIN_SAMPLES        = 30    # era 20
    MIN_SAMPLES_STRONG = 60    # para ajuste fuerte
    MAX_ADJUSTMENT     = 10.0  # era 8.0
    LOG_FILE           = "learning_data.json"

    def __init__(self, log_dir: str = "logs"):
        self._log_dir   = log_dir
        self._log_path  = os.path.join(log_dir, self.LOG_FILE)
        self._data:     dict = defaultdict(lambda: {"wins": 0, "losses": 0, "total": 0})
        self._adjustments: dict = {}
        self._regime_data: dict = defaultdict(
            lambda: {"wins": 0, "losses": 0, "total": 0}
        )
        self._bq_regime_data: dict = defaultdict(
            lambda: {"wins": 0, "losses": 0, "total": 0}
        )
        self._load()

    def register(self, event: str, zone: str, narrative: str,
                 result: str, score: int, direction: str,
                 session_regime: str = "", breakout_type: str = "",
                 continuation_quality: str = "") -> None:
        """Registra un trade completado."""
        is_win = result == "WIN"

        # Key compuesto principal
        key = f"{event}_{zone}"
        self._data[key]["total"] += 1
        if is_win:
            self._data[key]["wins"] += 1
        else:
            self._data[key]["losses"] += 1

        # Por régimen
        if session_regime:
            rk = session_regime
            self._regime_data[rk]["total"] += 1
            if is_win:
                self._regime_data[rk]["wins"] += 1

        # Por breakout_type + régimen
        if breakout_type and session_regime:
            brk = f"{breakout_type}_{session_regime}"
            self._bq_regime_data[brk]["total"] += 1
            if is_win:
                self._bq_regime_data[brk]["wins"] += 1

        # Por narrativa
        narr_key = f"NARR_{narrative}"
        self._data[narr_key]["total"] += 1
        if is_win:
            self._data[narr_key]["wins"] += 1

        # Por continuation quality
        if continuation_quality:
            cont_key = f"CONT_{continuation_quality}"
            self._data[cont_key]["total"] += 1
            if is_win:
                self._data[cont_key]["wins"] += 1

        self._save()

    def get_adjustment(self, event: str, zone: str,
                       narrative: str = "",
                       session_regime: str = "",
                       breakout_type: str = "") -> float:
        """
        Retorna ajuste de score basado en historial.
        Solo si hay >= MIN_SAMPLES trades para ese setup.
        """
        key = f"{event}_{zone}"
        d   = self._data.get(key, {"wins": 0, "total": 0})

        if d["total"] < self.MIN_SAMPLES:
            return 0.0

        wr = d["wins"] / d["total"]

        # Rango neutral 40-60% = sin ajuste
        if 0.40 <= wr <= 0.60:
            return 0.0

        # Ajuste conservador
        if d["total"] >= self.MIN_SAMPLES_STRONG:
            max_adj = self.MAX_ADJUSTMENT
        else:
            max_adj = self.MAX_ADJUSTMENT * 0.6

        if wr > 0.60:
            return round(min((wr - 0.60) * max_adj * 2.5, max_adj), 1)
        else:
            return round(max((wr - 0.40) * max_adj * 2.5, -max_adj), 1)

    def get_regime_quality(self, session_regime: str) -> str:
        """
        Retorna calidad del régimen basada en historial.
        GOOD / NEUTRAL / BAD
        """
        d = self._regime_data.get(session_regime, {"wins": 0, "total": 0})
        if d["total"] < self.MIN_SAMPLES:
            return "NEUTRAL"
        wr = d["wins"] / d["total"]
        if wr >= 0.55:   return "GOOD"
        if wr <= 0.35:   return "BAD"
        return "NEUTRAL"

    def get_breakout_regime_quality(self, breakout_type: str,
                                     session_regime: str) -> str:
        """Retorna si un tipo de breakout funciona en un régimen dado."""
        key = f"{breakout_type}_{session_regime}"
        d   = self._bq_regime_data.get(key, {"wins": 0, "total": 0})
        if d["total"] < self.MIN_SAMPLES:
            return "UNKNOWN"
        wr = d["wins"] / d["total"]
        if wr >= 0.55:   return "WORKS"
        if wr <= 0.35:   return "FAILS"
        return "NEUTRAL"

    def force_analyze(self) -> None:
        """Genera reporte del learning engine."""
        total_records = sum(d["total"] for d in self._data.values())
        confiables    = sum(1 for d in self._data.values()
                            if d["total"] >= self.MIN_SAMPLES)
        pendientes    = sum(1 for d in self._data.values()
                            if d["total"] < self.MIN_SAMPLES)

        print("\n── LEARNING ENGINE REPORT ──────────────────────")
        print(f"  Registros totales  : {total_records}")
        print(f"  Setups analizados  : {len(self._data)}")
        print(f"  Setups confiables  : {confiables} (≥{self.MIN_SAMPLES} trades)")
        print(f"  Setups pendientes  : {pendientes} (<{self.MIN_SAMPLES} trades)")

        if confiables > 0:
            print(f"\n  SETUPS CONFIABLES:")
            for key, d in self._data.items():
                if d["total"] >= self.MIN_SAMPLES:
                    wr = round(d["wins"] / d["total"] * 100, 1)
                    adj = self.get_adjustment(*key.split("_", 1))
                    print(f"    {key:<30} WR={wr}%  n={d['total']}  adj={'+' if adj>=0 else ''}{adj}")
        else:
            print(f"\n  Sin setups confiables aún (necesita {self.MIN_SAMPLES}+ trades por setup)")

        # Régimen report
        regime_confiables = {k: d for k, d in self._regime_data.items()
                             if d["total"] >= self.MIN_SAMPLES}
        if regime_confiables:
            print(f"\n  REGÍMENES APRENDIDOS:")
            for regime, d in regime_confiables.items():
                wr  = round(d["wins"] / d["total"] * 100, 1)
                cal = self.get_regime_quality(regime)
                print(f"    {regime:<20} WR={wr}%  n={d['total']}  quality={cal}")

        print("────────────────────────────────────────────────\n")

    def get_best_setups(self, min_n: int = None) -> list:
        mn = min_n or self.MIN_SAMPLES
        result = []
        for key, d in self._data.items():
            if d["total"] >= mn and d["total"] > 0:
                wr = d["wins"] / d["total"]
                if wr >= 0.55:
                    result.append({"key": key, "wr": round(wr, 3),
                                   "n": d["total"]})
        return sorted(result, key=lambda x: x["wr"], reverse=True)

    def get_worst_setups(self, min_n: int = None) -> list:
        mn = min_n or self.MIN_SAMPLES
        result = []
        for key, d in self._data.items():
            if d["total"] >= mn and d["total"] > 0:
                wr = d["wins"] / d["total"]
                if wr <= 0.40:
                    result.append({"key": key, "wr": round(wr, 3),
                                   "n": d["total"]})
        return sorted(result, key=lambda x: x["wr"])

    def _save(self) -> None:
        try:
            os.makedirs(self._log_dir, exist_ok=True)
            payload = {
                "data":          dict(self._data),
                "regime_data":   dict(self._regime_data),
                "bq_regime":     dict(self._bq_regime_data),
            }
            tmp_path = self._log_path + ".tmp"
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            shutil.move(tmp_path, self._log_path)   # atomic rename — safe on crash
        except Exception as e:
            _log.error("LearningEngine save failed: %s", e)

    def _load(self) -> None:
        try:
            if not os.path.exists(self._log_path):
                return
            with open(self._log_path, encoding="utf-8") as f:
                payload = json.load(f)
            for k, v in payload.get("data", {}).items():
                self._data[k] = v
            for k, v in payload.get("regime_data", {}).items():
                self._regime_data[k] = v
            for k, v in payload.get("bq_regime", {}).items():
                self._bq_regime_data[k] = v
        except json.JSONDecodeError as e:
            _log.error(
                "learning_data.json is corrupt (%s) — starting with empty state. "
                "Backup saved to %s.bak", e, self._log_path
            )
            try:
                shutil.copy(self._log_path, self._log_path + ".bak")
            except Exception:
                pass
        except Exception as e:
            _log.error("LearningEngine load failed: %s", e)
