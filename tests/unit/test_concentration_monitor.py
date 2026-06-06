"""
Unit tests — ConcentrationMonitor

Covers:
  - Story 5.1: per-setup PF tracking (VA80, FA, CONFLUENCE)
  - Story 5.2: degradation alert fires when rolling PF < floor after min_trades
  - Story 5.3: cooldown prevents duplicate alerts
  - Story 5.4: high-concentration label for VA80/FA setups
  - Story 5.5: get_summary() returns per-setup stats dict
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from concentration_monitor import ConcentrationMonitor, DegradationAlert


def _make_mon(**kwargs) -> ConcentrationMonitor:
    defaults = dict(min_trades=3, pf_floor=1.0, window=10, cooldown_trades=5)
    defaults.update(kwargs)
    return ConcentrationMonitor(**defaults)


def _win(mon, setup="VA80_LONG", pnl=2.5):
    mon.set_pending(setup)
    return mon.register_close(pnl_pts=pnl, win=True)


def _loss(mon, setup="VA80_LONG", pnl=-2.5):
    mon.set_pending(setup)
    return mon.register_close(pnl_pts=pnl, win=False)


# ══════════════════════════════════════════════════════════════════════════
#  Story 5.1 — per-setup tracking
# ══════════════════════════════════════════════════════════════════════════

class TestPerSetupTracking:

    def test_separate_stats_per_setup(self):
        mon = _make_mon()
        _win(mon, "VA80_LONG", 3.0)
        _win(mon, "VA80_LONG", 3.0)
        _loss(mon, "FA_SHORT",  -2.0)
        _loss(mon, "FA_SHORT",  -2.0)

        summary = mon.get_summary()
        assert "VA80_LONG" in summary
        assert "FA_SHORT"  in summary
        assert summary["VA80_LONG"]["trades"] == 2
        assert summary["FA_SHORT"]["trades"]  == 2

    def test_confluence_default_when_setup_is_none(self):
        mon = _make_mon()
        mon.set_pending("")   # empty → CONFLUENCE
        mon.register_close(pnl_pts=1.0, win=True)
        assert "CONFLUENCE" in mon.get_summary()

    def test_setup_type_preserved_across_pending_reset(self):
        mon = _make_mon()
        _win(mon, "VA80_LONG")
        _win(mon, "VA80_LONG")
        _loss(mon, "CONFLUENCE")
        s = mon.get_summary()
        assert s["VA80_LONG"]["trades"] == 2
        assert s["CONFLUENCE"]["trades"] == 1


# ══════════════════════════════════════════════════════════════════════════
#  Story 5.2 — degradation alert
# ══════════════════════════════════════════════════════════════════════════

class TestDegradationAlert:

    def test_no_alert_below_min_trades(self):
        mon = _make_mon(min_trades=5)
        for _ in range(4):
            alert = _loss(mon, "VA80_LONG")
        assert alert is None

    def test_alert_fires_when_pf_below_floor(self):
        mon = _make_mon(min_trades=3, pf_floor=1.0)
        # Alert fires on the trade that satisfies min_trades AND PF < floor.
        # With 2 losses → total=2 < min_trades=3 → no alert.
        for _ in range(2):
            assert _loss(mon, "VA80_LONG") is None
        # 3rd loss → total=3 ≥ min_trades → alert fires
        alert = _loss(mon, "VA80_LONG")
        assert alert is not None
        assert isinstance(alert, DegradationAlert)
        assert alert.setup_type == "VA80_LONG"
        assert alert.rolling_pf < 1.0

    def test_no_alert_when_pf_above_floor(self):
        mon = _make_mon(min_trades=3, pf_floor=1.0)
        _win(mon, "VA80_LONG", 10.0)
        _win(mon, "VA80_LONG", 10.0)
        _loss(mon, "VA80_LONG", -1.0)
        # PF = 20 / 1 = 20 — way above floor
        alert = _loss(mon, "VA80_LONG", -1.0)
        assert alert is None

    def test_alert_message_contains_setup_type(self):
        mon = _make_mon(min_trades=3)
        for _ in range(4):
            _loss(mon, "FA_SHORT")
        assert "FA_SHORT" in mon.get_summary()

    def test_alert_reports_correct_rolling_pf(self):
        mon = _make_mon(min_trades=3, window=10)
        # 1 win (3.0) then losses (-2.0 each).
        # Alert fires when total ≥ 3 AND PF < 1.0.
        # After 1 win + 2 losses: total=3, PF = 3.0/4.0 = 0.75 < 1.0 → alert.
        _win(mon, "VA80_LONG", 3.0)
        _loss(mon, "VA80_LONG", -2.0)
        alert = _loss(mon, "VA80_LONG", -2.0)   # trade #3 → alert fires
        assert alert is not None
        assert alert.rolling_pf < 1.0

    def test_consecutive_losses_reported(self):
        mon = _make_mon(min_trades=3)
        # 1 win then losses. Alert fires at trade 4 (1 win + 3 losses → total=4 ≥ 3,
        # PF = 5.0/6.0 = 0.83 < 1.0). Cooldown then suppresses until cooldown_trades=5 pass.
        _win(mon, "VA80_LONG", 5.0)
        _loss(mon, "VA80_LONG", -2.0)
        _loss(mon, "VA80_LONG", -2.0)
        alert = _loss(mon, "VA80_LONG", -2.0)   # trade #4 → alert fires
        assert alert is not None
        assert alert.consecutive_losses >= 3


# ══════════════════════════════════════════════════════════════════════════
#  Story 5.3 — cooldown between alerts
# ══════════════════════════════════════════════════════════════════════════

class TestAlertCooldown:

    def test_second_alert_suppressed_within_cooldown(self):
        mon = _make_mon(min_trades=3, cooldown_trades=5)
        # First alert fires at trade #3 (min_trades met, all losses)
        _loss(mon, "VA80_LONG")
        _loss(mon, "VA80_LONG")
        first_alert = _loss(mon, "VA80_LONG")   # trade #3 → alert
        assert first_alert is not None

        # Immediately more losses — within cooldown_trades=5
        for _ in range(4):
            alert = _loss(mon, "VA80_LONG")
            assert alert is None, "Alert should be suppressed within cooldown"

    def test_alert_fires_again_after_cooldown_expires(self):
        mon = _make_mon(min_trades=3, cooldown_trades=5)
        # Trigger first alert at trade #3
        _loss(mon, "VA80_LONG")
        _loss(mon, "VA80_LONG")
        first = _loss(mon, "VA80_LONG")  # trade #3 → first alert
        assert first is not None

        # Trades #4 and #5 are within cooldown → suppressed
        for _ in range(4):
            _loss(mon, "VA80_LONG")

        # Trade #8 = 3 + 5 → cooldown_trades=5 exhausted → alert fires again
        alert = _loss(mon, "VA80_LONG")
        assert alert is not None


# ══════════════════════════════════════════════════════════════════════════
#  Story 5.4 — high-concentration label
# ══════════════════════════════════════════════════════════════════════════

class TestHighConcentrationLabel:

    def test_va80_is_high_concentration(self):
        # Alert fires on trade #3 (min_trades=3, all losses)
        mon = _make_mon(min_trades=3)
        _loss(mon, "VA80_LONG")
        _loss(mon, "VA80_LONG")
        alert = _loss(mon, "VA80_LONG")
        assert alert is not None
        assert "CRITICAL" in alert.message

    def test_fa_is_high_concentration(self):
        mon = _make_mon(min_trades=3)
        _loss(mon, "FA_SHORT")
        _loss(mon, "FA_SHORT")
        alert = _loss(mon, "FA_SHORT")
        assert alert is not None
        assert "CRITICAL" in alert.message

    def test_confluence_is_not_high_concentration(self):
        mon = _make_mon(min_trades=3)
        _loss(mon, "CONFLUENCE")
        _loss(mon, "CONFLUENCE")
        alert = _loss(mon, "CONFLUENCE")
        assert alert is not None
        assert "WARNING" in alert.message
        assert "CRITICAL" not in alert.message


# ══════════════════════════════════════════════════════════════════════════
#  Story 5.5 — summary dict
# ══════════════════════════════════════════════════════════════════════════

class TestSummary:

    def test_summary_empty_at_start(self):
        mon = _make_mon()
        assert mon.get_summary() == {}

    def test_summary_pf_infinite_on_no_losses(self):
        mon = _make_mon()
        _win(mon, "VA80_LONG", 5.0)
        _win(mon, "VA80_LONG", 5.0)
        s = mon.get_summary()
        import math
        assert math.isinf(s["VA80_LONG"]["pf_all"])

    def test_summary_high_conc_flag_set_for_va80(self):
        mon = _make_mon()
        _win(mon, "VA80_SHORT")
        s = mon.get_summary()
        assert s["VA80_SHORT"]["high_conc"] is True

    def test_summary_high_conc_flag_false_for_confluence(self):
        mon = _make_mon()
        _win(mon, "CONFLUENCE")
        s = mon.get_summary()
        assert s["CONFLUENCE"]["high_conc"] is False

    def test_summary_includes_rolling_pf(self):
        mon = _make_mon(window=5)
        _win(mon, "VA80_LONG", 4.0)
        _loss(mon, "VA80_LONG", -2.0)
        s = mon.get_summary()
        # PF = 4.0 / 2.0 = 2.0
        assert s["VA80_LONG"]["pf_rolling"] == pytest.approx(2.0, abs=0.01)
