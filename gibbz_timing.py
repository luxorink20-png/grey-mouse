# gibbz_timing.py
# Timing Optimization Engine v1.0

from dataclasses import dataclass
from collections import deque


@dataclass
class TimingResult:
    entry_timing_score: int   = 50   # 0-100
    detection_delay:    int   = 0    # barras entre ETS y conf
    late_detection:     bool  = False
    early_opportunity:  bool  = False
    optimal_window:     bool  = False
    timing_grade:       str   = "UNKNOWN"  # OPTIMAL/ACCEPTABLE/LATE/MISSED


class TimingEngine:
    """
    Mide el timing entre detección temprana (ETS) y confirmación.
    Identifica si el sistema está llegando temprano, a tiempo o tarde.
    """

    def __init__(self):
        self._ets_activation_bar:  int = 0
        self._conf_activation_bar: int = 0
        self._bar_count:           int = 0
        self._ets_active:         bool = False
        self._conf_active:        bool = False
        self._first_ets_bar:       int = 0
        self._first_conf_bar:      int = 0

    def analyze(self, etil_r: "ETILResult",
                conf_r,
                validation,
                bar_count: int) -> TimingResult:

        self._bar_count = bar_count
        ets_score  = getattr(etil_r, "ets_score",  0)
        conf_score = getattr(conf_r, "confirmation_score", 0)
        validated  = getattr(validation, "validated", False)

        # Track primera activación ETS
        if ets_score >= 65 and not self._ets_active:
            self._ets_active = True
            self._first_ets_bar = bar_count

        # Track primera activación conf
        if conf_score >= 65 and not self._conf_active:
            self._conf_active = True
            self._first_conf_bar = bar_count

        # ── Detection delay ─────────────────────────────────────
        detection_delay = 0
        if self._first_ets_bar > 0 and self._first_conf_bar > 0:
            detection_delay = self._first_conf_bar - self._first_ets_bar

        # ── Flags ───────────────────────────────────────────────
        early_opportunity = (self._ets_active and
                             not self._conf_active and
                             ets_score >= 65)

        late_detection = (self._conf_active and
                          self._first_ets_bar == 0)

        optimal_window = (self._ets_active and
                          self._conf_active and
                          abs(detection_delay) <= 3)

        # ── Entry timing score ──────────────────────────────────
        score = 50

        if optimal_window:
            score = 85
        elif early_opportunity:
            score = 70   # detectó antes que conf — oportunidad
        elif late_detection:
            score = 30   # conf llegó sin ETS previo
        elif detection_delay > 10:
            score = 20   # demasiado tarde

        if ets_score >= 65:   score += 10
        if conf_score >= 65:  score += 10
        score = min(score, 100)

        # ── Timing grade ────────────────────────────────────────
        if score >= 80:   grade = "OPTIMAL"
        elif score >= 60: grade = "ACCEPTABLE"
        elif score >= 40: grade = "LATE"
        else:             grade = "MISSED"

        return TimingResult(
            entry_timing_score = score,
            detection_delay    = detection_delay,
            late_detection     = late_detection,
            early_opportunity  = early_opportunity,
            optimal_window     = optimal_window,
            timing_grade       = grade,
        )