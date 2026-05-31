# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — gibbz_bounce_detector.py
#  VAL/VAH Bounce Detector — zone + absorption + delta_slope + approach
# ╔══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass

_APPROACH_BARS = 3


@dataclass
class BounceResult:
    signal:       str    # BOUNCE_VAL_CONFIRMED | BOUNCE_VAH_CONFIRMED | NONE
    approach_val: int
    approach_vah: int
    absorption:   bool
    delta_slope:  str


class BounceDetector:

    def __init__(self, approach_bars: int = _APPROACH_BARS):
        self._req_bars     = approach_bars
        self._approach_val = 0
        self._approach_vah = 0
        self._prev_zone    = ""

    def update(self, context, result: dict, etil_r) -> BounceResult:
        zone        = context.zone
        price_move  = result["context"].get("price_move", 0.0)
        absorption  = result["context"].get("absorption", False)
        delta_slope = getattr(etil_r, "delta_slope", "FLAT")
        signal = self._check(zone, price_move, absorption, delta_slope)
        self._prev_zone = zone
        return BounceResult(signal, self._approach_val, self._approach_vah,
                            absorption, delta_slope)

    def _check(self, zone: str, price_move: float,
               absorption: bool, delta_slope: str) -> str:
        signal = "NONE"

        # ── VAL bounce ──────────────────────────────────────────────────
        if zone == "AT_VAL":
            if (self._approach_val >= self._req_bars
                    and absorption and delta_slope == "RISING"):
                signal = "BOUNCE_VAL_CONFIRMED"
            # stay at level — preserve counter
        elif zone in ("BELOW_VAL", "BELOW_IBL"):
            self._approach_val = 0          # broke through → reset
        else:
            if self._prev_zone == "AT_VAL":
                self._approach_val = 0      # bounced back up → reset
            elif price_move < -0.25:
                self._approach_val += 1     # falling toward VAL
            else:
                self._approach_val = 0      # not approaching → reset

        # ── VAH bounce ──────────────────────────────────────────────────
        if zone == "AT_VAH":
            if (self._approach_vah >= self._req_bars
                    and absorption and delta_slope == "FALLING"):
                if signal == "NONE":
                    signal = "BOUNCE_VAH_CONFIRMED"
            # stay at level — preserve counter
        elif zone in ("ABOVE_VAH", "AT_IBH", "ABOVE_IBH"):
            self._approach_vah = 0          # broke through → reset
        else:
            if self._prev_zone == "AT_VAH":
                self._approach_vah = 0      # rejected back down → reset
            elif price_move > 0.25:
                self._approach_vah += 1     # rising toward VAH
            else:
                self._approach_vah = 0      # not approaching → reset

        return signal
