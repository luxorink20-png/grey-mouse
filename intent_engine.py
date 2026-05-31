# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — intent_engine.py
#  Institutional Narrative Engine v1.0
#
#  PREGUNTA: ¿Qué están haciendo las instituciones AHORA MISMO?
#
#  NARRATIVAS:
#  INDUCTION     → trampa para retail, caza de stops
#  DISTRIBUTION  → venden en fuerza mientras suben
#  ACCUMULATION  → compran en debilidad silenciosamente
#  SQUEEZE       → atrapan un lado, explotan el otro
#  REBALANCE     → precio vuelve al POC tras extensión
#  UNCLEAR       → sin narrativa dominante
#
#  PIPELINE POSITION:
#  confluence → validator → [INTENT ENGINE] → risk → view
# ╔══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass
from collections import deque
from typing import Optional


# ══════════════════════════════════════════════════════════════════
#  NARRATIVE DEFINITIONS
# ══════════════════════════════════════════════════════════════════

NARRATIVES = {
    "INDUCTION": (
        "Instituciones inducen retail hacia trampa. "
        "Movimiento agresivo sin follow-through real."
    ),
    "DISTRIBUTION": (
        "Instituciones distribuyen posiciones largas. "
        "Venden en fuerza mientras precio parece subir."
    ),
    "ACCUMULATION": (
        "Instituciones acumulan en silencio. "
        "Absorben oferta sin mover precio significativamente."
    ),
    "SQUEEZE": (
        "Squeeze institucional activo. "
        "Un lado atrapado, explosion inminente."
    ),
    "REBALANCE": (
        "Precio rebalanceando hacia POC. "
        "Extension agotada, instituciones cubren posiciones."
    ),
    "UNCLEAR": (
        "Sin narrativa institucional dominante. "
        "Esperar mejor estructura."
    ),
}

TRAPPED_SIDES = {
    "INDUCTION":    "RETAIL",
    "DISTRIBUTION": "BUYERS",
    "ACCUMULATION": "SELLERS",
    "SQUEEZE":      "BOTH",
    "REBALANCE":    "NONE",
    "UNCLEAR":      "NONE",
}


# ══════════════════════════════════════════════════════════════════
#  INTENT RESULT
# ══════════════════════════════════════════════════════════════════

@dataclass
class IntentResult:
    """
    Full institutional narrative for one tick.

    narrative:      dominant institutional behavior
    trapped_side:   who is trapped (BUYERS/SELLERS/RETAIL/BOTH/NONE)
    likely_target:  estimated price target (0 = unknown)
    conviction:     0-100 confidence in the narrative
    description:    human-readable institutional read
    signals:        list of signals that triggered this narrative
    """
    narrative:     str
    trapped_side:  str
    likely_target: float
    conviction:    int
    description:   str
    signals:       list

    def __str__(self) -> str:
        return (
            self.narrative +
            " | trapped=" + self.trapped_side +
            " | target=" + str(round(self.likely_target, 2)) +
            " | conviction=" + str(self.conviction) + "%"
        )


# ══════════════════════════════════════════════════════════════════
#  PRICE HISTORY FOR INTENT DETECTION
# ══════════════════════════════════════════════════════════════════

@dataclass
class IntentBar:
    price:  float
    delta:  float
    volume: float
    high:   float
    low:    float
    event:  str
    zone:   str
    bias:   str


class IntentBuffer:

    def __init__(self, size: int = 15):
        self._bars: deque = deque(maxlen=size)

    def push(self, bar: IntentBar) -> None:
        self._bars.append(bar)

    def last(self, n: int = 5) -> list:
        data = list(self._bars)
        return data[-n:] if len(data) >= n else data

    def has_enough(self, n: int = 5) -> bool:
        return len(self._bars) >= n

    def delta_trend(self, n: int = 5) -> float:
        bars = self.last(n)
        if not bars:
            return 0.0
        return sum(b.delta for b in bars) / len(bars)

    def price_trend(self, n: int = 5) -> float:
        bars = self.last(n)
        if len(bars) < 2:
            return 0.0
        return bars[-1].price - bars[0].price

    def volume_trend(self, n: int = 5) -> float:
        bars = self.last(n)
        if not bars:
            return 0.0
        return sum(b.volume for b in bars) / len(bars)

    def recent_events(self, n: int = 5) -> list:
        return [b.event for b in self.last(n)]

    def recent_zones(self, n: int = 5) -> list:
        return [b.zone for b in self.last(n)]

    def consecutive_event(self, event: str, n: int = 3) -> bool:
        events = self.recent_events(n)
        return len(events) >= n and all(e == event for e in events)


# ══════════════════════════════════════════════════════════════════
#  NARRATIVE DETECTORS
#  Each returns (detected: bool, conviction: int, signals: list)
# ══════════════════════════════════════════════════════════════════

def detect_induction(
    bar:    IntentBar,
    buffer: IntentBuffer,
    tick:   float = 0.25
) -> tuple:
    """
    INDUCTION — institutional stop hunt / fake move.

    Signature:
    - Aggressive price move (INTENTO) in one direction
    - Delta DOES NOT confirm the move (divergence)
    - Immediate reversal follows
    - Often happens at VAH/VAL/POC
    """
    signals   = []
    conviction = 0

    if not buffer.has_enough(3):
        return False, 0, []

    bars = buffer.last(4)
    if len(bars) < 3:
        return False, 0, []

    price_move   = bars[-1].price - bars[-2].price
    delta_signal = bars[-1].delta

    # Price went up but delta negative = sellers absorbing the move
    if price_move > tick * 4 and delta_signal < -100:
        signals.append("price_up_delta_negative")
        conviction += 35

    # Price went down but delta positive = buyers absorbing
    if price_move < -tick * 4 and delta_signal > 100:
        signals.append("price_down_delta_positive")
        conviction += 35

    # Pattern: INTENTO followed immediately by FALLO
    recent_events = buffer.recent_events(3)
    if len(recent_events) >= 2:
        if recent_events[-2] == "INTENTO" and recent_events[-1] == "FALLO":
            signals.append("intento_then_fallo")
            conviction += 30

    # At key zone = more likely to be induction
    if bar.zone in ("AT_VAH", "AT_VAL", "AT_POC"):
        signals.append("at_key_zone")
        conviction += 20

    # Price returned through origin after spike
    if len(bars) >= 3:
        prev_range = bars[-2].high - bars[-2].low
        if prev_range > tick * 8:
            curr_close = bars[-1].price
            spike_mid  = (bars[-2].high + bars[-2].low) / 2
            if abs(curr_close - spike_mid) < tick * 3:
                signals.append("price_returned_to_spike_origin")
                conviction += 25

    detected = conviction >= 50 and len(signals) >= 2
    return detected, min(conviction, 95), signals


def detect_distribution(
    bar:    IntentBar,
    buffer: IntentBuffer,
    tick:   float = 0.25
) -> tuple:
    """
    DISTRIBUTION — institutions selling into strength.

    Signature:
    - Price at or above VAH (extension zone)
    - Negative delta despite price being elevated
    - Volume high but price not advancing
    - FALLO or AGOTAMIENTO events
    """
    signals    = []
    conviction = 0

    if not buffer.has_enough(4):
        return False, 0, []

    # Must be in upper zone
    if bar.zone not in ("AT_VAH", "ABOVE_VAH"):
        return False, 0, []

    signals.append("price_at_upper_zone")
    conviction += 20

    # Negative delta in bullish zone = distribution
    if bar.delta < -150:
        signals.append("negative_delta_at_vah")
        conviction += 35

    # High volume, low price advance
    avg_vol = buffer.volume_trend(5)
    if bar.volume > avg_vol * 1.3:
        price_trend = buffer.price_trend(3)
        if abs(price_trend) < tick * 3:
            signals.append("high_volume_low_advance")
            conviction += 25

    # Recent FALLO or AGOTAMIENTO = distribution confirmed
    recent = buffer.recent_events(4)
    if "FALLO" in recent or "AGOTAMIENTO" in recent:
        signals.append("reversal_event_present")
        conviction += 25

    # Consecutive bearish delta in upper zone
    bars = buffer.last(4)
    bearish_deltas = sum(1 for b in bars if b.delta < 0)
    if bearish_deltas >= 3:
        signals.append("consecutive_bearish_delta")
        conviction += 20

    detected = conviction >= 55 and len(signals) >= 2
    return detected, min(conviction, 95), signals


def detect_accumulation(
    bar:    IntentBar,
    buffer: IntentBuffer,
    tick:   float = 0.25
) -> tuple:
    """
    ACCUMULATION — institutions buying in weakness silently.

    Signature:
    - Price at or below VAL (discount zone)
    - Positive delta despite price being depressed
    - High volume but price not declining further
    - Absorption flag active
    """
    signals    = []
    conviction = 0

    if not buffer.has_enough(4):
        return False, 0, []

    # Must be in lower zone
    if bar.zone not in ("AT_VAL", "BELOW_VAL"):
        return False, 0, []

    signals.append("price_at_lower_zone")
    conviction += 20

    # Positive delta in bearish zone = accumulation
    if bar.delta > 150:
        signals.append("positive_delta_at_val")
        conviction += 35

    # High volume, price not falling further
    avg_vol = buffer.volume_trend(5)
    if bar.volume > avg_vol * 1.2:
        price_trend = buffer.price_trend(3)
        if price_trend > -tick * 2:
            signals.append("high_volume_holding_support")
            conviction += 25

    # Consecutive ACUMULACION events at low = absorption
    if buffer.consecutive_event("ACUMULACION", 3):
        signals.append("consecutive_acumulacion_at_low")
        conviction += 25

    # Consecutive positive delta bars
    bars = buffer.last(4)
    bullish_deltas = sum(1 for b in bars if b.delta > 0)
    if bullish_deltas >= 3:
        signals.append("consecutive_bullish_delta")
        conviction += 20

    detected = conviction >= 55 and len(signals) >= 2
    return detected, min(conviction, 95), signals


def detect_squeeze(
    bar:    IntentBar,
    buffer: IntentBuffer,
    tick:   float = 0.25
) -> tuple:
    """
    SQUEEZE — tight range then explosive move.

    Signature:
    - Multiple consecutive ACUMULACION bars (tight range)
    - Followed by sudden volume spike
    - Delta commitment in one direction
    - Classic pre-breakout signature
    """
    signals    = []
    conviction = 0

    if not buffer.has_enough(5):
        return False, 0, []

    # Multiple accumulation bars = compression
    recent = buffer.recent_events(5)
    acum_count = sum(1 for e in recent if e == "ACUMULACION")

    if acum_count >= 3:
        signals.append("compression_detected")
        conviction += 30

    # Price range compression
    bars = buffer.last(5)
    if len(bars) >= 5:
        highs = [b.high  for b in bars[:-1]]
        lows  = [b.low   for b in bars[:-1]]
        range_size = max(highs) - min(lows)
        if range_size < tick * 8:
            signals.append("tight_price_range")
            conviction += 25

    # Current bar volume spike
    avg_vol = buffer.volume_trend(5)
    if bar.volume > avg_vol * 2.0:
        signals.append("volume_spike")
        conviction += 30

    # Strong delta commitment
    if abs(bar.delta) > 300:
        signals.append("strong_delta_commitment")
        conviction += 25

    detected = conviction >= 60 and len(signals) >= 3
    return detected, min(conviction, 95), signals


def detect_rebalance(
    bar:         IntentBar,
    buffer:      IntentBuffer,
    poc_price:   float,
    tick:        float = 0.25
) -> tuple:
    """
    REBALANCE — price returning to POC after extension.

    Signature:
    - Price extended far from POC (> 10 ticks)
    - FALLO or AGOTAMIENTO event
    - Delta supports return direction
    - Classic mean reversion institutional move
    """
    signals    = []
    conviction = 0

    if poc_price <= 0:
        return False, 0, []

    distance_to_poc = bar.price - poc_price
    abs_distance    = abs(distance_to_poc)

    # Must be extended from POC
    if abs_distance < tick * 8:
        return False, 0, []

    signals.append("extended_from_poc_" + str(round(abs_distance, 2)) + "pts")
    conviction += 25

    # Reversal event present
    recent = buffer.recent_events(3)
    if "FALLO" in recent or "AGOTAMIENTO" in recent:
        signals.append("reversal_event")
        conviction += 35

    # Delta supports return direction
    if distance_to_poc > 0 and bar.delta < -50:
        signals.append("delta_supports_return_bearish")
        conviction += 25
    elif distance_to_poc < 0 and bar.delta > 50:
        signals.append("delta_supports_return_bullish")
        conviction += 25

    # Price already moving back toward POC
    price_trend = buffer.price_trend(3)
    if distance_to_poc > 0 and price_trend < -tick * 2:
        signals.append("price_moving_toward_poc")
        conviction += 20
    elif distance_to_poc < 0 and price_trend > tick * 2:
        signals.append("price_moving_toward_poc")
        conviction += 20

    detected = conviction >= 55 and len(signals) >= 2
    return detected, min(conviction, 95), signals


# ══════════════════════════════════════════════════════════════════
#  INTENT ENGINE — MAIN CLASS
# ══════════════════════════════════════════════════════════════════

class IntentEngine:
    """
    GIBBZ Institutional Narrative Engine v1.0

    Analyzes EVENT + ZONE + delta history to determine
    what institutions are doing right now.

    Priority order (highest conviction wins):
      1. SQUEEZE      — pre-explosion setup (most actionable)
      2. INDUCTION    — trap detected (avoid or fade)
      3. DISTRIBUTION — selling into strength
      4. ACCUMULATION — buying in weakness
      5. REBALANCE    — mean reversion toward POC
      6. UNCLEAR      — no dominant narrative

    Usage:
        intent = IntentEngine()
        result = intent.analyze(event_result, level_context,
                                confluence, validation)
    """

    def __init__(self, buffer_size: int = 15, tick: float = 0.25):
        self._buffer = IntentBuffer(size=buffer_size)
        self._tick   = tick
        self._last   = "UNCLEAR"

    def analyze(self,
                event_result:  dict,
                level_context,
                confluence,
                validation) -> IntentResult:
        """
        Main entry point. Analyzes one tick.

        Args:
            event_result:  dict from EventEngine
            level_context: LevelContext from InstitutionalLevels
            confluence:    ConfluenceResult from ConfluenceEngine
            validation:    ValidationResult from Validator

        Returns:
            IntentResult with full narrative analysis
        """
        ctx    = event_result.get("context", {})
        event  = event_result.get("event", "NONE")
        price  = getattr(level_context, "nearest_price", 0.0)
        zone   = getattr(level_context, "zone",          "UNKNOWN")
        bias   = getattr(level_context, "reaction_bias", "NEUTRAL")
        poc    = getattr(level_context, "nearest_price", 0.0)

        # Try to get actual price from confluence
        if hasattr(confluence, "score"):
            price = getattr(level_context, "nearest_price", price)

        delta  = ctx.get("delta",  0)
        volume = ctx.get("volume", 0)

        bar = IntentBar(
            price  = price,
            delta  = delta,
            volume = volume,
            high   = price,
            low    = price,
            event  = event.replace("ACUMULACI\u00d3N", "ACUMULACION"),
            zone   = zone,
            bias   = bias,
        )
        self._buffer.push(bar)

        if not self._buffer.has_enough(3):
            return IntentResult(
                narrative     = "UNCLEAR",
                trapped_side  = "NONE",
                likely_target = 0.0,
                conviction    = 0,
                description   = "Calentando buffer de intent...",
                signals       = [],
            )

        # ── RUN ALL DETECTORS ──────────────────────────────────────
        candidates = []

        sq_ok, sq_conv, sq_sig = detect_squeeze(
            bar, self._buffer, self._tick)
        if sq_ok:
            candidates.append(("SQUEEZE", sq_conv, sq_sig))

        ind_ok, ind_conv, ind_sig = detect_induction(
            bar, self._buffer, self._tick)
        if ind_ok:
            candidates.append(("INDUCTION", ind_conv, ind_sig))

        dist_ok, dist_conv, dist_sig = detect_distribution(
            bar, self._buffer, self._tick)
        if dist_ok:
            candidates.append(("DISTRIBUTION", dist_conv, dist_sig))

        acc_ok, acc_conv, acc_sig = detect_accumulation(
            bar, self._buffer, self._tick)
        if acc_ok:
            candidates.append(("ACCUMULATION", acc_conv, acc_sig))

        reb_ok, reb_conv, reb_sig = detect_rebalance(
            bar, self._buffer, poc, self._tick)
        if reb_ok:
            candidates.append(("REBALANCE", reb_conv, reb_sig))

        # ── SELECT HIGHEST CONVICTION ──────────────────────────────
        if not candidates:
            narrative  = "UNCLEAR"
            conviction = 0
            signals    = []
        else:
            candidates.sort(key=lambda x: x[1], reverse=True)
            narrative, conviction, signals = candidates[0]

        self._last = narrative

        # ── ESTIMATE TARGET ────────────────────────────────────────
        target = self._estimate_target(
            narrative, bar.price, level_context
        )

        description = NARRATIVES.get(narrative, "Sin narrativa definida.")
        trapped     = TRAPPED_SIDES.get(narrative, "NONE")

        return IntentResult(
            narrative     = narrative,
            trapped_side  = trapped,
            likely_target = target,
            conviction    = conviction,
            description   = description,
            signals       = signals,
        )

    def _estimate_target(self,
                         narrative:     str,
                         price:         float,
                         level_context) -> float:
        """
        Estimates likely price target based on narrative.
        Uses institutional level structure for reference.
        """
        vah = 0.0
        val = 0.0
        poc = 0.0

        # Extract levels safely
        try:
            near_levels = getattr(level_context, "near_levels", [])
            zone        = getattr(level_context, "zone", "")
            nearest     = getattr(level_context, "nearest_price", price)
            nearest_lvl = getattr(level_context, "nearest_level", "")
        except Exception:
            return 0.0

        if narrative == "DISTRIBUTION":
            # Target: VAL (institutions push price down to VAL)
            if "VAL" in nearest_lvl:
                return nearest
            return price * 0.998  # approximate -0.2%

        if narrative == "ACCUMULATION":
            # Target: VAH (institutions push price up to VAH)
            if "VAH" in nearest_lvl:
                return nearest
            return price * 1.002  # approximate +0.2%

        if narrative == "REBALANCE":
            # Target: POC
            if "POC" in nearest_lvl:
                return nearest
            return price  # unknown without full level set

        if narrative == "INDUCTION":
            # Target: opposite of fake move
            return nearest  # back to nearest institutional level

        if narrative == "SQUEEZE":
            # Target: breakout extension
            return price  # direction unknown until breakout

        return 0.0

    @property
    def last_narrative(self) -> str:
        return self._last