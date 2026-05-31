"""
Integration tests — Core signal pipeline

Tests the chain:
  EventEngine → InstitutionalLevels → ConfluenceEngine → Validator → IntentEngine → RiskEngine

No UDP, no audio, no filesystem I/O. All engines are real instances.

Pipeline call order (mandatory — each step depends on previous):
  res  = event.process(raw)
  ctx  = levels.get_context(price)
  anal = confluence.evaluate(res, ctx)
  val  = validator.validate(anal, res, raw)
  narr = intent.analyze(res, ctx, anal, val)
  rr   = risk.analyze(price, anal, val, narr, ctx)
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from event_engine      import EventEngine
from levels            import create_levels
from confluence_engine import ConfluenceEngine
from validator         import Validator, ValidationResult
from intent_engine     import IntentEngine
from risk_engine       import RiskEngine


VAH = 7200.0
POC = 7100.0
VAL = 7000.0


def _bar(price, ask=200, bid=150, high=None, low=None):
    h = high if high is not None else price + 0.5
    l = low  if low  is not None else price - 0.5
    return {"price": float(price), "open": float(price),
            "high": float(h), "low": float(l), "close": float(price),
            "volume": float(ask + bid), "ask_volume": float(ask),
            "bid_volume": float(bid), "delta": float(ask - bid), "trades": 1}


@pytest.fixture
def pipeline():
    return {
        "event":      EventEngine(window=10),
        "levels":     create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0),
        "confluence": ConfluenceEngine(history_size=10),
        "validator":  Validator(tick=0.25, min_liq_ticks=4),
        "intent":     IntentEngine(buffer_size=15, tick=0.25),
        "risk":       RiskEngine(tick=0.25),
    }


def _run_bar(p, price, ask=200, bid=150, high=None, low=None):
    """Run a single bar through the full pipeline and return all results."""
    raw  = _bar(price, ask=ask, bid=bid, high=high, low=low)
    res  = p["event"].process(raw)
    ctx  = p["levels"].get_context(price)
    anal = p["confluence"].evaluate(res, ctx)
    val  = p["validator"].validate(anal, res, raw)
    narr = p["intent"].analyze(res, ctx, anal, val)
    rr   = p["risk"].analyze(price, anal, val, narr, ctx)
    return raw, res, ctx, anal, val, narr, rr


# ── Result types ──────────────────────────────────────────────────────

class TestResultTypes:

    def test_event_result_is_dict(self, pipeline):
        raw, res, ctx, anal, val, narr, rr = _run_bar(pipeline, 7200.0)
        assert isinstance(res, dict)

    def test_level_context_has_zone(self, pipeline):
        _, _, ctx, _, _, _, _ = _run_bar(pipeline, 7200.0)
        assert hasattr(ctx, "zone")
        assert ctx.zone is not None

    def test_confluence_result_has_score(self, pipeline):
        _, _, _, anal, _, _, _ = _run_bar(pipeline, 7200.0)
        assert hasattr(anal, "score")
        assert 0 <= anal.score <= 100

    def test_confluence_result_has_bias(self, pipeline):
        _, _, _, anal, _, _, _ = _run_bar(pipeline, 7200.0)
        assert hasattr(anal, "bias")
        assert anal.bias in ("BULLISH", "BEARISH", "NEUTRAL")

    def test_validation_result_type(self, pipeline):
        _, _, _, _, val, _, _ = _run_bar(pipeline, 7200.0)
        assert isinstance(val.validated, bool)
        assert isinstance(val.filters_passed, list)
        assert isinstance(val.filters_failed, list)

    def test_intent_result_has_narrative(self, pipeline):
        _, _, _, _, _, narr, _ = _run_bar(pipeline, 7200.0)
        assert hasattr(narr, "narrative")
        assert narr.narrative in (
            "INDUCTION", "DISTRIBUTION", "ACCUMULATION",
            "SQUEEZE", "REBALANCE", "UNCLEAR"
        )

    def test_risk_result_is_bool_approved(self, pipeline):
        _, _, _, _, _, _, rr = _run_bar(pipeline, 7200.0)
        assert isinstance(rr.approved, bool)


# ── Warmup bars ───────────────────────────────────────────────────────

class TestWarmup:

    def test_warmup_bars_produce_low_score(self, pipeline):
        for p in [7200.0, 7200.25]:
            _, res, _, anal, _, _, _ = _run_bar(pipeline, p)
            if res["event"] == "INIT":
                assert anal.score <= 50


# ── Dead zone never produces a high-risk trade ────────────────────────

class TestDeadZoneNeverTrades:

    def test_flat_bars_produce_low_scores(self, pipeline):
        scores = []
        for _ in range(15):
            _, _, _, anal, val, _, rr = _run_bar(
                pipeline, 7150.0, ask=100, bid=100, high=7150.1, low=7149.9
            )
            scores.append(anal.score)
            if rr.approved:
                assert rr.position_size <= 0.5

        avg = sum(scores) / len(scores)
        assert avg <= 60  # flat market should not produce high scores


# ── Full pipeline with strong INTENTO ────────────────────────────────

class TestStrongSignal:

    def test_strong_intento_chain_does_not_crash(self, pipeline):
        # Warmup
        for p in [VAH - 1.0, VAH - 0.75, VAH - 0.5, VAH - 0.25, VAH]:
            _run_bar(pipeline, p, ask=250, bid=150)

        # Strong bullish INTENTO at VAH
        raw, res, ctx, anal, val, narr, rr = _run_bar(
            pipeline, VAH + 3.0, ask=800, bid=100,
            high=VAH + 4.0, low=VAH + 2.5
        )
        assert hasattr(anal, "score")
        assert hasattr(val, "validated")
        assert isinstance(rr.approved, bool)

    def test_validation_result_has_filter_lists(self, pipeline):
        for p in [VAH, VAH + 0.25, VAH + 0.5, VAH + 0.75, VAH + 1.0]:
            _run_bar(pipeline, p)

        _, _, _, _, val, _, _ = _run_bar(pipeline, VAH + 4.0, ask=600, bid=100)
        assert isinstance(val.filters_passed, list)
        assert isinstance(val.filters_failed, list)
        assert val.reason is not None


# ── Score below threshold is always rejected ──────────────────────────

class TestLowScoreRejected:

    def test_score_below_min_is_rejected(self, pipeline):
        _, _, _, anal, val, _, rr = _run_bar(
            pipeline, 7150.0, ask=100, bid=100, high=7150.1, low=7149.9
        )
        if anal.score < 42:
            assert rr.approved is False


# ── Pipeline state consistency across many bars ───────────────────────

class TestPipelineStatePersistence:

    def test_20_bar_sequence_does_not_crash(self, pipeline):
        prices = [VAH - 5 + i * 0.5 for i in range(20)]
        for p in prices:
            ask = 200 + int(p % 3) * 50
            _, _, _, anal, val, _, rr = _run_bar(pipeline, p, ask=ask, bid=150)
            assert 0 <= anal.score <= 100
            assert isinstance(val.validated, bool)
            assert isinstance(rr.approved, bool)

    def test_approved_trades_respect_risk_params(self, pipeline):
        prices = [VAH - 5 + i * 0.5 for i in range(40)]
        for p in prices:
            _, _, _, _, _, _, rr = _run_bar(pipeline, p, ask=250, bid=150)
            if rr.approved:
                assert rr.stop > 0
                assert rr.target_1 > 0
                assert rr.risk_reward >= RiskEngine.MIN_RR

    def test_engine_bar_count_matches_fed_bars(self, pipeline):
        for i in range(10):
            _run_bar(pipeline, 7200 + i * 0.25)
        assert pipeline["event"]._bar_count == 10
