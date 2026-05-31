# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — continuation_engine.py
#  Institutional Continuation Probability Engine v1.2
#
#  CAMBIOS v1.2 vs v1.1:
#  ─ CONT_BASE OVERRIDE: cuando raw_data["env"]=EFFICIENT_TREND,
#    cont_base se eleva a max(cont_base, 68) para corregir latencia
#    del session_regime que devuelve BALANCED_DAY durante expansión real
#  ─ TREND_DAY override: cuando raw_data["env"]=TREND_DAY, cont_base
#    se eleva a max(cont_base, 62)
#  ─ Override usa max() → NUNCA reduce cont_base, solo lo eleva
#  ─ trend_override_active: flag para logging/debugging
#  ─ REQUIERE: replay_debug.py inyecte raw["env"] = env_r.environment
#    antes de llamar a continuation.analyze() (línea 103)
#
#  PROBLEMA RESUELTO v1.2:
#  cont_base=50 (BALANCED_DAY) cuando env=EFFICIENT_TREND causaba
#  cont=60 < threshold=78 → trade bloqueado en 2 sesiones confirmadas:
#    → 02/02/2026 bar 8:  eff=100 sc=52 BLOQUEADO
#    → 22/01/2026 bar 158: eff=80  sc=64 BLOQUEADO
#  Con override cont_base=68 → cont≈78-82 → supera threshold=78
#
#  RIESGO NTE: CERO
#  EFFICIENT_TREND no apareció en ninguna de las 9 sesiones NTE.
#  El override solo activa en entorno de expansión real.
#
#  ROLLBACK:
#  1. Eliminar bloque v1.2 CONT_BASE OVERRIDE en este archivo
#  2. Eliminar línea raw["env"] = env_r.environment en replay_debug.py
#
#  CAMBIOS v1.1 vs v1.0:
#  ─ WEAK penalty ahora es DINÁMICA por régimen y por delta real
#  ─ Delta override: delta consistente fuerte en 3+ barras cancela
#    la penalización de bq_type WEAK (el movimiento real manda)
#  ─ Imbalance lookback: usa últimas 3 barras (no 5) para que
#    el delta reciente domine sobre el histórico del buffer
#  ─ Regime boost: EFFICIENT_TREND ahora suma como TREND_DAY
#  ─ Runner probability: EFFICIENT_TREND recibe boost igual a TREND_DAY
#
#  SIN CAMBIOS:
#  ─ follow_through logic
#  ─ pullback_analysis
#  ─ absorption_after_break
#  ─ expansion_speed
#  ─ estructura general del score
#  ─ continuation_quality thresholds
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ContinuationResult:
    continuation_probability:  int   = 50
    runner_probability:        int   = 30
    continuation_quality:      str   = "UNKNOWN"
    follow_through_strength:   int   = 0
    continuation_bias:         str   = "NEUTRAL"
    pullback_depth:            float = 0.0
    pullback_healthy:          bool  = False
    absorption_after_break:    bool  = False
    imbalance_persistence:     int   = 0
    speed_score:               int   = 0

    def __str__(self) -> str:
        return (f"cont={self.continuation_probability}% "
                f"runner={self.runner_probability}% "
                f"quality={self.continuation_quality} "
                f"bias={self.continuation_bias}")


@dataclass
class ContBar:
    price:      float
    high:       float
    low:        float
    open:       float
    close:      float
    delta:      float
    volume:     float
    price_move: float
    absorption: bool


# ── v1.1 — Clasificación de regímenes para penalización dinámica ──
_TREND_REGIMES      = {"TREND_DAY", "STRONG_TREND", "MOMENTUM",
                       "EXPANSION_DAY", "EFFICIENT_TREND"}
_ROTATIONAL_REGIMES = {"ROTATIONAL", "BALANCED_DAY", "COMPRESSION",
                       "LOW_VOL", "RANGE_DAY"}

# Penalización WEAK por régimen
_WEAK_PENALTY = {
    "TREND":      -8,    # En trend, WEAK con buen delta es válido
    "ROTATIONAL": -20,   # En rotational, WEAK merece penalización completa
    "DEFAULT":    -15,   # Resto
}
# ─────────────────────────────────────────────────────────────────


def _get_regime_class(regime: str) -> str:
    if regime in _TREND_REGIMES:
        return "TREND"
    if regime in _ROTATIONAL_REGIMES:
        return "ROTATIONAL"
    return "DEFAULT"


class ContinuationEngine:
    """
    Evalúa probabilidad de continuación post-breakout.

    v1.2 — Cambios clave:
    - cont_base override cuando raw_data["env"]=EFFICIENT_TREND (→68)
      o raw_data["env"]=TREND_DAY (→62)
    - raw_data["env"] es inyectado por replay_debug.py antes de llamar
      a este método: raw["env"] = env_r.environment
    - Override usa max() — nunca reduce cont_base existente
    - trend_override_active flag para debugging

    v1.1 — Cambios clave:
    - Penalización WEAK dinámica por régimen
    - Delta override: delta fuerte sostenido cancela penalización WEAK
    - Imbalance lookback = 3 barras (antes 5) → delta reciente domina
    - EFFICIENT_TREND recibe mismo boost que TREND_DAY en runner_prob
    """

    def __init__(self, window: int = 12, tick: float = 0.25):
        self._bars:        deque = deque(maxlen=window)
        self._tick:        float = tick
        self._avg_volume:  float = 0.0
        self._vol_samples: deque = deque(maxlen=30)

    def analyze(self, event_result: dict,
                confirmation,
                session_regime,
                raw_data: dict) -> ContinuationResult:

        ctx        = event_result.get("context", {})
        price      = float(raw_data.get("price",  0))
        high       = float(raw_data.get("high",   price))
        low        = float(raw_data.get("low",    price))
        open_      = float(raw_data.get("open",   price))
        close      = float(raw_data.get("close",  price))
        volume     = float(raw_data.get("volume", ctx.get("volume", 0)))
        delta      = ctx.get("delta",      0)
        price_move = ctx.get("price_move", 0)
        absorption = ctx.get("absorption", False)

        if volume > 0:
            self._vol_samples.append(volume)
        self._avg_volume = (sum(self._vol_samples) / len(self._vol_samples)
                            if self._vol_samples else volume)

        bar = ContBar(price=price, high=high, low=low, open=open_,
                      close=close, delta=delta, volume=volume,
                      price_move=price_move, absorption=absorption)
        self._bars.append(bar)

        if len(self._bars) < 3:
            return ContinuationResult(
                continuation_probability = 50,
                runner_probability       = 25,
                continuation_quality     = "UNKNOWN",
            )

        bars = list(self._bars)

        # ── Contexto externo ──────────────────────────────────────
        bq_type   = getattr(confirmation, "breakout_type",       "NONE")
        acc_type  = getattr(confirmation, "acceptance_type",     "NONE")
        conf_sc   = getattr(confirmation, "confirmation_score",   50)
        exp_eff   = getattr(confirmation, "expansion_efficiency", 0.5)

        regime    = getattr(session_regime, "session_regime",         "BALANCED_DAY")
        trend_str = getattr(session_regime, "trend_strength",          40)
        cont_base = getattr(session_regime, "continuation_probability", 50)

        # ── v1.2 — CONT_BASE OVERRIDE ─────────────────────────────
        # PROBLEMA CONFIRMADO EN 2 SESIONES (evidencia directa):
        #   02/02/2026 bar 8:   EFFICIENT_TREND eff=100 → cont=60 BLOQUEADO
        #   22/01/2026 bar 158: EFFICIENT_TREND eff=80  → cont=60 BLOQUEADO
        #
        # env viene de market_environment_analyzer (env_r.environment)
        # inyectado en raw_data por replay_debug.py línea 103:
        #   raw["env"] = env_r.environment
        #
        # session_regime.session_regime NO contiene EFFICIENT_TREND —
        # ese string es exclusivo del market_environment_analyzer.
        #
        # REGLAS DE SEGURIDAD:
        #   - Usa max() → NUNCA reduce cont_base, solo lo eleva
        #   - Solo activa con env=EFFICIENT_TREND o TREND_DAY
        #   - Sin efecto en ROTATIONAL/CHOPPY/TRAPPY/COMPRESSION
        #
        # ROLLBACK:
        #   1. Eliminar este bloque
        #   2. Eliminar raw["env"] = env_r.environment en replay_debug.py
        # ─────────────────────────────────────────────────────────
        trend_override_active = False
        env = raw_data.get("env", "")

        if env == "EFFICIENT_TREND":
            cont_base = max(cont_base, 68)
            trend_override_active = True
        elif env == "TREND_DAY":
            cont_base = max(cont_base, 62)
            trend_override_active = True
        # ── FIN v1.2 CONT_BASE OVERRIDE ───────────────────────────

        reg_class = _get_regime_class(regime)

        # ── MÉTRICAS ──────────────────────────────────────────────
        ft_score, ft_bias = self._follow_through(bars)
        pb_depth, pb_ok   = self._pullback_analysis(bars)
        absorb_post       = self._absorption_after_break(bars)
        imbalance         = self._imbalance_persistence(bars)
        speed             = self._expansion_speed(bars)

        # ── v1.1 — DELTA OVERRIDE ─────────────────────────────────
        recent3        = bars[-3:]
        bull_delta_str = sum(1 for b in recent3 if b.delta > 200)
        bear_delta_str = sum(1 for b in recent3 if b.delta < -200)
        delta_override = bull_delta_str >= 2 or bear_delta_str >= 2
        # ─────────────────────────────────────────────────────────

        # ── CONTINUATION PROBABILITY ──────────────────────────────
        cont_prob = cont_base

        if bq_type == "EXPLOSIVE":
            cont_prob += 18
        elif bq_type == "REAL":
            cont_prob += 10
        elif bq_type == "MODERATE":
            cont_prob += 0
        elif bq_type == "WEAK":
            if delta_override:
                cont_prob += 0
            else:
                cont_prob += _WEAK_PENALTY.get(reg_class,
                             _WEAK_PENALTY["DEFAULT"])
        elif bq_type == "FAKE":
            cont_prob -= 35

        if ft_score >= 75:   cont_prob += 12
        elif ft_score >= 50: cont_prob += 5
        elif ft_score < 30:  cont_prob -= 12

        if pb_ok:
            cont_prob += 8
        elif pb_depth > self._tick * 6:
            cont_prob -= 10

        if absorb_post:
            cont_prob -= 15

        if acc_type == "ACCEPTED":
            cont_prob += 8
        elif acc_type == "RECLAIM":
            cont_prob -= 20

        if imbalance >= 70:   cont_prob += 10
        elif imbalance < 30:  cont_prob -= 8

        if speed >= 70:
            cont_prob += 6

        cont_prob = max(5, min(cont_prob, 95))

        # ── RUNNER PROBABILITY ────────────────────────────────────
        runner_prob = cont_prob - 20

        if regime in ("TREND_DAY", "EXPANSION_DAY", "EFFICIENT_TREND"):
            runner_prob += 15
        elif regime in ("BALANCED_DAY", "ROTATIONAL_DAY", "TRAPPED_DAY"):
            runner_prob -= 15

        if bq_type == "EXPLOSIVE" and acc_type == "ACCEPTED":
            runner_prob += 15
        if trend_str >= 70:
            runner_prob += 10
        if exp_eff >= 0.65:
            runner_prob += 8

        if delta_override and reg_class == "TREND":
            runner_prob += 8

        runner_prob = max(5, min(runner_prob, 90))

        # ── CONTINUATION QUALITY ──────────────────────────────────
        if cont_prob >= 75:   quality = "STRONG"
        elif cont_prob >= 55: quality = "MODERATE"
        elif cont_prob >= 35: quality = "WEAK"
        else:                 quality = "NONE"

        if absorb_post and acc_type == "RECLAIM":
            quality   = "NONE"
            cont_prob = max(5, cont_prob - 20)

        return ContinuationResult(
            continuation_probability = cont_prob,
            runner_probability       = runner_prob,
            continuation_quality     = quality,
            follow_through_strength  = ft_score,
            continuation_bias        = ft_bias,
            pullback_depth           = round(pb_depth, 2),
            pullback_healthy         = pb_ok,
            absorption_after_break   = absorb_post,
            imbalance_persistence    = imbalance,
            speed_score              = speed,
        )

    # ── MÉTRICAS INTERNAS ─────────────────────────────────────────

    def _follow_through(self, bars: list) -> tuple:
        if len(bars) < 3:
            return 40, "NEUTRAL"
        recent = bars[-3:]
        pos    = sum(1 for b in recent if b.price_move > self._tick)
        neg    = sum(1 for b in recent if b.price_move < -self._tick)

        if pos >= 2 and neg == 0:  return 82, "BULLISH"
        if neg >= 2 and pos == 0:  return 82, "BEARISH"
        if pos == 2 and neg == 1:  return 55, "BULLISH"
        if neg == 2 and pos == 1:  return 55, "BEARISH"
        return 20, "NEUTRAL"

    def _pullback_analysis(self, bars: list) -> tuple:
        if len(bars) < 4:
            return 0.0, False
        prices = [b.price for b in bars]
        net    = prices[-1] - prices[0]
        if abs(net) < self._tick:
            return 0.0, False
        if net > 0:
            peak    = max(prices)
            retrace = peak - prices[-1]
        else:
            trough  = min(prices)
            retrace = prices[-1] - trough
        retrace     = max(0.0, retrace)
        total       = abs(net)
        retrace_pct = retrace / total if total > 0 else 1.0
        healthy     = retrace_pct <= 0.38
        return retrace, healthy

    def _absorption_after_break(self, bars: list) -> bool:
        if len(bars) < 2:
            return False
        curr = bars[-1]
        if (curr.absorption and
                self._avg_volume > 0 and
                curr.volume > self._avg_volume * 1.5 and
                abs(curr.price_move) < self._tick * 2):
            return True
        if abs(curr.delta) > 300 and abs(curr.price_move) < self._tick:
            return True
        return False

    def _imbalance_persistence(self, bars: list) -> int:
        if len(bars) < 2:
            return 50
        recent = bars[-min(3, len(bars)):]
        bull   = sum(1 for b in recent if b.delta > 80)
        bear   = sum(1 for b in recent if b.delta < -80)
        mixed  = sum(1 for b in recent if -80 <= b.delta <= 80)
        total  = len(recent)

        if max(bull, bear) >= total * 0.70:   return 85
        if max(bull, bear) >= total * 0.55:   return 65
        if mixed >= total * 0.60:             return 25
        return 45

    def _expansion_speed(self, bars: list) -> int:
        if len(bars) < 2:
            return 40
        recent = bars[-3:]
        moves  = [abs(b.price_move) for b in recent]
        avg    = sum(moves) / len(moves) if moves else 0
        if avg >= self._tick * 6:  return 90
        if avg >= self._tick * 4:  return 70
        if avg >= self._tick * 2:  return 50
        if avg >= self._tick:      return 30
        return 10