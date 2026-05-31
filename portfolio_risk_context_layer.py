# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ V3 — portfolio_risk_context_layer.py
#  Portfolio / Risk Context Layer v2.0
#
#  CAMBIOS v2.0 vs v1.0:
#  ─ fragmentation_index:    cuán fragmentado está el régimen
#  ─ signal_congestion:      densidad de señales en ventana activa
#  ─ setup_correlation:      correlación entre setups del mismo régimen
#  ─ PRS (Portfolio Risk Score 0-100): score único consolidado
#  ─ persistence:            HIGH/MED/LOW duración del cluster actual
#  ─ exposure_density:       LOW/MED/HIGH densidad de exposición
#  ─ output format alineado al spec:
#      PORTFOLIO: regime_cluster / persistence / exposure_density /
#                 correlation_index / PRS
#
#  RESTRICCIÓN: no ejecuta trades, no modifica señales.
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque
from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class PortfolioRiskResult:
    # Core cluster info
    regime_cluster:     str = "UNKNOWN"
    persistence:        str = "LOW"      # HIGH / MED / LOW
    cluster_bars_active:int = 0

    # Exposure
    exposure_density:   str = "LOW"      # LOW / MED / HIGH
    exposure_stacking_score: int = 0     # 0-100

    # Signals
    correlation_index:  int = 0          # 0-100
    signal_congestion:  str = "NONE"     # NONE / LOW / MEDIUM / HIGH
    congestion_score:   int = 0          # 0-100

    # Fragmentation
    fragmentation_index:int = 0          # 0-100
    transition_zone:    bool = False

    # Risk
    risk_concentration_index: int = 0    # 0-100
    setup_overlap_warning:    bool = False
    toxic_accumulation:       bool = False

    # Consolidated
    prs:                int = 0          # Portfolio Risk Score 0-100
    risk_note:          str = ""

    def portfolio_line(self) -> str:
        """Output format según spec."""
        return (
            f"PORTFOLIO: cluster={self.regime_cluster:<16} "
            f"persist={self.persistence:<4} "
            f"exposure={self.exposure_density:<6} "
            f"corr={self.correlation_index:3d} "
            f"frag={self.fragmentation_index:3d} "
            f"congestion={self.signal_congestion:<7} "
            f"PRS={self.prs:3d}"
        )

    def __str__(self) -> str:
        return self.portfolio_line()


class PortfolioRiskContextLayer:
    """
    Portfolio / Risk Context Layer v2.0

    Analiza el comportamiento del sistema a nivel portfolio:
    - clustering de regímenes con persistencia y fragmentación
    - densidad y correlación de señales
    - congestion de setups
    - Portfolio Risk Score (PRS) consolidado

    NO modifica señales ni decisiones del core.
    """

    CLUSTER_WINDOW    = 10
    OVERLAP_THRESHOLD = 3
    CONGESTION_WINDOW = 15
    CORRELATION_WINDOW= 20

    def __init__(self):
        # Historial general
        self._env_history:      deque = deque(maxlen=self.CLUSTER_WINDOW)
        self._env_long:         deque = deque(maxlen=50)
        self._ets_history:      deque = deque(maxlen=self.CORRELATION_WINDOW)
        self._trap_history:     deque = deque(maxlen=self.CLUSTER_WINDOW)
        self._gtal_history:     deque = deque(maxlen=self.CORRELATION_WINDOW)
        self._opp_history:      deque = deque(maxlen=self.CONGESTION_WINDOW)
        self._a_setup_recent:   deque = deque(maxlen=10)

        # Tracking de clusters
        self._bar_count:        int   = 0
        self._cluster_start:    int   = 0
        self._current_cluster:  str   = "UNKNOWN"
        self._last_env:         str   = ""
        self._regime_changes:   int   = 0

        # Para correlación por régimen
        self._setups_by_regime: Dict[str, list] = {}
        self._ets_by_regime:    Dict[str, list] = {}

        # Histórico de clusters para fragmentation
        self._cluster_history:  deque = deque(maxlen=30)
        self._cluster_durations:List[int] = []
        self._last_cluster_bar: int   = 0

    def analyze(self,
                env_r,
                etil_r,
                gtal_r,
                opp_r,
                bar_count: int) -> PortfolioRiskResult:

        self._bar_count = bar_count

        env  = getattr(env_r,  "environment",            "ROTATIONAL")
        trap = getattr(env_r,  "trap_density",           0)
        bfr  = getattr(env_r,  "breakout_failure_rate",  0)
        eff  = getattr(env_r,  "directional_efficiency", 0)
        ets  = getattr(etil_r, "ets_score",              0)
        ev   = getattr(gtal_r, "execution_validity",     "INVALID")
        opp  = getattr(opp_r,  "grade",                  "NONE")

        # ── HISTORIAL ────────────────────────────────────────────
        self._env_history.append(env)
        self._env_long.append(env)
        self._ets_history.append(ets)
        self._trap_history.append(trap)
        self._gtal_history.append(ev)
        self._opp_history.append(opp)
        self._a_setup_recent.append(1 if opp == "A" else 0)

        # Regime change detection
        if env != self._last_env and self._last_env != "":
            self._regime_changes += 1
            if self._last_cluster_bar > 0:
                duration = bar_count - self._last_cluster_bar
                self._cluster_durations.append(duration)
            self._last_cluster_bar = bar_count
        self._last_env = env

        # Por régimen
        if env not in self._setups_by_regime:
            self._setups_by_regime[env] = []
            self._ets_by_regime[env] = []
        self._setups_by_regime[env].append(opp)
        self._ets_by_regime[env].append(ets)

        # ── 1. CLUSTER DETECTION ─────────────────────────────────
        cluster, cluster_bars = self._detect_cluster()
        if cluster != self._current_cluster:
            self._current_cluster = cluster
            self._cluster_start = bar_count
            self._cluster_history.append(cluster)

        # ── 2. PERSISTENCE ───────────────────────────────────────
        persistence = self._calc_persistence(cluster_bars)

        # ── 3. FRAGMENTATION INDEX ───────────────────────────────
        fragmentation = self._calc_fragmentation()

        # ── 4. SIGNAL CONGESTION ─────────────────────────────────
        congestion_score, congestion_label = self._calc_signal_congestion()

        # ── 5. SETUP CORRELATION ─────────────────────────────────
        correlation = self._calc_setup_correlation(env)

        # ── 6. EXPOSURE ──────────────────────────────────────────
        exposure_score = self._calc_exposure_stacking(ets, ev, opp, trap)
        exposure_density = (
            "HIGH"   if exposure_score >= 60 else
            "MEDIUM" if exposure_score >= 30 else
            "LOW"
        )

        # ── 7. RISK CONCENTRATION INDEX ──────────────────────────
        rci = self._calc_risk_concentration(trap, bfr, eff, cluster)

        # ── 8. FLAGS ─────────────────────────────────────────────
        recent_a   = sum(self._a_setup_recent)
        overlap    = recent_a >= self.OVERLAP_THRESHOLD
        transition = self._is_transition_zone()
        toxic_envs = sum(1 for e in list(self._env_history)
                         if e in ("TRAPPY", "CHOPPY", "DEAD_MARKET"))
        toxic_accum = toxic_envs >= 4

        # ── 9. PRS — PORTFOLIO RISK SCORE ────────────────────────
        prs = self._calc_prs(
            rci, fragmentation, congestion_score,
            correlation, cluster, overlap, toxic_accum
        )

        # ── 10. RISK NOTE ─────────────────────────────────────────
        notes = []
        if overlap:
            notes.append(f"A-setup overlap ({recent_a}/10 bars)")
        if transition:
            notes.append("regime transition zone")
        if toxic_accum:
            notes.append(f"toxic accumulation ({toxic_envs} bars)")
        if rci >= 70:
            notes.append(f"high RCI={rci}")
        if fragmentation >= 60:
            notes.append(f"high fragmentation ({fragmentation})")
        if congestion_label in ("MEDIUM", "HIGH"):
            notes.append(f"signal congestion={congestion_label}")
        note = " | ".join(notes) if notes else ""

        return PortfolioRiskResult(
            regime_cluster           = cluster,
            persistence              = persistence,
            cluster_bars_active      = cluster_bars,
            exposure_density         = exposure_density,
            exposure_stacking_score  = exposure_score,
            correlation_index        = correlation,
            signal_congestion        = congestion_label,
            congestion_score         = congestion_score,
            fragmentation_index      = fragmentation,
            transition_zone          = transition,
            risk_concentration_index = rci,
            setup_overlap_warning    = overlap,
            toxic_accumulation       = toxic_accum,
            prs                      = prs,
            risk_note                = note,
        )

    # ── CLUSTER DETECTION ─────────────────────────────────────────

    def _detect_cluster(self) -> tuple:
        if len(self._env_history) < 3:
            return "UNKNOWN", 0

        envs  = list(self._env_history)
        total = len(envs)

        efficient  = sum(1 for e in envs if e == "EFFICIENT_TREND")
        toxic      = sum(1 for e in envs if e in ("TRAPPY", "CHOPPY", "DEAD_MARKET"))
        rotational = sum(1 for e in envs if e in ("ROTATIONAL", "BALANCED_DAY"))

        cluster_bars = self._bar_count - self._cluster_start

        if efficient >= total * 0.40:
            return "TREND_CLUSTER", cluster_bars
        elif toxic >= total * 0.40:
            return "TOXIC_CLUSTER", cluster_bars
        elif rotational >= total * 0.60:
            return "ROTATIONAL_CLUSTER", cluster_bars

        traps = list(self._trap_history)
        avg_trap = sum(traps) / len(traps) if traps else 0
        if avg_trap >= 50:
            return "TOXIC_CLUSTER", cluster_bars

        return "TRANSITION_ZONE", cluster_bars

    # ── PERSISTENCE ───────────────────────────────────────────────

    def _calc_persistence(self, cluster_bars: int) -> str:
        """
        Cuánto tiempo lleva el cluster actual sin cambiar.
        HIGH > 20 bars | MED 10-20 | LOW < 10
        """
        if cluster_bars >= 20:   return "HIGH"
        elif cluster_bars >= 10: return "MED"
        else:                    return "LOW"

    # ── FRAGMENTATION INDEX ───────────────────────────────────────

    def _calc_fragmentation(self) -> int:
        """
        Mide cuán fragmentado está el régimen.
        Alto = cambios frecuentes = mercado inestable.

        Basado en:
        - frecuencia de regime_changes
        - variedad de envs en ventana reciente
        - duración promedio de clusters
        """
        score = 0

        # Regime changes frecuentes
        if self._regime_changes >= 20:   score += 40
        elif self._regime_changes >= 10: score += 25
        elif self._regime_changes >= 5:  score += 10

        # Variedad de envs en ventana reciente
        if len(self._env_history) >= 5:
            env_list = list(self._env_history)[-5:]
            unique   = len(set(env_list))
            if unique >= 4:   score += 30
            elif unique >= 3: score += 20
            elif unique >= 2: score += 10

        # Duración promedio de clusters corta = fragmentado
        if self._cluster_durations:
            avg_dur = sum(self._cluster_durations) / len(self._cluster_durations)
            if avg_dur < 5:    score += 30
            elif avg_dur < 10: score += 15
            elif avg_dur < 15: score += 5

        return max(0, min(score, 100))

    # ── SIGNAL CONGESTION ─────────────────────────────────────────

    def _calc_signal_congestion(self) -> tuple:
        """
        Densidad de señales activas en la ventana reciente.
        Muchas señales simultáneas = congestion = riesgo de redundancia.

        Cuenta cuántos setups A/B/C aparecen en la ventana CONGESTION_WINDOW.
        """
        opp_list = list(self._opp_history)
        if not opp_list:
            return 0, "NONE"

        active = sum(1 for o in opp_list if o in ("A", "B", "C"))
        rate   = active / len(opp_list)

        # ETS activos en ventana
        ets_list   = list(self._ets_history)[-self.CONGESTION_WINDOW:]
        ets_active = sum(1 for e in ets_list if e >= 35)

        # Score compuesto
        score = int(rate * 60) + int(ets_active / max(len(ets_list), 1) * 40)
        score = max(0, min(score, 100))

        if score >= 60:   label = "HIGH"
        elif score >= 30: label = "MEDIUM"
        elif score >= 10: label = "LOW"
        else:             label = "NONE"

        return score, label

    # ── SETUP CORRELATION ─────────────────────────────────────────

    def _calc_setup_correlation(self, current_env: str) -> int:
        """
        Correlación entre setups dentro del mismo régimen.
        Alto = señales redundantes (misma causa, misma señal).
        Bajo = señales independientes (diversificación real).

        Medido como: % de setups A que ocurren en el mismo régimen
        vs total de setups A en la sesión.
        """
        total_a = sum(
            1 for setups in self._setups_by_regime.values()
            for s in setups if s == "A"
        )
        if total_a == 0:
            return 0

        # Setups A en el régimen actual
        current_setups = self._setups_by_regime.get(current_env, [])
        current_a      = sum(1 for s in current_setups if s == "A")

        if total_a > 0:
            correlation = int(current_a / total_a * 100)
        else:
            correlation = 0

        # Si todos los setups son del mismo régimen = máxima correlación
        return max(0, min(correlation, 100))

    # ── EXPOSURE STACKING ─────────────────────────────────────────

    def _calc_exposure_stacking(self, ets: int, ev: str,
                                  opp: str, trap: int) -> int:
        score = 0

        recent_ets = list(self._ets_history)[-10:]
        ets_active = sum(1 for e in recent_ets if e >= 50)
        score += ets_active * 5

        recent_gtal = list(self._gtal_history)[-10:]
        gtal_valid  = sum(1 for g in recent_gtal if g == "VALID")
        score += gtal_valid * 15

        if opp in ("A", "B"):
            score += 20

        if trap >= 60:
            score -= 20

        return max(0, min(score, 100))

    # ── RISK CONCENTRATION ────────────────────────────────────────

    def _calc_risk_concentration(self, trap: int, bfr: int,
                                  eff: int, cluster: str) -> int:
        score = 0

        if trap >= 70:   score += 40
        elif trap >= 50: score += 25
        elif trap >= 30: score += 10

        if bfr >= 50:    score += 25
        elif bfr >= 30:  score += 15

        if cluster == "TOXIC_CLUSTER":     score += 20
        elif cluster == "TRANSITION_ZONE": score += 10

        if eff < 20:     score += 15
        elif eff < 35:   score += 5

        if self._regime_changes >= 10:     score += 10

        return max(0, min(score, 100))

    # ── TRANSITION ZONE ───────────────────────────────────────────

    def _is_transition_zone(self) -> bool:
        if len(self._env_history) < 5:
            return False
        env_list = list(self._env_history)[-5:]
        return len(set(env_list)) >= 3

    # ── PRS — PORTFOLIO RISK SCORE ────────────────────────────────

    def _calc_prs(self, rci: int, fragmentation: int,
                   congestion: int, correlation: int,
                   cluster: str, overlap: bool,
                   toxic: bool) -> int:
        """
        Portfolio Risk Score consolidado (0-100).
        Alto = sistema en condición de riesgo estructural.
        Bajo = condiciones favorables.

        Componentes ponderados:
        - RCI (riesgo de concentración):   30%
        - Fragmentation (inestabilidad):   25%
        - Congestion (señales redundantes):20%
        - Correlation (diversificación):   15%
        - Flags (overlap/toxic):           10%
        """
        prs = int(
            rci           * 0.30 +
            fragmentation * 0.25 +
            congestion    * 0.20 +
            correlation   * 0.15
        )

        # Flags
        if overlap: prs += 5
        if toxic:   prs += 5

        # Cluster bonus/penalty
        if cluster == "TREND_CLUSTER":      prs -= 10  # buen contexto
        elif cluster == "TOXIC_CLUSTER":    prs += 10  # mal contexto
        elif cluster == "TRANSITION_ZONE":  prs += 5

        return max(0, min(prs, 100))

    # ── SESSION SUMMARY ───────────────────────────────────────────

    def session_summary(self) -> dict:
        env_list = list(self._env_long)
        toxic    = sum(1 for e in env_list if e in ("TRAPPY", "CHOPPY"))
        trend    = sum(1 for e in env_list if e == "EFFICIENT_TREND")

        avg_dur = (
            round(sum(self._cluster_durations) /
                  len(self._cluster_durations), 1)
            if self._cluster_durations else 0
        )

        return {
            "regime_changes":         self._regime_changes,
            "avg_cluster_duration":   avg_dur,
            "toxic_env_count":        toxic,
            "trend_env_count":        trend,
            "current_cluster":        self._current_cluster,
            "total_a_setups":         sum(self._a_setup_recent),
            "fragmentation_index":    self._calc_fragmentation(),
            "signal_congestion":      self._calc_signal_congestion()[1],
        }