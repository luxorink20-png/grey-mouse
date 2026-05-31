# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — gibbz_gap_fill.py
#  Gap Fill Detector — tracks open-vs-prev_close gap and fill completion
# ╔══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass

_MIN_GAP_PTS = 5.0


@dataclass
class GapFillResult:
    signal:       str    # GAP_UP_ACTIVE | GAP_DOWN_ACTIVE | GAP_FILL_COMPLETE | NO_GAP
    gap_pts:      float  # signed gap (open_price - prev_close)
    target:       float  # prev_close — the fill level
    dist_to_fill: float  # current_price - target


class GapFillDetector:

    def __init__(self, open_price: float, prev_close: float,
                 min_gap: float = _MIN_GAP_PTS):
        self._target  = prev_close
        self._gap_pts = round(open_price - prev_close, 2)

        if open_price > 0 and prev_close > 0 and abs(self._gap_pts) >= min_gap:
            self._state = "GAP_UP_ACTIVE" if self._gap_pts > 0 else "GAP_DOWN_ACTIVE"
        else:
            self._state = "NO_GAP"

    def update(self, price: float) -> GapFillResult:
        if self._state == "GAP_UP_ACTIVE" and price <= self._target:
            self._state = "GAP_FILL_COMPLETE"
        elif self._state == "GAP_DOWN_ACTIVE" and price >= self._target:
            self._state = "GAP_FILL_COMPLETE"
        return self._result(price)

    def _result(self, price: float) -> GapFillResult:
        return GapFillResult(
            signal       = self._state,
            gap_pts      = self._gap_pts,
            target       = self._target,
            dist_to_fill = round(price - self._target, 2),
        )
