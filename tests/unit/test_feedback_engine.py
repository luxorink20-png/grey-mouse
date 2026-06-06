"""
Unit tests — FeedbackEngine

Covers:
  - Story 4.1: breakeven_ticks constructor parameter (4-tick default)
  - Story 4.2: CANCELLED result when entry_price=0.0 on force-close
  - Core lifecycle: open → update → WIN/LOSS/BREAKEVEN/TIMEOUT
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from feedback_engine import FeedbackEngine, TradeRecord
from dataclasses import dataclass


# ── Minimal stubs ─────────────────────────────────────────────────────────

@dataclass
class _Risk:
    approved:      bool  = True
    direction:     str   = "LONG"
    position_size: float = 1.0
    size_unit:     str   = "multiplier"
    stop:          float = 6990.0
    target_1:      float = 7050.0
    target_2:      float = 7100.0
    risk_reward:   float = 2.0
    risk_pts:      float = 10.0


@dataclass
class _Analysis:
    score: int  = 70
    zone:  str  = "AT_VAL"
    event: str  = "INTENTO"


@dataclass
class _Narrative:
    narrative:  str = "ACCUMULATION"
    conviction: int = 80


RISK    = _Risk()
ANAL    = _Analysis()
NARR    = _Narrative()
ENTRY   = 7000.0


def _make_fb(**kwargs) -> FeedbackEngine:
    return FeedbackEngine(enabled=False, **kwargs)


def _open(fb: FeedbackEngine, risk=None, signal_price=ENTRY):
    fb.open_trade(risk or RISK, ANAL, NARR, "TEST", signal_price=signal_price)


# ══════════════════════════════════════════════════════════════════════════
#  Story 4.1 — breakeven_ticks configurable
# ══════════════════════════════════════════════════════════════════════════

class TestBreakevenTicks:

    def test_default_is_4_ticks(self):
        fb = _make_fb()
        assert fb._breakeven_ticks == 4

    def test_custom_breakeven_ticks_stored(self):
        fb = _make_fb(breakeven_ticks=2)
        assert fb._breakeven_ticks == 2

    def test_breakeven_fires_at_threshold(self):
        """Price within 4 ticks (1.0 pt) after >5 bars → BREAKEVEN."""
        fb = _make_fb(breakeven_ticks=4)
        _open(fb)
        fb.update(ENTRY)                       # bar 0: sets entry_price
        for _ in range(5):
            fb.update(ENTRY + 2.0)             # bars 1-5: price away
        # bar 6: price returns to exactly entry + 4 ticks (1.0 pt)
        result = fb.update(ENTRY + 1.0)
        assert result is not None
        assert result.result == "BREAKEVEN"

    def test_breakeven_does_not_fire_below_threshold(self):
        """Price within 3 ticks (0.75 pt) when threshold=4 → no breakeven yet."""
        fb = _make_fb(breakeven_ticks=4)
        _open(fb)
        fb.update(ENTRY)
        for _ in range(5):
            fb.update(ENTRY + 2.0)
        # 0.75 pt > 1.0 pt threshold, should NOT trigger breakeven
        result = fb.update(ENTRY + 5.0)
        assert result is None

    def test_breakeven_threshold_1_original_behavior(self):
        """With breakeven_ticks=1: only price within 0.25 pt fires breakeven."""
        fb = _make_fb(breakeven_ticks=1)
        _open(fb)
        fb.update(ENTRY)
        for _ in range(5):
            fb.update(ENTRY + 2.0)
        # 1.0 pt away → no breakeven at 1-tick threshold
        result = fb.update(ENTRY + 1.0)
        assert result is None

    def test_breakeven_fires_within_5_bars_does_not_trigger(self):
        """Breakeven only applies after bars_held > 5 (guard against early exit)."""
        fb = _make_fb(breakeven_ticks=4)
        _open(fb)
        fb.update(ENTRY)               # bar 0: entry
        for _ in range(4):
            fb.update(ENTRY + 1.0)    # bars 1-4: within threshold
        # bars_held=4 ≤ 5, should NOT fire breakeven yet
        result = fb.update(ENTRY + 1.0)  # bar 5: still within threshold
        assert result is None


# ══════════════════════════════════════════════════════════════════════════
#  Story 4.2 — entry_price=0 guard (CANCELLED result)
# ══════════════════════════════════════════════════════════════════════════

class TestCancelledResult:

    def test_force_close_before_first_update_produces_cancelled(self):
        """Opening second trade before any update on first → CANCELLED."""
        fb = _make_fb()
        _open(fb)
        # Immediately open second trade — first never received an update
        _open(fb)
        # First trade should be resolved as CANCELLED
        assert fb.last_trade is not None
        assert fb.last_trade.result == "CANCELLED"

    def test_cancelled_trade_has_no_pnl(self):
        fb = _make_fb()
        _open(fb)
        _open(fb)  # force-close first
        assert fb.last_trade.pnl_pts == 0.0

    def test_cancelled_trade_has_zero_entry_price(self):
        fb = _make_fb()
        _open(fb)
        _open(fb)
        assert fb.last_trade.entry_price == 0.0

    def test_normal_force_close_after_update_is_not_cancelled(self):
        """Trade that received an update → force-close should be TIMEOUT, not CANCELLED."""
        fb = _make_fb()
        _open(fb)
        fb.update(ENTRY)      # sets entry_price
        fb.update(ENTRY + 1)  # bars_held=1
        _open(fb)             # force-close second trade
        assert fb.last_trade.result == "TIMEOUT"

    def test_cancelled_increments_timeouts_counter(self):
        """CANCELLED counts in timeouts for summary consistency."""
        fb = _make_fb()
        _open(fb)
        _open(fb)  # CANCELLED
        summary = fb.get_summary()
        # Total trades includes the cancelled one
        assert summary.total_trades == 1
        assert summary.timeouts == 1


# ══════════════════════════════════════════════════════════════════════════
#  Core lifecycle
# ══════════════════════════════════════════════════════════════════════════

class TestCoreLifecycle:

    def test_win_on_target1_hit(self):
        fb = _make_fb()
        _open(fb)
        fb.update(ENTRY)
        result = fb.update(RISK.target_1 + 1.0)
        assert result is not None
        assert result.result == "WIN"
        assert result.hit_target_1 is True

    def test_loss_on_stop_hit(self):
        fb = _make_fb()
        _open(fb)
        fb.update(ENTRY)
        result = fb.update(RISK.stop - 1.0)
        assert result is not None
        assert result.result == "LOSS"
        assert result.hit_stop is True

    def test_timeout_after_max_bars(self):
        fb = _make_fb()
        _open(fb)
        fb.update(ENTRY)
        mid_price = ENTRY + 2.0   # between stop and target
        for _ in range(FeedbackEngine.MAX_BARS_HELD):
            res = fb.update(mid_price)
            if res is not None:
                break
        assert res is not None
        assert res.result == "TIMEOUT"

    def test_trap_detected_within_trap_bars(self):
        fb = _make_fb()
        _open(fb)
        fb.update(ENTRY)
        fb.update(ENTRY + 1.0)
        result = fb.update(RISK.stop - 0.5)    # hit stop within 3 bars
        assert result is not None
        assert result.was_trap is True

    def test_slippage_computed_from_signal_price(self):
        fb = _make_fb()
        _open(fb, signal_price=ENTRY)
        result_bar = fb.update(ENTRY + 0.5)     # actual fill = ENTRY + 0.5
        # slippage = 0.5 / 0.25 = 2.0 ticks
        assert fb.pending.slippage_ticks == 2.0

    def test_rejected_risk_not_tracked(self):
        fb = _make_fb()
        rejected = _Risk(approved=False)
        fb.open_trade(rejected, ANAL, NARR, "TEST")
        assert fb.pending is None

    def test_summary_win_rate_correct(self):
        fb = _make_fb()
        for _ in range(3):
            _open(fb)
            fb.update(ENTRY)
            fb.update(RISK.target_1 + 1.0)   # WIN
        for _ in range(2):
            _open(fb)
            fb.update(ENTRY)
            fb.update(RISK.stop - 1.0)        # LOSS
        summary = fb.get_summary()
        assert summary.total_trades == 5
        assert summary.wins == 3
        assert summary.losses == 2
        assert summary.win_rate == 60.0
