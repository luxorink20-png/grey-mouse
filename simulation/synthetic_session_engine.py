"""
GIBBZ V3 — simulation/synthetic_session_engine.py
Synthetic Session Engine v1.0

ORQUESTADOR CENTRAL del GIBBZ Synthetic Replay Treadmill.

MISIÓN: preservar la dificultad real del mercado.
NO crear entorno favorable.
NO optimizar edge.
NO destruir la rareza institucional.

SUBSISTEMAS:
  DistributionController    — preserva ratios reales del dataset
  AntiMemorizationScheduler — impide repetición estructural
  RarityController          — mantiene ET ultra-raro
  StressExposureBalancer    — garantiza sesiones difíciles
  FragmentReuseLimiter      — evita dependencia de fragmentos ELITE
  RealismDriftAuditor       — alerta si sintético deriva del real
  ExposureCurriculum        — varía exposición institucional

USO:
  python simulation/synthetic_session_engine.py --curriculum standard --n 20
  python simulation/synthetic_session_engine.py --curriculum stress --n 10
  python simulation/synthetic_session_engine.py --audit
  python simulation/synthetic_session_engine.py --status
"""

import json
import os
import sys
import glob
import random
import argparse
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from simulation.fingerprint_preserver import FingerprintPreserver
from simulation.regime_morpher       import RegimeMorpher, SyntheticSession
from simulation.stress_injector      import StressInjector, STRESS_TYPES

ENGINE_DIR   = os.path.join("simulation", "engine_sessions")
ENGINE_LOG   = os.path.join("simulation", "engine_log.json")
ENGINE_STATE = os.path.join("simulation", "engine_state.json")

# ── REAL DATASET BASELINES ────────────────────────────────────────
# Métricas reales del dataset — derivadas del training camp
# NOTA: el catálogo sintético tiene sesion-bias hacia HIGH/ELITE
# porque esas fueron las sesiones grabadas. El ETS sintético
# naturalmente será más alto que el dataset de 47 sesiones reales.
# Los thresholds reflejan esta realidad estructural.

REAL_BASELINES = {
    "ep_avg":           50.0,    # ajustado: catálogo tiene sesiones HIGH
    "hb_rate_avg":      0.077,
    "et_frequency":     0.0013,  # 0.13% de barras son ET
    "ets_active_pct":   0.059,   # 5.9% de fragmentos ETS>=65
    "starvation_score": 60.0,
    "edge_survival_2b": 0.33,
    "edge_survival_5b": 0.06,
    "coherence_avg":    95.0,
    "gtal_valid_rate":  0.0024,
}

# Thresholds de drift permitido — más amplios para el catálogo sintético
DRIFT_THRESHOLDS = {
    "ep_avg":           20.0,    # ±20 pts (catálogo tiene sesion-bias)
    "hb_rate_avg":      0.15,    # ±15% (stress sessions elevan HB)
    "et_frequency":     5.0,     # máx 5x la frecuencia real
    "ets_active_pct":   3.0,     # máx 3x
    "gtal_valid_rate":  3.0,     # máx 3x
    "edge_survival_5b": 2.0,     # máx 2x
}

# ── CURRICULA ─────────────────────────────────────────────────────
# Secuencias de tipos de sesión que definen la exposición

CURRICULA = {
    "standard": [
        # Refleja la distribución real del mercado
        # 50% rotational, 20% stress, 15% expansion, 10% vol, 5% elite
        ("RANDOM",    "LOW",    5),   # 5 sesiones random bajo estrés
        ("ELITE_SIM", "NONE",   1),   # 1 sesión expansion real
        ("STRESS",    "MEDIUM", 2),   # 2 sesiones con stress
        ("RANDOM",    "LOW",    3),   # 3 rotacionales
        ("STRESS",    "HIGH",   1),   # 1 stress alto
    ],

    "starvation": [
        # Máxima dificultad — simula mercados donde GIBBZ casi nunca activa
        ("STRESS",    "HIGH",   4),
        ("RANDOM",    "LOW",    3),
        ("STRESS",    "MEDIUM", 3),
    ],

    "expansion_hunt": [
        # Busca edge — sesiones de alta calidad con variaciones
        ("ELITE_SIM", "NONE",   3),
        ("STRESS",    "LOW",    2),
        ("ELITE_SIM", "NONE",   2),
        ("STRESS",    "MEDIUM", 2),
        ("RANDOM",    "LOW",    1),
    ],

    "robustness": [
        # Valida robustez — todos los tipos de stress
        ("ELITE_SIM", "NONE",   2),
        ("STRESS",    "HIGH",   3),
        ("RANDOM",    "LOW",    2),
        ("STRESS",    "MEDIUM", 3),
    ],
}


# ── DATA CLASSES ──────────────────────────────────────────────────

@dataclass
class SessionMetadata:
    """Metadata completa de una sesión generada por el engine."""
    session_id:         str   = ""
    curriculum:         str   = ""
    position_in_batch:  int   = 0
    generated_at:       str   = ""
    base_mode:          str   = ""
    stress_type:        str   = "NONE"
    stress_intensity:   str   = "NONE"

    # Métricas de calidad
    coherence:          float = 0.0
    hb_rate:            float = 0.0
    et_bars:            int   = 0
    ets_max:            int   = 0
    gtal_valid:         int   = 0
    total_bars:         int   = 0

    # Scores del engine
    realism_score:      float = 0.0
    rarity_score:       float = 0.0
    fingerprint_entropy:float = 0.0
    fragment_diversity: float = 0.0
    overfit_risk:       str   = "LOW"

    # Flags
    replay_ready:       bool  = False
    anti_mem_applied:   bool  = False
    rarity_forced:      bool  = False
    distribution_ok:    bool  = True

    def to_dict(self) -> dict:
        return self.__dict__.copy()


@dataclass
class EngineState:
    """Estado persistente del motor entre ejecuciones."""
    total_sessions_generated: int   = 0
    fragment_use_counts:      Dict  = field(default_factory=dict)
    recent_fingerprints:      List  = field(default_factory=list)
    et_sessions_last_10:      int   = 0
    hb_sessions_last_10:      int   = 0
    rotational_sessions_last_10: int = 0
    last_stress_types:        List  = field(default_factory=list)
    synthetic_ep_avg:         float = 0.0
    synthetic_hb_avg:         float = 0.0
    synthetic_et_freq:        float = 0.0
    overfit_warnings:         int   = 0

    def to_dict(self) -> dict:
        return self.__dict__.copy()


# ── SUBSYSTEMS ────────────────────────────────────────────────────

class DistributionController:
    """Preserva los ratios reales del dataset en la generación sintética."""

    def __init__(self, baselines: dict, thresholds: dict):
        self.baselines   = baselines
        self.thresholds  = thresholds
        self.history: List[SessionMetadata] = []

    def update(self, meta: SessionMetadata):
        self.history.append(meta)
        if len(self.history) > 50:
            self.history.pop(0)

    def should_force_rotational(self) -> bool:
        """¿El motor está generando demasiadas sesiones de expansión?"""
        if len(self.history) < 5:
            return False
        recent = self.history[-5:]
        et_heavy = sum(1 for m in recent if m.et_bars >= 5)
        return et_heavy >= 3

    def should_force_starvation(self) -> bool:
        """¿Demasiadas sesiones con señal alta?"""
        if len(self.history) < 5:
            return False
        recent  = self.history[-5:]
        ep_avg  = sum(m.ets_max for m in recent) / len(recent)
        return ep_avg > 65

    def get_drift_report(self) -> Dict:
        if not self.history:
            return {}
        n       = len(self.history)
        ep_avg  = sum(m.ets_max for m in self.history) / n
        hb_avg  = sum(m.hb_rate for m in self.history) / n
        et_freq = sum(m.et_bars for m in self.history) / max(
            sum(m.total_bars for m in self.history), 1)
        return {
            "ep_avg":       round(ep_avg, 1),
            "hb_avg":       round(hb_avg, 3),
            "et_frequency": round(et_freq, 5),
            "n_sessions":   n,
        }


class AntiMemorizationScheduler:
    """Impide repetición estructural y memorización de fragmentos."""

    COOLDOWN_PERIOD = 8    # sesiones antes de reutilizar mismo fragmento
    MAX_FP_REPEAT   = 3    # máximo de sesiones seguidas con mismo fingerprint

    def __init__(self):
        self.fragment_last_used: Dict[str, int] = {}
        self.fingerprint_streak: Dict[str, int] = {}
        self.session_counter    = 0
        self.recent_structures: List[str] = []

    def tick(self):
        self.session_counter += 1

    def register_session(self, meta: SessionMetadata,
                          fragment_ids: List[str]):
        for fid in fragment_ids:
            self.fragment_last_used[fid] = self.session_counter
        # Registrar fingerprint de la sesión
        fp_key = "|".join(sorted(
            [fp for fp in (meta.__dict__.get("fingerprints", []) or [])]))
        if fp_key:
            self.fingerprint_streak[fp_key] = (
                self.fingerprint_streak.get(fp_key, 0) + 1)
        # Limpiar streaks de otros fingerprints
        for k in list(self.fingerprint_streak.keys()):
            if k != fp_key:
                self.fingerprint_streak[k] = 0

    def is_fragment_on_cooldown(self, fragment_id: str) -> bool:
        last = self.fragment_last_used.get(fragment_id, -999)
        return (self.session_counter - last) < self.COOLDOWN_PERIOD

    def is_fingerprint_overused(self, fingerprint: str) -> bool:
        return (self.fingerprint_streak.get(fingerprint, 0) >=
                self.MAX_FP_REPEAT)

    def get_cooldown_fragments(self) -> List[str]:
        return [fid for fid, last in self.fragment_last_used.items()
                if (self.session_counter - last) < self.COOLDOWN_PERIOD]


class RarityController:
    """Mantiene el edge ultra-raro — no permite ET artificialmente frecuente."""

    ET_BUDGET_PER_10  = 2    # máx 2 sesiones con ET en cada 10
    ETS_BUDGET_PER_10 = 4    # máx 4 sesiones con ETS>=65 en cada 10

    def __init__(self):
        self.et_sessions:  List[bool] = []
        self.ets_sessions: List[bool] = []

    def register(self, meta: SessionMetadata):
        self.et_sessions.append(meta.et_bars >= 3)
        self.ets_sessions.append(meta.ets_max >= 65)
        if len(self.et_sessions) > 10:
            self.et_sessions.pop(0)
        if len(self.ets_sessions) > 10:
            self.ets_sessions.pop(0)

    def et_budget_exceeded(self) -> bool:
        if len(self.et_sessions) < 5:
            return False
        return sum(self.et_sessions) >= self.ET_BUDGET_PER_10

    def ets_budget_exceeded(self) -> bool:
        if len(self.ets_sessions) < 5:
            return False
        return sum(self.ets_sessions) >= self.ETS_BUDGET_PER_10

    def rarity_score(self, meta: SessionMetadata) -> float:
        """0-100: cuán raro es el edge de esta sesión (mayor = más raro)."""
        score = 50.0
        if meta.et_bars == 0:      score += 20
        if meta.ets_max < 50:      score += 15
        if meta.hb_rate >= 0.15:   score += 15
        if meta.gtal_valid == 0:   score += 10
        if meta.ets_max >= 65:     score -= 30
        if meta.et_bars >= 5:      score -= 20
        return max(0.0, min(100.0, score))


class StressExposureBalancer:
    """Garantiza exposición suficiente a sesiones difíciles."""

    MIN_STRESS_RATIO = 0.30   # mínimo 30% de sesiones con algún stress

    def __init__(self):
        self.stressed_sessions:   int = 0
        self.total_sessions:      int = 0
        self.last_stress_type:    str = "NONE"
        self.stress_type_counts:  Dict[str, int] = defaultdict(int)

    def register(self, stress_type: str):
        self.total_sessions += 1
        if stress_type != "NONE":
            self.stressed_sessions += 1
            self.last_stress_type = stress_type
            self.stress_type_counts[stress_type] += 1

    def needs_more_stress(self) -> bool:
        if self.total_sessions < 5:
            return False
        return (self.stressed_sessions / self.total_sessions <
                self.MIN_STRESS_RATIO)

    def get_underused_stress(self) -> Optional[str]:
        """Retorna el tipo de stress menos usado."""
        if not self.stress_type_counts:
            return "HB_SURGE"
        return min(STRESS_TYPES,
                   key=lambda t: self.stress_type_counts.get(t, 0))


class FragmentReuseLimiter:
    """Evita dependencia en fragmentos ELITE — especialmente 2026-03-11."""

    ELITE_FRAGMENTS = {
        "2026-03-11_007",  # ETS=85 HB=0% — el más valioso
        "2025-03-19_003",  # ETS=85 HB=0%
        "2026-02-02_002",  # OPENING_DRIVE ETS=75
        "2026-04-30_003",  # OPENING_DRIVE ETS=60
    }
    MAX_ELITE_REUSE = 3   # máximo de veces que un fragmento ELITE puede aparecer

    def __init__(self):
        self.reuse_counts:  Dict[str, int] = defaultdict(int)
        self.penalty_map:   Dict[str, float] = {}

    def register_fragments(self, fragment_ids: List[str]):
        for fid in fragment_ids:
            self.reuse_counts[fid] += 1
            if fid in self.ELITE_FRAGMENTS:
                count = self.reuse_counts[fid]
                # Penalidad exponencial por reuso de fragmentos ELITE
                self.penalty_map[fid] = min(1.0, count / self.MAX_ELITE_REUSE)

    def get_penalty(self, fragment_id: str) -> float:
        return self.penalty_map.get(fragment_id, 0.0)

    def is_overused(self, fragment_id: str) -> bool:
        if fragment_id in self.ELITE_FRAGMENTS:
            return self.reuse_counts[fragment_id] >= self.MAX_ELITE_REUSE
        return self.reuse_counts[fragment_id] >= 10

    def get_diversity_score(self, fragment_ids: List[str]) -> float:
        """0-100: diversidad de fragmentos (100 = todos únicos)."""
        if not fragment_ids:
            return 100.0
        unique_ratio = len(set(fragment_ids)) / len(fragment_ids)
        elite_count  = sum(1 for fid in fragment_ids
                           if fid in self.ELITE_FRAGMENTS)
        elite_penalty = elite_count * 10
        return max(0.0, min(100.0, unique_ratio * 100 - elite_penalty))


class RealismDriftAuditor:
    """Compara sintético vs real — alerta si deriva demasiado."""

    def __init__(self, baselines: dict, thresholds: dict):
        self.baselines  = baselines
        self.thresholds = thresholds
        self.warnings:  List[str] = []

    def audit(self, history: List[SessionMetadata]) -> Tuple[bool, List[str]]:
        """Retorna (within_bounds, list_of_warnings)."""
        if len(history) < 5:
            return True, []

        warnings = []
        n         = len(history)

        # ETS average
        syn_ep = sum(m.ets_max for m in history) / n
        if syn_ep > self.baselines["ep_avg"] + self.thresholds["ep_avg"]:
            warnings.append(
                f"[REALISM] ETS_avg drift: {syn_ep:.1f} vs "
                f"real {self.baselines['ep_avg']:.1f} "
                f"(+{syn_ep - self.baselines['ep_avg']:.1f})")

        # HB rate
        syn_hb = sum(m.hb_rate for m in history) / n
        if abs(syn_hb - self.baselines["hb_rate_avg"]) > self.thresholds["hb_rate_avg"]:
            warnings.append(
                f"[REALISM] HB_rate drift: {syn_hb:.1%} vs "
                f"real {self.baselines['hb_rate_avg']:.1%}")

        # ET frequency
        total_bars = max(sum(m.total_bars for m in history), 1)
        total_et   = sum(m.et_bars for m in history)
        syn_et_freq = total_et / total_bars
        if syn_et_freq > self.baselines["et_frequency"] * self.thresholds["et_frequency"]:
            warnings.append(
                f"[REALISM] ET_frequency elevated: {syn_et_freq:.4f} vs "
                f"real {self.baselines['et_frequency']:.4f}")

        # GTAL valid rate
        syn_gtal = sum(m.gtal_valid for m in history) / max(total_bars / 500, 1)
        if syn_gtal > self.baselines["gtal_valid_rate"] * self.thresholds["gtal_valid_rate"]:
            warnings.append(
                f"[REALISM] GTAL_valid_rate elevated: "
                f"overfit risk MEDIUM")

        self.warnings = warnings
        return len(warnings) == 0, warnings


# ── MAIN ENGINE ───────────────────────────────────────────────────

class SyntheticSessionEngine:
    """
    Orquestador central del GIBBZ Synthetic Replay Treadmill.
    Administra exposición, preserva realismo, evita overfitting.
    """

    def __init__(self, seed: Optional[int] = None, verbose: bool = True):
        self.fp       = FingerprintPreserver()
        self.morph    = RegimeMorpher(seed=seed)
        self.injector = StressInjector(seed=seed)
        self.rng      = random.Random(seed)
        self.verbose  = verbose

        # Subsistemas
        self.distribution = DistributionController(
            REAL_BASELINES, DRIFT_THRESHOLDS)
        self.anti_mem     = AntiMemorizationScheduler()
        self.rarity       = RarityController()
        self.stress_bal   = StressExposureBalancer()
        self.frag_limiter = FragmentReuseLimiter()
        self.auditor      = RealismDriftAuditor(
            REAL_BASELINES, DRIFT_THRESHOLDS)

        self.session_history: List[SessionMetadata] = []
        self.state = self._load_state()

        os.makedirs(ENGINE_DIR, exist_ok=True)

    def _log(self, subsystem: str, msg: str):
        if self.verbose:
            print(f"  ↳ [{subsystem:<12}] {msg}")

    # ── GENERATE SESSION ─────────────────────────────────────────

    def generate(self,
                 base_mode:      str = "RANDOM",
                 stress_type:    str = "NONE",
                 stress_intensity: str = "MEDIUM",
                 force_rotational: bool = False) -> Optional[SessionMetadata]:
        """Genera una sesión sintética con todos los controles activos."""

        self.anti_mem.tick()

        # Override: si el distribution controller detecta deriva
        if self.distribution.should_force_rotational() or force_rotational:
            base_mode  = "RANDOM"
            stress_type = "NONE"
            self._log("DISTRIBUTION",
                      "ET frequency elevated → forcing ROTATIONAL")

        if self.distribution.should_force_starvation():
            stress_type = self.rng.choice(["HB_SURGE", "ETS_DECAY"])
            self._log("RARITY",
                      f"ETS too high → injecting {stress_type} stress")

        # Generar sesión base
        if stress_type == "NONE" or base_mode == "STRESS":
            sessions = self.morph.generate_batch(n=1, mode=base_mode)
        else:
            sessions = self.morph.generate_batch(n=1, mode=base_mode)

        if not sessions:
            return None

        syn = sessions[0]

        # Aplicar stress si aplica
        if stress_type != "NONE" and stress_type in STRESS_TYPES:
            stressed, _ = self.injector.apply(
                syn, stress_type, stress_intensity)
            syn = stressed
            self.stress_bal.register(stress_type)
        else:
            self.stress_bal.register("NONE")

        # Construir metadata
        meta = SessionMetadata(
            session_id        = syn.session_id,
            generated_at      = datetime.now().isoformat(),
            base_mode         = base_mode,
            stress_type       = stress_type,
            stress_intensity  = stress_intensity,
            coherence         = syn.coherence_score,
            hb_rate           = syn.hb_rate,
            et_bars           = syn.et_bar_count,
            ets_max           = syn.ets_max,
            gtal_valid        = syn.gtal_valid_count,
            total_bars        = syn.total_bars,
            replay_ready      = syn.replay_ready,
            overfit_risk      = syn.overfitting_risk,
        )

        # Scores del engine
        meta.rarity_score       = self.rarity.rarity_score(meta)
        meta.fragment_diversity = self.frag_limiter.get_diversity_score(
            syn.fragment_ids)
        meta.fingerprint_entropy = self._compute_entropy(syn.fingerprints)
        meta.realism_score      = self._compute_realism_score(meta)

        # Verificar rarity budget
        if self.rarity.et_budget_exceeded() and meta.et_bars >= 3:
            self._log("RARITY",
                      "ET budget exceeded → marking rarity_forced=True")
            meta.rarity_forced = True

        # Registrar en subsistemas
        self.anti_mem.register_session(meta, syn.fragment_ids)
        self.frag_limiter.register_fragments(syn.fragment_ids)
        self.rarity.register(meta)
        self.distribution.update(meta)
        self.session_history.append(meta)

        # Auditoría de drift
        within_bounds, warnings = self.auditor.audit(self.session_history)
        if not within_bounds:
            for w in warnings:
                self._log("REALISM", w)
            meta.distribution_ok = False

        # Actualizar estado
        self.state.total_sessions_generated += 1
        for fid in syn.fragment_ids:
            self.state.fragment_use_counts[fid] = (
                self.state.fragment_use_counts.get(fid, 0) + 1)

        # Guardar sesión
        self._save_session(syn, meta)

        return meta

    def _compute_entropy(self, fingerprints: List[str]) -> float:
        """Entropía de fingerprints — mayor = más diverso."""
        if not fingerprints:
            return 0.0
        n     = len(fingerprints)
        uniq  = len(set(fingerprints))
        return round(uniq / n * 100, 1)

    def _compute_realism_score(self, meta: SessionMetadata) -> float:
        """Cuán realista es la sesión vs el dataset real. 0-100."""
        score = 100.0
        # Penalizar si ETS demasiado alto (irrealmente bueno)
        if meta.ets_max > REAL_BASELINES["ep_avg"] + 20:
            score -= 15
        # Penalizar si HB demasiado bajo (irrealmente limpio)
        if meta.hb_rate < 0.02:
            score -= 10
        # Penalizar si ET demasiado frecuente
        if meta.total_bars > 0:
            et_pct = meta.et_bars / meta.total_bars
            if et_pct > REAL_BASELINES["et_frequency"] * 3:
                score -= 20
        # Bonus por HB cercano al baseline
        if abs(meta.hb_rate - REAL_BASELINES["hb_rate_avg"]) < 0.03:
            score += 5
        return max(0.0, min(100.0, round(score, 1)))

    def _save_session(self, syn: SyntheticSession,
                       meta: SessionMetadata):
        path = os.path.join(ENGINE_DIR, f"{syn.session_id}.json")
        data = syn.to_dict()
        data["engine_metadata"] = meta.to_dict()
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)

    # ── CURRICULUM RUNNER ─────────────────────────────────────────

    def run_curriculum(self,
                        curriculum: str = "standard",
                        multiplier: int = 1) -> List[SessionMetadata]:
        """Ejecuta un currículo completo de exposición."""
        if curriculum not in CURRICULA:
            print(f"  Curricula disponibles: {list(CURRICULA.keys())}")
            return []

        plan = CURRICULA[curriculum]
        all_meta = []
        total = sum(n * multiplier for _, _, n in plan)

        self._log("CURRICULUM",
                  f"Starting '{curriculum}' — {total} sessions planned")

        for mode, stress, n_base in plan:
            n = n_base * multiplier
            for i in range(n):
                # Seleccionar stress inteligentemente
                if stress == "NONE":
                    stype = "NONE"
                    sintensity = "NONE"
                elif self.stress_bal.needs_more_stress():
                    stype = self.stress_bal.get_underused_stress()
                    sintensity = "MEDIUM"
                    self._log("STRESS_BAL",
                              f"Forcing underused stress: {stype}")
                else:
                    stype = self.rng.choice(STRESS_TYPES)
                    sintensity = stress

                meta = self.generate(
                    base_mode      = mode,
                    stress_type    = stype,
                    stress_intensity = sintensity,
                )
                if meta:
                    all_meta.append(meta)

        self._save_state()
        return all_meta

    # ── AUDIT / REPORT ────────────────────────────────────────────

    def audit(self) -> Dict:
        """Auditoría completa del estado del motor."""
        if not self.session_history:
            return {"status": "no sessions generated yet"}

        n   = len(self.session_history)
        within, warnings = self.auditor.audit(self.session_history)
        drift = self.distribution.get_drift_report()

        report = {
            "total_sessions":       n,
            "replay_ready_pct":     round(
                sum(1 for m in self.session_history if m.replay_ready) / n * 100, 1),
            "avg_coherence":        round(
                sum(m.coherence for m in self.session_history) / n, 1),
            "avg_hb_rate":          round(
                sum(m.hb_rate for m in self.session_history) / n, 3),
            "avg_ets_max":          round(
                sum(m.ets_max for m in self.session_history) / n, 1),
            "et_sessions":          sum(
                1 for m in self.session_history if m.et_bars >= 3),
            "avg_realism_score":    round(
                sum(m.realism_score for m in self.session_history) / n, 1),
            "avg_rarity_score":     round(
                sum(m.rarity_score for m in self.session_history) / n, 1),
            "distribution_drift":   drift,
            "within_bounds":        within,
            "realism_warnings":     warnings,
            "stress_balance":       dict(self.stress_bal.stress_type_counts),
            "overfit_risk":         "HIGH" if len(warnings) >= 2
                                    else "MEDIUM" if len(warnings) == 1
                                    else "LOW",
        }
        return report

    def print_report(self, meta_list: List[SessionMetadata]):
        n = len(meta_list)
        if not n:
            return

        ready   = [m for m in meta_list if m.replay_ready]
        low_risk= [m for m in meta_list if m.overfit_risk == "LOW"]
        et_sess = [m for m in meta_list if m.et_bars >= 3]
        avg_coh = sum(m.coherence for m in meta_list) / n
        avg_rea = sum(m.realism_score for m in meta_list) / n
        avg_rar = sum(m.rarity_score for m in meta_list) / n

        print(f"\n{'='*70}")
        print(f"  ENGINE BATCH REPORT — {n} sessions")
        print(f"{'─'*70}")
        print(f"  Replay ready:          {len(ready)}/{n}")
        print(f"  Low overfit risk:      {len(low_risk)}/{n}")
        print(f"  Sessions with ET>=3:   {len(et_sess)}/{n}")
        print(f"  Avg coherence:         {avg_coh:.1f}")
        print(f"  Avg realism score:     {avg_rea:.1f}/100")
        print(f"  Avg rarity score:      {avg_rar:.1f}/100")

        stress_dist = defaultdict(int)
        for m in meta_list:
            stress_dist[m.stress_type] += 1
        print(f"\n  Stress distribution:")
        for st, cnt in sorted(stress_dist.items(), key=lambda x: -x[1]):
            pct = cnt / n * 100
            print(f"    {st:<18} {cnt:3d}  ({pct:.0f}%)")

        print(f"\n  Top sessions by realism:")
        for m in sorted(meta_list,
                         key=lambda x: -x.realism_score)[:5]:
            print(f"    {m.session_id[-35:]:<35}  "
                  f"real={m.realism_score:5.1f}  "
                  f"rar={m.rarity_score:5.1f}  "
                  f"coh={m.coherence:5.1f}  "
                  f"HB={m.hb_rate:.0%}")

        # Audit
        audit = self.audit()
        print(f"\n  Realism audit:")
        print(f"    within_bounds:  {audit['within_bounds']}")
        print(f"    overfit_risk:   {audit['overfit_risk']}")
        if audit["realism_warnings"]:
            for w in audit["realism_warnings"]:
                print(f"    ⚠ {w}")
        print(f"{'='*70}\n")

    # ── STATE PERSISTENCE ─────────────────────────────────────────

    def _load_state(self) -> EngineState:
        if os.path.exists(ENGINE_STATE):
            try:
                d = json.load(open(ENGINE_STATE, encoding="utf-8"))
                s = EngineState()
                for k, v in d.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
                return s
            except Exception:
                pass
        return EngineState()

    def _save_state(self):
        with open(ENGINE_STATE, "w", encoding="utf-8") as f:
            json.dump(self.state.to_dict(), f, indent=2)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GIBBZ V3 — Synthetic Session Engine")
    parser.add_argument("--curriculum", type=str, default="standard",
                        choices=list(CURRICULA.keys()),
                        help="Currículo de exposición a ejecutar")
    parser.add_argument("--n",          type=int, default=1,
                        help="Multiplicador del currículo (1x, 2x, etc.)")
    parser.add_argument("--audit",      action="store_true",
                        help="Mostrar auditoría del estado actual")
    parser.add_argument("--status",     action="store_true",
                        help="Status rápido del motor")
    parser.add_argument("--seed",       type=int, default=42)
    parser.add_argument("--quiet",      action="store_true",
                        help="Suprimir logs del motor")
    args = parser.parse_args()

    engine = SyntheticSessionEngine(
        seed=args.seed, verbose=not args.quiet)

    if args.audit or args.status:
        # Correr un batch pequeño primero para tener datos
        print(f"\n  Running quick sample for audit...")
        meta_list = engine.run_curriculum("standard", multiplier=1)
        report    = engine.audit()
        print(f"\n  ENGINE STATUS:")
        for k, v in report.items():
            if k not in ("realism_warnings", "results"):
                print(f"    {k:<28} {v}")
        if report.get("realism_warnings"):
            print(f"  Warnings:")
            for w in report["realism_warnings"]:
                print(f"    ⚠ {w}")
    else:
        print(f"\n  Running curriculum '{args.curriculum}' ×{args.n}...")
        meta_list = engine.run_curriculum(args.curriculum,
                                          multiplier=args.n)
        engine.print_report(meta_list)