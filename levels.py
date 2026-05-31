# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — levels.py
#  Institutional Levels Context Engine
#  VAH / VAL / POC + Proximity + Reaction Zones
# ╚══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass
from typing import Optional


@dataclass
class Level:
    name:  str
    price: float
    kind:  str


@dataclass
class LevelContext:
    price:            float
    zone:             str
    nearest_level:    str
    nearest_price:    float
    nearest_distance: float
    is_near:          bool
    near_levels:      list
    high_prob_zone:   bool
    reaction_bias:    str
    summary:          str

    def __str__(self) -> str:
        near_str = " | ".join(self.near_levels) if self.near_levels else "none"
        return (
            f"Zone: {self.zone:<16} "
            f"Nearest: {self.nearest_level} ({self.nearest_distance:+.2f}pts) "
            f"Near: [{near_str}] "
            f"HPZ: {'YES' if self.high_prob_zone else 'no'} "
            f"Bias: {self.reaction_bias}"
        )


class InstitutionalLevels:

    def __init__(self,
                 vah:              float,
                 poc:              float,
                 val:              float,
                 ibh:              float = 0.0,
                 ibl:              float = 0.0,
                 vwap:             float = 0.0,
                 proximity_points: float = 2.0,
                 hpz_points:       float = 1.0):

        if not (val < poc < vah):
            raise ValueError(
                f"Invalid levels: VAL({val}) < POC({poc}) < VAH({vah}) required."
            )

        self._vah       = vah
        self._poc       = poc
        self._val       = val
        self._ibh       = ibh
        self._ibl       = ibl
        self._vwap      = vwap
        self._proximity = proximity_points
        self._hpz       = hpz_points
        self._custom:   list[Level] = []
        self._levels    = self._build_levels()

    def get_context(self, price: float) -> LevelContext:
        zone         = self._classify_zone(price)
        nearest      = self._find_nearest(price)
        nearest_dist = price - nearest.price

        near_levels = [
            f"NEAR_{lvl.name}"
            for lvl in self._levels
            if abs(price - lvl.price) <= self._proximity
        ]

        at_vah_edge = abs(price - self._vah) <= self._hpz
        at_val_edge = abs(price - self._val) <= self._hpz
        at_poc      = abs(price - self._poc) <= self._hpz
        high_prob   = at_vah_edge or at_val_edge or at_poc

        bias    = self._derive_bias(price, zone, nearest, nearest_dist)
        summary = self._build_summary(
            price, zone, nearest, nearest_dist, near_levels, high_prob
        )

        return LevelContext(
            price            = price,
            zone             = zone,
            nearest_level    = nearest.name,
            nearest_price    = nearest.price,
            nearest_distance = round(nearest_dist, 2),
            is_near          = len(near_levels) > 0,
            near_levels      = near_levels,
            high_prob_zone   = high_prob,
            reaction_bias    = bias,
            summary          = summary,
        )

    def update_levels(self,
                      vah: Optional[float] = None,
                      poc: Optional[float] = None,
                      val: Optional[float] = None) -> None:
        if vah: self._vah = vah
        if poc: self._poc = poc
        if val: self._val = val
        self._levels = self._build_levels()

    def add_custom_level(self, name: str, price: float) -> None:
        self._custom.append(Level(name=name, price=price, kind="CUSTOM"))
        self._levels = self._build_levels()

    def set_vwap(self, price: float) -> None:
        self._vwap   = price
        self._levels = self._build_levels()

    @property
    def vah(self) -> float: return self._vah

    @property
    def val(self) -> float: return self._val

    @property
    def poc(self) -> float: return self._poc

    @property
    def ibh(self) -> float: return self._ibh

    @property
    def ibl(self) -> float: return self._ibl

    @property
    def vwap(self) -> float: return self._vwap

    def _classify_zone(self, price: float) -> str:
        if abs(price - self._poc) <= self._proximity:
            return "AT_POC"
        if abs(price - self._vah) <= self._proximity:
            return "AT_VAH"
        if abs(price - self._val) <= self._proximity:
            return "AT_VAL"
        if self._ibh > 0 and abs(price - self._ibh) <= self._proximity:
            return "AT_IBH"
        if self._ibl > 0 and abs(price - self._ibl) <= self._proximity:
            return "AT_IBL"
        if self._ibh > 0 and price > self._ibh:
            return "ABOVE_IBH"
        if price > self._vah:
            return "ABOVE_VAH"
        if self._ibl > 0 and price < self._ibl:
            return "BELOW_IBL"
        if price < self._val:
            return "BELOW_VAL"
        if self._vwap > 0:
            if abs(price - self._vwap) <= 1.5:
                return "AT_VWAP"
            return "ABOVE_VWAP" if price > self._vwap else "BELOW_VWAP"
        return "IN_VALUE_AREA"

    def _find_nearest(self, price: float) -> Level:
        return min(self._levels, key=lambda lvl: abs(price - lvl.price))

    def _derive_bias(self, price: float, zone: str,
                     nearest: Level, dist: float) -> str:
        if zone == "ABOVE_IBH":
            return "BULLISH"
        if zone == "BELOW_IBL":
            return "BEARISH"
        if zone == "ABOVE_VWAP":
            return "BEARISH"
        if zone == "BELOW_VWAP":
            return "BULLISH"
        if zone == "AT_VWAP":
            return "NEUTRAL"
        if zone in ("ABOVE_VAH", "AT_VAH"):
            return "BEARISH"
        if zone in ("BELOW_VAL", "AT_VAL"):
            return "BULLISH"
        if zone in ("AT_IBH", "AT_IBL", "AT_POC"):
            return "NEUTRAL"
        if zone == "IN_VALUE_AREA":
            mid = (self._vah + self._val) / 2
            return "BULLISH" if price < mid else "BEARISH"
        return "NEUTRAL"

    def _build_summary(self, price: float, zone: str, nearest: Level,
                       dist: float, near_levels: list, hpz: bool) -> str:
        near_str = f" | {' '.join(near_levels)}" if near_levels else ""
        hpz_str  = " | HPZ" if hpz else ""
        return (
            f"{zone}{near_str}{hpz_str} "
            f"[{nearest.name}: {dist:+.2f}pts]"
        )

    def _build_levels(self) -> list[Level]:
        base = [
            Level("VAH", self._vah, "VAH"),
            Level("POC", self._poc, "POC"),
            Level("VAL", self._val, "VAL"),
        ]
        if self._ibh > 0:
            base.append(Level("IBH", self._ibh, "IBH"))
        if self._ibl > 0:
            base.append(Level("IBL", self._ibl, "IBL"))
        if self._vwap > 0:
            base.append(Level("VWAP", self._vwap, "VWAP"))
        return base + self._custom


def create_levels(vah: float, poc: float, val: float,
                  proximity: float = 2.0,
                  ibh: float = 0.0, ibl: float = 0.0,
                  vwap: float = 0.0) -> InstitutionalLevels:
    return InstitutionalLevels(
        vah=vah, poc=poc, val=val,
        ibh=ibh, ibl=ibl, vwap=vwap,
        proximity_points=proximity,
        hpz_points=1.0
    )