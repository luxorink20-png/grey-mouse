# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — gibbz_failed_auction.py
#  Failed Auction / Liquidity Sweep Detector
#  Breakout above VAH or below VAL + return inside VA ≤5 bars + delta reversal
# ╔══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass

_MAX_OUTSIDE_BARS = 5


@dataclass
class FAResult:
    signal:       str    # FAILED_AUCTION_LONG | FAILED_AUCTION_SHORT | NONE
    state:        str
    bars_outside: int


class FADetector:

    def __init__(self, vah: float, val: float):
        self._vah          = vah
        self._val          = val
        self._state        = "WATCHING"
        self._bars_outside = 0

    def update(self, price: float, delta: float) -> FAResult:
        inside = self._val <= price <= self._vah
        above  = price > self._vah
        below  = price < self._val

        if self._state == "WATCHING":
            if above:   self._state = "OUTSIDE_VAH"; self._bars_outside = 1
            elif below: self._state = "OUTSIDE_VAL"; self._bars_outside = 1

        elif self._state == "OUTSIDE_VAH":
            if inside:
                # Delta must revert: breakout was buying pressure, return needs selling
                self._state = "CONFIRMED_SHORT" if delta < 0 else "WATCHING"
                if self._state == "WATCHING": self._bars_outside = 0
            elif below:
                self._state = "OUTSIDE_VAL"; self._bars_outside = 1
            else:   # still above VAH
                self._bars_outside += 1
                if self._bars_outside > _MAX_OUTSIDE_BARS:
                    self._state = "WATCHING"; self._bars_outside = 0

        elif self._state == "OUTSIDE_VAL":
            if inside:
                # Delta must revert: breakout was selling pressure, return needs buying
                self._state = "CONFIRMED_LONG" if delta > 0 else "WATCHING"
                if self._state == "WATCHING": self._bars_outside = 0
            elif above:
                self._state = "OUTSIDE_VAH"; self._bars_outside = 1
            else:   # still below VAL
                self._bars_outside += 1
                if self._bars_outside > _MAX_OUTSIDE_BARS:
                    self._state = "WATCHING"; self._bars_outside = 0

        elif self._state == "CONFIRMED_SHORT":
            if above:   self._state = "OUTSIDE_VAH"; self._bars_outside = 1
            elif below: self._state = "OUTSIDE_VAL"; self._bars_outside = 1

        elif self._state == "CONFIRMED_LONG":
            if below:   self._state = "OUTSIDE_VAL"; self._bars_outside = 1
            elif above: self._state = "OUTSIDE_VAH"; self._bars_outside = 1

        return self._result()

    def _result(self) -> FAResult:
        if self._state == "CONFIRMED_LONG":
            return FAResult("FAILED_AUCTION_LONG",  self._state, self._bars_outside)
        if self._state == "CONFIRMED_SHORT":
            return FAResult("FAILED_AUCTION_SHORT", self._state, self._bars_outside)
        return FAResult("NONE", self._state, self._bars_outside)
