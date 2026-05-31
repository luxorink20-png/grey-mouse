# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — gibbz_va_rule80.py
#  80% Rule Value Area Detector — Market Profile return-inside rule
# ╔══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass

_CONFIRM_BARS = 2   # consecutive bars inside VA to confirm the rule


@dataclass
class VA80Result:
    signal:      str    # VA_RULE80_LONG | VA_RULE80_SHORT | NONE
    state:       str    # diagnostic
    target:      float  # VAH for LONG, VAL for SHORT, 0.0 otherwise
    return_bars: int    # consecutive bars back inside VA after exit


class VA80Detector:

    def __init__(self, vah: float, val: float, open_price: float = 0.0):
        self._vah         = vah
        self._val         = val
        self._return_bars = 0
        if open_price > 0:
            # Rule only valid when session opens inside the Value Area
            self._state = "WATCHING" if val <= open_price <= vah else "DISABLED"
        else:
            self._state = "IDLE"    # determine from first bar

    def update(self, price: float) -> VA80Result:
        inside = self._val <= price <= self._vah
        above  = price > self._vah
        below  = price < self._val

        if self._state == "IDLE":
            self._state = "WATCHING" if inside else "DISABLED"

        elif self._state == "WATCHING":
            if above:   self._state = "OUTSIDE_ABOVE"; self._return_bars = 0
            elif below: self._state = "OUTSIDE_BELOW"; self._return_bars = 0

        elif self._state == "OUTSIDE_ABOVE":
            if inside:  self._state = "RETURN_ABOVE";  self._return_bars = 1

        elif self._state == "OUTSIDE_BELOW":
            if inside:  self._state = "RETURN_BELOW";  self._return_bars = 1

        elif self._state == "RETURN_ABOVE":
            if above:   self._state = "OUTSIDE_ABOVE"; self._return_bars = 0
            elif below: self._state = "OUTSIDE_BELOW"; self._return_bars = 0
            else:                                       # still inside
                self._return_bars += 1
                if self._return_bars >= _CONFIRM_BARS:
                    self._state = "CONFIRMED_SHORT"

        elif self._state == "RETURN_BELOW":
            if below:   self._state = "OUTSIDE_BELOW"; self._return_bars = 0
            elif above: self._state = "OUTSIDE_ABOVE"; self._return_bars = 0
            else:                                       # still inside
                self._return_bars += 1
                if self._return_bars >= _CONFIRM_BARS:
                    self._state = "CONFIRMED_LONG"

        elif self._state == "CONFIRMED_SHORT":
            if above:   self._state = "OUTSIDE_ABOVE"; self._return_bars = 0  # invalidated
            elif below: self._state = "WATCHING";      self._return_bars = 0  # target hit

        elif self._state == "CONFIRMED_LONG":
            if below:   self._state = "OUTSIDE_BELOW"; self._return_bars = 0  # invalidated
            elif above: self._state = "WATCHING";      self._return_bars = 0  # target hit

        return self._result()

    def _result(self) -> VA80Result:
        if self._state == "CONFIRMED_LONG":
            return VA80Result("VA_RULE80_LONG",  self._state, self._vah, self._return_bars)
        if self._state == "CONFIRMED_SHORT":
            return VA80Result("VA_RULE80_SHORT", self._state, self._val, self._return_bars)
        return VA80Result("NONE", self._state, 0.0, self._return_bars)
