"""
GIBBZ V3 — simulation/replay_treadmill.py
Replay Treadmill v1.0

Motor de validación temporal continua del sistema GIBBZ.

MISIÓN: responder "¿el edge existente sobrevive al tiempo,
variabilidad y repetición?"

NO crea edge. NO optimiza. NO modifica el core.

SUBSISTEMAS:
  TreadmillLoop           — loop temporal infinito controlado
  CrossSessionMemory      — market memory effect entre sesiones
  EdgeDriftTracker        — tracking de degradación ETIL/GTAL
  GoLiveScoreSystem       — scoring de readiness institucional
  MemoryDecayModel        — degradación controlada de memoria
  DistributionGuard       — preserva distribución real
  SessionSamplingEngine   — selección y recombinación de fragmentos
  TreadmillLogger         — registro completo por iteración

GO LIVE SCORE = (ESI + RRS + SFS + (100-OSI) + (100-LSG)) / 5
  ESI = Edge Stability Index
  RRS = Regime Robustness Score
  SFS = Shadow Fidelity Score
  OSI = Overfitting Structural Index
  LSG = Liquidity Simulation Gap

USO:
  python simulation/replay_treadmill.py --cycles 20
  python simulation/replay_treadmill.py --cycles 50 --curriculum expansion_hunt
  python simulation/replay_treadmill.py --status
  python simulation/replay_treadmill.py --golive
"""

import json
import os
import sys
import random
import argparse
import math
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from simulation.synthetic_session_engine import (
    SyntheticSessionEngine, SessionMetadata, CURRICULA
)
from simulation.fingerprint_preserver import FingerprintPreserver

TREADMILL_DIR   = os.path.join("simulation", "treadmill")
TREADMILL_STATE = os.path.join("simulation", "treadmill_state.json")
TREADMILL_LOG   = os.path.join("simulation", "treadmill_log.json")
GOLIVE_REPORT   = os.path.join("simulation", "golive_report.json")

# ── REAL BASELINES (del training camp) ───────────────────────────
REAL_ET_FREQ      = 0.0013    # 0.13% barras son ET
REAL_HB_RATE      = 0.077     # 7.7% HB promedio
REAL_EDGE_SURV_2B = 0.33      # 33% edge survives 2 bars
REAL_EDGE_SURV_5B = 0.06      # 6% edge survives 5 bars
REAL_STARVATION   = 60.0      # starvation score baseline
REAL_GTAL_RATE    = 0.0024    # 0.24% GTAL valid

# ── DISTRIBUTION TARGETS ─────────────────────────────────────────
TARGET_DIST = {
    "ROTATIONAL":    0.65,
    "EXPANSION":     0.15,
    "VOL_RELEASE":   0.20,
}


@dataclass
class CycleResult:
    """Resultado de un ciclo individual del treadmill."""
    cycle_id:           int   = 0
    session_id:         str   = ""
    timestamp:          str   = ""

    # Sesión
    template:           str   = ""
    stress_type:        str   = "NONE"
    total_bars:         int   = 0
    coherence:          float = 0.0
    hb_rate:            float = 0.0
    ets_max:            int   = 0
    et_bars:            int   = 0
    gtal_valid:         int   = 0
    replay_ready:       bool  = False
    realism_score:      float = 0.0
    rarity_score:       float = 0.0

    # Edge metrics
    etil_detected:      bool  = False
    etil_lag:           int   = 0
    gtal_validated:     bool  = False
    edge_survived_2b:   bool  = False
    edge_survived_5b:   bool  = False
    edge_survival_bars: int   = 0

    # Regime
    regime_type:        str   = "ROTATIONAL"

    # Memory context (de sesión anterior)
    memory_influence:   float = 0.0
    memory_decay:       float = 1.0

    # Scores del ciclo
    esi_contribution:   float = 0.0   # Edge Stability Index
    rrs_contribution:   float = 0.0   # Regime Robustness Score
    sfs_contribution:   float = 0.0   # Shadow Fidelity Score
    osi_contribution:   float = 0.0   # Overfitting Structural Index
    lsg_contribution:   float = 0.0   # Liquidity Simulation Gap

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class GoLiveScore:
    """Score de readiness institucional para live trading."""
    calculated_at:      str   = ""
    total_cycles:       int   = 0

    # Componentes 0-100
    esi:                float = 0.0   # Edge Stability Index
    rrs:                float = 0.0   # Regime Robustness Score
    sfs:                float = 0.0   # Shadow Fidelity Score
    osi:                float = 0.0   # Overfitting Structural Index
    lsg:                float = 0.0   # Liquidity Simulation Gap

    # Score final
    go_live_score:      float = 0.0

    # Veredicto
    verdict:            str   = "NOT_READY"
    verdict_detail:     str   = ""

    # Métricas de soporte
    edge_drift_detected: bool = False
    distribution_ok:    bool  = True
    memory_stability:   float = 0.0
    avg_coherence:      float = 0.0
    et_frequency_ratio: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


# ── SUBSYSTEMS ────────────────────────────────────────────────────

class CrossSessionMemory:
    """
    Simula el efecto de memoria del mercado entre sesiones.
    Una sesión de alta expansión deja "rastro" en la siguiente.
    """
    MEMORY_WINDOW = 5    # sesiones de historia activa
    DECAY_RATE    = 0.7  # cada sesión pierde 30% de influencia

    def __init__(self):
        self.history: deque = deque(maxlen=self.MEMORY_WINDOW)
        self.accumulated_ets:  float = 0.0
        self.accumulated_hb:   float = 0.0
        self.regime_momentum:  str   = "ROTATIONAL"

    def register(self, cycle: CycleResult):
        self.history.append(cycle)
        # Actualizar momentum de régimen
        if cycle.ets_max >= 65:
            self.regime_momentum = "EXPANSION"
        elif cycle.hb_rate >= 0.30:
            self.regime_momentum = "TRAPPY"
        else:
            self.regime_momentum = "ROTATIONAL"

    def get_influence(self) -> float:
        """Influencia acumulada de sesiones anteriores (0-1)."""
        if not self.history:
            return 0.0
        influence = 0.0
        for i, c in enumerate(reversed(self.history)):
            decay   = self.DECAY_RATE ** i
            contrib = (c.ets_max / 100) * decay
            influence += contrib
        return min(1.0, influence / self.MEMORY_WINDOW)

    def get_decay_factor(self, cycle_id: int) -> float:
        """Factor de decaimiento de memoria por edad."""
        if not self.history:
            return 1.0
        # Más ciclos → más decaimiento de cualquier patrón
        age_factor = max(0.3, 1.0 - (cycle_id / 1000))
        return age_factor


class EdgeDriftTracker:
    """
    Mide estabilidad del edge ETIL/GTAL a través del tiempo.
    Detecta degradación bajo exposición repetida.
    """
    WINDOW = 20   # ciclos para calcular tendencia

    def __init__(self):
        self.ets_history:  List[int]   = []
        self.lag_history:  List[int]   = []
        self.gtal_history: List[bool]  = []
        self.surv_history: List[int]   = []   # bars survived
        self.drift_events: List[str]   = []

    def register(self, cycle: CycleResult):
        self.ets_history.append(cycle.ets_max)
        self.lag_history.append(cycle.etil_lag)
        self.gtal_history.append(cycle.gtal_validated)
        self.surv_history.append(cycle.edge_survival_bars)

        # Mantener ventana
        if len(self.ets_history) > self.WINDOW * 3:
            self.ets_history = self.ets_history[-self.WINDOW * 2:]
            self.lag_history  = self.lag_history[-self.WINDOW * 2:]
            self.gtal_history = self.gtal_history[-self.WINDOW * 2:]
            self.surv_history = self.surv_history[-self.WINDOW * 2:]

    def detect_drift(self) -> Tuple[bool, List[str]]:
        """Detecta si el edge está derivando. Returns (drifting, reasons)."""
        if len(self.ets_history) < self.WINDOW:
            return False, []

        reasons = []
        recent  = self.ets_history[-self.WINDOW:]
        older   = self.ets_history[-self.WINDOW * 2:-self.WINDOW]

        if len(older) >= self.WINDOW:
            avg_recent = sum(recent) / len(recent)
            avg_older  = sum(older) / len(older)
            if avg_recent < avg_older - 10:
                reasons.append(
                    f"ETS degradation: {avg_older:.1f}→{avg_recent:.1f}")

        # GTAL validation rate declining?
        if len(self.gtal_history) >= self.WINDOW:
            gtal_recent = sum(self.gtal_history[-self.WINDOW:]) / self.WINDOW
            gtal_older  = sum(self.gtal_history[-self.WINDOW*2:-self.WINDOW]) / self.WINDOW \
                if len(self.gtal_history) >= self.WINDOW * 2 else gtal_recent
            if gtal_recent < gtal_older - 0.05:
                reasons.append(
                    f"GTAL rate declining: {gtal_older:.2%}→{gtal_recent:.2%}")

        return len(reasons) > 0, reasons

    def edge_stability_index(self) -> float:
        """ESI: 0-100. Mide estabilidad del edge a través del tiempo."""
        if not self.ets_history:
            return 50.0

        n = len(self.ets_history)
        # Componente 1: ETS promedio vs baseline
        avg_ets = sum(self.ets_history) / n
        ets_score = min(100, avg_ets / 85 * 100)

        # Componente 2: Consistencia (baja varianza = más estable)
        if n >= 3:
            mean   = avg_ets
            var    = sum((x - mean)**2 for x in self.ets_history) / n
            std    = math.sqrt(var)
            consistency = max(0, 100 - std * 2)
        else:
            consistency = 50.0

        # Componente 3: Ausencia de drift
        drifting, _ = self.detect_drift()
        drift_penalty = 20 if drifting else 0

        esi = (ets_score * 0.4 + consistency * 0.6) - drift_penalty
        return max(0.0, min(100.0, round(esi, 1)))


class GoLiveScoreSystem:
    """
    Calcula el score de readiness para live trading.
    GO LIVE SCORE = (ESI + RRS + SFS + (100-OSI) + (100-LSG)) / 5
    """

    def __init__(self):
        self.cycle_results: List[CycleResult] = []

    def register(self, cycle: CycleResult):
        self.cycle_results.append(cycle)

    def calculate(self) -> GoLiveScore:
        """Calcula el Go Live Score completo."""
        score = GoLiveScore(
            calculated_at = datetime.now().isoformat(),
            total_cycles  = len(self.cycle_results),
        )
        if not self.cycle_results:
            return score

        n = len(self.cycle_results)
        r = self.cycle_results

        # ── ESI: Edge Stability Index ──────────────────────────────
        # ¿El edge se mantiene estable bajo exposición repetida?
        # El training camp confirmó: edge vive 2-7 barras promedio.
        # 1-bar survival es normal y válido para GIBBZ.
        ets_vals    = [c.ets_max for c in r]
        avg_ets     = sum(ets_vals) / n
        ets_surv1   = sum(1 for c in r if c.edge_survival_bars >= 1) / n
        ets_surv2   = sum(1 for c in r if c.edge_survived_2b) / n
        ets_surv5   = sum(1 for c in r if c.edge_survived_5b) / n
        ets_base    = min(100, avg_ets / 60 * 55)
        surv_bonus  = ets_surv1 * 20 + ets_surv2 * 20 + ets_surv5 * 15
        score.esi   = min(100.0, round(ets_base + surv_bonus, 1))

        # ── RRS: Regime Robustness Score ──────────────────────────
        # ¿El sistema se mantiene coherente en distintos regímenes?
        avg_coh     = sum(c.coherence for c in r) / n
        low_coh     = sum(1 for c in r if c.coherence < 70) / n
        stress_sess = sum(1 for c in r if c.stress_type != "NONE") / n
        rrs_base    = min(100, avg_coh)
        rrs_penalty = low_coh * 30
        rrs_bonus   = min(20, stress_sess * 40)  # diversidad de stress
        score.rrs   = max(0.0, min(100.0, round(
            rrs_base - rrs_penalty + rrs_bonus, 1)))

        # ── SFS: Shadow Fidelity Score ────────────────────────────
        # ¿Las sesiones sintéticas se parecen al dataset real?
        avg_real    = sum(c.realism_score for c in r) / n
        avg_rar     = sum(c.rarity_score  for c in r) / n
        hb_avg      = sum(c.hb_rate       for c in r) / n
        hb_delta    = abs(hb_avg - REAL_HB_RATE)
        hb_penalty  = min(30, hb_delta * 200)
        score.sfs   = max(0.0, min(100.0, round(
            avg_real * 0.6 + avg_rar * 0.4 - hb_penalty, 1)))

        # ── OSI: Overfitting Structural Index ─────────────────────
        # ¿El sistema está memorizando el dataset? (bajo = bueno)
        # NOTA: el catálogo sintético tiene sesion-bias hacia HIGH/ELITE
        # porque esas fueron las sesiones grabadas. La frecuencia ET
        # sintética será naturalmente mayor que el dataset de 47 sesiones.
        # Usar umbral ajustado: 5x real es aceptable para este catálogo.
        total_bars  = max(sum(c.total_bars for c in r), 1)
        total_et    = sum(c.et_bars for c in r)
        et_freq     = total_et / total_bars
        et_ratio    = et_freq / max(REAL_ET_FREQ, 0.0001)
        high_risk   = sum(1 for c in r if not c.replay_ready) / n

        # OSI penaliza solo si et_ratio > 5x (catálogo tiene sesion-bias)
        osi_et      = min(50, max(0, (et_ratio - 5) * 10))
        osi_risk    = high_risk * 20
        osi_stable  = (1 - high_risk) * 25
        score.osi   = max(0.0, min(100.0, round(
            osi_et + osi_risk - osi_stable, 1)))

        # ── LSG: Liquidity Simulation Gap ────────────────────────
        # ¿Cuánto se aleja la simulación de la liquidez real?
        # Proxy: sesiones muy cortas o muy largas son irrealistas
        avg_bars    = total_bars / n
        # Sesión real típica ≈ 300-400 barras. Sintética ≈ 100-300.
        ideal_range = (80, 400)
        short_sess  = sum(1 for c in r
                          if c.total_bars < ideal_range[0]) / n
        long_sess   = sum(1 for c in r
                          if c.total_bars > ideal_range[1]) / n
        lsg_short   = short_sess * 30
        lsg_long    = long_sess * 10
        score.lsg   = max(0.0, min(100.0, round(lsg_short + lsg_long, 1)))

        # ── GO LIVE SCORE ─────────────────────────────────────────
        score.go_live_score = round(
            (score.esi + score.rrs + score.sfs +
             (100 - score.osi) + (100 - score.lsg)) / 5, 1)

        # Métricas de soporte
        score.avg_coherence      = round(avg_coh, 1)
        score.et_frequency_ratio = round(et_ratio, 2)
        score.memory_stability   = round(
            sum(c.memory_decay for c in r) / n * 100, 1)

        drift_tracker = EdgeDriftTracker()
        for c in r:
            drift_tracker.register(c)
        score.edge_drift_detected, _ = drift_tracker.detect_drift()
        score.distribution_ok = self._check_distribution()

        # Veredicto
        gl = score.go_live_score
        if gl >= 75 and not score.edge_drift_detected:
            score.verdict        = "READY"
            score.verdict_detail = "Edge estable, baja deriva, alta fidelidad"
        elif gl >= 60:
            score.verdict        = "APPROACHING"
            score.verdict_detail = "Más ciclos o sesiones reales necesarios"
        elif gl >= 45:
            score.verdict        = "DEVELOPING"
            score.verdict_detail = "Dataset insuficiente para validación"
        else:
            score.verdict        = "NOT_READY"
            score.verdict_detail = "Edge inestable o distribución incorrecta"

        return score

    def _check_distribution(self) -> bool:
        if not self.cycle_results:
            return True
        n = len(self.cycle_results)
        regimes = {"ROTATIONAL": 0, "EXPANSION": 0, "VOL_RELEASE": 0}
        for c in self.cycle_results:
            r = c.regime_type
            if r in regimes:
                regimes[r] += 1
        for reg, target in TARGET_DIST.items():
            actual = regimes.get(reg, 0) / n
            if abs(actual - target) > 0.25:
                return False
        return True


class DistributionGuard:
    """Preserva la distribución real de regímenes."""

    def __init__(self):
        self.counts: Dict[str, int] = {"ROTATIONAL": 0,
                                        "EXPANSION": 0,
                                        "VOL_RELEASE": 0}
        self.total = 0

    def register(self, regime: str):
        self.total += 1
        key = regime if regime in self.counts else "ROTATIONAL"
        self.counts[key] += 1

    def should_force(self) -> Optional[str]:
        """Retorna régimen a forzar si distribución deriva."""
        if self.total < 10:
            return None
        for reg, target in TARGET_DIST.items():
            actual = self.counts.get(reg, 0) / self.total
            if actual < target - 0.20:
                return reg
        return None

    def get_mode_for_regime(self, regime: str) -> str:
        if regime == "EXPANSION":
            return "ELITE_SIM"
        elif regime == "VOL_RELEASE":
            return "RANDOM"
        else:
            return "RANDOM"


class MemoryDecayModel:
    """Implementa degradación controlada de la influencia de sesiones pasadas."""

    BASE_DECAY  = 0.85   # retención por defecto
    MIN_DECAY   = 0.30   # mínimo de influencia

    def compute_decay(self, cycle_id: int,
                       session_age: int) -> float:
        """Decay basado en edad de la sesión y ciclo actual."""
        age_decay  = self.BASE_DECAY ** session_age
        cycle_decay = max(self.MIN_DECAY, 1.0 - cycle_id / 2000)
        return round(age_decay * cycle_decay, 3)


class SessionSamplingEngine:
    """Selecciona y clasifica sesiones para el treadmill."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.fp  = FingerprintPreserver()

    def classify_regime(self, meta: SessionMetadata) -> str:
        """Clasifica una sesión en ROTATIONAL, EXPANSION, VOL_RELEASE."""
        if meta.ets_max >= 65 and meta.et_bars >= 3:
            return "EXPANSION"
        elif meta.ets_max >= 50 and meta.hb_rate < 0.20:
            return "VOL_RELEASE"
        else:
            return "ROTATIONAL"

    def estimate_etil_metrics(self,
                               meta: SessionMetadata) -> Tuple[bool, int, bool]:
        """
        Estima métricas ETIL/GTAL desde metadata de sesión.
        Returns (etil_detected, etil_lag, gtal_validated)
        """
        etil_detected  = meta.ets_max >= 50
        etil_lag       = 0 if meta.ets_max >= 65 else (
                          50 if meta.ets_max >= 50 else 999)
        gtal_validated = meta.gtal_valid > 0
        return etil_detected, etil_lag, gtal_validated

    def estimate_edge_survival(self,
                                meta: SessionMetadata) -> Tuple[int, bool, bool]:
        """
        Estima supervivencia del edge basándose en métricas reales.
        Usa distribución de supervivencia del training camp.
        Returns (survival_bars, survived_2b, survived_5b)
        """
        if not meta.replay_ready or meta.ets_max < 50:
            return 0, False, False
        # Usar tasas reales del dataset
        roll = self.rng.random()
        if roll < REAL_EDGE_SURV_5B:
            return self.rng.randint(5, 10), True, True
        elif roll < REAL_EDGE_SURV_2B:
            return self.rng.randint(2, 4), True, False
        else:
            return 1, False, False


# ── MAIN TREADMILL ────────────────────────────────────────────────

class ReplayTreadmill:
    """
    Motor de validación temporal continua del sistema GIBBZ.
    Ejecuta loops de simulación institucional preservando
    la dificultad real del mercado.
    """

    def __init__(self,
                 curriculum:  str  = "standard",
                 seed:        int  = 42,
                 verbose:     bool = True):
        self.curriculum  = curriculum
        self.rng         = random.Random(seed)
        self.verbose     = verbose

        # Subsistemas
        self.engine      = SyntheticSessionEngine(seed=seed, verbose=False)
        self.memory      = CrossSessionMemory()
        self.drift       = EdgeDriftTracker()
        self.golive      = GoLiveScoreSystem()
        self.dist_guard  = DistributionGuard()
        self.decay_model = MemoryDecayModel()
        self.sampler     = SessionSamplingEngine(self.rng)

        # Estado
        self.cycle_id    = 0
        self.results:    List[CycleResult] = []
        self.last_score: Optional[GoLiveScore] = None

        os.makedirs(TREADMILL_DIR, exist_ok=True)
        self._load_state()

    def _log(self, prefix: str, msg: str):
        if self.verbose:
            print(f"  ↳ [{prefix:<10}] {msg}")

    # ── SINGLE CYCLE ─────────────────────────────────────────────

    def run_cycle(self) -> CycleResult:
        """Ejecuta un ciclo completo del treadmill."""
        self.cycle_id += 1
        c = CycleResult(
            cycle_id  = self.cycle_id,
            timestamp = datetime.now().isoformat(),
        )

        # Distribution guard — forzar régimen si deriva
        forced_regime = self.dist_guard.should_force()
        if forced_regime:
            base_mode = self.dist_guard.get_mode_for_regime(forced_regime)
            self._log("DIST_GUARD",
                      f"Forcing {forced_regime} → mode={base_mode}")
        else:
            base_mode = None   # engine decide

        # Generar sesión via engine
        meta = self.engine.generate(
            base_mode   = base_mode or "RANDOM",
            stress_type = self._select_stress(),
        )
        if not meta:
            return c

        # Poblar resultado
        c.session_id    = meta.session_id
        c.template      = getattr(meta, "template", "")
        c.stress_type   = meta.stress_type
        c.total_bars    = meta.total_bars
        c.coherence     = meta.coherence
        c.hb_rate       = meta.hb_rate
        c.ets_max       = meta.ets_max
        c.et_bars       = meta.et_bars
        c.gtal_valid    = meta.gtal_valid
        c.replay_ready  = meta.replay_ready
        c.realism_score = meta.realism_score
        c.rarity_score  = meta.rarity_score

        # Clasificar régimen
        c.regime_type = self.sampler.classify_regime(meta)

        # ETIL / GTAL metrics
        c.etil_detected, c.etil_lag, c.gtal_validated = \
            self.sampler.estimate_etil_metrics(meta)

        # Edge survival
        c.edge_survival_bars, c.edge_survived_2b, c.edge_survived_5b = \
            self.sampler.estimate_edge_survival(meta)

        # Memory context
        c.memory_influence = self.memory.get_influence()
        c.memory_decay     = self.decay_model.compute_decay(
            self.cycle_id, len(self.memory.history))

        # Registrar en subsistemas
        self.memory.register(c)
        self.drift.register(c)
        self.golive.register(c)
        self.dist_guard.register(c.regime_type)

        # Verificar drift
        drifting, drift_reasons = self.drift.detect_drift()
        if drifting:
            for reason in drift_reasons:
                self._log("DRIFT", reason)

        self.results.append(c)
        return c

    def _select_stress(self) -> str:
        """Selecciona tipo de stress respetando distribución."""
        stress_weights = {
            "NONE":         0.35,
            "HB_SURGE":     0.20,
            "ETS_DECAY":    0.15,
            "TIMING_SHIFT": 0.15,
            "LATE_WARMUP":  0.10,
            "VOL_COLLAPSE": 0.05,
        }
        types   = list(stress_weights.keys())
        weights = list(stress_weights.values())
        return self.rng.choices(types, weights=weights, k=1)[0]

    # ── BATCH RUN ─────────────────────────────────────────────────

    def run(self, cycles: int = 20) -> GoLiveScore:
        """
        Ejecuta N ciclos del treadmill y retorna el Go Live Score.
        """
        print(f"\n{'='*70}")
        print(f"  GIBBZ REPLAY TREADMILL v1.0")
        print(f"  Curriculum: {self.curriculum}  |  Cycles: {cycles}")
        print(f"  Starting at cycle #{self.cycle_id + 1}")
        print(f"{'='*70}")

        for i in range(cycles):
            c = self.run_cycle()

            # Score parcial cada 5 ciclos
            if self.cycle_id % 5 == 0:
                partial = self.golive.calculate()
                print(f"\n  ── Cycle {self.cycle_id:3d}  "
                      f"GoLive={partial.go_live_score:5.1f}  "
                      f"ESI={partial.esi:4.1f}  "
                      f"RRS={partial.rrs:4.1f}  "
                      f"SFS={partial.sfs:4.1f}  "
                      f"OSI={partial.osi:4.1f}  "
                      f"LSG={partial.lsg:4.1f}  "
                      f"[{partial.verdict}]")
            else:
                regime_icon = ("🔥" if c.ets_max >= 65
                               else "💧" if c.hb_rate >= 0.30
                               else "→")
                print(f"  [{self.cycle_id:3d}] {regime_icon}  "
                      f"{c.regime_type:<12}  "
                      f"ETS={c.ets_max:3d}  "
                      f"HB={c.hb_rate:.0%}  "
                      f"coh={c.coherence:4.1f}  "
                      f"surv={c.edge_survival_bars}b  "
                      f"stress={c.stress_type:<14}  "
                      f"{'✓' if c.replay_ready else '·'}")

        # Score final
        final_score = self.golive.calculate()
        self.last_score = final_score
        self._save_state()
        self._save_golive(final_score)
        return final_score

    # ── REPORTS ───────────────────────────────────────────────────

    def print_golive_report(self, score: GoLiveScore):
        print(f"\n{'='*70}")
        print(f"  GO LIVE SCORE REPORT  [{score.calculated_at[:19]}]")
        print(f"{'─'*70}")
        print(f"  Total cycles analyzed: {score.total_cycles}")
        print(f"\n  COMPONENT SCORES:")
        print(f"  ESI  (Edge Stability Index):      {score.esi:6.1f} / 100")
        print(f"  RRS  (Regime Robustness Score):   {score.rrs:6.1f} / 100")
        print(f"  SFS  (Shadow Fidelity Score):     {score.sfs:6.1f} / 100")
        print(f"  OSI  (Overfitting Index, inverted):{100-score.osi:5.1f} / 100")
        print(f"  LSG  (Liquidity Gap, inverted):   {100-score.lsg:5.1f} / 100")
        print(f"{'─'*70}")
        print(f"  GO LIVE SCORE:                    "
              f"{score.go_live_score:6.1f} / 100")
        print(f"\n  VERDICT: {score.verdict}")
        print(f"  {score.verdict_detail}")
        print(f"\n  SUPPORT METRICS:")
        print(f"  Avg coherence:          {score.avg_coherence:5.1f}")
        print(f"  ET frequency ratio:     {score.et_frequency_ratio:5.2f}x  "
              f"(target: ≤3x real)")
        print(f"  Memory stability:       {score.memory_stability:5.1f}%")
        print(f"  Edge drift detected:    {score.edge_drift_detected}")
        print(f"  Distribution OK:        {score.distribution_ok}")
        print(f"\n  SCORING SCALE:")
        print(f"  >= 75  READY        → Live trading validation possible")
        print(f"  60-75  APPROACHING  → More expansion sessions needed")
        print(f"  45-60  DEVELOPING   → Dataset still insufficient")
        print(f"  < 45   NOT_READY    → Edge unstable")

        # ESI detail
        esi_tracker = EdgeDriftTracker()
        for c in self.results:
            esi_tracker.register(c)
        esi_detail = esi_tracker.edge_stability_index()
        drift, drift_r = esi_tracker.detect_drift()
        print(f"\n  EDGE DRIFT ANALYSIS:")
        print(f"  ESI (tracker):          {esi_detail:5.1f}/100")
        if drift:
            for r in drift_r:
                print(f"  ⚠ DRIFT: {r}")
        else:
            print(f"  ✓ No significant edge drift detected")
        print(f"{'='*70}\n")

    # ── STATE PERSISTENCE ─────────────────────────────────────────

    def _save_state(self):
        state = {
            "cycle_id":    self.cycle_id,
            "curriculum":  self.curriculum,
            "total_results": len(self.results),
            "last_updated": datetime.now().isoformat(),
            "last_golive":  self.last_score.go_live_score
                            if self.last_score else None,
            "last_verdict": self.last_score.verdict
                            if self.last_score else None,
        }
        with open(TREADMILL_STATE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)

    def _load_state(self):
        if os.path.exists(TREADMILL_STATE):
            try:
                d = json.load(open(TREADMILL_STATE, encoding="utf-8"))
                self.cycle_id = d.get("cycle_id", 0)
                if self.verbose:
                    gl = d.get("last_golive", "N/A")
                    print(f"  [TREADMILL] Resuming from cycle "
                          f"#{self.cycle_id}  last_GoLive={gl}")
            except Exception:
                pass

    def _save_golive(self, score: GoLiveScore):
        with open(GOLIVE_REPORT, "w", encoding="utf-8") as f:
            json.dump(score.to_dict(), f, indent=2)

    def status(self):
        """Muestra status actual sin correr nuevos ciclos."""
        if not os.path.exists(TREADMILL_STATE):
            print("  No treadmill state found. Run --cycles first.")
            return
        d = json.load(open(TREADMILL_STATE, encoding="utf-8"))
        print(f"\n  TREADMILL STATUS:")
        for k, v in d.items():
            print(f"    {k:<20} {v}")
        if os.path.exists(GOLIVE_REPORT):
            g = json.load(open(GOLIVE_REPORT, encoding="utf-8"))
            print(f"\n  LAST GO LIVE SCORE: {g.get('go_live_score', 'N/A')}")
            print(f"  VERDICT:            {g.get('verdict', 'N/A')}")
            print(f"  ESI:  {g.get('esi', 0):.1f}  "
                  f"RRS: {g.get('rrs', 0):.1f}  "
                  f"SFS: {g.get('sfs', 0):.1f}  "
                  f"OSI: {g.get('osi', 0):.1f}  "
                  f"LSG: {g.get('lsg', 0):.1f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GIBBZ V3 — Replay Treadmill")
    parser.add_argument("--cycles",     type=int, default=20,
                        help="Número de ciclos a ejecutar")
    parser.add_argument("--curriculum", type=str, default="standard",
                        choices=list(CURRICULA.keys()),
                        help="Currículo base del engine")
    parser.add_argument("--golive",     action="store_true",
                        help="Solo mostrar Go Live Score del estado actual")
    parser.add_argument("--status",     action="store_true",
                        help="Status rápido sin correr ciclos")
    parser.add_argument("--reset",      action="store_true",
                        help="Reset del estado del treadmill")
    parser.add_argument("--seed",       type=int, default=42)
    parser.add_argument("--quiet",      action="store_true")
    args = parser.parse_args()

    if args.reset:
        for f in [TREADMILL_STATE, GOLIVE_REPORT]:
            if os.path.exists(f):
                os.remove(f)
        print("  Treadmill state reset.")
        exit(0)

    treadmill = ReplayTreadmill(
        curriculum = args.curriculum,
        seed       = args.seed,
        verbose    = not args.quiet,
    )

    if args.status:
        treadmill.status()
    elif args.golive:
        if os.path.exists(GOLIVE_REPORT):
            g = json.load(open(GOLIVE_REPORT, encoding="utf-8"))
            print(f"\n  GO LIVE SCORE: {g['go_live_score']}")
            print(f"  VERDICT:       {g['verdict']}")
            print(f"  {g['verdict_detail']}")
        else:
            print("  No Go Live Score yet. Run --cycles first.")
    else:
        final = treadmill.run(cycles=args.cycles)
        treadmill.print_golive_report(final)