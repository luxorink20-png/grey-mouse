"""Unit tests for quality_engine.py (Wave 1 — Institutional Fusion)."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
import pytest
from quality_engine import QualityEngine, QualityResult


# ── Minimal mock objects ──────────────────────────────────────────────────

class _Conf:
    def __init__(self, score=75, event="INTENTO"):
        self.score = score
        self.event = event

class _Val:
    def __init__(self, validated=True, adjusted_score=75):
        self.validated      = validated
        self.adjusted_score = adjusted_score

class _Ctx:
    def __init__(self, zone="AT_VAH"):
        self.zone = zone

class _Intent:
    def __init__(self, conviction=80):
        self.conviction = conviction

class _Risk:
    def __init__(self, rr=2.0):
        self.risk_reward = rr


def make_result(score=75, event="INTENTO", zone="AT_VAH",
                conviction=80, rr=2.0, threshold=62) -> QualityResult:
    eng = QualityEngine(threshold=threshold)
    return eng.score(
        confluence    = _Conf(score=score, event=event),
        validation    = _Val(),
        level_context = _Ctx(zone=zone),
        intent        = _Intent(conviction=conviction),
        risk_result   = _Risk(rr=rr),
    )


# ── Tests ─────────────────────────────────────────────────────────────────

class TestQualityEnginePass:

    def test_high_quality_setup_passes(self):
        r = make_result(score=80, event="INTENTO", zone="AT_VAH", conviction=85, rr=2.5)
        assert r.passes
        assert r.score >= 62

    def test_at_val_passes(self):
        r = make_result(score=75, zone="AT_VAL")
        assert r.passes

    def test_va80_setup_passes(self):
        # VA80 profile: AT_VAH, INTENTO, high conviction
        r = make_result(score=80, event="INTENTO", zone="AT_VAH", conviction=90, rr=3.0)
        assert r.passes

    def test_score_above_100_capped(self):
        r = make_result(score=100, conviction=100, rr=3.0)
        assert r.score <= 100


class TestQualityEngineFail:

    def test_low_confluence_fails(self):
        # confluence 45 → 22pts + small other components → total < 62
        r = make_result(score=45, event="FALLO", zone="OUTSIDE_RANGE",
                        conviction=55, rr=1.5)
        assert not r.passes

    def test_fallo_outside_range_fails(self):
        r = make_result(score=48, event="FALLO", zone="OUTSIDE_RANGE",
                        conviction=58, rr=1.6)
        assert not r.passes

    def test_in_value_area_low_conf_fails(self):
        r = make_result(score=50, event="ACUMULACIÓN", zone="IN_VALUE_AREA",
                        conviction=60, rr=1.5)
        assert not r.passes


class TestQualityEngineBreakdown:

    def test_breakdown_keys_present(self):
        r = make_result()
        for key in ("confluence", "zone", "event", "conviction", "rr"):
            assert key in r.breakdown

    def test_breakdown_sum_equals_score(self):
        r = make_result()
        assert sum(r.breakdown.values()) == r.score

    def test_confluence_dominates(self):
        r = make_result(score=80)
        assert r.breakdown["confluence"] >= r.breakdown["zone"]
        assert r.breakdown["confluence"] >= r.breakdown["event"]

    def test_high_rr_adds_max_pts(self):
        r_high = make_result(rr=3.0)
        r_low  = make_result(rr=1.5)
        assert r_high.breakdown["rr"] > r_low.breakdown["rr"]


class TestQualityEngineThreshold:

    def test_threshold_respected(self):
        # At threshold=70, the same setup that passes at 62 may fail
        r62 = make_result(score=55, event="AGOTAMIENTO", zone="AT_POC",
                          conviction=70, rr=2.0, threshold=62)
        r70 = make_result(score=55, event="AGOTAMIENTO", zone="AT_POC",
                          conviction=70, rr=2.0, threshold=70)
        # Score is fixed; threshold determines pass/fail
        assert r62.score == r70.score
        if r62.score < 70:
            assert not r70.passes

    def test_default_threshold_is_62(self):
        eng = QualityEngine()
        assert eng.threshold == 62

    def test_result_returns_correct_threshold(self):
        r = make_result(threshold=65)
        assert r.threshold == 65


class TestQualityEngineMissingFields:

    def test_missing_event_uses_default_weight(self):
        """Missing event attribute should not raise."""
        class NoEvent:
            score = 70
        eng = QualityEngine(threshold=62)
        r = eng.score(
            confluence    = NoEvent(),
            validation    = _Val(),
            level_context = _Ctx(),
            intent        = _Intent(),
            risk_result   = _Risk(),
        )
        assert isinstance(r.score, int)

    def test_unknown_zone_uses_fallback_weight(self):
        r = make_result(zone="UNKNOWN_ZONE")
        assert r.breakdown["zone"] == int(0.60 * 20)
