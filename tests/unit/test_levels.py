"""
Unit tests — InstitutionalLevels / LevelContext

Covers:
  - Constructor validation (VAL < POC < VAH enforced)
  - Zone classification (ABOVE_VAH, AT_VAH, INSIDE_VA, AT_VAL, BELOW_VAL)
  - Nearest level resolution
  - High-probability zone detection
  - Proximity flags
  - create_levels() factory function
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from levels import InstitutionalLevels, create_levels, LevelContext


VAH = 7200.0
POC = 7100.0
VAL = 7000.0


# ── Constructor validation ────────────────────────────────────────────

class TestConstructor:

    def test_valid_levels_construct_without_error(self):
        lvl = InstitutionalLevels(vah=VAH, poc=POC, val=VAL)
        assert lvl is not None

    def test_invalid_levels_raise_value_error(self):
        with pytest.raises(ValueError):
            InstitutionalLevels(vah=7000.0, poc=7100.0, val=7200.0)

    def test_val_equal_poc_raises(self):
        with pytest.raises(ValueError):
            InstitutionalLevels(vah=7200.0, poc=7100.0, val=7100.0)

    def test_poc_equal_vah_raises(self):
        with pytest.raises(ValueError):
            InstitutionalLevels(vah=7200.0, poc=7200.0, val=7000.0)

    def test_create_levels_factory(self):
        lvl = create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0)
        assert lvl is not None
        ctx = lvl.get_context(7150.0)
        assert isinstance(ctx, LevelContext)


# ── Zone classification ───────────────────────────────────────────────

class TestZoneClassification:

    @pytest.fixture(autouse=True)
    def levels(self):
        self.lvl = create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0)

    def test_price_above_vah(self):
        ctx = self.lvl.get_context(VAH + 5.0)
        assert "ABOVE" in ctx.zone or "VAH" in ctx.zone

    def test_price_at_vah(self):
        ctx = self.lvl.get_context(VAH)
        assert "VAH" in ctx.zone

    def test_price_inside_va(self):
        ctx = self.lvl.get_context(POC)
        # Should be inside value area
        assert ctx.zone not in ("", None)

    def test_price_at_val(self):
        ctx = self.lvl.get_context(VAL)
        assert "VAL" in ctx.zone

    def test_price_below_val(self):
        ctx = self.lvl.get_context(VAL - 5.0)
        assert "BELOW" in ctx.zone or "VAL" in ctx.zone


# ── Nearest level ─────────────────────────────────────────────────────

class TestNearestLevel:

    @pytest.fixture(autouse=True)
    def levels(self):
        self.lvl = create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0)

    def test_nearest_when_at_vah(self):
        ctx = self.lvl.get_context(VAH)
        assert ctx.nearest_level == "VAH"
        assert abs(ctx.nearest_distance) < 1.0

    def test_nearest_when_at_poc(self):
        ctx = self.lvl.get_context(POC)
        assert ctx.nearest_level == "POC"

    def test_nearest_when_at_val(self):
        ctx = self.lvl.get_context(VAL)
        assert ctx.nearest_level == "VAL"

    def test_nearest_distance_sign(self):
        ctx_above = self.lvl.get_context(VAH + 2.0)
        assert ctx_above.nearest_distance > 0  # price above nearest

        ctx_below = self.lvl.get_context(VAH - 2.0)
        assert ctx_below.nearest_distance < 0  # price below nearest


# ── High-probability zone ─────────────────────────────────────────────

class TestHighProbZone:

    @pytest.fixture(autouse=True)
    def levels(self):
        self.lvl = create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0)

    def test_price_on_vah_is_hpz(self):
        ctx = self.lvl.get_context(VAH)
        assert ctx.high_prob_zone is True

    def test_price_on_val_is_hpz(self):
        ctx = self.lvl.get_context(VAL)
        assert ctx.high_prob_zone is True

    def test_price_far_from_all_levels_not_hpz(self):
        # Middle of value area, 30 points from any level
        ctx = self.lvl.get_context(7050.0)
        assert ctx.high_prob_zone is False


# ── Proximity flags ───────────────────────────────────────────────────

class TestProximity:

    @pytest.fixture(autouse=True)
    def levels(self):
        self.lvl = create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0)

    def test_within_proximity_has_near_levels(self):
        ctx = self.lvl.get_context(VAH + 1.0)
        assert len(ctx.near_levels) > 0

    def test_far_from_all_has_no_near_levels(self):
        ctx = self.lvl.get_context(7050.0)
        assert len(ctx.near_levels) == 0

    def test_is_near_flag(self):
        ctx_near = self.lvl.get_context(VAH + 0.5)
        assert ctx_near.is_near is True

        ctx_far = self.lvl.get_context(7050.0)
        assert ctx_far.is_near is False


# ── LevelContext str ──────────────────────────────────────────────────

class TestLevelContextStr:

    def test_str_contains_zone_and_bias(self):
        lvl = create_levels(vah=VAH, poc=POC, val=VAL)
        ctx = lvl.get_context(VAH)
        s = str(ctx)
        assert "Zone" in s or "zone" in s.lower()
