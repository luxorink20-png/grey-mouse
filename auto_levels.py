# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — auto_levels.py
#  Volume Profile Auto-Builder from Live Feed
#
#  Builds VAH / POC / VAL from incoming UDP bar data so that
#  levels.json is updated automatically at session start — no
#  manual entry required.
#
#  Algorithm (standard market profile):
#    1. Bin prices to tick size (0.25 for ES/MES)
#    2. POC = price bin with highest cumulative volume
#    3. Expand value area from POC toward the higher-volume side
#       until 70% of total session volume is covered
#    4. Upper boundary = VAH, lower boundary = VAL
#
#  Deduplication (bar_ts):
#    GibbzBridge sends 3-10 UDP packets per ATAS bar (same bar, same
#    price, same timestamp). Without deduplication, 100 raw ticks only
#    cover ~25 unique bars → value area as narrow as 1-2 pts.
#    add_tick(bar_ts=...) skips packets for bars already seen, so
#    min_ticks counts unique ATAS bars, not raw UDP packets.
#
#  Invariant: VAL ≤ POC ≤ VAH always holds in the returned dict.
#
#  Usage (engine.py):
#    vp = VolumeProfileBuilder(tick=0.25, min_ticks=100)
#    for tick in ticks:
#        vp.add_tick(tick["price"], tick["volume"], bar_ts=tick["timestamp"])
#    if vp.is_ready():
#        result = vp.calculate()   # {"vah": ..., "poc": ..., "val": ...}
# ╚══════════════════════════════════════════════════════════════════╝

from __future__ import annotations
from typing import Optional
from log_config import get_logger

_log = get_logger(__name__)


class VolumeProfileBuilder:
    """
    Accumulates price-volume observations and computes a Volume Profile.

    Deduplication: pass bar_ts (ATAS bar timestamp) to add_tick() so
    that multiple UDP packets for the same bar only count once.
    Each unique bar_ts = one unique ATAS bar = one data point.

    is_ready() → True once min_ticks unique bars AND min_levels unique
    price buckets have been accumulated.  Call calculate() at that point.

    calculate() returns None when called on empty / insufficient data
    so it is safe to call unconditionally.
    """

    DEFAULT_TICK           = 0.25
    DEFAULT_VALUE_AREA_PCT = 0.70
    DEFAULT_MIN_TICKS      = 100   # unique ATAS bars before triggering
    DEFAULT_MIN_LEVELS     = 20    # unique price buckets minimum

    def __init__(
        self,
        tick:           float = DEFAULT_TICK,
        value_area_pct: float = DEFAULT_VALUE_AREA_PCT,
        min_ticks:      int   = DEFAULT_MIN_TICKS,
        min_levels:     int   = DEFAULT_MIN_LEVELS,
    ) -> None:
        self._tick           = tick
        self._value_area_pct = value_area_pct
        self._min_ticks      = min_ticks
        self._min_levels     = min_levels
        self._vol_by_price:  dict[float, float] = {}
        self._count:         int   = 0      # unique bars counted
        self._last_bar_ts:   float = -1.0   # dedup sentinel

    # ── Public API ────────────────────────────────────────────────

    def add_tick(self, price: float, volume: float,
                 bar_ts: float = 0.0) -> None:
        """
        Add one bar's price-volume to the profile.

        bar_ts: ATAS bar timestamp (tick["timestamp"]).  When non-zero,
        packets sharing the same timestamp are treated as the same bar
        and skipped after the first.  Pass 0.0 to disable deduplication.

        Ignores invalid prices (≤0) and negative volume.
        """
        if price <= 0 or volume < 0:
            return
        # Deduplicate: one data point per unique ATAS bar
        if bar_ts != 0.0:
            if bar_ts == self._last_bar_ts:
                return
            self._last_bar_ts = bar_ts

        bucket = round(round(price / self._tick) * self._tick, 4)
        self._vol_by_price[bucket] = self._vol_by_price.get(bucket, 0.0) + volume
        self._count += 1

    def is_ready(self) -> bool:
        """True when enough unique bars and price levels have been accumulated."""
        return (
            self._count >= self._min_ticks
            and len(self._vol_by_price) >= self._min_levels
        )

    def calculate(self) -> Optional[dict]:
        """
        Compute VAH, POC, VAL.

        Returns dict with keys "vah", "poc", "val" (all float).
        Returns None if the profile has no data.

        Guarantee: val <= poc <= vah.
        """
        if not self._vol_by_price:
            return None

        sorted_prices = sorted(self._vol_by_price)
        poc = max(self._vol_by_price, key=self._vol_by_price.__getitem__)

        lo_idx = sorted_prices.index(poc)
        hi_idx = lo_idx

        total_vol = sum(self._vol_by_price.values())
        covered   = self._vol_by_price[poc]
        target    = total_vol * self._value_area_pct

        # Expand value area toward whichever adjacent bin has more volume
        while covered < target:
            above_vol = (
                self._vol_by_price[sorted_prices[hi_idx + 1]]
                if hi_idx + 1 < len(sorted_prices)
                else 0.0
            )
            below_vol = (
                self._vol_by_price[sorted_prices[lo_idx - 1]]
                if lo_idx > 0
                else 0.0
            )

            if above_vol == 0.0 and below_vol == 0.0:
                break

            if above_vol >= below_vol:
                hi_idx += 1
                covered += above_vol
            else:
                lo_idx -= 1
                covered += below_vol

        vah = sorted_prices[hi_idx]
        val = sorted_prices[lo_idx]
        coverage_pct = covered / total_vol * 100 if total_vol > 0 else 0.0

        _log.debug(
            "VolumeProfile computed | bars=%d levels=%d "
            "VAL=%.2f POC=%.2f VAH=%.2f coverage=%.1f%%",
            self._count, len(sorted_prices), val, poc, vah, coverage_pct,
        )

        return {"vah": vah, "poc": poc, "val": val}

    # ── Diagnostics ───────────────────────────────────────────────

    @property
    def tick_count(self) -> int:
        """Number of unique ATAS bars counted (after deduplication)."""
        return self._count

    @property
    def unique_levels(self) -> int:
        return len(self._vol_by_price)
