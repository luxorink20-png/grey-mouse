# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — gibbz_or_timer.py
#  Opening Range Timer — tracks OR period (9:30–10:30 ET)
# ╔══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass
from datetime    import datetime, timezone, timedelta

_RTH_HOUR = 9
_RTH_MIN  = 30


@dataclass
class ORTimerResult:
    phase:       str    # PRE_RTH | OR_BUILDING | OR_COMPLETE | NO_TS
    in_or:       bool
    or_complete: bool
    or_high:     float
    or_low:      float
    elapsed_min: float  # minutes since RTH open; -1.0 if pre-RTH


class ORTimer:

    def __init__(self, or_duration_min: int = 60):
        self._or_dur  = or_duration_min
        self._or_high = None
        self._or_low  = None

    def update(self, bar: dict) -> ORTimerResult:
        ts = bar.get("timestamp", 0)
        if not ts:
            return ORTimerResult("NO_TS", False, False, 0.0, 0.0, -1.0)

        dt_utc  = datetime.fromtimestamp(ts, tz=timezone.utc)
        et_off  = _et_offset(dt_utc)
        dt_et   = dt_utc + et_off
        rth_et  = dt_et.replace(hour=_RTH_HOUR, minute=_RTH_MIN,
                                 second=0, microsecond=0)
        rth_utc = rth_et - et_off

        if dt_utc < rth_utc:
            return ORTimerResult("PRE_RTH", False, False, 0.0, 0.0, -1.0)

        elapsed = (dt_utc - rth_utc).total_seconds() / 60.0
        price   = bar.get("price", 0.0)

        if price and elapsed <= self._or_dur:
            self._or_high = max(self._or_high, price) if self._or_high else price
            self._or_low  = min(self._or_low,  price) if self._or_low  else price

        if elapsed <= self._or_dur:
            return ORTimerResult(
                "OR_BUILDING", True, False,
                self._or_high or 0.0,
                self._or_low  or 0.0,
                round(elapsed, 1),
            )
        return ORTimerResult(
            "OR_COMPLETE", False, True,
            self._or_high or 0.0,
            self._or_low  or 0.0,
            round(elapsed, 1),
        )


def _et_offset(dt_utc: datetime) -> timedelta:
    # DST begins 2nd Sunday March 02:00 EST = 07:00 UTC
    # DST ends   1st Sunday Nov    02:00 EDT = 06:00 UTC
    y      = dt_utc.year
    mar1   = datetime(y,  3, 1, tzinfo=timezone.utc)
    sun2   = (6 - mar1.weekday()) % 7 + 7
    dst_on = mar1 + timedelta(days=sun2, hours=7)

    nov1    = datetime(y, 11, 1, tzinfo=timezone.utc)
    sun1    = (6 - nov1.weekday()) % 7
    dst_off = nov1 + timedelta(days=sun1, hours=6)

    return timedelta(hours=-4) if dst_on <= dt_utc < dst_off else timedelta(hours=-5)
