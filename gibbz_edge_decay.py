# gibbz_edge_decay.py
# Edge Decay Model v1.0

from collections import deque
from dataclasses import dataclass


@dataclass
class EdgeDecayResult:
    edge_strength:    int   = 100  # 0-100 fuerza actual del edge
    decay_rate:       str   = "NONE"  # NONE/SLOW/MODERATE/FAST/EXPIRED
    bars_active:      int   = 0    # barras desde activación
    lifespan_pct:     int   = 100  # % de vida restante estimado
    edge_expired:     bool  = False
    peak_bar:         int   = 0    # barra de máxima fuerza


class EdgeDecayEngine:
    """
    Modela la degradación del edge después de aparición de EFFICIENT_TREND.
    El edge tiene una vida útil — después de ciertos eventos se degrada.
    """

    LIFESPAN_BARS = 15   # vida estimada del edge en barras
    TRAP_IMPACT   = 0.8  # penalización por trap alto

    def __init__(self):
        self._activation_bar:  int   = 0
        self._bar_count:       int   = 0
        self._peak_strength:   int   = 0
        self._peak_bar:        int   = 0
        self._active:          bool  = False
        self._expired_last:    bool  = False
        self._eff_history:     deque = deque(maxlen=10)
        self._trap_history:    deque = deque(maxlen=10)
        self._strength_history:deque = deque(maxlen=20)

    def analyze(self, env_r, etil_r: "ETILResult",
                bar_count: int) -> EdgeDecayResult:

        self._bar_count = bar_count
        eff  = getattr(env_r, "directional_efficiency", 0)
        trap = getattr(env_r, "trap_density", 0)
        env  = getattr(env_r, "environment",  "ROTATIONAL")
        ets  = getattr(etil_r, "ets_score",   0)

        self._eff_history.append(eff)
        self._trap_history.append(trap)

        # Re-activar si el edge expiró y hay nuevo spike ETS≥65
        new_signal = (env == "EFFICIENT_TREND" or ets >= 65)
        if self._active and self._expired_last and new_signal:
            self._active = False
            self._peak_strength = 0
            self._strength_history.clear()

        # Activar edge cuando EFFICIENT_TREND o ETS >= 65
        if new_signal and not self._active:
            self._active = True
            self._activation_bar = bar_count

        if not self._active:
            return EdgeDecayResult()

        bars_active = bar_count - self._activation_bar

        # ── Edge strength ───────────────────────────────────────
        # Base: eff actual
        strength = eff

        # Penalizar por trap
        trap_penalty = max(0, (trap - 30) * 0.5)
        strength -= trap_penalty

        # Penalizar por tiempo (decay temporal)
        time_decay = min(bars_active * 2, 40)
        strength -= time_decay

        # Penalizar si eff cayendo
        if len(self._eff_history) >= 3:
            eff_trend = list(self._eff_history)[-3:]
            if eff_trend[-1] < eff_trend[0] - 20:
                strength -= 15

        strength = max(0, min(int(strength), 100))
        self._strength_history.append(strength)

        # Track peak
        if strength > self._peak_strength:
            self._peak_strength = strength
            self._peak_bar = bar_count

        # ── Lifespan ────────────────────────────────────────────
        lifespan_pct = max(0, 100 - int(bars_active / self.LIFESPAN_BARS * 100))

        # ── Decay rate ──────────────────────────────────────────
        if bars_active == 0:
            decay_rate = "NONE"
        elif strength <= 0 or lifespan_pct <= 0:
            decay_rate = "EXPIRED"
        elif len(self._strength_history) >= 3:
            hist = list(self._strength_history)[-5:]
            drop = hist[0] - hist[-1] if hist else 0
            if drop >= 40:   decay_rate = "FAST"
            elif drop >= 20: decay_rate = "MODERATE"
            elif drop >= 5:  decay_rate = "SLOW"
            else:            decay_rate = "NONE"
        else:
            decay_rate = "NONE"

        expired = (decay_rate == "EXPIRED" or
                   lifespan_pct <= 0 or
                   strength <= 10)

        self._expired_last = expired

        return EdgeDecayResult(
            edge_strength = strength,
            decay_rate    = decay_rate,
            bars_active   = bars_active,
            lifespan_pct  = lifespan_pct,
            edge_expired  = expired,
            peak_bar      = self._peak_bar,
        )