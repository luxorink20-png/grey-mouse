"""
Integration tests — Validator v9.1 live-path gates

Verifies that all 5 gates blocked in live trading (pre-fix) now function
correctly when context engines are passed to validator.validate().

Gates under test:
  Gate 1 — TOXIC_REGIME    (session_regime in LIQUIDATION/HIGH_VOL_DAY)
  Gate 2 — TOXIC_ENV       (market_env.tradeable=False or env in TOXIC_ENVS)
  Gate 3 — FAKE_BREAKOUT   (confirmation.breakout_type == "FAKE")
  Gate 3 — STRUCT_OPPOSED  (dynamic penalty by regime class)
  Gate 4 — CONT_WEAK       (continuation_quality=WEAK in non-trend regime)
  Happy  — all gates clear, trade validates
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from validator         import Validator, ValidationResult
from confluence_engine import ConfluenceEngine
from event_engine      import EventEngine
from levels            import create_levels


VAH = 7200.0
POC = 7100.0
VAL = 7000.0

TICK = 0.25


# ── Helpers ───────────────────────────────────────────────────────────

def _bar(price, ask=300, bid=150):
    return {
        "price":      float(price),
        "open":       float(price),
        "high":       float(price) + 1.0,
        "low":        float(price) - 1.0,
        "close":      float(price),
        "volume":     float(ask + bid),
        "ask_volume": float(ask),
        "bid_volume": float(bid),
        "delta":      float(ask - bid),
        "trades":     0,
        "env":        "ROTATIONAL",
        "zone":       "AT_VAH",
    }


def _warm_pipeline(price, n=8):
    """Return a warmed (event_result, level_context, analysis) tuple."""
    eng    = EventEngine(window=10)
    lvls   = create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0)
    conf   = ConfluenceEngine(history_size=10)
    for _ in range(n - 1):
        raw = _bar(price, ask=300, bid=100)
        eng.process(raw)
    raw    = _bar(price, ask=400, bid=100)   # strong ask → INTENTO
    result = eng.process(raw)
    ctx    = lvls.get_context(price)
    # Warm confluence without context (neutral baseline)
    analysis = conf.evaluate(result, ctx)
    return raw, result, ctx, analysis


# ── Gate 1: TOXIC_REGIME ──────────────────────────────────────────────

class TestToxicRegimeGate:

    def test_liquidation_regime_rejects(self, make_session_regime):
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAH)
        regime = make_session_regime(regime="LIQUIDATION")

        vr = val.validate(analysis, result, raw, session_regime=regime)

        assert vr.validated is False
        assert "TOXIC_REGIME" in vr.filters_failed

    def test_high_vol_day_rejects(self, make_session_regime):
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAH)
        regime = make_session_regime(regime="HIGH_VOL_DAY")

        vr = val.validate(analysis, result, raw, session_regime=regime)

        assert vr.validated is False
        assert "TOXIC_REGIME" in vr.filters_failed

    def test_balanced_day_passes_regime_gate(self, make_session_regime):
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAH)
        regime = make_session_regime(regime="BALANCED_DAY")

        vr = val.validate(analysis, result, raw, session_regime=regime)

        assert "TOXIC_REGIME" not in vr.filters_failed


# ── Gate 2: TOXIC_ENV ─────────────────────────────────────────────────

class TestToxicEnvGate:

    def test_not_tradeable_rejects(self, make_market_env):
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAH)
        env = make_market_env(environment="ROTATIONAL", tradeable=False)

        vr = val.validate(analysis, result, raw, market_env=env)

        assert vr.validated is False
        assert "TOXIC_ENV" in vr.filters_failed

    def test_trappy_env_rejects(self, make_market_env):
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAH)
        env = make_market_env(environment="TRAPPY", tradeable=True)

        vr = val.validate(analysis, result, raw, market_env=env)

        assert vr.validated is False
        assert "TOXIC_ENV" in vr.filters_failed

    def test_dead_market_rejects(self, make_market_env):
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAH)
        env = make_market_env(environment="DEAD_MARKET", tradeable=True)

        vr = val.validate(analysis, result, raw, market_env=env)

        assert vr.validated is False
        assert "TOXIC_ENV" in vr.filters_failed

    def test_efficient_trend_passes_env_gate(self, make_market_env):
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAH)
        env = make_market_env(environment="EFFICIENT_TREND", tradeable=True)

        vr = val.validate(analysis, result, raw, market_env=env)

        assert "TOXIC_ENV" not in vr.filters_failed


# ── Gate 3: FAKE_BREAKOUT ─────────────────────────────────────────────

class TestFakeBreakoutGate:

    def test_fake_breakout_always_rejects(self, make_confirmation):
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAH)
        conf = make_confirmation(breakout_type="FAKE", confirmation_score=95)

        vr = val.validate(analysis, result, raw, confirmation=conf)

        assert vr.validated is False
        assert "FAKE_BREAKOUT" in vr.filters_failed

    def test_real_breakout_passes_gate(self, make_confirmation):
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAH)
        conf = make_confirmation(breakout_type="REAL", confirmation_score=90)

        vr = val.validate(analysis, result, raw, confirmation=conf)

        assert "FAKE_BREAKOUT" not in vr.filters_failed


# ── Gate 3: STRUCT_OPPOSED penalty by regime ──────────────────────────

class TestStructOpposedPenalty:

    def test_trend_day_struct_opposed_applies_minus15(
        self, make_confirmation, make_session_regime
    ):
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAH)
        # Confluence bias is BEARISH (VAH zone). BULLISH struct = opposed.
        # With no continuation (cont_p=0 < 75), validator returns hard block:
        # TREND_DAY_STRUCT_OPPOSED (early return), penalty still stored as -15.
        conf   = make_confirmation(breakout_type="MODERATE",
                                   structure_bias="BULLISH",
                                   confirmation_score=70)
        regime = make_session_regime(regime="TREND_DAY")

        vr = val.validate(analysis, result, raw,
                          confirmation=conf, session_regime=regime)

        assert vr.validated is False
        # Either hard-block (TREND_DAY_STRUCT_OPPOSED) or penalty path
        # (STRUCT_OPPOSED) — both indicate the -15 penalty was applied.
        struct_blocked = (
            "TREND_DAY_STRUCT_OPPOSED" in vr.filters_failed
            or "STRUCT_OPPOSED" in vr.filters_failed
        )
        assert struct_blocked
        assert vr.score_breakdown.get("penalties", {}).get("struct_opposed") == -15

    def test_rotational_struct_opposed_applies_minus6(
        self, make_confirmation, make_session_regime
    ):
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAH)
        conf   = make_confirmation(breakout_type="MODERATE",
                                   structure_bias="BULLISH",
                                   confirmation_score=70)
        regime = make_session_regime(regime="ROTATIONAL")

        vr = val.validate(analysis, result, raw,
                          confirmation=conf, session_regime=regime)

        assert "STRUCT_OPPOSED" in vr.filters_failed
        assert vr.score_breakdown.get("penalties", {}).get("struct_opposed") == -6

    def test_struct_aligned_adds_bonus(
        self, make_confirmation, make_session_regime
    ):
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAH)
        # Analysis bias at VAH is BEARISH — align struct with BEARISH
        conf   = make_confirmation(breakout_type="MODERATE",
                                   structure_bias="BEARISH",   # aligned
                                   confirmation_score=70)
        regime = make_session_regime(regime="BALANCED_DAY")

        vr = val.validate(analysis, result, raw,
                          confirmation=conf, session_regime=regime)

        assert "STRUCT_ALIGNED" in vr.filters_passed
        assert vr.score_breakdown.get("bonuses", {}).get("struct_aligned", 0) > 0


# ── Gate 4: CONT_WEAK rejection ───────────────────────────────────────

class TestContWeakGate:

    def test_cont_weak_in_rotational_rejects(
        self, make_continuation, make_session_regime
    ):
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAH)
        cont   = make_continuation(quality="WEAK", probability=45, risk=20)
        regime = make_session_regime(regime="ROTATIONAL")

        vr = val.validate(analysis, result, raw,
                          continuation=cont, session_regime=regime)

        assert vr.validated is False
        assert "CONT_WEAK" in vr.filters_failed

    def test_cont_weak_in_trend_day_passes(
        self, make_continuation, make_session_regime
    ):
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAH)
        cont   = make_continuation(quality="WEAK", probability=80, risk=20)
        regime = make_session_regime(regime="TREND_DAY")

        vr = val.validate(analysis, result, raw,
                          continuation=cont, session_regime=regime)

        # WEAK in TREND_DAY does not trigger CONT_WEAK rejection
        assert "CONT_WEAK" not in vr.filters_failed


# ── Happy path: all gates clear ───────────────────────────────────────

class TestHappyPath:

    def test_all_gates_pass_produces_validated_true(
        self, make_session_regime, make_confirmation, make_market_env, make_continuation
    ):
        # Use VAL zone: INTENTO at AT_VAL gives base score 80 (BULLISH bias),
        # ensuring adjusted score comfortably exceeds MIN_SCORE_TO_TRADE=45.
        val = Validator(tick=TICK, min_liq_ticks=4)
        raw, result, ctx, analysis = _warm_pipeline(VAL)

        regime = make_session_regime(regime="TREND_DAY")
        conf   = make_confirmation(breakout_type="REAL",
                                   structure_bias="BULLISH",   # aligned with BULLISH at VAL
                                   confirmation_score=85)
        env    = make_market_env(environment="EFFICIENT_TREND", tradeable=True)
        cont   = make_continuation(quality="STRONG", probability=90, risk=10)

        vr = val.validate(
            analysis, result, raw,
            confirmation=conf,
            session_regime=regime,
            continuation=cont,
            market_env=env,
        )

        assert vr.validated is True
        assert "TOXIC_REGIME"  not in vr.filters_failed
        assert "TOXIC_ENV"     not in vr.filters_failed
        assert "FAKE_BREAKOUT" not in vr.filters_failed
        assert "CONT_WEAK"     not in vr.filters_failed
