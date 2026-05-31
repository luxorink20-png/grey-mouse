# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — gibbz_poc_magnet.py
#  POC Magnet Detector — price far from POC signals mean-reversion pull
# ╔══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass

_MIN_DIST   = 15.0   # pts from POC to activate magnet signal
_REACH_BAND =  3.0   # pts from POC to consider "reached"


@dataclass
class POCMagnetResult:
    signal: str     # POC_MAGNET_LONG | POC_MAGNET_SHORT | POC_REACHED | NO_SIGNAL
    dist:   float   # signed distance (price - POC); negative = below POC


class POCMagnetDetector:

    def __init__(self, poc: float,
                 min_dist:   float = _MIN_DIST,
                 reach_band: float = _REACH_BAND):
        self._poc        = poc
        self._min_dist   = min_dist
        self._reach_band = reach_band
        self._state      = "NO_SIGNAL"

    def update(self, price: float) -> POCMagnetResult:
        dist     = price - self._poc      # positive = above POC
        abs_dist = abs(dist)

        if self._state == "NO_SIGNAL":
            if dist >= self._min_dist:     self._state = "POC_MAGNET_SHORT"
            elif dist <= -self._min_dist:  self._state = "POC_MAGNET_LONG"

        elif self._state == "POC_MAGNET_LONG":
            if abs_dist <= self._reach_band:   self._state = "POC_REACHED"
            elif dist >= self._min_dist:       self._state = "POC_MAGNET_SHORT"

        elif self._state == "POC_MAGNET_SHORT":
            if abs_dist <= self._reach_band:   self._state = "POC_REACHED"
            elif dist <= -self._min_dist:      self._state = "POC_MAGNET_LONG"

        elif self._state == "POC_REACHED":
            # Reactivates if price moves far from POC again
            if dist >= self._min_dist:     self._state = "POC_MAGNET_SHORT"
            elif dist <= -self._min_dist:  self._state = "POC_MAGNET_LONG"

        return POCMagnetResult(signal=self._state, dist=round(dist, 2))
