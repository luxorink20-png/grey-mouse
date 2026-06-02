"""
Unit tests — validator.py helper functions + Validator class

Covers:
  - adjust_gamma: inside walls, near wall, outside
  - adjust_expansion: no buffer, weak expansion
  - adjust_liquidity: BULLISH and BEARISH paths
  - check_trap: insufficient buffer, trapped condition
  - PriceBuffer: push, last, recent_highs, recent_lows, has_enough
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from validator import (
    adjust_gamma, adjust_expansion, adjust_liquidity,
    check_trap, PriceBuffer, PriceBar
)


# ── adjust_gamma ──────────────────────────────────────────────────────

class TestAdjustGamma:

    def test_inside_gamma_walls_returns_15(self):
        penalty, msg = adjust_gamma(price=7150.0, call_wall=7200.0, put_wall=7100.0)
        assert penalty == 15
        assert "dentro" in msg.lower() or "GEX" in msg

    def test_near_call_wall_returns_5(self):
        # Price ABOVE call_wall but within tick*4 = 1.0 pt — outside walls, near call
        penalty, _ = adjust_gamma(price=7200.5, call_wall=7200.0, put_wall=6800.0,
                                   tick=0.25)
        assert penalty == 5

    def test_near_put_wall_returns_5(self):
        # Price BELOW put_wall but within tick*4 — outside walls, near put
        penalty, _ = adjust_gamma(price=6999.5, call_wall=7200.0, put_wall=7000.0,
                                   tick=0.25)
        assert penalty == 5

    def test_free_field_returns_0(self):
        # Price well above call_wall — free field (no gamma walls relevant)
        penalty, _ = adjust_gamma(price=7500.0, call_wall=7400.0, put_wall=6800.0)
        assert penalty == 0

    def test_no_levels_returns_0(self):
        penalty, _ = adjust_gamma(price=7150.0, call_wall=None, put_wall=None)
        assert penalty == 0

    def test_invalid_config_put_above_call(self):
        penalty, _ = adjust_gamma(price=7150.0, call_wall=7000.0, put_wall=7200.0)
        assert penalty == 0


# ── PriceBuffer ───────────────────────────────────────────────────────

def _make_bar(p):
    return PriceBar(price=p, high=p + 0.5, low=p - 0.5, delta=50, volume=200)


class TestPriceBuffer:

    def test_empty_buffer_has_enough_false(self):
        buf = PriceBuffer(size=10)
        assert buf.has_enough(1) is False

    def test_has_enough_after_push(self):
        buf = PriceBuffer(size=10)
        for p in [7200, 7201, 7202]:
            buf.push(_make_bar(p))
        assert buf.has_enough(3) is True
        assert buf.has_enough(4) is False

    def test_recent_prices_returns_correct_count(self):
        buf = PriceBuffer(size=10)
        for p in range(7200, 7210):
            buf.push(_make_bar(p))
        assert len(buf.recent_prices(3)) == 3
        assert len(buf.recent_prices(10)) == 10

    def test_recent_highs_and_lows(self):
        buf = PriceBuffer(size=10)
        for p in [7200, 7205, 7195]:
            buf.push(_make_bar(p))
        highs = buf.recent_highs(3)
        lows  = buf.recent_lows(3)
        assert max(highs) > min(lows)

    def test_buffer_respects_maxsize(self):
        buf = PriceBuffer(size=3)
        for p in range(7200, 7210):
            buf.push(_make_bar(p))
        # Only last 3 should be kept
        assert len(buf.last(10)) == 3

    def test_last_returns_most_recent(self):
        buf = PriceBuffer(size=10)
        for p in [7200, 7201, 7202, 7203]:
            buf.push(_make_bar(p))
        recent = buf.last(2)
        assert recent[-1].price == 7203


# ── adjust_expansion ─────────────────────────────────────────────────

class TestAdjustExpansion:

    def test_warming_up_returns_zero(self):
        buf = PriceBuffer(size=10)
        result = {"event": "INTENTO", "context": {"delta": 200, "volume": 500}}
        penalty, _ = adjust_expansion(result, buf)
        assert penalty == 0

    def test_no_penalty_when_strong_expansion(self):
        buf = PriceBuffer(size=10)
        for p in [7200, 7203, 7206]:
            buf.push(_make_bar(p))
        result = {"event": "INTENTO", "context": {"delta": 200, "volume": 500}}
        penalty, msg = adjust_expansion(result, buf)
        assert penalty == 0
        assert "OK" in msg

    def test_penalty_when_weak_delta(self):
        buf = PriceBuffer(size=10)
        for p in [7200, 7203, 7206]:
            buf.push(_make_bar(p))
        result = {"event": "INTENTO", "context": {"delta": 10, "volume": 500}}
        penalty, _ = adjust_expansion(result, buf)
        assert penalty > 0

    def test_acumulacion_event_returns_zero(self):
        buf = PriceBuffer(size=10)
        for p in [7200, 7201, 7202]:
            buf.push(_make_bar(p))
        result = {"event": "ACUMULACION", "context": {"delta": 10, "volume": 50}}
        penalty, _ = adjust_expansion(result, buf)
        assert penalty == 0


# ── adjust_liquidity ─────────────────────────────────────────────────

class TestAdjustLiquidity:

    def test_warming_up_returns_zero(self):
        buf = PriceBuffer(size=10)
        penalty, _ = adjust_liquidity(7200, "BULLISH", buf)
        assert penalty == 0

    def test_bullish_low_too_close_penalises(self):
        buf = PriceBuffer(size=10)
        # Price at 7200, recent lows near 7199 (0.75 pts < min_dist 1.0)
        for _ in range(3):
            buf.push(PriceBar(price=7200, high=7200.5, low=7199.5,
                              delta=50, volume=200))
        penalty, msg = adjust_liquidity(
            price=7200.0, bias="BULLISH", buffer=buf,
            min_ticks=4, tick=0.25
        )
        assert penalty > 0

    def test_bullish_low_far_no_penalty(self):
        buf = PriceBuffer(size=10)
        for _ in range(3):
            buf.push(PriceBar(price=7200, high=7205, low=7190,
                              delta=50, volume=200))
        penalty, _ = adjust_liquidity(
            price=7200.0, bias="BULLISH", buffer=buf,
            min_ticks=4, tick=0.25
        )
        assert penalty == 0

    def test_bearish_high_too_close_penalises(self):
        buf = PriceBuffer(size=10)
        for _ in range(3):
            buf.push(PriceBar(price=7200, high=7200.5, low=7199.5,
                              delta=-50, volume=200))
        penalty, _ = adjust_liquidity(
            price=7200.0, bias="BEARISH", buffer=buf,
            min_ticks=4, tick=0.25
        )
        assert penalty > 0

    def test_neutral_bias_returns_zero(self):
        buf = PriceBuffer(size=10)
        for _ in range(3):
            buf.push(_make_bar(7200))
        penalty, _ = adjust_liquidity(7200, "NEUTRAL", buf)
        assert penalty == 0


# ── check_trap ────────────────────────────────────────────────────────

class TestCheckTrap:

    def test_warming_up_returns_false(self):
        buf = PriceBuffer(size=10)
        result = {"event": "INTENTO", "context": {"delta": 50}}
        trapped, _, _ = check_trap(result, buf)
        assert trapped is False

    def test_no_trap_in_trending_market(self):
        buf = PriceBuffer(size=10)
        for p in [7195, 7197, 7200, 7203]:
            buf.push(_make_bar(p))
        result = {"event": "INTENTO", "context": {"delta": 200}}
        trapped, penalty, _ = check_trap(result, buf)
        assert trapped is False
        assert penalty == 0
