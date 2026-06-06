"""
Shared pytest fixtures for GIBBZ test suite.
All fixtures are deterministic — no live UDP, no audio, no filesystem I/O.
"""

import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ── Raw bar factory ───────────────────────────────────────────────────

def make_bar(price=7200.0, high=None, low=None,
             ask_volume=200, bid_volume=150, volume=None,
             delta=None, trades=0):
    high = high if high is not None else price + 1.0
    low  = low  if low  is not None else price - 1.0
    ask  = ask_volume
    bid  = bid_volume
    vol  = volume if volume is not None else ask + bid
    dlt  = delta  if delta  is not None else ask - bid
    return {
        "price":      float(price),
        "open":       float(price),
        "high":       float(high),
        "low":        float(low),
        "close":      float(price),
        "volume":     float(vol),
        "ask_volume": float(ask),
        "bid_volume": float(bid),
        "delta":      float(dlt),
        "trades":     trades,
    }


@pytest.fixture
def bar_factory():
    return make_bar


# ── Standard market levels ────────────────────────────────────────────

@pytest.fixture
def standard_levels():
    """VAL=7000, POC=7100, VAH=7200 — a clean three-level bracket."""
    from levels import create_levels
    return create_levels(vah=7200.0, poc=7100.0, val=7000.0, proximity=2.0)


@pytest.fixture
def standard_levels_wide():
    """Wider bracket for regression tests."""
    from levels import create_levels
    return create_levels(vah=7326.0, poc=7135.0, val=6826.0, proximity=2.0)


# ── Mock result objects ───────────────────────────────────────────────

class _MockConfluence:
    def __init__(self, score=75, bias="BEARISH",
                 classification="HIGH QUALITY", event="INTENTO"):
        self.score          = score
        self.bias           = bias
        self.classification = classification
        self.event          = event


class _MockValidation:
    def __init__(self, validated=True, adjusted_score=75, reason="OK"):
        self.validated      = validated
        self.adjusted_score = adjusted_score
        self.filters_passed = ["GAMMA", "EXPANSION", "LIQUIDITY", "TRAP"]
        self.filters_failed = []
        self.reason         = reason


class _MockIntent:
    def __init__(self, narrative="SQUEEZE", conviction=80):
        self.narrative  = narrative
        self.conviction = conviction


class _MockLevelContext:
    def __init__(self, zone="AT_VAH", nearest_level="VAH",
                 nearest_price=7200.0, high_prob_zone=True,
                 reaction_bias="BEARISH"):
        self.zone          = zone
        self.nearest_level = nearest_level
        self.nearest_price = nearest_price
        self.high_prob_zone = high_prob_zone
        self.reaction_bias = reaction_bias


@pytest.fixture
def mock_confluence():
    return _MockConfluence


@pytest.fixture
def mock_validation():
    return _MockValidation


@pytest.fixture
def mock_intent():
    return _MockIntent


@pytest.fixture
def mock_level_context():
    return _MockLevelContext


# ── Validator context mocks (live-path gates) ─────────────────────────

class _MockSessionRegime:
    def __init__(self, regime="BALANCED_DAY"):
        self.session_regime             = regime
        self.regime_confidence          = 60
        self.trend_strength             = 40
        self.volatility_state           = "NORMAL"
        self.continuation_probability   = 50
        self.mean_reversion_probability = 50

    def is_range_regime(self):
        return self.session_regime in (
            "BALANCED_DAY", "ROTATIONAL_DAY", "LOW_VOL_DAY", "TRAPPED_DAY"
        )


class _MockConfirmationResult:
    def __init__(self, breakout_type="MODERATE", acceptance_type="EXPANSION",
                 confirmation_score=70, structure_bias="NEUTRAL"):
        self.breakout_type        = breakout_type
        self.acceptance_type      = acceptance_type
        self.confirmation_score   = confirmation_score
        self.structure_bias       = structure_bias
        self.confirmed            = True


class _MockMarketEnv:
    def __init__(self, environment="ROTATIONAL", tradeable=True,
                 trap_density=0, breakout_failure_rate=0,
                 directional_efficiency=60, danger_level=0):
        self.environment             = environment
        self.tradeable               = tradeable
        self.trap_density            = trap_density
        self.breakout_failure_rate   = breakout_failure_rate
        self.directional_efficiency  = directional_efficiency
        self.danger_level            = danger_level
        self.confidence              = 60
        self.rotation_factor         = 0


class _MockContinuationResult:
    def __init__(self, quality="MODERATE", probability=75, risk=30):
        self.continuation_quality      = quality
        self.continuation_probability  = probability
        self.continuation_risk         = risk


@pytest.fixture
def make_session_regime():
    return _MockSessionRegime


@pytest.fixture
def make_confirmation():
    return _MockConfirmationResult


@pytest.fixture
def make_market_env():
    return _MockMarketEnv


@pytest.fixture
def make_continuation():
    return _MockContinuationResult


# ── Warmed-up EventEngine ─────────────────────────────────────────────

@pytest.fixture
def warmed_engine():
    """EventEngine that has already consumed WARMUP_BARS initialisation ticks."""
    from event_engine import EventEngine
    eng = EventEngine(window=10)
    base = 7200.0
    for i in range(5):
        eng.process(make_bar(price=base + i * 0.25))
    return eng
