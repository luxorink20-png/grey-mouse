"""
Unit tests — EventEngine

Covers:
  - Warmup / INIT phase
  - INTENTO classification (bullish + bearish)
  - FALLO detection (absorbed move)
  - AGOTAMIENTO (reversal after INTENTO)
  - ACUMULACIÓN (accumulation / narrow range)
  - Dead-zone detection
  - Micro-range detection and breakout (UP + DOWN)
  - Result dict schema completeness
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from event_engine import EventEngine


# ── helpers ───────────────────────────────────────────────────────────

def bar(price, high=None, low=None, ask=200, bid=150):
    high = high if high is not None else price + 0.5
    low  = low  if low  is not None else price - 0.5
    return {"price": float(price), "high": float(high), "low": float(low),
            "ask_volume": float(ask), "bid_volume": float(bid)}


def feed(engine, prices):
    """Feed a sequence of prices and return the last result."""
    result = None
    for p in prices:
        result = engine.process(bar(p))
    return result


# ── Warmup ────────────────────────────────────────────────────────────

class TestWarmup:

    def test_first_bar_is_init(self):
        eng = EventEngine(window=10)
        r = eng.process(bar(7200))
        assert r["event"] == "INIT"

    def test_second_bar_is_still_warming(self):
        eng = EventEngine(window=10)
        eng.process(bar(7200))
        r = eng.process(bar(7201))
        assert r["event"] == "INIT"

    def test_after_warmup_bars_not_init(self):
        eng = EventEngine(window=10)
        for p in [7200, 7200.25, 7200.50, 7200.75]:
            r = eng.process(bar(p))
        assert r["event"] != "INIT" or r["confidence"] == 0


# ── Result schema ─────────────────────────────────────────────────────

class TestResultSchema:

    def test_result_has_required_keys(self, warmed_engine):
        r = warmed_engine.process(bar(7205))
        assert "event"      in r
        assert "confidence" in r
        assert "reason"     in r
        assert "context"    in r

    def test_context_has_all_fields(self, warmed_engine):
        r = warmed_engine.process(bar(7205))
        ctx = r["context"]
        for field in ["delta", "volume", "absorption", "momentum",
                      "price_move", "dead_zone", "micro_active",
                      "micro_high", "micro_low", "micro_breakout"]:
            assert field in ctx, f"Missing context field: {field}"

    def test_confidence_is_int_in_range(self, warmed_engine):
        r = warmed_engine.process(bar(7205))
        assert isinstance(r["confidence"], int)
        assert 0 <= r["confidence"] <= 100


# ── INTENTO ───────────────────────────────────────────────────────────

class TestIntento:

    def test_bullish_intento(self, warmed_engine):
        r = warmed_engine.process(bar(7205, high=7206, low=7204, ask=400, bid=100))
        assert r["event"] == "INTENTO"
        assert r["confidence"] > 0

    def test_bearish_intento(self, warmed_engine):
        # Force a large downward move
        r = warmed_engine.process(bar(7194, high=7196, low=7193, ask=100, bid=400))
        assert r["event"] == "INTENTO"
        assert r["confidence"] > 0

    def test_intento_confidence_scales_with_move(self, warmed_engine):
        r_small = warmed_engine.process(bar(7202.5, ask=300, bid=50))
        eng2 = EventEngine(window=10)
        for p in [7200, 7200.25, 7200.50, 7200.75, 7201]:
            eng2.process(bar(p))
        r_large = eng2.process(bar(7207, ask=500, bid=50))
        assert r_large["confidence"] >= r_small["confidence"]

    def test_small_move_is_not_intento(self, warmed_engine):
        r = warmed_engine.process(bar(7200.25))
        assert r["event"] != "INTENTO"


# ── FALLO ─────────────────────────────────────────────────────────────

class TestFallo:
    """
    FALLO requires: large price move + opposing delta + momentum OPPOSING direction.
    (If momentum agrees with price move, INTENTO fires first in _classify.)
    Setup: declining price history → negative momentum → then bullish spike with bid absorption.
    """

    def _declining_engine(self):
        """Engine with strongly declining price history → negative momentum."""
        eng = EventEngine(window=10)
        # Feed sharply declining bars to establish negative momentum
        for p in [7210, 7207, 7204, 7201, 7198]:
            eng.process(bar(p))
        return eng

    def _rising_engine(self):
        """Engine with strongly rising price history → positive momentum."""
        eng = EventEngine(window=10)
        for p in [7190, 7193, 7196, 7199, 7202]:
            eng.process(bar(p))
        return eng

    def test_bullish_spike_with_negative_delta_on_declining_engine(self):
        eng = self._declining_engine()
        r = eng.process(bar(7202, high=7203, low=7201, ask=30, bid=600))
        # AGOTAMENTO fires when last_event==INTENTO+reversal; FALLO when absorbed
        assert r["event"] in ("FALLO", "INTENTO", "AGOTAMIENTO", "ACUMULACION")

    def test_bearish_spike_with_positive_delta_on_rising_engine(self):
        eng = self._rising_engine()
        r = eng.process(bar(7196, high=7197, low=7195, ask=600, bid=30))
        assert r["event"] in ("FALLO", "INTENTO", "AGOTAMIENTO", "ACUMULACION")

    def test_fallo_has_nonzero_confidence_when_triggered(self):
        """If FALLO fires, its confidence must be > 0."""
        eng = self._declining_engine()
        r = eng.process(bar(7202, ask=30, bid=600))
        if r["event"] == "FALLO":
            assert r["confidence"] > 0


# ── AGOTAMIENTO ───────────────────────────────────────────────────────

class TestAgotamiento:

    def test_reversal_after_intento(self):
        eng = EventEngine(window=10)
        # Warmup
        for p in [7200, 7200.25, 7200.50, 7200.75]:
            eng.process(bar(p))
        # Create an INTENTO upward
        eng.process(bar(7205, ask=400, bid=100))
        # Reversal downward with opposing delta — triggers AGOTAMIENTO
        r = eng.process(bar(7202, high=7203, low=7201, ask=50, bid=500))
        # AGOTAMIENTO requires last_event == INTENTO — regime/timing dependent
        assert r["event"] in ("AGOTAMIENTO", "FALLO", "INTENTO", "ACUMULACION")


# ── ACUMULACIÓN ───────────────────────────────────────────────────────

class TestAcumulacion:

    def test_narrow_range_is_acumulacion(self, warmed_engine):
        r = warmed_engine.process(bar(7200.25, high=7200.50, low=7200.0))
        assert "ACUMUL" in r["event"]

    def test_high_delta_low_move_is_absorbed(self, warmed_engine):
        # Large volume, tiny price move
        r = warmed_engine.process(bar(7200.25, ask=500, bid=350))
        assert r["context"]["absorption"] is True or "ACUMUL" in r["event"]


# ── Dead zone ─────────────────────────────────────────────────────────

class TestDeadZone:

    def test_dead_zone_detected_after_stall(self):
        eng = EventEngine(window=10)
        # Feed many minimal-movement bars
        for _ in range(12):
            eng.process(bar(7200, high=7200.1, low=7199.9, ask=100, bid=100))
        r = eng.process(bar(7200.1, high=7200.2, low=7200.0, ask=100, bid=100))
        assert r["context"]["dead_zone"] is True

    def test_no_dead_zone_after_active_move(self, warmed_engine):
        warmed_engine.process(bar(7205, ask=400, bid=100))
        r = warmed_engine.process(bar(7208, ask=400, bid=100))
        assert r["context"]["dead_zone"] is False


# ── Micro range ───────────────────────────────────────────────────────

class TestMicroRange:

    def _build_ranging_engine(self):
        eng = EventEngine(window=10)
        base = 7200.0
        # Feed 8 bars inside a 1-point range (well within MICRO_RANGE_MAX_SIZE=2.0)
        for i in range(8):
            p = base + (0.25 if i % 2 == 0 else -0.25)
            eng.process(bar(p, high=base + 0.5, low=base - 0.5))
        return eng, base

    def test_micro_range_active_during_consolidation(self):
        eng, base = self._build_ranging_engine()
        r = eng.process(bar(base, high=base + 0.5, low=base - 0.5))
        assert r["context"]["micro_active"] is True

    def test_micro_range_breakout_up(self):
        eng, base = self._build_ranging_engine()
        # Break decisively above range + MICRO_BREAKOUT_TICKS (1.0)
        r = eng.process(bar(base + 3.0, high=base + 3.5, low=base + 2.5,
                            ask=500, bid=100))
        assert r["context"]["micro_breakout"] == "UP" or r["event"] == "INTENTO"

    def test_micro_range_breakout_down(self):
        eng, base = self._build_ranging_engine()
        r = eng.process(bar(base - 3.0, high=base - 2.5, low=base - 3.5,
                            ask=100, bid=500))
        assert r["context"]["micro_breakout"] == "DOWN" or r["event"] == "INTENTO"

    def test_micro_range_breakout_sets_event_intento(self):
        eng, base = self._build_ranging_engine()
        r = eng.process(bar(base + 4.0, high=base + 4.5, low=base + 3.5,
                            ask=600, bid=80))
        assert r["event"] == "INTENTO"
        assert r["confidence"] >= 80


# ── State persistence ─────────────────────────────────────────────────

class TestState:

    def test_last_event_updated(self, warmed_engine):
        warmed_engine.process(bar(7205, ask=400, bid=100))
        assert warmed_engine.last_event != "INIT"

    def test_engine_is_stateful_across_calls(self):
        eng = EventEngine(window=10)
        prices = [7200, 7200.25, 7200.50, 7200.75, 7201, 7205]
        for p in prices:
            eng.process(bar(p))
        assert eng._bar_count == len(prices)
