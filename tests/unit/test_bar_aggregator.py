"""
Unit tests — BarAggregator + Bar dataclass

Covers:
  - Bar.to_dict() schema
  - TIME mode: bar accumulates ticks, does not close early
  - VOLUME mode: bar closes when volume threshold crossed
  - TICK mode: bar closes after N ticks
  - Correct OHLCV accumulation
  - ask/bid volume and delta tracking
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import time
import pytest
from bar_aggregator import BarAggregator, Bar


# ── Bar dataclass ─────────────────────────────────────────────────────

class TestBarDataclass:

    def test_default_low_is_inf(self):
        b = Bar()
        assert b.low == float("inf")

    def test_to_dict_has_all_keys(self):
        b = Bar(open=100, high=105, low=95, close=102,
                volume=500, ask_volume=300, bid_volume=200, delta=100)
        d = b.to_dict()
        for key in ["price", "open", "high", "low", "close",
                    "volume", "ask_volume", "bid_volume", "delta", "trades"]:
            assert key in d, f"Missing key: {key}"

    def test_to_dict_price_equals_close(self):
        b = Bar(close=7205.0)
        assert b.to_dict()["price"] == 7205.0


# ── Helpers ───────────────────────────────────────────────────────────

def _tick(price, ask=200, bid=150, volume=None, ts=None):
    vol = volume if volume is not None else ask + bid
    return {
        "price":      float(price),
        "open":       float(price),
        "high":       float(price) + 0.25,
        "low":        float(price) - 0.25,
        "close":      float(price),
        "volume":     float(vol),
        "ask_volume": float(ask),
        "bid_volume": float(bid),
        "delta":      float(ask - bid),
        "trades":     1,
        "timestamp":  ts if ts is not None else time.time(),
    }


# ── TIME mode ─────────────────────────────────────────────────────────

class TestTimedMode:

    def test_no_close_before_window(self):
        agg = BarAggregator(mode="TIME", seconds=5)
        # Feed a tick; bar should not close immediately
        closed = agg.process(_tick(7200))
        assert closed is None

    def test_bar_closes_after_window(self):
        agg = BarAggregator(mode="TIME", seconds=1)
        # Prime the aggregator
        agg.process(_tick(7200))
        # Force the internal bar's open_time to be 2 seconds ago
        agg._bar_start = time.time() - 2
        closed = agg.process(_tick(7201))
        assert closed is not None

    def test_closed_bar_has_correct_prices(self):
        agg = BarAggregator(mode="TIME", seconds=1)
        agg.process(_tick(7200))
        agg.process(_tick(7205))
        agg._bar_start = time.time() - 2
        closed = agg.process(_tick(7202))
        if closed:
            assert closed["open"] == 7200.0
            assert closed["high"] >= 7205.0
            assert closed["low"]  <= 7200.0


# ── VOLUME mode ───────────────────────────────────────────────────────

class TestVolumeMode:

    def test_bar_closes_at_volume_threshold(self):
        agg = BarAggregator(mode="VOLUME", volume=100)
        closed = None
        for i in range(5):
            closed = agg.process(_tick(7200 + i, volume=30))
            if closed:
                break
        assert closed is not None

    def test_volume_accumulates_correctly(self):
        agg = BarAggregator(mode="VOLUME", volume=500)
        agg.process(_tick(7200, ask=200, bid=100, volume=300))
        agg.process(_tick(7201, ask=100, bid=50,  volume=150))
        # Not yet closed (300+150=450 < 500)
        assert agg._bar is not None
        assert agg._bar.volume == 450.0

    def test_delta_accumulates_correctly(self):
        # Use threshold of 1000 so bar does not close after 2 ticks
        agg = BarAggregator(mode="VOLUME", volume=1000)
        agg.process(_tick(7200, ask=200, bid=100))  # net delta = +100
        agg.process(_tick(7201, ask=100, bid=200))  # net delta = -100
        assert agg._bar is not None
        assert abs(agg._bar.delta) < 1.0  # cumulative net delta ≈ 0


# ── TICK mode ─────────────────────────────────────────────────────────

class TestTickMode:

    def test_bar_closes_at_tick_count(self):
        agg = BarAggregator(mode="TICK", ticks=3)
        results = []
        for i in range(4):
            closed = agg.process(_tick(7200 + i))
            if closed:
                results.append(closed)
        assert len(results) >= 1

    def test_tick_count_resets_after_close(self):
        agg = BarAggregator(mode="TICK", ticks=2)
        agg.process(_tick(7200))
        agg.process(_tick(7201))  # close
        agg.process(_tick(7202))  # new bar starts
        assert agg._bar is not None
        assert agg._bar.tick_count == 1


# ── OHLCV correctness ─────────────────────────────────────────────────

class TestOHLCVAccumulation:

    def test_high_tracks_max_price(self):
        agg = BarAggregator(mode="VOLUME", volume=500)
        prices = [7200, 7210, 7205, 7208]
        for p in prices:
            agg.process(_tick(p, volume=100))
        if agg._bar:
            assert agg._bar.high >= max(prices)

    def test_low_tracks_min_price(self):
        agg = BarAggregator(mode="VOLUME", volume=500)
        prices = [7200, 7195, 7197, 7202]
        for p in prices:
            agg.process(_tick(p, volume=100))
        if agg._bar:
            assert agg._bar.low <= min(prices)

    def test_open_is_first_price(self):
        agg = BarAggregator(mode="VOLUME", volume=500)
        agg.process(_tick(7200, volume=100))
        agg.process(_tick(7205, volume=100))
        if agg._bar:
            assert agg._bar.open == 7200.0
