# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — edge_learning.py
#  Edge Learning System v1.0
#
#  Aprende automáticamente qué contextos tienen edge real.
#  Auto-penaliza setups con WR < 35% después de 20 muestras.
#  Auto-bloquea setups con WR < 20% después de 20 muestras.
# ╚══════════════════════════════════════════════════════════════════╝

import json
import os
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional, List


@dataclass
class EdgeRecord:
    wins:       int   = 0
    losses:     int   = 0
    total:      int   = 0
    total_pnl:  float = 0.0
    max_loss:   float = 0.0
    sum_sq_pnl: float = 0.0   # para calcular volatilidad

    @property
    def wr(self) -> float:
        return self.wins / self.total if self.total > 0 else 0.5

    @property
    def avg_pnl(self) -> float:
        return self.total_pnl / self.total if self.total > 0 else 0.0

    @property
    def expectancy(self) -> float:
        return self.avg_pnl

    def to_dict(self) -> dict:
        return {
            "wins": self.wins, "losses": self.losses,
            "total": self.total, "total_pnl": round(self.total_pnl, 2),
            "max_loss": round(self.max_loss, 2),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "EdgeRecord":
        r = cls()
        r.wins      = d.get("wins",      0)
        r.losses    = d.get("losses",    0)
        r.total     = d.get("total",     0)
        r.total_pnl = d.get("total_pnl", 0.0)
        r.max_loss  = d.get("max_loss",  0.0)
        return r


class EdgeLearningSystem:
    """
    Sistema de aprendizaje de edge v1.0

    Registra estadísticas por múltiples dimensiones:
    - breakout_type × regime
    - zone × event
    - narrative
    - environment
    - continuation_quality

    Auto-penaliza / auto-bloquea según historial.
    Mínimo 20 muestras para modificar comportamiento.
    """

    MIN_SAMPLES_PENALIZE = 20
    MIN_SAMPLES_BLOCK    = 20
    WR_PENALIZE_THRESH   = 0.35
    WR_BLOCK_THRESH      = 0.20
    LOG_FILE             = "edge_learning.json"

    def __init__(self, log_dir: str = "logs"):
        self._log_dir  = log_dir
        self._log_path = os.path.join(log_dir, self.LOG_FILE)
        self._records: dict = defaultdict(EdgeRecord)
        self._load()

    def update(self, result: str, pnl_pts: float,
               breakout_type: str  = "",
               session_regime: str = "",
               zone:           str = "",
               event:          str = "",
               narrative:      str = "",
               environment:    str = "",
               continuation_quality: str = "") -> None:
        """
        Registra un trade completado en todas las dimensiones.
        """
        is_win = result == "WIN"

        # Registrar en todas las dimensiones relevantes
        keys = []

        if breakout_type and session_regime:
            keys.append(f"BQ_{breakout_type}__{session_regime}")

        if zone and event:
            keys.append(f"ZE_{zone}__{event}")

        if narrative:
            keys.append(f"NARR__{narrative}")

        if environment:
            keys.append(f"ENV__{environment}")

        if continuation_quality:
            keys.append(f"CONT__{continuation_quality}")

        if session_regime:
            keys.append(f"REGIME__{session_regime}")

        if breakout_type:
            keys.append(f"BQ__{breakout_type}")

        for key in keys:
            r = self._records[key]
            r.total     += 1
            r.total_pnl += pnl_pts
            if is_win:
                r.wins += 1
            else:
                r.losses += 1
                r.max_loss = min(r.max_loss, pnl_pts)

        self._save()

    def get_edge_score(self, breakout_type: str  = "",
                       session_regime: str = "",
                       zone:           str = "",
                       event:          str = "",
                       narrative:      str = "",
                       environment:    str = "",
                       continuation_quality: str = "") -> int:
        """
        Retorna un score de edge histórico 0-100.
        0 = sin edge histórico / datos insuficientes
        100 = edge muy fuerte históricamente
        """
        scores = []

        # Buscar por dimensiones
        checks = [
            f"BQ_{breakout_type}__{session_regime}" if breakout_type and session_regime else None,
            f"ZE_{zone}__{event}"                   if zone and event else None,
            f"NARR__{narrative}"                    if narrative else None,
            f"ENV__{environment}"                   if environment else None,
            f"CONT__{continuation_quality}"         if continuation_quality else None,
            f"BQ__{breakout_type}"                  if breakout_type else None,
            f"REGIME__{session_regime}"             if session_regime else None,
        ]

        for key in checks:
            if key is None:
                continue
            r = self._records.get(key)
            if r is None or r.total < self.MIN_SAMPLES_PENALIZE:
                continue
            # Score basado en WR y expectancy
            wr_score   = int(r.wr * 100)
            exp_score  = min(100, max(0, int((r.avg_pnl + 5) * 10)))
            score      = int(wr_score * 0.6 + exp_score * 0.4)
            scores.append(score)

        if not scores:
            return 50   # sin datos = neutral

        return int(sum(scores) / len(scores))

    def get_score_adjustment(self, **kwargs) -> float:
        """
        Retorna ajuste de score basado en edge histórico.
        Positivo = bonus, negativo = penalización.
        """
        edge_sc = self.get_edge_score(**kwargs)
        if edge_sc == 50:
            return 0.0   # sin datos
        if edge_sc >= 65:
            return min(round((edge_sc - 50) * 0.15, 1), 8.0)
        if edge_sc <= 35:
            return max(round((edge_sc - 50) * 0.20, 1), -12.0)
        return 0.0

    def should_block(self, **kwargs) -> tuple:
        """
        Retorna (should_block: bool, reason: str).
        Bloquea si WR < 20% con >= MIN_SAMPLES.
        """
        checks = {
            "BQ_" + kwargs.get("breakout_type","") + "__" + kwargs.get("session_regime",""):
                (kwargs.get("breakout_type") and kwargs.get("session_regime")),
            "ENV__" + kwargs.get("environment",""):
                bool(kwargs.get("environment")),
            "CONT__" + kwargs.get("continuation_quality",""):
                bool(kwargs.get("continuation_quality")),
        }

        for key, valid in checks.items():
            if not valid:
                continue
            r = self._records.get(key)
            if r is None or r.total < self.MIN_SAMPLES_BLOCK:
                continue
            if r.wr < self.WR_BLOCK_THRESH:
                return True, f"Edge learning bloquea: {key} WR={round(r.wr*100,1)}%"

        return False, ""

    def should_penalize(self, **kwargs) -> tuple:
        """
        Retorna (penalty: int, reason: str).
        Penaliza score si WR < 35% con >= MIN_SAMPLES.
        """
        total_penalty = 0
        reasons       = []

        checks = [
            f"BQ_{kwargs.get('breakout_type','')}__{kwargs.get('session_regime','')}"
            if kwargs.get("breakout_type") and kwargs.get("session_regime") else None,
            f"REGIME__{kwargs.get('session_regime','')}"
            if kwargs.get("session_regime") else None,
        ]

        for key in checks:
            if not key:
                continue
            r = self._records.get(key)
            if r is None or r.total < self.MIN_SAMPLES_PENALIZE:
                continue
            if r.wr < self.WR_PENALIZE_THRESH:
                pen = min(int((self.WR_PENALIZE_THRESH - r.wr) * 60), 20)
                total_penalty += pen
                reasons.append(f"{key}:WR={round(r.wr*100,1)}%")

        return total_penalty, " | ".join(reasons)

    def recommend_filters(self) -> List[dict]:
        """
        Retorna lista de recomendaciones basadas en el historial.
        """
        recs = []
        for key, r in self._records.items():
            if r.total < self.MIN_SAMPLES_PENALIZE:
                continue
            wr = r.wr
            if wr < self.WR_BLOCK_THRESH:
                recs.append({
                    "key": key, "action": "BLOCK",
                    "wr": round(wr*100, 1), "n": r.total,
                    "avg_pnl": round(r.avg_pnl, 2),
                })
            elif wr < self.WR_PENALIZE_THRESH:
                recs.append({
                    "key": key, "action": "PENALIZE",
                    "wr": round(wr*100, 1), "n": r.total,
                    "avg_pnl": round(r.avg_pnl, 2),
                })
            elif wr >= 0.60:
                recs.append({
                    "key": key, "action": "PRIORITIZE",
                    "wr": round(wr*100, 1), "n": r.total,
                    "avg_pnl": round(r.avg_pnl, 2),
                })
        return sorted(recs, key=lambda x: x["wr"])

    def print_report(self) -> None:
        print("\n── EDGE LEARNING REPORT ───────────────────────")
        total = sum(r.total for r in self._records.values())
        print(f"  Total trades registrados : {total}")
        print(f"  Dimensiones aprendidas   : {len(self._records)}")

        recs = self.recommend_filters()
        if recs:
            print(f"\n  RECOMENDACIONES ({len(recs)}):")
            for rec in recs[:15]:
                icon = "🚫" if rec["action"]=="BLOCK" else "⚠" if rec["action"]=="PENALIZE" else "✓"
                print(f"    {icon} {rec['action']:<10} {rec['key']:<35} "
                      f"WR={rec['wr']:5.1f}%  n={rec['n']:3d}  "
                      f"avg={rec['avg_pnl']:+.2f}pts")
        else:
            print("  Sin datos suficientes para recomendaciones.")
        print("────────────────────────────────────────────────\n")

    def _save(self) -> None:
        try:
            os.makedirs(self._log_dir, exist_ok=True)
            payload = {k: v.to_dict() for k, v in self._records.items()}
            with open(self._log_path, "w") as f:
                json.dump(payload, f, indent=2)
        except Exception:
            pass

    def _load(self) -> None:
        try:
            if not os.path.exists(self._log_path):
                return
            with open(self._log_path) as f:
                payload = json.load(f)
            for k, v in payload.items():
                self._records[k] = EdgeRecord.from_dict(v)
        except Exception:
            pass