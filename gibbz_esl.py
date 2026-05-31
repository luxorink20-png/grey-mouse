# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ V3 — gibbz_esl.py
#  Execution Simulation Layer v1.0
#
#  FUNCIÓN:
#  Simula si una señal VALIDADA por GTAL habría sido ejecutable
#  en mercado real considerando latencia, slippage, liquidez y
#  velocidad de movimiento.
#
#  ACTIVACIÓN:
#  Solo procesa barras donde GTAL_EV = "VALID"
#  Barras INVALID son ignoradas — output vacío.
#
#  OUTPUTS:
#  - execution_probability  (0-100)
#  - slippage_estimate      (puntos relativos)
#  - fill_likelihood        (HIGH / MEDIUM / LOW)
#  - latency_risk           (LOW / MEDIUM / HIGH)
#  - final_executable_score (0-100)
#
#  INTEGRACIÓN:
#  ETIL → Timing → Edge → Opportunity → GTAL → ESL → Logger
#
#  RESTRICCIÓN ABSOLUTA:
#  ESL NO modifica OPP, GTAL, ETS, ni edge scores.
#  Solo agrega "Execution Realism Score".
#  NO optimiza performance ni winrate.
#  Solo modela realidad de ejecución.
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque
from dataclasses import dataclass


@dataclass
class ESLResult:
    active:                  bool  = False   # False si GTAL=INVALID
    execution_probability:   int   = 0       # 0-100
    slippage_estimate:       float = 0.0     # puntos estimados
    fill_likelihood:         str   = "LOW"   # HIGH / MEDIUM / LOW
    latency_risk:            str   = "HIGH"  # LOW / MEDIUM / HIGH
    final_executable_score:  int   = 0       # 0-100
    skip_reason:             str   = ""      # por qué no aplica

    def __str__(self) -> str:
        if not self.active:
            return f"ESL=SKIP ({self.skip_reason})"
        return (f"ESL={self.final_executable_score:3d} "
                f"Fill={self.fill_likelihood:<6} "
                f"Slip={self.slippage_estimate:.2f} "
                f"Lat={self.latency_risk}")


class ESLEngine:
    """
    Execution Simulation Layer.

    Modela la realidad de ejecución para señales ya validadas por GTAL.
    Solo actúa post-GTAL — no interfiere con ninguna capa anterior.

    3 capas de análisis:
    1. Distance Model   — cuántas barras entre ETS y GTAL_VALID
    2. Speed Model      — aceleración del movimiento penaliza fill
    3. Liquidity Model  — proxy de liquidez via cont/eff/env
    """

    TICK_SIZE = 0.25  # MES tick

    def __init__(self):
        self._price_history:   deque = deque(maxlen=10)
        self._volume_history:  deque = deque(maxlen=10)
        self._range_history:   deque = deque(maxlen=10)
        self._delta_history:   deque = deque(maxlen=10)
        self._gtal_valid_bar:  int   = 0
        self._ets_first_bar:   int   = 0
        self._bar_count:       int   = 0

    def analyze(self,
                gtal_r,
                etil_r,
                timing_r,
                env_r,
                cont_r,
                raw_data: dict,
                bar_count: int) -> ESLResult:

        self._bar_count = bar_count

        # Actualizar historial siempre (independiente de GTAL)
        price  = raw_data.get("price",  0)
        volume = raw_data.get("volume", 0)
        high   = raw_data.get("high",   price)
        low    = raw_data.get("low",    price)
        delta  = raw_data.get("delta",  0)

        self._price_history.append(price)
        self._volume_history.append(volume)
        self._range_history.append(high - low)
        self._delta_history.append(abs(delta))

        # Track primer bar ETS activo
        ets = getattr(etil_r, "ets_score", 0)
        if ets >= 65 and self._ets_first_bar == 0:
            self._ets_first_bar = bar_count

        # ── GTAL GATE — solo procesar si VALID ───────────────────
        ev = getattr(gtal_r, "execution_validity", "INVALID")
        if ev != "VALID":
            return ESLResult(
                active=False,
                skip_reason=f"GTAL={ev}"
            )

        # Track primer bar GTAL valid
        if self._gtal_valid_bar == 0:
            self._gtal_valid_bar = bar_count

        # ── EXTRAER CONTEXTO ──────────────────────────────────────
        eff      = getattr(env_r,    "directional_efficiency", 50)
        trap     = getattr(env_r,    "trap_density",           0)
        bfr      = getattr(env_r,    "breakout_failure_rate",  0)
        env_name = getattr(env_r,    "environment",            "ROTATIONAL")
        cont_p   = getattr(cont_r,   "continuation_probability", 50)
        t_score  = getattr(timing_r, "entry_timing_score",     50)
        t_grade  = getattr(timing_r, "timing_grade",           "LATE")

        # ── 1. DISTANCE MODEL ─────────────────────────────────────
        # Distancia entre primer ETS y validación GTAL
        # Más distancia = más riesgo de ejecución tardía
        distance_score, latency_risk = self._distance_model(bar_count)

        # ── 2. SPEED MODEL ────────────────────────────────────────
        # Movimiento rápido = más slippage
        speed_penalty, slippage_est = self._speed_model()

        # ── 3. LIQUIDITY MODEL ────────────────────────────────────
        # Proxy de liquidez usando cont/eff/env
        liquidity_score, fill_likelihood = self._liquidity_model(
            cont_p, eff, env_name, trap, bfr
        )

        # ── EXECUTION PROBABILITY ─────────────────────────────────
        exec_prob = 50  # base

        # Distance contribution (max +25 / min -25)
        exec_prob += distance_score

        # Liquidity contribution (max +25)
        exec_prob += liquidity_score

        # Timing contribution (max +15)
        if t_grade == "OPTIMAL":    exec_prob += 15
        elif t_grade == "ACCEPTABLE": exec_prob += 8
        elif t_grade == "LATE":     exec_prob -= 10
        elif t_grade == "MISSED":   exec_prob -= 20

        # Speed penalty (max -20)
        exec_prob -= speed_penalty

        # Environment premium
        if env_name == "EFFICIENT_TREND": exec_prob += 10
        elif env_name in ("CHOPPY", "TRAPPY"): exec_prob -= 15

        exec_prob = max(0, min(exec_prob, 100))

        # ── FINAL EXECUTABLE SCORE ────────────────────────────────
        # Combina execution_prob con GTAL RT score
        rt_score = getattr(gtal_r, "real_tradeability_score", 0)
        final_score = int(exec_prob * 0.6 + rt_score * 0.4)
        final_score = max(0, min(final_score, 100))

        return ESLResult(
            active                 = True,
            execution_probability  = exec_prob,
            slippage_estimate      = round(slippage_est, 2),
            fill_likelihood        = fill_likelihood,
            latency_risk           = latency_risk,
            final_executable_score = final_score,
        )

    # ── DISTANCE MODEL ────────────────────────────────────────────

    def _distance_model(self, bar_count: int) -> tuple:
        """
        Mide distancia entre primer ETS y validación GTAL.
        Distancia 0-2 bars = entrada limpia
        Distancia 3-5 bars = riesgo moderado
        Distancia 6+  bars = entrada tardía
        """
        if self._ets_first_bar == 0:
            return -10, "HIGH"

        distance = bar_count - self._ets_first_bar

        if distance <= 1:
            return 25, "LOW"
        elif distance <= 2:
            return 15, "LOW"
        elif distance <= 4:
            return 5,  "MEDIUM"
        elif distance <= 6:
            return -5, "MEDIUM"
        else:
            return -20, "HIGH"

    # ── SPEED MODEL ───────────────────────────────────────────────

    def _speed_model(self) -> tuple:
        """
        Movimiento rápido = más slippage.
        Usa rango de velas recientes como proxy de velocidad.
        """
        if len(self._range_history) < 3:
            return 0, self.TICK_SIZE * 2

        ranges = list(self._range_history)[-5:]
        avg_range = sum(ranges) / len(ranges)

        # Slippage estimado basado en velocidad
        if avg_range >= self.TICK_SIZE * 8:
            # Movimiento muy rápido — slippage alto
            slip = avg_range * 0.3
            return 20, round(slip, 2)
        elif avg_range >= self.TICK_SIZE * 5:
            slip = avg_range * 0.2
            return 10, round(slip, 2)
        elif avg_range >= self.TICK_SIZE * 3:
            slip = avg_range * 0.1
            return 5,  round(slip, 2)
        else:
            slip = self.TICK_SIZE * 1
            return 0,  round(slip, 2)

    # ── LIQUIDITY MODEL ───────────────────────────────────────────

    def _liquidity_model(self, cont_p: int, eff: int,
                          env_name: str, trap: int,
                          bfr: int) -> tuple:
        """
        Proxy de liquidez usando cont/eff/env.
        EFFICIENT_TREND = alta ejecutabilidad = fill más limpio.
        ROTATIONAL = fill menos predecible.
        """
        score = 0

        # Eff contribution
        if eff >= 80:   score += 15
        elif eff >= 60: score += 10
        elif eff >= 40: score += 5
        else:           score -= 5

        # Cont contribution
        if cont_p >= 80: score += 10
        elif cont_p >= 65: score += 5

        # Trap penaliza fill
        if trap >= 60:  score -= 15
        elif trap >= 40: score -= 8

        # BFR penaliza fill
        if bfr >= 30:   score -= 10
        elif bfr >= 15: score -= 5

        # Determinar fill likelihood
        total = score
        if total >= 15:   fill = "HIGH"
        elif total >= 5:  fill = "MEDIUM"
        else:             fill = "LOW"

        return score, fill