# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ V3 — gibbz_gtal.py
#  Ground Truth Alignment Layer v1.0
#
#  FUNCIÓN:
#  Valida si cada señal detectada por V3 habría sido ejecutable
#  en tiempo real — SIN información futura (zero hindsight bias).
#
#  PRINCIPIO:
#  Solo usa datos hasta la barra actual.
#  Compara lo que el sistema VE ahora vs lo que necesitaría
#  para ejecutar institucionalmente.
#
#  OUTPUTS:
#  - real_tradeability_score (0-100)
#  - hindsight_bias_flag     (True/False)
#  - execution_validity      (VALID / INVALID)
#
#  INTEGRACIÓN:
#  ETIL → Timing → Edge Decay → Opportunity → GTAL → Logger
#
#  RESTRICCIÓN CRÍTICA:
#  Este módulo hace el sistema MÁS SEVERO, no más rentable.
#  NO optimizar performance — aumentar fidelidad real.
#
#  INTOCABLE:
#  - No modifica ETS scoring
#  - No modifica conf_r
#  - No modifica cont_r
#  - No modifica edge decay
#  - No recalibra nada retrospectivamente
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque
from dataclasses import dataclass


@dataclass
class GTALResult:
    real_tradeability_score: int   = 0       # 0-100
    hindsight_bias_flag:     bool  = False   # True = señal contaminada
    execution_validity:      str   = "INVALID"  # VALID / INVALID
    bias_source:             str   = ""      # qué causó el hindsight
    rt_breakdown:            dict  = None    # componentes del score

    def __post_init__(self):
        if self.rt_breakdown is None:
            self.rt_breakdown = {}

    def __str__(self) -> str:
        hb = "HB=TRUE " if self.hindsight_bias_flag else "HB=FALSE"
        return (f"RT={self.real_tradeability_score:3d} "
                f"{hb} "
                f"EV={self.execution_validity}")


class GTALEngine:
    """
    Ground Truth Alignment Layer.

    Responde la pregunta institucional real:
    "En este momento exacto, con solo los datos disponibles hasta
    esta barra, ¿habría sido ejecutable esta señal?"

    No usa información futura.
    No suaviza señales.
    No recalibra retrospectivamente.
    Solo evalúa lo que el sistema VE ahora.
    """

    # Umbrales institucionales mínimos para ejecución real
    MIN_ETS_FOR_EXECUTION    = 65   # ETS mínimo en este momento
    MIN_CONF_FOR_EXECUTION   = 50   # conf mínimo en este momento
    MIN_CONT_FOR_EXECUTION   = 72   # cont mínimo en este momento
    MIN_EDGE_FOR_EXECUTION   = 40   # edge mínimo en este momento
    MAX_TRAP_FOR_EXECUTION   = 60   # trap máximo permitido
    MAX_BFR_FOR_EXECUTION    = 30   # bfr máximo permitido

    # Ventana de lookback para detección de inconsistencia
    CONSISTENCY_WINDOW = 5

    def __init__(self):
        self._ets_history:   deque = deque(maxlen=self.CONSISTENCY_WINDOW)
        self._conf_history:  deque = deque(maxlen=self.CONSISTENCY_WINDOW)
        self._cont_history:  deque = deque(maxlen=self.CONSISTENCY_WINDOW)
        self._edge_history:  deque = deque(maxlen=self.CONSISTENCY_WINDOW)
        self._env_history:   deque = deque(maxlen=self.CONSISTENCY_WINDOW)
        self._opp_history:   deque = deque(maxlen=self.CONSISTENCY_WINDOW)
        self._bar_count:     int   = 0

    def analyze(self,
                etil_r,
                timing_r,
                decay_r,
                opp_r,
                env_r,
                conf_r,
                cont_r,
                bar_count: int) -> GTALResult:

        self._bar_count = bar_count

        # ── Extraer valores ACTUALES (no futuros) ─────────────────
        ets       = getattr(etil_r,   "ets_score",              0)
        ets_class = getattr(etil_r,   "classification",         "NOISE")
        conf_sc   = getattr(conf_r,   "confirmation_score",     0)
        cont_p    = getattr(cont_r,   "continuation_probability", 0)
        edge_str  = getattr(decay_r,  "edge_strength",          0)
        expired   = getattr(decay_r,  "edge_expired",           True)
        decay_rt  = getattr(decay_r,  "decay_rate",             "NONE")
        trap      = getattr(env_r,    "trap_density",           0)
        bfr       = getattr(env_r,    "breakout_failure_rate",  0)
        env       = getattr(env_r,    "environment",            "ROTATIONAL")
        t_grade   = getattr(timing_r, "timing_grade",           "MISSED")
        t_score   = getattr(timing_r, "entry_timing_score",     0)
        opp_grade = getattr(opp_r,    "grade",                  "NONE")
        early_opp = getattr(timing_r, "early_opportunity",      False)

        # Actualizar historiales
        self._ets_history.append(ets)
        self._conf_history.append(conf_sc)
        self._cont_history.append(cont_p)
        self._edge_history.append(edge_str)
        self._env_history.append(env)
        self._opp_history.append(opp_grade)

        # ── HINDSIGHT BIAS DETECTION ──────────────────────────────
        hindsight_bias, bias_source = self._detect_hindsight_bias(
            ets, conf_sc, cont_p, edge_str, env,
            opp_grade, early_opp, expired, decay_rt
        )

        # ── REAL TRADEABILITY SCORE ───────────────────────────────
        rt_score, breakdown = self._calc_rt_score(
            ets, conf_sc, cont_p, edge_str,
            trap, bfr, env, t_score, t_grade,
            opp_grade, hindsight_bias, expired
        )

        # ── EXECUTION VALIDITY ────────────────────────────────────
        execution_validity = self._determine_validity(
            rt_score, hindsight_bias, opp_grade,
            ets, conf_sc, cont_p, edge_str,
            trap, bfr, expired
        )

        return GTALResult(
            real_tradeability_score = rt_score,
            hindsight_bias_flag     = hindsight_bias,
            execution_validity      = execution_validity,
            bias_source             = bias_source,
            rt_breakdown            = breakdown,
        )

    # ── HINDSIGHT BIAS DETECTION ──────────────────────────────────

    def _detect_hindsight_bias(self,
                                ets, conf_sc, cont_p, edge_str,
                                env, opp_grade, early_opp,
                                expired, decay_rt) -> tuple:
        """
        Detecta si la señal actual está contaminada por hindsight.

        Casos de hindsight bias:
        1. OPP=A pero conf < MIN_CONF — el sistema "sabe" que viene conf
        2. ETS alto pero edge ya EXPIRED — señal llegó tarde
        3. EARLY_TREND detectado pero ETS cae inmediatamente
        4. OPP=A en bar donde cont < MIN_CONT — inconsistencia temporal
        5. Timing OPTIMAL pero ETS inconsistente en ventana reciente
        """
        bias_sources = []

        # BIAS 1: Opportunity clasificada sin confirmation real
        if opp_grade in ("A", "B") and conf_sc < self.MIN_CONF_FOR_EXECUTION:
            bias_sources.append(f"OPP={opp_grade} sin conf real (conf={conf_sc})")

        # BIAS 2: Edge expirado pero aún clasificando como oportunidad
        if expired and opp_grade != "NONE":
            bias_sources.append(f"OPP={opp_grade} con edge expirado")

        # BIAS 3: ETS spike aislado — no sostenido
        if len(self._ets_history) >= 3:
            ets_list = list(self._ets_history)
            prev_avg = sum(ets_list[:-1]) / max(len(ets_list) - 1, 1)
            if ets >= 65 and prev_avg < 20:
                bias_sources.append(
                    f"ETS spike aislado (prev_avg={int(prev_avg)})")

        # BIAS 4: Cont alto pero historial inestable
        if cont_p >= self.MIN_CONT_FOR_EXECUTION:
            if len(self._cont_history) >= 3:
                cont_list = list(self._cont_history)
                min_cont = min(cont_list[:-1]) if len(cont_list) > 1 else cont_p
                if min_cont < 30:
                    bias_sources.append(
                        f"cont inestable (min_prev={min_cont})")

        # BIAS 5: Timing OPTIMAL pero ETS no sostenido en ventana
        if len(self._ets_history) >= self.CONSISTENCY_WINDOW:
            ets_list = list(self._ets_history)
            ets_above = sum(1 for e in ets_list if e >= 50)
            if ets_above < 2 and ets >= 65:
                bias_sources.append(
                    f"ETS no sostenido ({ets_above}/{self.CONSISTENCY_WINDOW} bars)")

        has_bias = len(bias_sources) > 0
        source   = " | ".join(bias_sources) if bias_sources else ""
        return has_bias, source

    # ── REAL TRADEABILITY SCORE ───────────────────────────────────

    def _calc_rt_score(self,
                       ets, conf_sc, cont_p, edge_str,
                       trap, bfr, env, t_score, t_grade,
                       opp_grade, hindsight_bias, expired) -> tuple:
        """
        Score de ejecutabilidad real en este momento exacto.
        Cada componente refleja el estado ACTUAL sin proyección futura.
        """
        score = 0
        breakdown = {}

        # Componente 1: ETS actual (max 25)
        if ets >= 65:      ets_pts = 25
        elif ets >= 50:    ets_pts = 15
        elif ets >= 35:    ets_pts = 8
        else:              ets_pts = 0
        score += ets_pts
        breakdown["ets"] = ets_pts

        # Componente 2: Confirmation actual (max 25)
        if conf_sc >= 70:  conf_pts = 25
        elif conf_sc >= 55: conf_pts = 18
        elif conf_sc >= 40: conf_pts = 10
        else:              conf_pts = 0
        score += conf_pts
        breakdown["conf"] = conf_pts

        # Componente 3: Continuation actual (max 20)
        if cont_p >= 80:   cont_pts = 20
        elif cont_p >= 65: cont_pts = 14
        elif cont_p >= 50: cont_pts = 7
        else:              cont_pts = 0
        score += cont_pts
        breakdown["cont"] = cont_pts

        # Componente 4: Edge vigor (max 15)
        if not expired and edge_str >= 70:  edge_pts = 15
        elif not expired and edge_str >= 50: edge_pts = 10
        elif not expired and edge_str >= 30: edge_pts = 5
        else:                               edge_pts = 0
        score += edge_pts
        breakdown["edge"] = edge_pts

        # Componente 5: Environment quality (max 10)
        if env == "EFFICIENT_TREND":   env_pts = 10
        elif env == "ROTATIONAL":      env_pts = 5
        else:                          env_pts = 0
        score += env_pts
        breakdown["env"] = env_pts

        # Penalizaciones ACTUALES (no futuras)
        # Penalizar trap alto
        if trap > self.MAX_TRAP_FOR_EXECUTION:
            trap_pen = min(20, (trap - self.MAX_TRAP_FOR_EXECUTION) // 2)
            score -= trap_pen
            breakdown["trap_penalty"] = -trap_pen

        # Penalizar bfr alto
        if bfr > self.MAX_BFR_FOR_EXECUTION:
            bfr_pen = min(15, (bfr - self.MAX_BFR_FOR_EXECUTION) // 3)
            score -= bfr_pen
            breakdown["bfr_penalty"] = -bfr_pen

        # Penalizar hindsight bias
        if hindsight_bias:
            score = int(score * 0.6)
            breakdown["hindsight_penalty"] = "x0.6"

        score = max(0, min(score, 100))
        return score, breakdown

    # ── EXECUTION VALIDITY ────────────────────────────────────────

    def _determine_validity(self,
                             rt_score, hindsight_bias, opp_grade,
                             ets, conf_sc, cont_p, edge_str,
                             trap, bfr, expired) -> str:
        """
        Determina si la señal es ejecutable institucionalmente
        en este momento exacto.

        VALID requiere:
        - RT score >= 45
        - Sin hindsight bias
        - OPP != NONE
        - Todos los componentes mínimos presentes
        - Edge no expirado
        """
        # Hard blocks — cualquiera invalida
        if hindsight_bias:
            return "INVALID"
        if expired:
            return "INVALID"
        if opp_grade == "NONE":
            return "INVALID"
        if rt_score < 45:
            return "INVALID"

        # Verificar mínimos institucionales ACTUALES
        if ets < self.MIN_ETS_FOR_EXECUTION:
            return "INVALID"
        if cont_p < self.MIN_CONT_FOR_EXECUTION:
            return "INVALID"
        if trap > self.MAX_TRAP_FOR_EXECUTION:
            return "INVALID"
        if bfr > self.MAX_BFR_FOR_EXECUTION:
            return "INVALID"

        return "VALID"