# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ V3 — adaptive_parameter_layer.py
#  Adaptive Parameter Learning Layer v2.1
#
#  CAMBIOS v2.1 vs v2.0:
#  ─ WINDOW_MICRO = 5: ventana dedicada a early trend analysis
#  ─ detect_micro_over_rejection(): detección rápida sin warmup
#  ─ micro_over_rejection:    True/False (señal paralela)
#  ─ micro_window_alignment:  HIGH/MED/LOW
#  ─ micro_signal_strength:   0-100
#
#  FILOSOFÍA v2.1:
#  El adaptive estructural sigue siendo lento y robusto.
#  La micro-window es el puente entre:
#    ETIL (edge transitorio rápido)
#    Adaptive (estabilidad estructural lenta)
#  Sin contaminar ninguno.
#
#  micro_over_rejection es EXCLUSIVO para ACG research mode.
#  NUNCA para ejecución real.
#  NO reemplaza filtering_quality estructural.
#
#  RESTRICCIÓN ABSOLUTA:
#  NO sobreescribe parámetros del core.
#  Solo "shadow mode" — propone overlays informativos.
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque
from dataclasses import dataclass, field
from typing import Dict


@dataclass
class AdaptiveParameterResult:
    # Shadow parameters (NO aplicados al core)
    shadow_conf_threshold:    int   = 65
    shadow_ets_threshold:     int   = 65
    shadow_decay_sensitivity: float = 1.0

    # Drift — LOW / MED / HIGH
    ets_drift:  str = "LOW"
    conf_drift: str = "LOW"

    # Decay shift
    decay_shift: str = "NO"

    # Filtering quality estructural
    over_rejection:    bool = False
    under_filtering:   bool = False
    filtering_quality: str = "OK"

    # ── v2.1 MICRO OVER-REJECTION ────────────────────────────────
    # Señal paralela — solo para ACG research mode
    # NO reemplaza filtering_quality
    micro_over_rejection:   bool = False    # detección en ventana de 5 barras
    micro_window_alignment: str  = "LOW"   # HIGH / MED / LOW
    micro_signal_strength:  int  = 0       # 0-100
    # ─────────────────────────────────────────────────────────────

    # Stability
    system_stability_score: int = 0

    # Calibración por régimen
    regime_calibration: dict = field(default_factory=dict)

    # Meta
    bars_observed:          int   = 0
    confidence_in_proposal: int   = 0

    def adaptive_line(self) -> str:
        """Output format según spec."""
        micro_str = ""
        if self.micro_over_rejection:
            micro_str = (f" | MICRO_OR=TRUE "
                         f"align={self.micro_window_alignment} "
                         f"str={self.micro_signal_strength}")
        return (
            f"ADAPTIVE: ets_drift={self.ets_drift:<4} "
            f"conf_drift={self.conf_drift:<4} "
            f"decay_shift={self.decay_shift:<3} "
            f"filter={self.filtering_quality:<16} "
            f"shadow_conf={self.shadow_conf_threshold} "
            f"shadow_ets={self.shadow_ets_threshold} "
            f"stab={self.system_stability_score:3d}"
            f"{micro_str}"
        )

    def __str__(self) -> str:
        return self.adaptive_line()


class AdaptiveParameterLayer:
    """
    Adaptive Parameter Learning Layer v2.1

    Observa distribuciones reales y detecta:
    - ETS drift por régimen (LOW/MED/HIGH)
    - Conf drift (LOW/MED/HIGH)
    - Decay shift estructural (YES/NO)
    - Over-rejection estructural (requiere warmup)
    - Under-filtering estructural (requiere warmup)

    v2.1 NUEVO:
    - Micro Over-Rejection Detection (WINDOW_MICRO=5)
      Responde: "¿El sistema perdió un edge real antes
      de que el adaptive pudiera reconocerlo?"
      Exclusivo para ACG research mode.

    SHADOW MODE: parámetros propuestos son SOLO informativos.
    El core NUNCA los usa.
    """

    WARMUP_BARS    = 20
    WINDOW_LONG    = 50
    WINDOW_SHORT   = 10
    WINDOW_DECAY   = 15
    WINDOW_MICRO   = 5     # v2.1 — ventana de early trend analysis

    # Umbrales del core — referencia inmutable
    CORE_CONF_THRESHOLD = 65
    CORE_ETS_THRESHOLD  = 65
    CORE_CONT_THRESHOLD = 72

    # Drift thresholds
    DRIFT_LOW  = 0.15
    DRIFT_MED  = 0.35
    DRIFT_HIGH = 0.55

    def __init__(self):
        # Distribuciones globales (sin cambios desde v2.0)
        self._ets_all:   deque = deque(maxlen=self.WINDOW_LONG)
        self._conf_all:  deque = deque(maxlen=self.WINDOW_LONG)
        self._cont_all:  deque = deque(maxlen=self.WINDOW_LONG)
        self._edge_all:  deque = deque(maxlen=self.WINDOW_LONG)
        self._hb_all:    deque = deque(maxlen=self.WINDOW_LONG)
        self._valid_all: deque = deque(maxlen=self.WINDOW_LONG)
        self._env_all:   deque = deque(maxlen=self.WINDOW_LONG)

        # Ventanas cortas para drift
        self._ets_short:  deque = deque(maxlen=self.WINDOW_SHORT)
        self._conf_short: deque = deque(maxlen=self.WINDOW_SHORT)
        self._edge_short: deque = deque(maxlen=self.WINDOW_DECAY)

        # Histórico de edge para decay shift
        self._edge_peaks:    deque = deque(maxlen=10)
        self._decay_windows: deque = deque(maxlen=5)

        # Por régimen
        self._ets_by_regime:  Dict[str, list] = {}
        self._conf_by_regime: Dict[str, list] = {}

        # ── v2.1 MICRO WINDOW ────────────────────────────────────
        # Almacena las últimas WINDOW_MICRO barras completas
        # para análisis de early trend sin warmup
        self._micro_ets:   deque = deque(maxlen=self.WINDOW_MICRO)
        self._micro_conf:  deque = deque(maxlen=self.WINDOW_MICRO)
        self._micro_cont:  deque = deque(maxlen=self.WINDOW_MICRO)
        self._micro_env:   deque = deque(maxlen=self.WINDOW_MICRO)
        self._micro_hb:    deque = deque(maxlen=self.WINDOW_MICRO)
        self._micro_valid: deque = deque(maxlen=self.WINDOW_MICRO)
        # ─────────────────────────────────────────────────────────

        self._bar_count: int = 0

    def analyze(self,
                etil_r,
                gtal_r,
                conf_r,
                cont_r,
                env_r,
                pnl_r,
                bar_count: int) -> AdaptiveParameterResult:

        self._bar_count = bar_count

        ets      = getattr(etil_r, "ets_score",              0)
        conf_sc  = getattr(conf_r, "confirmation_score",     0)
        cont_p   = getattr(cont_r, "continuation_probability", 0)
        hb_flag  = getattr(gtal_r, "hindsight_bias_flag",    False)
        ev       = getattr(gtal_r, "execution_validity",     "INVALID")
        env      = getattr(env_r,  "environment",            "ROTATIONAL")
        edge_eff = getattr(pnl_r,  "edge_efficiency_score",  0)

        # Acumular distribuciones globales (sin cambios)
        self._ets_all.append(ets)
        self._conf_all.append(conf_sc)
        self._cont_all.append(cont_p)
        self._edge_all.append(edge_eff)
        self._hb_all.append(1 if hb_flag else 0)
        self._valid_all.append(1 if ev == "VALID" else 0)
        self._env_all.append(env)
        self._ets_short.append(ets)
        self._conf_short.append(conf_sc)
        self._edge_short.append(edge_eff)

        if ets >= 65:
            self._edge_peaks.append(edge_eff)

        if env not in self._ets_by_regime:
            self._ets_by_regime[env]  = []
            self._conf_by_regime[env] = []
        self._ets_by_regime[env].append(ets)
        self._conf_by_regime[env].append(conf_sc)

        # ── v2.1 MICRO WINDOW — acumular siempre (sin warmup) ────
        self._micro_ets.append(ets)
        self._micro_conf.append(conf_sc)
        self._micro_cont.append(cont_p)
        self._micro_env.append(env)
        self._micro_hb.append(hb_flag)
        self._micro_valid.append(ev == "VALID")
        # ─────────────────────────────────────────────────────────

        # ── v2.1 MICRO OVER-REJECTION — sin warmup ───────────────
        micro_or, micro_align, micro_str = self._detect_micro_over_rejection()
        # ─────────────────────────────────────────────────────────

        # Warmup para análisis estructural
        if bar_count < self.WARMUP_BARS:
            return AdaptiveParameterResult(
                bars_observed           = bar_count,
                confidence_in_proposal  = int(bar_count / self.WARMUP_BARS * 30),
                # Micro disponible desde bar 1
                micro_over_rejection    = micro_or,
                micro_window_alignment  = micro_align,
                micro_signal_strength   = micro_str,
            )

        # Análisis estructural (sin cambios desde v2.0)
        ets_drift_level  = self._calc_ets_drift()
        conf_drift_level = self._calc_conf_drift()
        decay_shift      = self._detect_decay_shift()
        over_rej, under_filt, filt_quality = self._check_filtering_quality()
        shadow_conf  = self._propose_shadow_conf(over_rej, under_filt)
        shadow_ets   = self._propose_shadow_ets()
        shadow_decay = self._propose_shadow_decay(decay_shift)
        stability    = self._calc_stability()
        regime_calib = self._build_regime_calibration()
        confidence   = min(int(bar_count / 100 * 100), 85)

        return AdaptiveParameterResult(
            shadow_conf_threshold    = shadow_conf,
            shadow_ets_threshold     = shadow_ets,
            shadow_decay_sensitivity = shadow_decay,
            ets_drift                = ets_drift_level,
            conf_drift               = conf_drift_level,
            decay_shift              = decay_shift,
            over_rejection           = over_rej,
            under_filtering          = under_filt,
            filtering_quality        = filt_quality,
            system_stability_score   = stability,
            regime_calibration       = regime_calib,
            bars_observed            = bar_count,
            confidence_in_proposal   = confidence,
            # v2.1 micro — siempre disponible
            micro_over_rejection     = micro_or,
            micro_window_alignment   = micro_align,
            micro_signal_strength    = micro_str,
        )

    # ── v2.1 MICRO OVER-REJECTION DETECTION ──────────────────────

    def _detect_micro_over_rejection(self) -> tuple:
        """
        Detecta OVER_REJECTION en ventana de 5 barras.
        Disponible desde bar 1 — sin warmup.

        Responde: "¿El sistema perdió un edge real antes
        de que el adaptive estructural pudiera reconocerlo?"

        ACTIVA si TODAS estas condiciones se cumplen:
        1. Al menos 2 barras con ETS >= 65
        2. conf promedio < 60
        3. cont promedio >= 72
        4. EFFICIENT_TREND aparece al menos 1 vez
        5. GTAL=VALID aparece al menos 1 vez
        6. HB=False en todas las barras relevantes

        Returns:
            (micro_or: bool, alignment: str, strength: int)
        """
        ets_list   = list(self._micro_ets)
        conf_list  = list(self._micro_conf)
        cont_list  = list(self._micro_cont)
        env_list   = list(self._micro_env)
        hb_list    = list(self._micro_hb)
        valid_list = list(self._micro_valid)

        # Necesita al menos 3 barras para tener contexto mínimo
        if len(ets_list) < 3:
            return False, "LOW", 0

        # Condición 1: Al menos 2 barras con ETS >= 65
        high_ets_count = sum(1 for e in ets_list if e >= 65)
        if high_ets_count < 2:
            return False, "LOW", 0

        # Condición 2: conf promedio < 60
        avg_conf = sum(conf_list) / len(conf_list)
        if avg_conf >= 60:
            return False, "LOW", 0

        # Condición 3: al menos 1 barra con cont >= 72
        # Usamos max porque barras iniciales tienen cont bajo por warmup.
        # Si AL MENOS UNA barra tiene cont >= 72, señal válida.
        max_cont = max(cont_list)
        avg_cont = max_cont  # para calc_micro_strength
        if max_cont < 72:
            return False, "LOW", 0

        # Condición 4: EFFICIENT_TREND al menos 1 vez
        has_et = any(e == "EFFICIENT_TREND" for e in env_list)
        if not has_et:
            return False, "LOW", 0

        # Condición 5: GTAL=VALID al menos 1 vez
        has_valid = any(v for v in valid_list)
        if not has_valid:
            return False, "LOW", 0

        # Condición 6: barras con ETS >= 65 VALID no deben tener HB=True
        # Solo bloquear si HB=True Y GTAL=VALID simultáneamente.
        # Bar con HB=True pero INVALID (como bar 6) es filtrada por GTAL
        # y no contamina barras VALID siguientes.
        for i, ets_val in enumerate(ets_list):
            if ets_val >= 65 and i < len(hb_list) and i < len(valid_list):
                if hb_list[i] and valid_list[i]:
                    return False, "LOW", 0

        # ── Todas las condiciones cumplidas — calcular fuerza ────
        strength = self._calc_micro_strength(
            high_ets_count, avg_conf, avg_cont, ets_list, valid_list
        )

        alignment = (
            "HIGH" if strength >= 70 else
            "MED"  if strength >= 45 else
            "LOW"
        )

        return True, alignment, strength

    def _calc_micro_strength(self,
                              high_ets_count: int,
                              avg_conf: float,
                              avg_cont: float,
                              ets_list: list,
                              valid_list: list) -> int:
        """
        Calcula la fuerza de la señal micro OR (0-100).

        Componentes:
        - ETS density:     cuántas barras tienen ETS >= 65 (max 30)
        - conf gap:        cuánto está por debajo del threshold (max 25)
        - cont quality:    cuánto supera el mínimo de 72 (max 25)
        - GTAL valid ratio: proporción de barras VALID (max 20)
        """
        score = 0

        # ETS density (max 30)
        ets_density = high_ets_count / max(len(ets_list), 1)
        score += int(ets_density * 30)

        # Conf gap — cuánto está por debajo del threshold
        # Gap grande = señal más clara de over_rejection
        conf_gap = self.CORE_CONF_THRESHOLD - avg_conf
        if conf_gap >= 20:   score += 25
        elif conf_gap >= 10: score += 15
        elif conf_gap >= 5:  score += 8

        # Cont quality — cont alto = señal real
        cont_excess = avg_cont - self.CORE_CONT_THRESHOLD
        if cont_excess >= 20:   score += 25
        elif cont_excess >= 10: score += 15
        elif cont_excess >= 0:  score += 8

        # GTAL valid ratio (max 20)
        valid_rate = sum(1 for v in valid_list if v) / max(len(valid_list), 1)
        score += int(valid_rate * 20)

        return max(0, min(score, 100))

    # ── MÉTODOS ESTRUCTURALES (sin cambios desde v2.0) ───────────

    def _calc_ets_drift(self) -> str:
        if (len(self._ets_short) < self.WINDOW_SHORT or
                len(self._ets_all) < self.WINDOW_LONG // 2):
            return "LOW"
        hist_avg  = sum(self._ets_all)   / len(self._ets_all)
        short_avg = sum(self._ets_short) / len(self._ets_short)
        if hist_avg <= 0:
            return "LOW"
        drift_ratio = abs(short_avg - hist_avg) / max(hist_avg, 1)
        if drift_ratio >= self.DRIFT_HIGH:   return "HIGH"
        elif drift_ratio >= self.DRIFT_MED:  return "MED"
        return "LOW"

    def _calc_conf_drift(self) -> str:
        if (len(self._conf_short) < self.WINDOW_SHORT or
                len(self._conf_all) < self.WINDOW_LONG // 2):
            return "LOW"
        hist_avg  = sum(self._conf_all)   / len(self._conf_all)
        short_avg = sum(self._conf_short) / len(self._conf_short)
        if hist_avg <= 0:
            return "LOW"
        drift_ratio = abs(short_avg - hist_avg) / max(hist_avg, 1)
        if drift_ratio >= self.DRIFT_HIGH:   return "HIGH"
        elif drift_ratio >= self.DRIFT_MED:  return "MED"
        return "LOW"

    def _detect_decay_shift(self) -> str:
        edge_list = list(self._edge_short)
        if len(edge_list) < 8:
            return "NO"
        mid    = len(edge_list) // 2
        first  = edge_list[:mid]
        second = edge_list[mid:]
        peak_first = max(first) if first else 0
        if peak_first > 50:
            drop = peak_first - (sum(second) / len(second) if second else peak_first)
            if drop >= 30:
                if len(self._edge_peaks) >= 3:
                    avg_peak = sum(self._edge_peaks) / len(self._edge_peaks)
                    if abs(peak_first - avg_peak) / max(avg_peak, 1) >= 0.30:
                        return "YES"
        return "NO"

    def _check_filtering_quality(self) -> tuple:
        if len(self._ets_all) < 20:
            return False, False, "OK"
        ets_list   = list(self._ets_all)
        conf_list  = list(self._conf_all)
        valid_list = list(self._valid_all)
        hb_list    = list(self._hb_all)
        high_ets_count  = sum(1 for e in ets_list if e >= 50)
        high_conf_count = sum(1 for c in conf_list if c >= self.CORE_CONF_THRESHOLD)
        ets_rate  = high_ets_count  / max(len(ets_list),  1)
        conf_rate = high_conf_count / max(len(conf_list), 1)
        over_rejection = (ets_rate >= 0.05 and conf_rate < 0.01)
        valid_count  = sum(valid_list)
        hb_and_valid = sum(
            1 for v, h in zip(valid_list, hb_list)
            if v == 1 and h == 1
        )
        under_filtering = (
            valid_count > 0 and
            hb_and_valid / max(valid_count, 1) >= 0.20
        )
        if over_rejection:   quality = "OVER_REJECTION"
        elif under_filtering: quality = "UNDER_FILTERING"
        else:                quality = "OK"
        return over_rejection, under_filtering, quality

    def _propose_shadow_conf(self, over_rej: bool, under_filt: bool) -> int:
        if over_rej:
            conf_list = list(self._conf_all)
            sorted_c  = sorted(conf_list)
            p90       = sorted_c[int(len(sorted_c) * 0.90)]
            return min(max(p90, 55), self.CORE_CONF_THRESHOLD)
        elif under_filt:
            return min(self.CORE_CONF_THRESHOLD + 5, 75)
        return self.CORE_CONF_THRESHOLD

    def _propose_shadow_ets(self) -> int:
        ets_list = list(self._ets_all)
        if len(ets_list) < 10:
            return self.CORE_ETS_THRESHOLD
        high_ets = sum(1 for e in ets_list if e >= 65)
        if high_ets / len(ets_list) > 0.20:
            return min(self.CORE_ETS_THRESHOLD + 5, 75)
        return self.CORE_ETS_THRESHOLD

    def _propose_shadow_decay(self, decay_shift: str) -> float:
        edge_list = list(self._edge_all)
        if len(edge_list) < 10:
            return 1.0
        if decay_shift == "YES":
            recent = edge_list[-5:]
            if max(recent) > 60 and min(recent) < 20:
                return 1.3
        elif max(edge_list[-5:] if len(edge_list) >= 5 else [0]) < 40:
            return 0.8
        return 1.0

    def _calc_stability(self) -> int:
        if len(self._valid_all) < 5:
            return 50
        score    = 100
        hb_rate  = sum(self._hb_all) / max(len(self._hb_all), 1)
        score   -= int(hb_rate * 40)
        if self._edge_all:
            avg_edge = sum(self._edge_all) / len(self._edge_all)
            if avg_edge < 20:
                score -= 15
        return max(0, min(score, 100))

    def _build_regime_calibration(self) -> dict:
        calib = {}
        for regime, ets_vals in self._ets_by_regime.items():
            if len(ets_vals) < 3:
                continue
            conf_vals = self._conf_by_regime.get(regime, [])
            calib[regime] = {
                "bars_seen":  len(ets_vals),
                "avg_ets":    round(sum(ets_vals) / len(ets_vals), 1),
                "max_ets":    max(ets_vals),
                "avg_conf":   round(sum(conf_vals) / max(len(conf_vals), 1), 1),
                "ets_active": sum(1 for e in ets_vals if e >= 65),
            }
        return calib

    def session_report(self) -> dict:
        if not self._ets_all:
            return {}
        ets_list  = list(self._ets_all)
        conf_list = list(self._conf_all)
        hb_list   = list(self._hb_all)
        over_rej, under_filt, filt_q = self._check_filtering_quality()
        micro_or, micro_align, micro_str = self._detect_micro_over_rejection()
        return {
            "bars_analyzed":       self._bar_count,
            "ets_distribution": {
                "avg":          round(sum(ets_list) / len(ets_list), 1),
                "max":          max(ets_list),
                "pct_above_65": round(
                    sum(1 for e in ets_list if e >= 65) / len(ets_list) * 100, 1),
            },
            "conf_distribution": {
                "avg":          round(sum(conf_list) / len(conf_list), 1),
                "max":          max(conf_list),
                "pct_above_65": round(
                    sum(1 for c in conf_list if c >= 65) / len(conf_list) * 100, 1),
            },
            "ets_drift":              self._calc_ets_drift(),
            "conf_drift":             self._calc_conf_drift(),
            "decay_shift":            self._detect_decay_shift(),
            "filtering_quality":      filt_q,
            "over_rejection":         over_rej,
            "under_filtering":        under_filt,
            "micro_over_rejection":   micro_or,
            "micro_window_alignment": micro_align,
            "micro_signal_strength":  micro_str,
            "hindsight_rate_pct":     round(sum(hb_list) / len(hb_list) * 100, 1),
            "system_stability":       self._calc_stability(),
            "shadow_conf_proposed":   self._propose_shadow_conf(over_rej, under_filt),
            "shadow_ets_proposed":    self._propose_shadow_ets(),
            "core_conf_threshold":    self.CORE_CONF_THRESHOLD,
            "core_ets_threshold":     self.CORE_ETS_THRESHOLD,
            "regime_calibration":     self._build_regime_calibration(),
        }