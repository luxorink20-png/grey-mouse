"""Unit tests for confidence_engine.py (Wave 1 — Institutional Fusion)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import pytest
from confidence_engine import ConfidenceEngine, ConfidenceResult


def _record(eng: ConfidenceEngine, wins: int, losses: int, pnl_win=5.0, pnl_loss=-3.0):
    """Helper: register alternating outcomes (wins first, then losses)."""
    for _ in range(wins):
        eng.register_outcome(win=True,  pnl_pts=pnl_win)
    for _ in range(losses):
        eng.register_outcome(win=False, pnl_pts=pnl_loss)


class TestConfidenceEngineColdStart:

    def test_cold_start_returns_neutral(self):
        eng = ConfidenceEngine()
        r = eng.score(quality_score=75)
        # < MIN_TRADES_FOR_SCALING → neutral 0.5 multiplier
        assert r.multiplier == pytest.approx(0.75, abs=0.01)
        assert r.trades_in_window == 0

    def test_below_min_trades_neutral(self):
        eng = ConfidenceEngine()
        eng.register_outcome(True, 5.0)
        eng.register_outcome(True, 5.0)
        r = eng.score(quality_score=75)
        assert r.trades_in_window == 2
        # Still below MIN_TRADES_FOR_SCALING=5 → neutral
        assert r.multiplier == pytest.approx(0.75, abs=0.01)


class TestConfidenceEngineMultiplierRange:

    def test_multiplier_is_between_05_and_10(self):
        eng = ConfidenceEngine()
        for _ in range(10):
            eng.register_outcome(True, 8.0)
        r = eng.score(quality_score=75)
        assert 0.5 <= r.multiplier <= 1.0

    def test_all_wins_high_multiplier(self):
        eng = ConfidenceEngine()
        _record(eng, wins=20, losses=0)
        r = eng.score(quality_score=80)
        assert r.multiplier > 0.7

    def test_all_losses_low_multiplier(self):
        eng = ConfidenceEngine()
        _record(eng, wins=0, losses=20)
        r = eng.score(quality_score=40)
        assert r.multiplier < 0.75

    def test_higher_quality_score_higher_multiplier(self):
        eng = ConfidenceEngine()
        _record(eng, wins=10, losses=5)
        r_high = eng.score(quality_score=85)
        r_low  = eng.score(quality_score=45)
        assert r_high.multiplier >= r_low.multiplier


class TestConfidenceEngineLabels:

    def test_all_wins_label_high_or_very_high(self):
        eng = ConfidenceEngine()
        _record(eng, wins=20, losses=0)
        r = eng.score(quality_score=85)
        assert r.label in ("HIGH", "VERY HIGH")

    def test_all_losses_label_low(self):
        eng = ConfidenceEngine()
        _record(eng, wins=0, losses=20)
        r = eng.score(quality_score=40)
        assert r.label in ("VERY LOW", "LOW", "MODERATE")


class TestConfidenceEngineRollingWindow:

    def test_window_maxlen_20(self):
        eng = ConfidenceEngine()
        for i in range(30):
            eng.register_outcome(i % 3 != 0, 2.0)
        assert eng._outcomes.maxlen == 20
        assert len(eng._outcomes) == 20

    def test_old_outcomes_drop_off(self):
        eng = ConfidenceEngine()
        # Fill with losses
        _record(eng, wins=0, losses=20)
        r_losses = eng.score(quality_score=70)
        # Now fill with wins (replaces old losses)
        _record(eng, wins=20, losses=0)
        r_wins = eng.score(quality_score=70)
        assert r_wins.multiplier > r_losses.multiplier


class TestConfidenceEngineDrawdown:

    def test_drawdown_reduces_multiplier(self):
        eng_flat = ConfidenceEngine()
        eng_dd   = ConfidenceEngine()

        # Both have same WR (50%)
        _record(eng_flat, wins=10, losses=10, pnl_win=5.0, pnl_loss=-5.0)
        # eng_dd has heavy losses → in drawdown
        _record(eng_dd,   wins=5,  losses=15, pnl_win=5.0, pnl_loss=-8.0)

        r_flat = eng_flat.score(quality_score=70)
        r_dd   = eng_dd.score(quality_score=70)
        assert r_flat.multiplier >= r_dd.multiplier


class TestConfidenceEngineResult:

    def test_result_is_dataclass(self):
        eng = ConfidenceEngine()
        _record(eng, wins=10, losses=5)
        r = eng.score(quality_score=70)
        assert isinstance(r, ConfidenceResult)
        assert hasattr(r, "score")
        assert hasattr(r, "label")
        assert hasattr(r, "multiplier")
        assert hasattr(r, "trades_in_window")

    def test_score_between_0_and_1(self):
        eng = ConfidenceEngine()
        _record(eng, wins=10, losses=5)
        r = eng.score(quality_score=70)
        assert 0.0 <= r.score <= 1.0
