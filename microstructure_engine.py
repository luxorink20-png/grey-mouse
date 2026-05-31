# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — microstructure_engine.py
#  Institutional Micro Range & Breakout Detector v2.0
#
#  CAMBIOS v2.0:
#  - Mínimo 6 velas (era 4) para rango válido
#  - Máximo 20 velas — rango muy largo = no es compresión
#  - compression_strength: 0-100 basado en ATR, overlap, delta
#  - NO detectar chop aleatorio como compresión
#  - Breakout confirmado: mínimo 3 ticks fuera + delta coherente
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MicrostructureResult:
    active:               bool  = False
    range_high:           float = 0.0
    range_low:            float = 0.0
    range_size:           float = 0.0
    compression_active:   bool  = False
    compression_strength: int   = 0      # 0-100
    breakout:             Optional[str] = None
    target1:              float = 0.0
    runner:               float = 0.0
    confidence:           int   = 0
    bars_in_range:        int   = 0
    reason:               str   = ""

    def to_dict(self) -> dict:
        return {
            "active":               self.active,
            "range_high":           self.range_high,
            "range_low":            self.range_low,
            "range_size":           self.range_size,
            "compression_active":   self.compression_active,
            "compression_strength": self.compression_strength,
            "breakout":             self.breakout,
            "target1":              self.target1,
            "runner":               self.runner,
            "confidence":           self.confidence,
            "bars_in_range":        self.bars_in_range,
            "reason":               self.reason,
        }


@dataclass
class MicroBar:
    price: float; high: float; low: float
    delta: float; volume: float
    price_move: float; absorption: bool
    event: str; zone: str


class MicrostructureEngine:
    """
    Detector de micro rangos institucionales v2.0

    Rango válido:
    - 6-20 velas (era 4)
    - Compresión real: rango ≤ 2.0 puntos (8 ticks)
    - Volumen activo: no detectar chop sin participación
    - Delta neutral/inconsistente durante compresión
    - Breakout: mínimo 3 ticks + delta coherente
    """

    MIN_BARS_COMPRESSION  = 6     # era 4
    MAX_BARS_COMPRESSION  = 20    # nuevo límite superior
    COMPRESSION_THRESHOLD = 2.0   # 8 ticks MES
    BREAKOUT_THRESHOLD    = 0.75  # 3 ticks mínimo (era 1.0)
    MIN_VOLUME_FACTOR     = 0.65
    DELTA_CONFIRM_MIN     = 60
    RUNNER_MULTIPLIER     = 1.5
    RELEVANT_ZONES = {
        "AT_VAH", "AT_VAL", "AT_POC",
        "ABOVE_VAH", "BELOW_VAL", "IN_VALUE_AREA"
    }

    def __init__(self, window: int = 25):
        self._bars:          deque = deque(maxlen=window)
        self._range_high:    float = 0.0
        self._range_low:     float = 0.0
        self._range_bars:    int   = 0
        self._in_range:      bool  = False
        self._avg_volume:    float = 0.0
        self._vol_samples:   deque = deque(maxlen=30)
        self._last_breakout: Optional[str] = None
        self._breakout_bars: int   = 0
        self._atr_samples:   deque = deque(maxlen=14)

    def analyze(self, event_result: dict, level_context,
                confluence, raw_data: dict) -> MicrostructureResult:

        ctx        = event_result.get("context", {})
        event      = event_result.get("event",   "NONE")
        price      = float(raw_data.get("price", 0))
        high       = float(raw_data.get("high",  price))
        low        = float(raw_data.get("low",   price))
        volume     = float(raw_data.get("volume", ctx.get("volume", 0)))
        delta      = ctx.get("delta",      0)
        price_move = ctx.get("price_move", 0)
        absorption = ctx.get("absorption", False)
        zone       = getattr(level_context, "zone", "UNKNOWN")
        score      = getattr(confluence,    "score", 0)

        # ATR tracking
        self._atr_samples.append(high - low)

        if volume > 0:
            self._vol_samples.append(volume)
        self._avg_volume = (sum(self._vol_samples) / len(self._vol_samples)
                            if self._vol_samples else volume)

        bar = MicroBar(price=price, high=high, low=low, delta=delta,
                       volume=volume, price_move=price_move,
                       absorption=absorption, event=event, zone=zone)
        self._bars.append(bar)

        if len(self._bars) < self.MIN_BARS_COMPRESSION:
            return MicrostructureResult(reason="Calentando buffer")

        compression, comp_strength = self._detect_compression(zone, score)

        if compression:
            breakout, breakout_dir = self._detect_breakout(
                price, high, low, delta, price_move, absorption, volume
            )
            if breakout:
                self._last_breakout = breakout_dir
                self._breakout_bars = 1
                range_size = self._range_high - self._range_low
                t1, runner = self._calculate_targets(price, breakout_dir, range_size)
                conf = self._breakout_confidence(
                    delta, price_move, absorption, volume, score, comp_strength
                )
                self._in_range   = False
                self._range_bars = 0
                return MicrostructureResult(
                    active             = True,
                    range_high         = round(self._range_high, 2),
                    range_low          = round(self._range_low,  2),
                    range_size         = round(range_size, 2),
                    compression_active = False,
                    compression_strength = comp_strength,
                    breakout           = breakout_dir,
                    target1            = round(t1,     2),
                    runner             = round(runner, 2),
                    confidence         = conf,
                    bars_in_range      = self._range_bars,
                    reason             = f"Breakout {breakout_dir} | comp={comp_strength}",
                )

            range_size = self._range_high - self._range_low
            return MicrostructureResult(
                active             = True,
                range_high         = round(self._range_high, 2),
                range_low          = round(self._range_low,  2),
                range_size         = round(range_size, 2),
                compression_active = True,
                compression_strength = comp_strength,
                breakout           = None,
                confidence         = self._compression_confidence(score, comp_strength),
                bars_in_range      = self._range_bars,
                reason             = f"Compresión {self._range_bars} velas | strength={comp_strength}",
            )

        if self._last_breakout is not None:
            self._breakout_bars += 1
            if self._breakout_bars > 5:
                self._last_breakout = None
                self._breakout_bars = 0

        return MicrostructureResult(active=False, reason="Sin micro rango activo")

    # ──────────────────────────────────────────────────────────────
    #  COMPRESIÓN v2.0
    # ──────────────────────────────────────────────────────────────

    def _detect_compression(self, zone: str, score: int) -> tuple:
        bars = list(self._bars)
        if len(bars) < self.MIN_BARS_COMPRESSION:
            return False, 0

        if zone not in self.RELEVANT_ZONES:
            self._in_range = False; self._range_bars = 0
            return False, 0

        if score < 20:
            return False, 0

        recent = bars[-self.MIN_BARS_COMPRESSION:]
        highs  = [b.high  for b in recent]
        lows   = [b.low   for b in recent]
        r_high = max(highs); r_low = min(lows)
        r_size = r_high - r_low

        if r_size > self.COMPRESSION_THRESHOLD:
            self._in_range = False; self._range_bars = 0
            return False, 0

        # Volumen activo — no detectar chop muerto
        avg_recent_vol = sum(b.volume for b in recent) / len(recent)
        if self._avg_volume > 0 and avg_recent_vol < self._avg_volume * self.MIN_VOLUME_FACTOR:
            self._in_range = False; self._range_bars = 0
            return False, 0

        # Delta neutral durante compresión (institución absorbe)
        avg_abs_delta = sum(abs(b.delta) for b in recent) / len(recent)
        if avg_abs_delta < 30:
            return False, 0

        # Eventos de acumulación
        acum_count = sum(1 for b in recent if "ACUMULACI" in b.event)
        if acum_count < self.MIN_BARS_COMPRESSION - 2:
            self._in_range = False; self._range_bars = 0
            return False, 0

        # Demasiadas velas = rango muerto, no compresión
        if not self._in_range:
            self._in_range   = True
            self._range_high = r_high
            self._range_low  = r_low
            self._range_bars = len(recent)
        else:
            self._range_high = max(self._range_high, r_high)
            self._range_low  = min(self._range_low,  r_low)
            self._range_bars += 1

        if self._range_bars > self.MAX_BARS_COMPRESSION:
            self._in_range = False; self._range_bars = 0
            return False, 0

        # Compression strength
        comp_strength = self._calc_compression_strength(recent, r_size, avg_abs_delta)
        return True, comp_strength

    def _calc_compression_strength(self, bars: list,
                                    range_size: float,
                                    avg_delta: float) -> int:
        """0-100 basado en ATR relativo, overlap, delta contraction."""
        score = 0

        # ATR actual vs histórico
        if self._atr_samples and len(self._atr_samples) >= 5:
            avg_atr = sum(self._atr_samples) / len(self._atr_samples)
            recent_atr = sum(b.high - b.low for b in bars) / len(bars)
            if avg_atr > 0:
                atr_ratio = recent_atr / avg_atr
                if atr_ratio < 0.5:   score += 30
                elif atr_ratio < 0.7: score += 20
                elif atr_ratio < 0.9: score += 10

        # Overlap entre velas
        overlaps = 0
        for i in range(1, len(bars)):
            if (min(bars[i].high, bars[i-1].high) >
                    max(bars[i].low,  bars[i-1].low)):
                overlaps += 1
        overlap_ratio = overlaps / max(len(bars)-1, 1)
        if overlap_ratio >= 0.8: score += 30
        elif overlap_ratio >= 0.6: score += 20
        elif overlap_ratio >= 0.4: score += 10

        # Absorción
        absorb_count = sum(1 for b in bars if b.absorption)
        score += min(20, absorb_count * 8)

        # Rango pequeño
        if range_size <= 0.5:   score += 20
        elif range_size <= 1.0: score += 10
        elif range_size <= 1.5: score += 5

        return min(score, 100)

    # ──────────────────────────────────────────────────────────────
    #  BREAKOUT v2.0 — mínimo 3 ticks + delta
    # ──────────────────────────────────────────────────────────────

    def _detect_breakout(self, price, high, low, delta,
                          price_move, absorption, volume) -> tuple:
        if self._range_high <= 0 or self._range_low <= 0:
            return False, None

        # Breakout UP
        if price > self._range_high + self.BREAKOUT_THRESHOLD:
            if delta > self.DELTA_CONFIRM_MIN and not absorption:
                return True, "UP"
            if price_move > self.BREAKOUT_THRESHOLD * 2:
                return True, "UP"

        # Breakout DOWN
        if price < self._range_low - self.BREAKOUT_THRESHOLD:
            if delta < -self.DELTA_CONFIRM_MIN and not absorption:
                return True, "DOWN"
            if price_move < -self.BREAKOUT_THRESHOLD * 2:
                return True, "DOWN"

        return False, None

    def _calculate_targets(self, price, direction, range_size) -> tuple:
        if direction == "UP":
            t1     = self._range_high + range_size
            runner = self._range_high + range_size * self.RUNNER_MULTIPLIER
        else:
            t1     = self._range_low - range_size
            runner = self._range_low - range_size * self.RUNNER_MULTIPLIER
        return t1, runner

    def _breakout_confidence(self, delta, price_move, absorption,
                              volume, score, comp_strength) -> int:
        conf = 0
        if abs(delta) > self.DELTA_CONFIRM_MIN * 2: conf += 28
        elif abs(delta) > self.DELTA_CONFIRM_MIN:   conf += 18
        if abs(price_move) > self.BREAKOUT_THRESHOLD * 2: conf += 24
        elif abs(price_move) > self.BREAKOUT_THRESHOLD:   conf += 14
        if not absorption: conf += 18
        if self._avg_volume > 0 and volume > self._avg_volume * 1.2: conf += 14
        if score >= 70: conf += 8
        if comp_strength >= 70: conf += 8
        return min(conf, 95)

    def _compression_confidence(self, score, comp_strength) -> int:
        base = 50
        if self._range_bars >= 8:  base += 12
        if self._range_bars >= 12: base += 8
        if score >= 55: base += 8
        if comp_strength >= 60: base += 10
        return min(base, 85)

    @property
    def is_in_compression(self): return self._in_range
    @property
    def range_high(self): return self._range_high
    @property
    def range_low(self):  return self._range_low
    @property
    def bars_in_range(self): return self._range_bars