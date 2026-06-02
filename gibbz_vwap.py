# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — gibbz_vwap.py
#  VWAP Engine — cumulative bar-by-bar VWAP + VWAP_RECLAIM detection
# ╔══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass
from datetime    import datetime, timezone

from gibbz_or_timer import _et_offset   # shared DST util

_RTH_HOUR     = 9
_RTH_MIN      = 30
_AT_VWAP_BAND = 1.5   # ±pts for AT_VWAP classification
_RECLAIM_BARS = 2     # min consecutive ABOVE+delta>0 bars to confirm reclaim


@dataclass
class VWAPResult:
    vwap:            float   # current VWAP; 0.0 = not yet accumulated
    state:           str     # AT_VWAP | ABOVE_VWAP | BELOW_VWAP | NO_DATA
    reclaim:         bool    # active VWAP_RECLAIM event (below→above, delta>0)
    reclaim_bars:    int     # consecutive bars confirming reclaim
    rejection:       bool    = False  # active VWAP_REJECTION event (above→below, delta<0)
    rejection_bars:  int     = 0      # consecutive bars confirming rejection


class VWAPEngine:

    def __init__(self, at_band: float = _AT_VWAP_BAND,
                 reclaim_min_bars: int = _RECLAIM_BARS,
                 rth_required: bool = True):
        self._band           = at_band
        self._min_bars       = reclaim_min_bars
        self._rth_required   = rth_required
        self._cum_pv         = 0.0
        self._cum_vol        = 0.0
        self._rth_started    = not rth_required  # skip gate when not required
        self._rth_date       = None
        self._was_below      = False
        self._reclaim_bars   = 0
        self._was_above      = False
        self._rejection_bars = 0

    def update(self, bar: dict) -> VWAPResult:
        ts = bar.get("timestamp", 0)
        if ts and self._rth_required:
            self._check_rth(ts)

        price  = bar.get("price",  0.0)
        volume = bar.get("volume", 0.0)

        if not self._rth_started or not price or not volume:
            return VWAPResult(0.0, "NO_DATA", False, 0)

        self._cum_pv  += price * volume
        self._cum_vol += volume
        vwap = self._cum_pv / self._cum_vol

        diff = price - vwap
        if abs(diff) <= self._band:
            state = "AT_VWAP"
        elif diff > 0:
            state = "ABOVE_VWAP"
        else:
            state = "BELOW_VWAP"

        delta     = bar.get("delta", 0)
        reclaim   = self._update_reclaim(state, delta)
        rejection = self._update_rejection(state, delta)

        return VWAPResult(
            vwap           = round(vwap, 2),
            state          = state,
            reclaim        = reclaim,
            reclaim_bars   = self._reclaim_bars,
            rejection      = rejection,
            rejection_bars = self._rejection_bars,
        )

    # ── internals ────────────────────────────────────────────────────

    def _check_rth(self, ts: int):
        from datetime import timedelta
        dt_utc  = datetime.fromtimestamp(ts, tz=timezone.utc)
        et_off  = _et_offset(dt_utc)
        dt_et   = dt_utc + et_off
        rth_et  = dt_et.replace(hour=_RTH_HOUR, minute=_RTH_MIN,
                                 second=0, microsecond=0)
        rth_utc = rth_et - et_off
        today   = dt_et.date()

        if today != self._rth_date:
            self._cum_pv       = 0.0
            self._cum_vol      = 0.0
            self._rth_started  = False
            self._rth_date     = today
            self._was_below    = False
            self._reclaim_bars = 0

        if not self._rth_started and dt_utc >= rth_utc:
            self._rth_started = True

    def _update_reclaim(self, state: str, delta: float) -> bool:
        if state == "BELOW_VWAP":
            self._was_below    = True
            self._reclaim_bars = 0
            return False

        if state == "AT_VWAP":
            return self._reclaim_bars >= self._min_bars

        # state == "ABOVE_VWAP"
        if delta > 0 and self._was_below:
            self._reclaim_bars += 1
        else:
            self._was_below    = False
            self._reclaim_bars = 0

        return self._reclaim_bars >= self._min_bars

    def _update_rejection(self, state: str, delta: float) -> bool:
        """Symmetric rejection: was ABOVE_VWAP, now BELOW_VWAP with negative delta."""
        if state == "ABOVE_VWAP":
            self._was_above      = True
            self._rejection_bars = 0
            return False

        if state == "AT_VWAP":
            return self._rejection_bars >= self._min_bars

        # state == "BELOW_VWAP"
        if delta < 0 and self._was_above:
            self._rejection_bars += 1
        else:
            self._was_above      = False
            self._rejection_bars = 0

        return self._rejection_bars >= self._min_bars
