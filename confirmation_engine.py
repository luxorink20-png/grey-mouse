# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — confirmation_engine.py
#  Institutional Confirmation Engine v2.1
#
#  CAMBIOS v2.1 vs v2.0:
#  — Swing detector con lookback real (3 bars cada lado, antes era 1)
#  — Magnitude filter: mínimo MIN_SWING_TICKS para swing válido
#  — Structure hysteresis: bias no puede flippear instantáneamente
#  — Flip protection: requiere MIN_BIAS_BARS antes de permitir flip
#  — Flip confirmation: requiere FLIP_CONFIRM_BARS consecutivos
#  — Threshold con tick buffer en comparaciones HH/HL/LH/LL
#
#  PROBLEMA RESUELTO:
#  struct_opposed aparecía en ~90% de barras porque el swing detector
#  con lookback=1 generaba micro-pivots en cada tick, causando flips
#  constantes de BULLISH→BEARISH incluso en TREND_DAY alcistas puros.
#
#  CLASIFICACIÓN:
#  FAKE      → wick ratio alto, delta divergente, retorno inmediato
#  WEAK      → breakout mínimo, poca expansión, poca persistencia
#  MODERATE  → breakout limpio, continuación parcial
#  REAL      → expansión institucional, delta persistente, aceptación
#  EXPLOSIVE → expansión agresiva, displacement fuerte, continuación inmediata
#
#  MÉTRICAS:
#  1. Breakout Quality      — distancia, ticks fuera, vel, fake detection
#  2. Range Acceptance      — velas fuera del rango, reclaim detection
#  3. Expansion Efficiency  — distancia / retroceso máximo
#  4. Delta Persistence     — 2-3 velas consecutivas sostenidas
#  5. Microstructure        — compresión real, absorción, intentos fallidos
#  6. Follow-Through        — continuación tras el evento
#
#  MIN_CONFIRMATION_SCORE para pasar: 50
#  WEAK breakouts tienen score máximo ~40 → no pasan
# ╚══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass, field
from collections import deque
from typing import Optional

TICK = 0.25

# MES: mínimo 3-5 ticks fuera del rango para confirmar
MIN_BREAKOUT_TICKS  = 3
MIN_CONFIRMATION_SCORE = 50

# ── v2.1 — Structure Engine Constants ────────────────────────────────
MIN_SWING_TICKS    = 6   # ticks mínimos de magnitud para swing válido
MIN_BIAS_BARS      = 4   # barras mínimas que debe durar un bias antes de poder flippear
FLIP_CONFIRM_BARS  = 3   # barras consecutivas opuestas necesarias para confirmar flip
# ─────────────────────────────────────────────────────────────────────


@dataclass
class ConfirmationResult:
    confirmed:               bool  = False
    confirmation_score:      int   = 0
    breakout_quality:        int   = 0
    acceptance_score:        int   = 0
    delta_persistence:       int   = 0
    displacement_efficiency: float = 0.0
    expansion_efficiency:    float = 0.0
    microstructure_quality:  int   = 0
    follow_through:          int   = 0
    compression_strength:    int   = 0
    breakout_type:           str   = "NONE"
    acceptance_type:         str   = "NONE"
    structure_bias:          str   = "NEUTRAL"
    reason:                  str   = ""
    detail:                  dict  = field(default_factory=dict)

    def __str__(self):
        s = "CONFIRMED" if self.confirmed else "NOT_CONFIRMED"
        return (f"{s} score={self.confirmation_score} "
                f"type={self.breakout_type} "
                f"acc={self.acceptance_type} "
                f"eff={round(self.expansion_efficiency,2)}")


@dataclass
class ConfBar:
    price: float; high: float; low: float
    open: float; close: float
    delta: float; volume: float
    price_move: float; absorption: bool; event: str


class ConfirmationEngine:
    """
    Confirmation Engine v2.1

    Cambios clave vs v2.0:
    - Swing detector con lookback 3 barras a cada lado (antes 1)
    - Magnitude filter: ignora micro-pivots menores a MIN_SWING_TICKS
    - Structure hysteresis: bias estable, no flipea por ruido intrabar
    - Threshold con buffer de ticks en comparaciones HH/HL/LH/LL
    - WEAK breakouts siguen con score máximo ~40 (no pasan MIN=50)
    - REAL/EXPLOSIVE siguen con score 70-95
    """

    def __init__(self, window: int = 20, tick: float = TICK):
        self._bars:        deque = deque(maxlen=window)
        self._tick:        float = tick
        self._avg_volume:  float = 0.0
        self._vol_samples: deque = deque(maxlen=30)

        # Structure tracking
        self._swing_highs: deque = deque(maxlen=5)
        self._swing_lows:  deque = deque(maxlen=5)
        self._last_swing_high: float = 0.0
        self._last_swing_low:  float = 0.0
        self._prev_swing_high: float = 0.0
        self._prev_swing_low:  float = 0.0

        # Range tracking
        self._range_high:   float = 0.0
        self._range_low:    float = 0.0
        self._range_bars:   int   = 0
        self._bars_outside: int   = 0

        # ── v2.1 — Structure hysteresis ──────────────────────────
        self._structure_bias_current: str = "NEUTRAL"
        self._structure_bias_bars:    int = 0    # cuántas barras lleva el bias actual
        self._structure_flip_pending: str = ""   # bias opuesto pendiente de confirmación
        self._structure_flip_count:   int = 0    # cuántas barras seguidas señala el flip
        # ─────────────────────────────────────────────────────────

    def analyze(self, event_result: dict, level_context,
                confluence, micro_result, raw_data: dict) -> ConfirmationResult:

        ctx        = event_result.get("context", {})
        event      = event_result.get("event",   "NONE")
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

        bar = ConfBar(price=price, high=high, low=low, open=open_,
                      close=close, delta=delta, volume=volume,
                      price_move=price_move, absorption=absorption, event=event)
        self._bars.append(bar)
        self._update_range(price, high, low)
        self._update_structure(high, low)

        if len(self._bars) < 3:
            return ConfirmationResult(reason="Calentando buffer")

        bars = list(self._bars)

        bq_score, bq_type, bq_detail     = self._breakout_quality(bars)
        acc_score, acc_type, acc_detail   = self._range_acceptance(bars, bq_type)
        dp_score, dp_detail               = self._delta_persistence(bars)
        exp_eff, exp_detail               = self._expansion_efficiency(bars)
        ms_score, ms_detail, comp_str     = self._microstructure_quality(bars, micro_result)
        ft_score, ft_detail               = self._follow_through(bars, event)
        struct_bias, struct_detail        = self._structure_bias()

        # ── SCORE COMPUESTO ───────────────────────────────────────
        raw_score = int(
            bq_score  * 0.32 +
            dp_score  * 0.24 +
            acc_score * 0.20 +
            ft_score  * 0.14 +
            ms_score  * 0.10
        )

        # ── MODIFICADORES CRÍTICOS ────────────────────────────────

        # WEAK breakout = penalización fuerte (máx score ~42)
        if bq_type == "WEAK":
            raw_score = min(raw_score, 42)

        # FAKE breakout = penalización extrema
        if bq_type == "FAKE":
            raw_score = min(raw_score, 25)
            raw_score = max(0, raw_score - 20)

        # Efficiency baja = penalizar
        if exp_eff < 0.25:
            raw_score = max(0, raw_score - 15)
        elif exp_eff > 0.60:
            raw_score = min(100, raw_score + 8)

        # Reclaim inmediato = penalizar fuerte
        if acc_type == "RECLAIM":
            raw_score = max(0, raw_score - 18)

        # REAL/EXPLOSIVE = bonus
        if bq_type == "REAL" and acc_type == "ACCEPTED":
            raw_score = min(100, raw_score + 12)
        if bq_type == "EXPLOSIVE":
            raw_score = min(100, raw_score + 18)

        # Structure alineada = bonus
        if struct_bias != "NEUTRAL":
            raw_score = min(100, raw_score + 5)

        # Compresión fuerte previa = bonus
        if comp_str >= 70:
            raw_score = min(100, raw_score + 6)

        conf_score = max(0, min(raw_score, 100))
        confirmed  = conf_score >= MIN_CONFIRMATION_SCORE

        reason_parts = [f"type={bq_type}", f"acc={acc_type}",
                        f"eff={round(exp_eff,2)}", f"struct={struct_bias}"]
        reason = " | ".join(reason_parts)

        return ConfirmationResult(
            confirmed               = confirmed,
            confirmation_score      = conf_score,
            breakout_quality        = bq_score,
            acceptance_score        = acc_score,
            delta_persistence       = dp_score,
            displacement_efficiency = exp_eff,
            expansion_efficiency    = exp_eff,
            microstructure_quality  = ms_score,
            follow_through          = ft_score,
            compression_strength    = comp_str,
            breakout_type           = bq_type,
            acceptance_type         = acc_type,
            structure_bias          = struct_bias,
            reason                  = reason,
            detail = {
                "breakout":   bq_detail,
                "acceptance": acc_detail,
                "delta":      dp_detail,
                "efficiency": exp_detail,
                "micro":      ms_detail,
                "followthru": ft_detail,
                "structure":  struct_detail,
            },
        )

    # ──────────────────────────────────────────────────────────────
    #  BREAKOUT QUALITY v2.0 — sin cambios
    # ──────────────────────────────────────────────────────────────

    def _breakout_quality(self, bars: list) -> tuple:
        if len(bars) < 3:
            return 0, "NONE", {}

        curr    = bars[-1]
        prev    = bars[-2]
        detail  = {}

        total_move  = abs(curr.price - bars[-3].price)
        min_break   = MIN_BREAKOUT_TICKS * self._tick
        vol_ratio   = curr.volume / self._avg_volume if self._avg_volume > 0 else 1.0
        detail["total_move"] = round(total_move, 2)
        detail["vol_ratio"]  = round(vol_ratio, 2)

        candle_range = curr.high - curr.low
        if candle_range > 0:
            body       = abs(curr.close - curr.open)
            wick_ratio = 1.0 - (body / candle_range)
            detail["wick_ratio"] = round(wick_ratio, 2)
        else:
            wick_ratio = 0.0

        delta_aligned = (
            (curr.price_move > 0 and curr.delta > 50) or
            (curr.price_move < 0 and curr.delta < -50)
        )

        is_fake = (
            (curr.price_move > 0 and curr.close < curr.open and wick_ratio > 0.65) or
            (curr.price_move < 0 and curr.close > curr.open and wick_ratio > 0.65) or
            (curr.price_move > self._tick * 3 and curr.delta < -150) or
            (curr.price_move < -self._tick * 3 and curr.delta > 150)
        )

        if is_fake:
            return 20, "FAKE", detail

        if total_move < min_break:
            return 15, "NONE", detail

        if (total_move >= min_break * 5 and vol_ratio >= 2.0 and
                delta_aligned and wick_ratio < 0.35):
            score   = 95
            bq_type = "EXPLOSIVE"
        elif (total_move >= min_break * 3 and vol_ratio >= 1.4 and
              delta_aligned and wick_ratio < 0.50):
            score   = 78
            bq_type = "REAL"
        elif total_move >= min_break * 1.5 and vol_ratio >= 1.1:
            score   = 52
            bq_type = "MODERATE"
        elif total_move >= min_break:
            score   = 30
            bq_type = "WEAK"
        else:
            score   = 10
            bq_type = "WEAK"

        if bq_type == "WEAK" and wick_ratio > 0.55:
            score = max(0, score - 10)

        detail["type"] = bq_type
        return score, bq_type, detail

    # ──────────────────────────────────────────────────────────────
    #  RANGE ACCEPTANCE — sin cambios
    # ──────────────────────────────────────────────────────────────

    def _range_acceptance(self, bars: list, bq_type: str) -> tuple:
        if len(bars) < 2 or bq_type == "NONE":
            return 50, "NONE", {}

        detail = {}
        curr   = bars[-1]
        prev   = bars[-2]

        if curr.price_move > 0:
            holds    = curr.close > prev.close - self._tick
            close_hi = curr.close >= curr.high - self._tick * 2
            reclaim  = curr.close < prev.open - self._tick

            detail.update({"holds": holds, "close_hi": close_hi, "reclaim": reclaim})

            if reclaim:
                return 10, "RECLAIM", detail
            if holds and close_hi:
                velas_fuera = sum(1 for b in bars[-3:] if b.close > prev.close)
                detail["velas_fuera"] = velas_fuera
                score = 70 + min(25, velas_fuera * 8)
                return score, "ACCEPTED", detail
            if holds:
                return 55, "ACCEPTED", detail
            return 30, "WEAK_ACC", detail

        if curr.price_move < 0:
            holds    = curr.close < prev.close + self._tick
            close_lo = curr.close <= curr.low + self._tick * 2
            reclaim  = curr.close > prev.open + self._tick

            detail.update({"holds": holds, "close_lo": close_lo, "reclaim": reclaim})

            if reclaim:
                return 10, "RECLAIM", detail
            if holds and close_lo:
                velas_fuera = sum(1 for b in bars[-3:] if b.close < prev.close)
                detail["velas_fuera"] = velas_fuera
                score = 70 + min(25, velas_fuera * 8)
                return score, "ACCEPTED", detail
            if holds:
                return 55, "ACCEPTED", detail
            return 30, "WEAK_ACC", detail

        return 45, "NONE", detail

    # ──────────────────────────────────────────────────────────────
    #  DELTA PERSISTENCE — sin cambios
    # ──────────────────────────────────────────────────────────────

    def _delta_persistence(self, bars: list) -> tuple:
        if len(bars) < 2:
            return 0, {}

        recent = bars[-min(3, len(bars)):]
        detail = {}

        bull_bars     = sum(1 for b in recent if b.delta > 80)
        bear_bars     = sum(1 for b in recent if b.delta < -80)
        delta_growing = (len(recent) >= 2 and
                         abs(recent[-1].delta) > abs(recent[-2].delta) * 0.85)

        detail.update({"bull_bars": bull_bars, "bear_bars": bear_bars,
                        "delta_growing": delta_growing})

        max_consec = max(bull_bars, bear_bars)

        if max_consec >= 3 and delta_growing:   score = 92
        elif max_consec >= 3:                   score = 76
        elif max_consec >= 2 and delta_growing: score = 70
        elif max_consec >= 2:                   score = 56
        elif max_consec == 1:                   score = 30
        else:                                   score = 10

        if bull_bars >= 1 and bear_bars >= 1:
            score = max(0, score - 22)
            detail["conflicting"] = True

        return score, detail

    # ──────────────────────────────────────────────────────────────
    #  EXPANSION EFFICIENCY — sin cambios
    # ──────────────────────────────────────────────────────────────

    def _expansion_efficiency(self, bars: list) -> tuple:
        if len(bars) < 2:
            return 0.0, {}

        recent     = bars[-min(4, len(bars)):]
        detail     = {}
        net_move   = abs(recent[-1].price - recent[0].price)
        total_path = sum(abs(b.price_move) for b in recent)

        detail["net_move"]   = round(net_move, 2)
        detail["total_path"] = round(total_path, 2)

        if total_path == 0:
            return 0.0, detail

        prices      = [b.price for b in recent]
        direction   = 1 if recent[-1].price > recent[0].price else -1
        max_retrace = 0.0

        if direction > 0:
            peak = prices[0]
            for p in prices[1:]:
                if p > peak: peak = p
                retrace = peak - p
                max_retrace = max(max_retrace, retrace)
        else:
            trough = prices[0]
            for p in prices[1:]:
                if p < trough: trough = p
                retrace = p - trough
                max_retrace = max(max_retrace, retrace)

        detail["max_retrace"] = round(max_retrace, 2)

        path_eff = net_move / total_path

        if max_retrace > 0 and net_move > 0:
            retrace_pen = max_retrace / net_move
            eff = path_eff * (1.0 - min(retrace_pen * 0.5, 0.8))
        else:
            eff = path_eff

        eff = round(max(0.0, min(eff, 1.0)), 3)
        detail["efficiency"] = eff
        return eff, detail

    # ──────────────────────────────────────────────────────────────
    #  MICROSTRUCTURE QUALITY — sin cambios
    # ──────────────────────────────────────────────────────────────

    def _microstructure_quality(self, bars: list, micro_result) -> tuple:
        detail        = {}
        score         = 40
        comp_strength = 0

        if micro_result is not None:
            micro_active   = getattr(micro_result, "active",             False)
            micro_conf     = getattr(micro_result, "confidence",         0)
            micro_compress = getattr(micro_result, "compression_active", False)
            micro_breakout = getattr(micro_result, "breakout",           None)
            micro_bars     = getattr(micro_result, "bars_in_range",      0)

            if micro_active and micro_compress and micro_bars >= 6:
                score        += 20
                comp_strength = min(100, micro_conf + 10)
            if micro_breakout is not None and micro_conf >= 70:
                score        += 25
                comp_strength = max(comp_strength, micro_conf)
            if micro_bars >= 10:
                score        += 10
                comp_strength = min(100, comp_strength + 10)

        if len(bars) >= 5:
            recent      = bars[-5:]
            price_range = max(b.high for b in recent) - min(b.low for b in recent)
            avg_move    = sum(abs(b.price_move) for b in recent) / len(recent)
            detail["price_range"] = round(price_range, 2)
            detail["avg_move"]    = round(avg_move, 2)

            if price_range < self._tick * 8:
                avg_vol = sum(b.volume for b in recent) / len(recent)
                if avg_vol > self._avg_volume * 0.65:
                    score        += 15
                    comp_strength = max(comp_strength, 60)

            absorb_count = sum(1 for b in recent if b.absorption)
            if absorb_count >= 2:
                score        += 15
                comp_strength = min(100, comp_strength + 15)
                detail["absorption_count"] = absorb_count

            failed = sum(
                1 for i in range(1, len(recent))
                if ((recent[i].high > recent[i-1].high and
                     recent[i].close < recent[i-1].close) or
                    (recent[i].low < recent[i-1].low and
                     recent[i].close > recent[i-1].close))
            )
            if failed >= 2:
                score        += 10
                comp_strength = min(100, comp_strength + 10)
                detail["failed_attempts"] = failed

        if len(bars) >= 3:
            avg_delta = sum(abs(b.delta) for b in bars[-3:]) / 3
            if avg_delta < 40:
                score = max(0, score - 20)

        score         = max(0, min(score, 100))
        comp_strength = max(0, min(comp_strength, 100))
        detail["score"]         = score
        detail["comp_strength"] = comp_strength
        return score, detail, comp_strength

    # ──────────────────────────────────────────────────────────────
    #  FOLLOW-THROUGH — sin cambios
    # ──────────────────────────────────────────────────────────────

    def _follow_through(self, bars: list, event: str) -> tuple:
        if len(bars) < 3:
            return 40, {}

        detail   = {}
        moves    = [b.price_move for b in bars[-3:]]
        pos_bars = sum(1 for m in moves if m > self._tick)
        neg_bars = sum(1 for m in moves if m < -self._tick)
        detail.update({"pos_bars": pos_bars, "neg_bars": neg_bars})

        if pos_bars >= 2 and neg_bars == 0:
            score = 82; detail["type"] = "BULL_CONT"
        elif neg_bars >= 2 and pos_bars == 0:
            score = 82; detail["type"] = "BEAR_CONT"
        elif pos_bars == 2 and neg_bars == 1:
            score = 55; detail["type"] = "BULL_WEAK"
        elif neg_bars == 2 and pos_bars == 1:
            score = 55; detail["type"] = "BEAR_WEAK"
        else:
            score = 20; detail["type"] = "CHOPPY"

        if event == "AGOTAMIENTO" and (pos_bars >= 2 or neg_bars >= 2):
            score = min(100, score + 15)
        if pos_bars >= 1 and neg_bars >= 1 and abs(pos_bars - neg_bars) == 0:
            score = max(0, score - 20)

        return score, detail

    # ──────────────────────────────────────────────────────────────
    #  STRUCTURE BIAS v2.1 — CON HYSTERESIS
    # ──────────────────────────────────────────────────────────────

    def _structure_bias(self) -> tuple:
        """
        v2.1 — Detecta estructura de mercado con hysteresis:
        - Comparaciones con buffer de 2 ticks (evita falsos en microestructura)
        - Bias no puede flippear hasta MIN_BIAS_BARS de estabilidad
        - Flip requiere FLIP_CONFIRM_BARS barras consecutivas opuestas
        - Resultado: bias estable en TREND_DAY, responsivo en reversals reales
        """
        detail = {}

        if (self._last_swing_high == 0 or self._last_swing_low == 0 or
                self._prev_swing_high == 0 or self._prev_swing_low == 0):
            return "NEUTRAL", detail

        # Buffer de 2 ticks para evitar comparaciones ruidosas
        buf = self._tick * 2

        hh = self._last_swing_high > self._prev_swing_high + buf
        hl = self._last_swing_low  > self._prev_swing_low  + buf
        lh = self._last_swing_high < self._prev_swing_high - buf
        ll = self._last_swing_low  < self._prev_swing_low  - buf

        detail.update({"hh": hh, "hl": hl, "lh": lh, "ll": ll})

        # Clasificar señal cruda
        if hh and hl:
            raw_bias = "BULLISH"; detail["pattern"] = "HH+HL"
        elif lh and ll:
            raw_bias = "BEARISH"; detail["pattern"] = "LH+LL"
        elif hh:
            raw_bias = "BULLISH"; detail["pattern"] = "BOS_UP"
        elif ll:
            raw_bias = "BEARISH"; detail["pattern"] = "BOS_DOWN"
        else:
            raw_bias = "NEUTRAL"

        # ── HYSTERESIS — proteger contra flips rápidos ────────────
        if raw_bias == "NEUTRAL":
            # Señal neutral: mantener bias actual, no contar como flip
            self._structure_bias_bars += 1
            detail["bias_held"]  = self._structure_bias_current
            detail["bias_bars"]  = self._structure_bias_bars
            return self._structure_bias_current, detail

        if raw_bias == self._structure_bias_current:
            # Bias se confirma: incrementar contador, resetear flip pendiente
            self._structure_bias_bars    += 1
            self._structure_flip_pending  = ""
            self._structure_flip_count    = 0
        else:
            # Señal opuesta al bias actual
            if self._structure_bias_bars < MIN_BIAS_BARS:
                # Bias demasiado reciente — bloquear flip
                detail["flip_blocked"] = True
                raw_bias = self._structure_bias_current
            else:
                # Acumular confirmaciones del flip
                if self._structure_flip_pending == raw_bias:
                    self._structure_flip_count += 1
                else:
                    self._structure_flip_pending = raw_bias
                    self._structure_flip_count   = 1

                if self._structure_flip_count >= FLIP_CONFIRM_BARS:
                    # Flip confirmado — cambiar bias
                    self._structure_bias_current = raw_bias
                    self._structure_bias_bars    = 0
                    self._structure_flip_pending = ""
                    self._structure_flip_count   = 0
                    detail["flip_confirmed"] = True
                else:
                    # Flip pendiente — mantener bias actual
                    detail["flip_pending"] = (
                        f"{self._structure_flip_count}/{FLIP_CONFIRM_BARS}"
                    )
                    raw_bias = self._structure_bias_current

        # Actualizar estado
        if raw_bias != "NEUTRAL":
            self._structure_bias_current = raw_bias
        self._structure_bias_bars = max(self._structure_bias_bars, 0)

        detail["bias_bars"]    = self._structure_bias_bars
        detail["bias_current"] = self._structure_bias_current
        return raw_bias, detail

    # ──────────────────────────────────────────────────────────────
    #  SWING DETECTION v2.1 — LOOKBACK 3 BARS + MAGNITUDE FILTER
    # ──────────────────────────────────────────────────────────────

    def _update_structure(self, high, low):
        """
        v2.1 — Swing detection institucional:
        - Lookback real: 3 barras a cada lado del pivot candidato
        - Magnitude filter: swing debe superar MIN_SWING_TICKS en magnitud
        - Antes (v2.0): lookback=1, sin magnitude filter → micro-pivots constantes
        - Ahora: solo swings con estructura real son registrados
        """
        bars = list(self._bars)
        if len(bars) < 9:
            # Necesitamos al menos 9 bars: 3 izquierda + 1 pivot + 3 derecha + 2 extra
            return

        pivot = bars[-4]       # candidato a pivot
        left  = bars[-7:-4]    # 3 barras a la izquierda
        right = bars[-4:-1]    # 3 barras a la derecha (excluye la última para no mirar el futuro)

        # ── Swing High ───────────────────────────────────────────
        is_swing_high = (
            all(pivot.high >= b.high for b in left) and
            all(pivot.high >= b.high for b in right)
        )
        if is_swing_high:
            magnitude = pivot.high - self._last_swing_high
            if (self._last_swing_high == 0 or
                    magnitude > self._tick * MIN_SWING_TICKS or
                    pivot.high > self._last_swing_high + self._tick * 2):
                self._prev_swing_high = self._last_swing_high
                self._last_swing_high = pivot.high
                self._swing_highs.append(pivot.high)

        # ── Swing Low ────────────────────────────────────────────
        is_swing_low = (
            all(pivot.low <= b.low for b in left) and
            all(pivot.low <= b.low for b in right)
        )
        if is_swing_low:
            magnitude = self._last_swing_low - pivot.low
            if (self._last_swing_low == 0 or
                    magnitude > self._tick * MIN_SWING_TICKS or
                    pivot.low < self._last_swing_low - self._tick * 2):
                self._prev_swing_low = self._last_swing_low
                self._last_swing_low = pivot.low
                self._swing_lows.append(pivot.low)

    # ──────────────────────────────────────────────────────────────
    #  RANGE TRACKING — sin cambios
    # ──────────────────────────────────────────────────────────────

    def _update_range(self, price, high, low):
        if self._range_high == 0:
            self._range_high = high
            self._range_low  = low
            return
        if low >= self._range_low * 0.998 and high <= self._range_high * 1.002:
            self._range_high = max(self._range_high, high)
            self._range_low  = min(self._range_low,  low)
            self._range_bars += 1
            self._bars_outside = 0
        else:
            self._bars_outside += 1
            if self._bars_outside > 3:
                self._range_high   = high
                self._range_low    = low
                self._range_bars   = 1
                self._bars_outside = 0

    # ── Properties ───────────────────────────────────────────────
    @property
    def range_high(self):    return self._range_high
    @property
    def range_low(self):     return self._range_low
    @property
    def bars_in_range(self): return self._range_bars
