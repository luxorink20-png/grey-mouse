# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — adaptive_continuation.py
#  Adaptive Continuation Engine v1.1
#
#  CAMBIOS v1.1 vs v1.0:
#  ─ EFFICIENT_TREND boost: cuando env_name=EFFICIENT_TREND,
#    base_prob recibe +15 igual que TREND_DAY
#  ─ WEAK penalty cancelada en EFFICIENT_TREND: cuando
#    env_name=EFFICIENT_TREND, bq_type=WEAK no penaliza -20
#  ─ Mismo patrón que continuation_engine.py v1.2
#
#  PROBLEMA RESUELTO v1.1:
#  _cont_p=60 en validator porque adaptive_continuation no
#  reconocía EFFICIENT_TREND como régimen de tendencia.
#  base_prob=55 - 20(WEAK) = 35 → después ajustes = ~60
#  Con fix: base_prob=55 + 15(EFFICIENT_TREND) = 70 → cont_p=75+
#
#  RIESGO NTE: CERO
#  EFFICIENT_TREND no apareció en 9 sesiones NTE del dataset.
#
#  ROLLBACK: revertir líneas del bloque v1.1 EFFICIENT_TREND
#
#  SIN CAMBIOS:
#  ─ exhaustion detection
#  ─ momentum decay
#  ─ fake continuation
#  ─ trend compression
#  ─ trapped detection
#  ─ inefficient impulse
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class AdaptiveContinuationResult:
    continuation_quality:    str   = "UNKNOWN"  # STRONG/MODERATE/WEAK/EXHAUSTED/NONE
    continuation_probability:int   = 50   # 0-100
    continuation_risk:       int   = 30   # 0-100 (alto = peligroso)
    momentum_decay:          int   = 0    # 0-100 (alto = debilitamiento)
    exhaustion:              bool  = False
    runner_probability:      int   = 30   # 0-100
    fake_continuation:       bool  = False
    trend_compression:       bool  = False
    inefficient_impulse:     bool  = False
    trapped:                 bool  = False
    reason:                  str   = ""

    def is_safe(self) -> bool:
        """Retorna True si la continuación es suficientemente segura."""
        if self.exhaustion:
            return False
        if self.continuation_risk > 75:
            return False
        if self.momentum_decay > 70:
            return False
        return True

    def __str__(self) -> str:
        return (f"quality={self.continuation_quality} "
                f"prob={self.continuation_probability}% "
                f"risk={self.continuation_risk} "
                f"decay={self.momentum_decay} "
                f"exhaust={self.exhaustion}")


@dataclass
class ContAdaptBar:
    price:      float
    high:       float
    low:        float
    open:       float
    close:      float
    delta:      float
    volume:     float
    price_move: float
    absorption: bool


class AdaptiveContinuationEngine:
    """
    Motor de continuación adaptativo v1.1

    Analiza señales de agotamiento del movimiento.
    Complementa continuation_engine.py — donde ese mide
    probabilidad de continuación, este detecta señales de parada.
    """

    WINDOW = 15

    def __init__(self, tick: float = 0.25):
        self._tick        = tick
        self._bars: deque = deque(maxlen=self.WINDOW)
        self._avg_vol:float = 0.0
        self._vol_s: deque = deque(maxlen=25)

    def analyze_continuation(self, event_result: dict,
                              confirmation,
                              session_regime,
                              market_env,
                              raw_data: dict) -> AdaptiveContinuationResult:

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
            self._vol_s.append(volume)
        self._avg_vol = (sum(self._vol_s) / len(self._vol_s)
                         if self._vol_s else volume)

        bar = ContAdaptBar(price=price, high=high, low=low,
                           open=open_, close=close, delta=delta,
                           volume=volume, price_move=price_move,
                           absorption=absorption)
        self._bars.append(bar)

        if len(self._bars) < 3:
            return AdaptiveContinuationResult(
                continuation_quality    = "UNKNOWN",
                continuation_probability= 50,
            )

        bars = list(self._bars)

        # Contexto externo
        bq_type   = getattr(confirmation, "breakout_type",       "NONE") if confirmation else "NONE"
        regime    = getattr(session_regime, "session_regime",    "BALANCED_DAY") if session_regime else "BALANCED_DAY"
        trend_str = getattr(session_regime, "trend_strength",    40) if session_regime else 40
        env_name  = getattr(market_env, "environment",           "ROTATIONAL") if market_env else "ROTATIONAL"
        dir_eff   = getattr(market_env, "directional_efficiency", 50) if market_env else 50

        # ── SEÑALES DE AGOTAMIENTO ────────────────────────────────
        decay        = self._momentum_decay(bars)
        exhaustion   = self._detect_exhaustion(bars, decay)
        fake_cont    = self._detect_fake_continuation(bars)
        compression  = self._detect_trend_compression(bars)
        trapped      = self._detect_trapped(bars)
        inefficient  = self._detect_inefficient_impulse(bars)

        # ── CONTINUATION RISK ─────────────────────────────────────
        risk = 20  # base
        if decay >= 60:      risk += 20
        if exhaustion:       risk += 25
        if fake_cont:        risk += 20
        if compression:      risk += 10
        if trapped:          risk += 15
        if inefficient:      risk += 15
        if env_name in ("TRAPPY", "CHOPPY", "LIQUIDATION"):
            risk += 20
        if env_name == "DEAD_MARKET":
            risk += 25
        risk = min(risk, 100)

        # ── CONTINUATION PROBABILITY ──────────────────────────────
        base_prob = 55

        # ── v1.1 — EFFICIENT_TREND BOOST ──────────────────────────
        # EFFICIENT_TREND debe recibir mismo boost que TREND_DAY.
        # Sin este fix, env=EFFICIENT_TREND cae al default (55)
        # y bq_type=WEAK aplica -20 → base_prob=35 → cont_p≈60.
        # ROLLBACK: revertir a línea original abajo
        # ─────────────────────────────────────────────────────────
        if regime in ("TREND_DAY", "EXPANSION_DAY") or env_name == "EFFICIENT_TREND":
            base_prob += 15
        elif regime in ("BALANCED_DAY", "ROTATIONAL_DAY"):
            base_prob -= 15
        # ── FIN v1.1 EFFICIENT_TREND BOOST ────────────────────────

        if bq_type == "EXPLOSIVE":   base_prob += 18
        elif bq_type == "REAL":      base_prob += 10
        elif bq_type == "WEAK":
            # ── v1.1 — WEAK penalty cancelada en EFFICIENT_TREND ──
            # En EFFICIENT_TREND el breakout WEAK con eff=100 es válido.
            # Sin este fix: -20 aplicaba incluso con eff=100.
            # ROLLBACK: revertir a "base_prob -= 20"
            # ─────────────────────────────────────────────────────
            base_prob -= 0 if env_name == "EFFICIENT_TREND" else 20
            # ── FIN v1.1 ──────────────────────────────────────────
        elif bq_type == "FAKE":      base_prob -= 35

        if decay >= 60:              base_prob -= 18
        if exhaustion:               base_prob -= 25
        if fake_cont:                base_prob -= 15
        if dir_eff >= 70:            base_prob += 10
        elif dir_eff < 35:           base_prob -= 15
        base_prob = max(5, min(base_prob, 95))

        # ── RUNNER PROBABILITY ────────────────────────────────────
        runner = base_prob - 20
        if exhaustion or decay >= 70:
            runner = max(5, runner - 20)
        if regime in ("TREND_DAY", "EXPANSION_DAY") and bq_type in ("REAL","EXPLOSIVE"):
            runner = min(runner + 15, 85)
        runner = max(5, min(runner, 90))

        # ── QUALITY ───────────────────────────────────────────────
        if exhaustion or fake_cont:
            quality = "EXHAUSTED"
        elif base_prob >= 70 and not trapped and decay < 40:
            quality = "STRONG"
        elif base_prob >= 50 and decay < 55:
            quality = "MODERATE"
        elif base_prob >= 30:
            quality = "WEAK"
        else:
            quality = "NONE"

        # Razón
        parts = []
        if exhaustion:   parts.append("exhaustion")
        if decay >= 60:  parts.append(f"decay={decay}")
        if fake_cont:    parts.append("fake_cont")
        if compression:  parts.append("compression")
        if trapped:      parts.append("trapped")
        if inefficient:  parts.append("inefficient")
        reason = " | ".join(parts) if parts else "OK"

        return AdaptiveContinuationResult(
            continuation_quality     = quality,
            continuation_probability = base_prob,
            continuation_risk        = risk,
            momentum_decay           = decay,
            exhaustion               = exhaustion,
            runner_probability       = runner,
            fake_continuation        = fake_cont,
            trend_compression        = compression,
            inefficient_impulse      = inefficient,
            trapped                  = trapped,
            reason                   = reason,
        )

    # ──────────────────────────────────────────────────────────────
    #  SEÑALES DE AGOTAMIENTO
    # ──────────────────────────────────────────────────────────────

    def _momentum_decay(self, bars: list) -> int:
        """
        Cada barra avanza menos que la anterior = decay.
        0 = sin decay, 100 = decay total.
        """
        if len(bars) < 3:
            return 0
        moves = [abs(b.price_move) for b in bars[-5:]]
        if len(moves) < 3 or moves[0] == 0:
            return 0
        declining = sum(
            1 for i in range(1, len(moves))
            if moves[i] < moves[i-1] * 0.85
        )
        rate = declining / max(len(moves)-1, 1)
        deltas = [abs(b.delta) for b in bars[-5:]]
        delta_declining = sum(
            1 for i in range(1, len(deltas))
            if deltas[i] < deltas[i-1] * 0.80
        ) if len(deltas) > 1 else 0
        delta_rate = delta_declining / max(len(deltas)-1, 1)
        combined = (rate * 0.6 + delta_rate * 0.4)
        return min(int(combined * 100), 100)

    def _detect_exhaustion(self, bars: list, decay: int) -> bool:
        """
        Exhaustion: movimiento grande seguido de velas pequeñas
        con volumen decayendo.
        """
        if len(bars) < 4:
            return False
        recent     = bars[-4:]
        moves      = [abs(b.price_move) for b in recent]
        vols       = [b.volume for b in recent]
        if moves[0] > self._tick * 4:
            small_after = sum(1 for m in moves[1:] if m < self._tick * 2)
            vol_decay   = (vols[-1] < vols[0] * 0.65)
            if small_after >= 2 and vol_decay:
                return True
        if decay >= 80:
            return True
        curr = bars[-1]
        if (curr.absorption and
                self._avg_vol > 0 and
                curr.volume > self._avg_vol * 1.8 and
                abs(curr.price_move) < self._tick):
            return True
        return False

    def _detect_fake_continuation(self, bars: list) -> bool:
        """
        Fake: precio avanza pero delta NO acompaña.
        O: precio avanza pero cierra contra la dirección.
        """
        if len(bars) < 2:
            return False
        curr = bars[-1]
        prev = bars[-2]
        if (curr.price_move > self._tick * 3 and
                curr.delta < -100):
            return True
        if (curr.price_move < -self._tick * 3 and
                curr.delta > 100):
            return True
        candle_range = curr.high - curr.low
        if candle_range > self._tick * 4:
            body = abs(curr.close - curr.open)
            if body < candle_range * 0.25:
                return True
        return False

    def _detect_trend_compression(self, bars: list) -> bool:
        """Barras cada vez más pequeñas = energía agotándose."""
        if len(bars) < 4:
            return False
        moves = [abs(b.price_move) for b in bars[-4:]]
        all_smaller = all(
            moves[i] <= moves[i-1] * 0.80
            for i in range(1, len(moves))
        )
        if all_smaller and moves[-1] < self._tick * 2:
            return True
        return False

    def _detect_trapped(self, bars: list) -> bool:
        """
        Precio no puede romper un nivel después de múltiples intentos.
        """
        if len(bars) < 5:
            return False
        recent = bars[-5:]
        highs  = [b.high  for b in recent]
        lows   = [b.low   for b in recent]
        max_high = max(highs)
        near_top = sum(1 for h in highs if h >= max_high * 0.999)
        if near_top >= 3 and recent[-1].price < max_high - self._tick*2:
            return True
        min_low = min(lows)
        near_bot = sum(1 for l in lows if l <= min_low * 1.001)
        if near_bot >= 3 and recent[-1].price > min_low + self._tick*2:
            return True
        return False

    def _detect_inefficient_impulse(self, bars: list) -> bool:
        """Delta alto pero poco avance = institución absorbiendo."""
        if len(bars) < 2:
            return False
        recent = bars[-3:]
        total_delta = sum(abs(b.delta) for b in recent)
        net_move    = abs(recent[-1].price - recent[0].price)
        if total_delta > 500 and net_move < self._tick * 3:
            return True
        return False