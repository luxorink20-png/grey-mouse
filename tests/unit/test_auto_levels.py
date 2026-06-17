"""
Unit tests for auto_levels.VolumeProfileBuilder.

Covers:
  - Empty / insufficient data guards
  - Bar-timestamp deduplication (one data point per unique ATAS bar)
  - POC detection (max-volume bin)
  - Value area expansion algorithm (70%)
  - Tick rounding (0.25 increments)
  - VAL < POC < VAH invariant
  - Volume accumulation for repeated prices
  - Edge cases: single level, two levels, skewed distributions
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from auto_levels import VolumeProfileBuilder


# ── Helpers ──────────────────────────────────────────────────────────

def _build(ticks: list[tuple[float, float]], tick=0.25,
           min_ticks=1, min_levels=1) -> VolumeProfileBuilder:
    """Construct a VolumeProfileBuilder and feed it the given (price, vol) pairs."""
    vp = VolumeProfileBuilder(tick=tick, min_ticks=min_ticks, min_levels=min_levels)
    for price, vol in ticks:
        vp.add_tick(price, vol)
    return vp


# ── Empty / insufficient data ─────────────────────────────────────────

class TestGuards:

    def test_empty_calculate_returns_none(self):
        vp = VolumeProfileBuilder()
        assert vp.calculate() is None

    def test_is_ready_false_below_min_ticks(self):
        vp = VolumeProfileBuilder(min_ticks=100, min_levels=1)
        for _ in range(99):
            vp.add_tick(7000.0, 100.0)
        assert not vp.is_ready()

    def test_is_ready_false_below_min_levels(self):
        vp = VolumeProfileBuilder(min_ticks=1, min_levels=5)
        # Add 10 ticks but all at the same price (1 unique level)
        for _ in range(10):
            vp.add_tick(7000.0, 100.0)
        assert not vp.is_ready()

    def test_is_ready_true_when_both_thresholds_met(self):
        vp = VolumeProfileBuilder(min_ticks=3, min_levels=3)
        vp.add_tick(7000.0, 100.0)
        vp.add_tick(7000.25, 200.0)
        vp.add_tick(7000.50, 150.0)
        assert vp.is_ready()

    def test_invalid_price_ignored(self):
        vp = _build([(0.0, 100.0), (-1.0, 50.0)])
        assert vp.tick_count == 0
        assert vp.calculate() is None

    def test_negative_volume_ignored(self):
        vp = VolumeProfileBuilder()
        vp.add_tick(7000.0, -50.0)
        assert vp.tick_count == 0


# ── Bar-timestamp deduplication ───────────────────────────────────────

class TestDeduplication:
    """
    GibbzBridge sends 3-10 UDP packets per ATAS bar (same price, same
    timestamp).  Without deduplication, 100 raw packets = ~25 unique bars
    → value area as narrow as 1-2 pts.  These tests verify that bar_ts
    causes duplicates to be dropped.
    """

    def test_duplicate_bar_ts_counted_once(self):
        vp = VolumeProfileBuilder(min_ticks=1, min_levels=1)
        ts = 1718000000.0
        vp.add_tick(7010.0, 500.0, bar_ts=ts)
        vp.add_tick(7010.0, 500.0, bar_ts=ts)   # same ts → skip
        vp.add_tick(7010.0, 500.0, bar_ts=ts)   # same ts → skip
        assert vp.tick_count == 1, "Three packets for same bar_ts must count as 1"

    def test_different_bar_ts_both_counted(self):
        vp = VolumeProfileBuilder(min_ticks=1, min_levels=1)
        vp.add_tick(7010.0, 500.0, bar_ts=1718000000.0)
        vp.add_tick(7010.25, 300.0, bar_ts=1718000005.0)   # next bar
        assert vp.tick_count == 2

    def test_dedup_prevents_narrow_value_area(self):
        # Simulate GibbzBridge: 4 packets per bar, 30 bars, tiny price range
        vp = VolumeProfileBuilder(tick=0.25, min_ticks=30, min_levels=3)
        base_ts = 1718000000.0
        for bar_i in range(30):
            price = round(7010.0 + (bar_i % 3) * 0.25, 2)
            ts = base_ts + bar_i * 5.0   # each bar is 5s apart
            for _ in range(4):           # 4 UDP packets per bar
                vp.add_tick(price, 500.0, bar_ts=ts)
        assert vp.tick_count == 30, (
            f"Expected 30 unique bars, got {vp.tick_count}"
        )
        assert vp.unique_levels == 3   # only 3 unique prices

    def test_without_bar_ts_all_packets_counted(self):
        # bar_ts=0.0 disables deduplication
        vp = VolumeProfileBuilder(min_ticks=1, min_levels=1)
        for _ in range(5):
            vp.add_tick(7010.0, 100.0, bar_ts=0.0)
        assert vp.tick_count == 5

    def test_ts_zero_does_not_deduplicate(self):
        # Explicitly: passing bar_ts=0.0 means "no timestamp, always accept"
        vp = VolumeProfileBuilder(min_ticks=1, min_levels=1)
        vp.add_tick(7010.0, 100.0, bar_ts=0.0)
        vp.add_tick(7010.0, 100.0, bar_ts=0.0)
        assert vp.tick_count == 2

    def test_real_world_narrow_range_does_not_fire_with_dedup(self):
        # Reproduce the exact bug: 100 raw packets, ~25 unique bars, 3 price levels
        # With dedup: tick_count=25, unique_levels=3 → is_ready(min_levels=20) False
        vp = VolumeProfileBuilder(tick=0.25, min_ticks=100, min_levels=20)
        base_ts = 1718000000.0
        for bar_i in range(25):   # 25 unique bars
            price = round(7010.0 + (bar_i % 3) * 0.25, 2)
            ts = base_ts + bar_i * 5.0
            for _ in range(4):
                vp.add_tick(price, 500.0, bar_ts=ts)
        # After 100 raw packets (25 unique bars), should NOT be ready yet
        assert vp.tick_count == 25
        assert not vp.is_ready(), (
            "VP must not fire on 25 unique bars / 3 price levels — "
            "would produce a useless 0.5pt value area"
        )


# ── POC detection ─────────────────────────────────────────────────────

class TestPOC:

    def test_poc_is_highest_volume_price(self):
        vp = _build([
            (7000.0, 100.0),
            (7000.25, 500.0),   # highest
            (7000.50, 200.0),
        ])
        result = vp.calculate()
        assert result is not None
        assert result["poc"] == 7000.25

    def test_poc_with_single_level(self):
        vp = _build([(7150.0, 300.0)] * 5)
        result = vp.calculate()
        assert result is not None
        assert result["poc"] == 7150.0
        assert result["vah"] == result["poc"] == result["val"]

    def test_tick_count(self):
        vp = _build([(7000.0, 100.0)] * 10)
        assert vp.tick_count == 10

    def test_unique_levels(self):
        vp = _build([
            (7000.0, 100.0),
            (7000.25, 200.0),
            (7000.0, 50.0),   # duplicate — same bucket
        ])
        assert vp.unique_levels == 2

    def test_volume_accumulates_for_same_price(self):
        vp = _build([
            (7000.0, 100.0),
            (7000.0, 200.0),
            (7000.25, 50.0),
        ])
        result = vp.calculate()
        # 7000.0 has 300 vol vs 7000.25 has 50 vol → POC = 7000.0
        assert result["poc"] == 7000.0


# ── Tick rounding ────────────────────────────────────────────────────

class TestTickRounding:

    def test_price_rounded_to_quarter_point(self):
        # 7000.10 / 0.25 = 28000.40 → round → 28000 → 7000.0
        # 7000.11 / 0.25 = 28000.44 → round → 28000 → 7000.0
        vp = _build([(7000.10, 100.0), (7000.11, 200.0)])
        assert vp.unique_levels == 1
        result = vp.calculate()
        assert result["poc"] == 7000.0

    def test_price_halfway_rounds_to_nearest_tick(self):
        # 7000.125 → rounds to 7000.25 (python round half-to-even may vary;
        # test the determinism, not the specific tie-break direction)
        vp = _build([(7000.125, 100.0)])
        result = vp.calculate()
        assert result is not None
        assert result["poc"] in (7000.0, 7000.25)


# ── Value area algorithm ─────────────────────────────────────────────

class TestValueArea:

    def test_vah_above_poc_and_val_below_poc(self):
        # Symmetric distribution around 7000.50
        ticks = [
            (7000.0,  100.0),
            (7000.25, 200.0),
            (7000.50, 500.0),  # POC
            (7000.75, 200.0),
            (7001.0,  100.0),
        ]
        vp = _build(ticks)
        result = vp.calculate()
        assert result is not None
        assert result["val"] <= result["poc"] <= result["vah"]
        assert result["poc"] == 7000.50

    def test_val_lt_poc_lt_vah_invariant_always_holds(self):
        import random
        random.seed(0)
        ticks = [(round(7000 + random.uniform(-5, 5), 2), random.uniform(10, 500))
                 for _ in range(200)]
        vp = _build(ticks)
        result = vp.calculate()
        assert result is not None
        assert result["val"] <= result["poc"] <= result["vah"]

    def test_value_area_covers_at_least_70_percent(self):
        ticks = [
            (6998.0,  50.0),
            (6998.25, 100.0),
            (6998.50, 300.0),
            (6998.75, 500.0),  # POC
            (6999.0,  400.0),
            (6999.25, 200.0),
            (6999.50, 100.0),
            (6999.75,  50.0),
        ]
        vp = _build(ticks)
        result = vp.calculate()
        assert result is not None

        total_vol = sum(v for _, v in ticks)
        # Compute volume in value area
        in_va = sum(v for p, v in ticks if result["val"] <= p <= result["vah"])
        assert in_va / total_vol >= 0.70, (
            f"Value area covers only {in_va/total_vol:.1%} (< 70%)"
        )

    def test_skewed_distribution_expands_toward_high_volume_side(self):
        # High volume above POC → VAH should be further from POC than VAL
        ticks = [
            (6999.0, 100.0),
            (6999.25, 200.0),
            (6999.50, 500.0),  # POC
            (6999.75, 800.0),
            (7000.0,  700.0),
            (7000.25, 600.0),
        ]
        vp = _build(ticks)
        result = vp.calculate()
        assert result is not None
        distance_above = result["vah"] - result["poc"]
        distance_below = result["poc"] - result["val"]
        assert distance_above >= distance_below, (
            "Expected value area to extend further above POC "
            f"(above={distance_above}, below={distance_below})"
        )

    def test_two_level_profile(self):
        ticks = [
            (7000.0,  300.0),  # POC
            (7000.25, 100.0),
        ]
        vp = _build(ticks)
        result = vp.calculate()
        assert result is not None
        assert result["poc"] == 7000.0
        assert result["val"] <= result["poc"] <= result["vah"]
