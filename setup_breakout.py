# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC — setup_breakout.py
#  Consolidation-Breakout detector.
#
#  Logic:
#    1. Build a "consolidation zone": the last CONSOL_BARS bars must
#       fit within a range ≤ RANGE_MAX_PTS points.
#    2. When the current bar's close breaks ABOVE the consolidation
#       high (LONG) or BELOW the consolidation low (SHORT):
#       a. Volume must be ≥ VOL_SPIKE_MIN × average volume of the
#          consolidation bars.
#       b. Delta must confirm the breakout direction.
#    3. Stop is placed at the opposite end of the consolidation range
#       (plus a small buffer).
#    4. Target is RANGE_EXTENSION × consolidation range beyond the
#       breakout level (capped by run_backtest at 20 pts).
#
#  Integration: instantiate once per session in full_backtest.run_session().
#  Call update(bar) every bar after the bar dict is built.
# ╚══════════════════════════════════════════════════════════════════╝

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

_CONSOL_BARS    = 8      # bars that must fit within the consolidation range (tightened)
_RANGE_MAX_PTS  = 6.0    # maximum range (pts) to qualify as consolidation (tightened)
_VOL_SPIKE_MIN  = 2.5    # volume ratio: current / average of consolidation bars (tightened)
_DELTA_CONFIRM  = 200    # minimum |delta| for breakout direction confirmation (tightened)
_STOP_BUFFER    = 1.5    # pts inside the consolidation for the stop
_RANGE_EXTENSION = 2.0   # target = consolidation_range × this factor (raised for better R:R)


@dataclass
class BreakoutResult:
    signal:              str    # BREAKOUT_LONG | BREAKOUT_SHORT | NONE
    state:               str    # diagnostic
    consolidation_range: float  # pts
    volume_ratio:        float  # current_vol / avg_consolidation_vol
    stop_pts:            float  # suggested stop distance in points
    target_pts:          float  # suggested target distance in points


class BreakoutDetector:
    """
    Stateless-per-bar but stateful across bars within a session.
    Reset by creating a new instance at session start.
    """

    def __init__(
        self,
        consol_bars:    int   = _CONSOL_BARS,
        range_max:      float = _RANGE_MAX_PTS,
        vol_spike_min:  float = _VOL_SPIKE_MIN,
        delta_confirm:  int   = _DELTA_CONFIRM,
        stop_buffer:    float = _STOP_BUFFER,
        range_extension: float = _RANGE_EXTENSION,
    ) -> None:
        self._consol_bars    = consol_bars
        self._range_max      = range_max
        self._vol_spike_min  = vol_spike_min
        self._delta_confirm  = delta_confirm
        self._stop_buffer    = stop_buffer
        self._range_extension = range_extension
        # deque stores (high, low, volume, delta) per bar
        self._history: deque[tuple[float, float, float, float]] = deque(
            maxlen=consol_bars + 1
        )
        self._cooldown = 0

    def update(self, bar: dict) -> BreakoutResult:
        price  = float(bar.get("price", 0.0))
        high   = float(bar.get("high",   price))
        low    = float(bar.get("low",    price))
        vol    = float(bar.get("volume", 0.0))
        delta  = float(bar.get("delta",  0.0))

        self._history.append((high, low, vol, delta))

        _NO = BreakoutResult("NONE", "NONE", 0.0, 0.0, 0.0, 0.0)

        if self._cooldown > 0:
            self._cooldown -= 1
            return BreakoutResult("NONE", "COOLDOWN", 0.0, 0.0, 0.0, 0.0)

        if len(self._history) < self._consol_bars + 1:
            return BreakoutResult("NONE", "INSUFFICIENT_DATA", 0.0, 0.0, 0.0, 0.0)

        hist         = list(self._history)
        consol_bars  = hist[:-1]   # last _consol_bars bars
        current      = hist[-1]

        c_high = max(b[0] for b in consol_bars)
        c_low  = min(b[1] for b in consol_bars)
        c_range = c_high - c_low

        # Only fire if these bars were in consolidation
        if c_range > self._range_max:
            return BreakoutResult("NONE", "RANGE_TOO_WIDE", c_range, 0.0, 0.0, 0.0)

        avg_vol = sum(b[2] for b in consol_bars) / max(len(consol_bars), 1)
        vol_ratio = current[2] / avg_vol if avg_vol > 0 else 0.0
        cur_delta = current[3]
        cur_high  = current[0]
        cur_low   = current[1]

        if vol_ratio < self._vol_spike_min:
            return BreakoutResult("NONE", "VOLUME_INSUFFICIENT", c_range, vol_ratio, 0.0, 0.0)

        # Breakout LONG: current bar closes above consolidation high
        if cur_high > c_high and cur_delta >= self._delta_confirm:
            stop_pts  = round(c_range + self._stop_buffer, 2)
            tgt_pts   = round(c_range * self._range_extension, 2)
            if stop_pts > 0 and tgt_pts > 0:
                self._cooldown = 12
                return BreakoutResult(
                    "BREAKOUT_LONG", "CONSOL_BREAKOUT",
                    c_range, vol_ratio, stop_pts, tgt_pts,
                )

        # Breakout SHORT: current bar closes below consolidation low
        if cur_low < c_low and cur_delta <= -self._delta_confirm:
            stop_pts  = round(c_range + self._stop_buffer, 2)
            tgt_pts   = round(c_range * self._range_extension, 2)
            if stop_pts > 0 and tgt_pts > 0:
                self._cooldown = 12
                return BreakoutResult(
                    "BREAKOUT_SHORT", "CONSOL_BREAKOUT",
                    c_range, vol_ratio, stop_pts, tgt_pts,
                )

        return BreakoutResult("NONE", "NO_BREAKOUT", c_range, vol_ratio, 0.0, 0.0)
