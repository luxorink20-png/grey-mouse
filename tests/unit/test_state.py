"""
Unit tests — GibbzState
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from state import GibbzState


class TestGibbzState:

    def test_initial_state(self):
        s = GibbzState()
        assert s.price == 0.0
        assert s.is_running is False
        assert s.last_event == "INIT"
        assert s.level_context is None

    def test_start_sets_running(self):
        s = GibbzState()
        s.start()
        assert s.is_running is True

    def test_stop_clears_running(self):
        s = GibbzState()
        s.start()
        s.stop()
        assert s.is_running is False

    def test_update_price(self):
        s = GibbzState()
        s.update_price(7205.25)
        assert s.price == 7205.25

    def test_update_price_accepts_int(self):
        s = GibbzState()
        s.update_price(7200)
        assert s.price == 7200

    def test_multiple_price_updates(self):
        s = GibbzState()
        for p in [7200, 7201, 7202]:
            s.update_price(p)
        assert s.price == 7202.0

    def test_independent_instances(self):
        s1 = GibbzState()
        s2 = GibbzState()
        s1.start()
        s1.update_price(7999)
        assert s2.is_running is False
        assert s2.price == 0.0
