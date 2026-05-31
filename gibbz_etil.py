# gibbz_etil.py
# Early Trend Intelligence Layer v1.0
# ADITIVO — no modifica ningún módulo existente

from collections import deque
from dataclasses import dataclass


@dataclass
class ETILResult:
    ets_score:        int   = 0      # 0-100
    classification:   str   = "NOISE"  # NOISE/WATCH/EARLY_TREND
    eff_acceleration: int   = 0      # velocidad de subida de eff
    trap_decay_rate:  int   = 0      # velocidad de bajada de trap
    delta_slope:      str   = "FLAT" # RISING/FALLING/FLAT
    zone_transition:  bool  = False  # cambió de zona en últimas 3 bars
    regime_shift:     bool  = False  # cambió a EFFICIENT_TREND
    bars_since_shift: int   = 0      # barras desde último shift
    confidence:       int   = 0      # confianza del score 0-100


class ETILEngine:
    """
    Early Trend Intelligence Layer.
    Detecta inicio de tendencia institucional en bars 1-20.
    NO reemplaza confirmation_engine — solo detecta temprano.

    Principio: detection != execution
    """

    WINDOW = 20

    def __init__(self):
        self._eff_history:   deque = deque(maxlen=self.WINDOW)
        self._trap_history:  deque = deque(maxlen=self.WINDOW)
        self._delta_history: deque = deque(maxlen=self.WINDOW)
        self._zone_history:  deque = deque(maxlen=5)
        self._env_history:   deque = deque(maxlen=10)
        self._bar_count:     int   = 0
        self._shift_bar:     int   = 0
        self._last_env:      str   = ""

    def analyze(self, env_r, cont_r, conf_r, raw_data: dict) -> ETILResult:
        self._bar_count += 1

        eff   = getattr(env_r,  "directional_efficiency", 0)
        trap  = getattr(env_r,  "trap_density",           0)
        env   = getattr(env_r,  "environment",            "ROTATIONAL")
        delta = raw_data.get("delta", 0)
        zone  = raw_data.get("zone",  "UNKNOWN")
        cont  = getattr(cont_r, "continuation_probability", 50)

        self._eff_history.append(eff)
        self._trap_history.append(trap)
        self._delta_history.append(delta)
        self._zone_history.append(zone)
        self._env_history.append(env)

        # ── Regime shift detection ──────────────────────────────
        regime_shift = (env == "EFFICIENT_TREND" and
                        self._last_env != "EFFICIENT_TREND")
        if regime_shift:
            self._shift_bar = self._bar_count
        self._last_env = env

        bars_since_shift = (self._bar_count - self._shift_bar
                            if self._shift_bar > 0 else 0)

        # ── Eff acceleration ────────────────────────────────────
        eff_accel = self._calc_eff_acceleration()

        # ── Trap decay ──────────────────────────────────────────
        trap_decay = self._calc_trap_decay()

        # ── Delta slope ─────────────────────────────────────────
        delta_slope = self._calc_delta_slope()

        # ── Zone transition ─────────────────────────────────────
        zone_transition = self._calc_zone_transition()

        # ── ETS SCORE ───────────────────────────────────────────
        ets = 0

        # Component 1: Eff acceleration (max 30 pts)
        if eff_accel >= 30:   ets += 30
        elif eff_accel >= 20: ets += 20
        elif eff_accel >= 10: ets += 10

        # Component 2: Trap decay (max 20 pts)
        if trap_decay >= 30:   ets += 20
        elif trap_decay >= 15: ets += 12
        elif trap_decay >= 5:  ets += 5

        # Component 3: Delta slope (max 20 pts)
        if delta_slope == "RISING":   ets += 20
        elif delta_slope == "STABLE": ets += 10

        # Component 4: Zone transition (max 15 pts)
        if zone_transition: ets += 15

        # Component 5: Regime shift timing (max 15 pts)
        if regime_shift:
            ets += 15
        elif env == "EFFICIENT_TREND" and bars_since_shift <= 3:
            ets += 10
        elif env == "EFFICIENT_TREND" and bars_since_shift <= 8:
            ets += 5

        # ── CLASSIFICATION ──────────────────────────────────────
        if ets >= 65:
            classification = "EARLY_TREND"
        elif ets >= 35:
            classification = "WATCH"
        else:
            classification = "NOISE"

        # ── CONFIDENCE ──────────────────────────────────────────
        confidence = min(self._bar_count * 5, 100)
        if self._bar_count < 5:
            confidence = 20
            classification = "NOISE"

        return ETILResult(
            ets_score        = ets,
            classification   = classification,
            eff_acceleration = eff_accel,
            trap_decay_rate  = trap_decay,
            delta_slope      = delta_slope,
            zone_transition  = zone_transition,
            regime_shift     = regime_shift,
            bars_since_shift = bars_since_shift,
            confidence       = confidence,
        )

    def _calc_eff_acceleration(self) -> int:
        if len(self._eff_history) < 3:
            return 0
        recent = list(self._eff_history)[-5:]
        if len(recent) < 2:
            return 0
        delta = recent[-1] - recent[0]
        return max(0, delta)

    def _calc_trap_decay(self) -> int:
        if len(self._trap_history) < 3:
            return 0
        recent = list(self._trap_history)[-5:]
        if len(recent) < 2:
            return 0
        delta = recent[0] - recent[-1]
        return max(0, delta)

    def _calc_delta_slope(self) -> str:
        if len(self._delta_history) < 3:
            return "FLAT"
        recent = list(self._delta_history)[-5:]
        pos = sum(1 for d in recent if d > 100)
        neg = sum(1 for d in recent if d < -100)
        total = len(recent)
        if pos >= total * 0.6:   return "RISING"
        if neg >= total * 0.6:   return "FALLING"
        if pos >= total * 0.4:   return "STABLE"
        return "FLAT"

    def _calc_zone_transition(self) -> bool:
        if len(self._zone_history) < 3:
            return False
        zones = list(self._zone_history)
        return len(set(zones[-3:])) >= 2