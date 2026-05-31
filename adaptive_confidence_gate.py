# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ V3 — adaptive_confidence_gate.py
#  Adaptive Confidence Gate v1.2
#
#  CAMBIOS v1.2 vs v1.1:
#  ─ Condición 5 acepta micro_over_rejection como alternativa a
#    filtering_quality == OVER_REJECTION
#  ─ micro_or leído desde adapt_r.micro_over_rejection (v2.1)
#  ─ activation_reason incluye fuente (OR o MICRO_OR)
#  ─ rejection_reason más informativo cuando falla condición 5
#
#  FIX v1.1:
#  - register_outcome solo se registra cuando ACG activó en esa barra
#  - consecutive_fail_lock solo aplica después de activaciones reales
#
#  FUNCIÓN:
#  Determina si el conf_threshold puede relajarse de 65 a 55
#  en contextos institucionalmente específicos donde
#  AdaptiveParameterLayer detecta OVER_REJECTION o MICRO_OR.
#
#  MODO DE OPERACIÓN:
#  RESEARCH MODE — NO modifica el validator principal.
#  Solo reporta effective_conf_threshold como información.
#  El validator sigue usando su threshold original.
#
#  PROTECCIONES:
#  - Máximo 3 activaciones relaxed por sesión
#  - Cooldown mínimo 5 barras entre activaciones
#  - Si 2 relaxed activations consecutivas terminan HB=True → lock
#  - Si PRS > 45 → STANDARD forzado
#  - NUNCA activa en CHOPPY/TRAPPY/DEAD_MARKET
#  - NUNCA activa con HB=True
#  - NUNCA activa con fragmentation > 25
# ╚══════════════════════════════════════════════════════════════════╝
 
from dataclasses import dataclass
from collections import deque
 
 
STANDARD_THRESHOLD      = 65
RELAXED_THRESHOLD       = 55
TOXIC_ENVIRONMENTS      = {"CHOPPY", "TRAPPY", "DEAD_MARKET"}
MAX_RELAXED_PER_SESSION = 3
COOLDOWN_BARS           = 5
CONSECUTIVE_FAIL_LOCK   = 2
 
 
@dataclass
class AdaptiveConfidenceResult:
    effective_conf_threshold: int  = STANDARD_THRESHOLD
    relaxed_mode_active:      bool = False
    safety_lock:              bool = False
    session_locked:           bool = False
    activation_reason:        str  = ""
    rejection_reason:         str  = ""
    regime_alignment:         str  = ""
    relaxed_count:            int  = 0
    bars_since_last:          int  = 0
    would_change_outcome:     bool = False
    # v1.2 — indica si activó por micro_or o por or estructural
    activated_by_micro:       bool = False
 
    def acg_line(self) -> str:
        if self.session_locked or self.safety_lock:
            return (f"ACG: mode=LOCKED conf={self.effective_conf_threshold} "
                    f"reason={self.rejection_reason[:40]}")
        elif self.relaxed_mode_active:
            src = "[MICRO_OR]" if self.activated_by_micro else "[STRUCT_OR]"
            return (f"ACG: mode=RELAXED conf={self.effective_conf_threshold} "
                    f"{src} reason={self.activation_reason} "
                    f"[{self.relaxed_count}/{MAX_RELAXED_PER_SESSION}]")
        else:
            return (f"ACG: mode=STANDARD conf={self.effective_conf_threshold} "
                    f"| {self.rejection_reason[:55]}")
 
    def __str__(self) -> str:
        return self.acg_line()
 
 
class AdaptiveConfidenceGate:
    """
    Adaptive Confidence Gate v1.2
 
    Evalúa si las condiciones institucionales permiten
    relajar el confidence threshold de 65 a 55.
 
    Requiere alineación de 10 condiciones simultáneas.
    Una sola falla → STANDARD mode.
 
    v1.2: La condición 5 acepta:
      - filtering_quality == "OVER_REJECTION"  (estructural, post-warmup)
      - micro_over_rejection == True            (micro, sin warmup)
    La micro permite activación en ventana EARLY_TREND
    antes de que el adaptive estructural complete su warmup.
 
    MODO INVESTIGACIÓN:
    Reporta el threshold efectivo sin inyectarlo al validator.
    """
 
    def __init__(self):
        self._relaxed_count:       int   = 0
        self._last_relaxed_bar:    int   = 0
        self._session_locked:      bool  = False
        self._session_lock_reason: str   = ""
        self._bar_count:           int   = 0
        self._last_acg_active_bar: int   = 0
        self._relaxed_outcomes:    deque = deque(maxlen=CONSECUTIVE_FAIL_LOCK)
        self._activation_log:      list  = []
 
    def analyze(self,
                env_r,
                conf_r,
                cont_r,
                etil_r,
                gtal_r,
                port_r,
                adapt_r,
                raw_data:  dict,
                bar_count: int) -> AdaptiveConfidenceResult:
 
        self._bar_count = bar_count
 
        env       = getattr(env_r,   "environment",              "ROTATIONAL")
        trap      = getattr(env_r,   "trap_density",             0)
        bfr       = getattr(env_r,   "breakout_failure_rate",    0)
        conf_sc   = getattr(conf_r,  "confirmation_score",       0)
        cont_p    = getattr(cont_r,  "continuation_probability", 0)
        ets       = getattr(etil_r,  "ets_score",                0)
        ev        = getattr(gtal_r,  "execution_validity",       "INVALID")
        hb_flag   = getattr(gtal_r,  "hindsight_bias_flag",      False)
        frag_idx  = getattr(port_r,  "fragmentation_index",      100)
        congestion= getattr(port_r,  "signal_congestion",        "HIGH")
        prs       = getattr(port_r,  "prs",                      100)
        filt_qual = getattr(adapt_r, "filtering_quality",        "OK")
 
        # ── v1.2 — leer micro_over_rejection del adaptive v2.1 ──
        micro_or        = getattr(adapt_r, "micro_over_rejection",   False)
        micro_align     = getattr(adapt_r, "micro_window_alignment", "LOW")
        micro_strength  = getattr(adapt_r, "micro_signal_strength",  0)
        # ─────────────────────────────────────────────────────────
 
        bars_since_last = bar_count - self._last_relaxed_bar
 
        # ── SESSION LOCK ──────────────────────────────────────────
        if self._session_locked:
            return AdaptiveConfidenceResult(
                effective_conf_threshold = STANDARD_THRESHOLD,
                session_locked           = True,
                safety_lock              = True,
                rejection_reason         = self._session_lock_reason,
                relaxed_count            = self._relaxed_count,
                bars_since_last          = bars_since_last,
            )
 
        # ── MAX ACTIVACIONES ──────────────────────────────────────
        if self._relaxed_count >= MAX_RELAXED_PER_SESSION:
            return AdaptiveConfidenceResult(
                effective_conf_threshold = STANDARD_THRESHOLD,
                session_locked           = True,
                rejection_reason         = f"max relaxed reached ({MAX_RELAXED_PER_SESSION}/session)",
                relaxed_count            = self._relaxed_count,
                bars_since_last          = bars_since_last,
            )
 
        # ── COOLDOWN ──────────────────────────────────────────────
        if (self._last_relaxed_bar > 0 and
                bars_since_last < COOLDOWN_BARS):
            return AdaptiveConfidenceResult(
                effective_conf_threshold = STANDARD_THRESHOLD,
                rejection_reason         = f"cooldown ({bars_since_last}/{COOLDOWN_BARS} bars)",
                relaxed_count            = self._relaxed_count,
                bars_since_last          = bars_since_last,
            )
 
        # ── PRS SAFETY ────────────────────────────────────────────
        if prs > 45:
            return AdaptiveConfidenceResult(
                effective_conf_threshold = STANDARD_THRESHOLD,
                rejection_reason         = f"PRS={prs} > 45",
                relaxed_count            = self._relaxed_count,
                bars_since_last          = bars_since_last,
            )
 
        # ── 10 CONDICIONES ────────────────────────────────────────
        conditions = self._check_all_conditions(
            env, ets, cont_p, ev, hb_flag,
            filt_qual, micro_or, frag_idx,
            congestion, bfr, trap
        )
 
        if not conditions["all_pass"]:
            return AdaptiveConfidenceResult(
                effective_conf_threshold = STANDARD_THRESHOLD,
                relaxed_mode_active      = False,
                rejection_reason         = conditions["fail_reason"],
                regime_alignment         = conditions["regime_alignment"],
                relaxed_count            = self._relaxed_count,
                bars_since_last          = bars_since_last,
            )
 
        # ── ACTIVAR RELAXED MODE ──────────────────────────────────
        self._relaxed_count    += 1
        self._last_relaxed_bar  = bar_count
        self._last_acg_active_bar = bar_count
 
        # Identificar fuente de activación
        activated_by_micro = (micro_or and filt_qual != "OVER_REJECTION")
 
        would_change = (RELAXED_THRESHOLD <= conf_sc < STANDARD_THRESHOLD)
 
        src_tag = "MICRO_OR" if activated_by_micro else "STRUCT_OR"
        activation_reason = (
            f"ET+ETS{ets}+cont{cont_p}+{src_tag}"
            + (f"+str{micro_strength}" if activated_by_micro else "")
        )
 
        self._activation_log.append({
            "bar":              bar_count,
            "conf":             conf_sc,
            "ets":              ets,
            "env":              env,
            "ev":               ev,
            "would_change":     would_change,
            "source":           src_tag,
            "micro_strength":   micro_strength if activated_by_micro else 0,
        })
 
        return AdaptiveConfidenceResult(
            effective_conf_threshold = RELAXED_THRESHOLD,
            relaxed_mode_active      = True,
            safety_lock              = False,
            session_locked           = False,
            activation_reason        = activation_reason,
            rejection_reason         = "",
            regime_alignment         = conditions["regime_alignment"],
            relaxed_count            = self._relaxed_count,
            bars_since_last          = 0,
            would_change_outcome     = would_change,
            activated_by_micro       = activated_by_micro,
        )
 
    # ── 10 CONDICIONES ────────────────────────────────────────────
 
    def _check_all_conditions(self,
                               env, ets, cont_p, ev, hb_flag,
                               filt_qual, micro_or,    # v1.2: micro_or añadido
                               frag_idx, congestion,
                               bfr, trap) -> dict:
 
        # 1. EFFICIENT_TREND
        if env != "EFFICIENT_TREND":
            return {"all_pass": False,
                    "fail_reason": f"env={env} (needs EFFICIENT_TREND)",
                    "regime_alignment": "MISALIGNED"}
 
        # 2. ETS >= 65
        if ets < 65:
            return {"all_pass": False,
                    "fail_reason": f"ETS={ets} < 65",
                    "regime_alignment": "PARTIAL"}
 
        # 3. Continuation >= 72
        if cont_p < 72:
            return {"all_pass": False,
                    "fail_reason": f"cont={cont_p} < 72",
                    "regime_alignment": "PARTIAL"}
 
        # 4. GTAL VALID
        if ev != "VALID":
            return {"all_pass": False,
                    "fail_reason": f"GTAL={ev} (needs VALID)",
                    "regime_alignment": "PARTIAL"}
 
        # ── v1.2 — CONDICIÓN 5 EXTENDIDA ─────────────────────────
        # Acepta: filtering_quality==OVER_REJECTION (estructural)
        #    O:   micro_over_rejection==True (micro, sin warmup)
        # Esto permite activación en ventana EARLY_TREND
        # antes de que el adaptive estructural complete su warmup.
        struct_or = (filt_qual == "OVER_REJECTION")
        if not struct_or and not micro_or:
            return {"all_pass": False,
                    "fail_reason": (
                        f"filter={filt_qual} micro_or={micro_or} "
                        f"(needs OVER_REJECTION or micro_or=True)"
                    ),
                    "regime_alignment": "PARTIAL"}
        # ─────────────────────────────────────────────────────────
 
        # 6. Fragmentation <= 25
        if frag_idx > 25:
            return {"all_pass": False,
                    "fail_reason": f"frag={frag_idx} > 25",
                    "regime_alignment": "PARTIAL"}
 
        # 7. Congestion != HIGH
        if congestion == "HIGH":
            return {"all_pass": False,
                    "fail_reason": "congestion=HIGH",
                    "regime_alignment": "PARTIAL"}
 
        # 8. BFR <= 30
        if bfr > 30:
            return {"all_pass": False,
                    "fail_reason": f"bfr={bfr} > 30",
                    "regime_alignment": "PARTIAL"}
 
        # 9. Trap <= 60
        if trap > 60:
            return {"all_pass": False,
                    "fail_reason": f"trap={trap} > 60",
                    "regime_alignment": "PARTIAL"}
 
        # 10. No hindsight bias
        if hb_flag:
            return {"all_pass": False,
                    "fail_reason": "HB=True",
                    "regime_alignment": "CONTAMINATED"}
 
        return {"all_pass": True,
                "fail_reason": "",
                "regime_alignment": "ALIGNED"}
 
    # ── REGISTER OUTCOME ──────────────────────────────────────────
 
    def register_relaxed_outcome(self, gtal_valid: bool, hb: bool) -> None:
        """
        Registra el outcome de una barra donde ACG ACTIVÓ.
        SOLO llamar cuando acg_r.relaxed_mode_active == True.
        """
        outcome = "PASS" if (gtal_valid and not hb) else "FAIL"
        self._relaxed_outcomes.append(outcome)
 
        outcomes = list(self._relaxed_outcomes)
        if (len(outcomes) >= CONSECUTIVE_FAIL_LOCK and
                all(o == "FAIL" for o in outcomes[-CONSECUTIVE_FAIL_LOCK:])):
            self._session_locked = True
            self._session_lock_reason = (
                f"consecutive relaxed failures "
                f"({CONSECUTIVE_FAIL_LOCK} activations ended FAIL/HB)"
            )
 
    def session_summary(self) -> dict:
        would_change = sum(
            1 for log in self._activation_log
            if log.get("would_change", False)
        )
        micro_activations = sum(
            1 for log in self._activation_log
            if log.get("source") == "MICRO_OR"
        )
        return {
            "total_relaxed_activations": self._relaxed_count,
            "max_allowed":               MAX_RELAXED_PER_SESSION,
            "session_locked":            self._session_locked,
            "would_change_outcome":      would_change,
            "micro_or_activations":      micro_activations,
            "struct_or_activations":     self._relaxed_count - micro_activations,
            "activation_log":            self._activation_log,
        }
 