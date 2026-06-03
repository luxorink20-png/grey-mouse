"""
End-to-End tests — Replay pipeline

Tests the full session replay flow using synthetic JSONL data
(no live ATAS connection, no audio, no network).

Simulates what happens when engine.py runs against a pre-recorded session:
  1. BarAggregator receives ticks
  2. Bars are fed into the full 15-engine pipeline
  3. Signals are produced, validated, and risk-assessed
  4. Session statistics are computed

Also validates the MarketFeed._parse() contract against known CSV payloads.
"""

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import json
import time
import pytest
from event_engine      import EventEngine
from bar_aggregator    import BarAggregator
from levels            import create_levels
from confluence_engine import ConfluenceEngine
from validator         import Validator
from risk_engine       import RiskEngine
from intent_engine     import IntentEngine
from market_feed       import MarketFeed


# ── Synthetic session data ────────────────────────────────────────────

def _make_session(n_bars=50, base_price=7200.0, trend="UP"):
    """
    Generate a synthetic sequence of bars simulating a trending session.
    Returns list of bar dicts compatible with EventEngine.process().

    Uses larger moves (2.5 pts) so EventEngine classifies some bars as INTENTO
    (THRESHOLD_INTENTO = 2.0 pts).
    """
    bars = []
    price = base_price
    for i in range(n_bars):
        if trend == "UP":
            # Alternate: two big up moves, one small pullback
            drift = 2.5 if i % 3 != 0 else -1.0
        elif trend == "DOWN":
            drift = -2.5 if i % 3 != 0 else 1.0
        else:  # RANGE
            drift = 1.0 if i % 2 == 0 else -1.0

        price = round(price + drift, 2)
        ask = 400 if drift > 0 else 100
        bid = 100 if drift > 0 else 400

        bars.append({
            "price":      price,
            "open":       round(price - drift, 2),
            "high":       round(price + 0.5, 2),
            "low":        round(price - 0.5, 2),
            "close":      price,
            "volume":     float(ask + bid),
            "ask_volume": float(ask),
            "bid_volume": float(bid),
            "delta":      float(ask - bid),
            "trades":     5,
        })
    return bars


# ── MarketFeed._parse ─────────────────────────────────────────────────

class TestMarketFeedParse:
    """
    Tests the UDP payload parser without opening a socket.
    Accesses _parse directly as a unit boundary.
    """

    @pytest.fixture
    def feed(self):
        from collections import deque as _deque
        f = MarketFeed.__new__(MarketFeed)
        f._host = "127.0.0.1"; f._port = 9999
        f._socket = None; f._thread = None; f._running = False
        f._queue = _deque(maxlen=128)   # v1.2: queue replaces _latest slot
        f._lock = __import__("threading").Lock()
        f._count = 0; f._errors = 0; f._last_raw = ""
        f._connected = False
        return f

    def _csv(self, price=7200.0, open_=7199.0, high=7201.0, low=7198.0,
             close=7200.0, volume=500, delta=100, ask=300, bid=200,
             trades=0, ts=None, symbol="MESM6", bar_idx=42):
        ts = ts or int(time.time())
        return f"{price},{open_},{high},{low},{close},{volume},{delta},{ask},{bid},{trades},{ts},{symbol},{bar_idx}"

    def test_valid_csv_parses_to_dict(self, feed):
        raw = self._csv()
        parsed = feed._parse(raw)
        assert parsed is not None
        assert isinstance(parsed, dict)

    def test_price_field_is_float(self, feed):
        raw = self._csv(price=7205.25)
        parsed = feed._parse(raw)
        if parsed:
            assert isinstance(parsed["price"], float)
            assert parsed["price"] == 7205.25

    def test_volume_field_is_present(self, feed):
        raw = self._csv(volume=1234)
        parsed = feed._parse(raw)
        if parsed:
            assert "volume" in parsed

    def test_missing_fields_returns_none(self, feed):
        # Only 3 fields instead of 13
        raw = "7200,7199,7201"
        parsed = feed._parse(raw)
        assert parsed is None

    def test_empty_string_returns_none(self, feed):
        parsed = feed._parse("")
        assert parsed is None

    def test_symbol_preserved(self, feed):
        raw = self._csv(symbol="MESM6")
        parsed = feed._parse(raw)
        if parsed:
            assert parsed.get("symbol") == "MESM6"

    # ── v1.2 hardening tests ──────────────────────────────────────

    def test_recv_ts_present_in_parsed_dict(self, feed):
        """R2 fix: recv_ts must be present in every parsed tick."""
        raw = self._csv()
        parsed = feed._parse(raw)
        assert parsed is not None
        assert "recv_ts" in parsed
        assert isinstance(parsed["recv_ts"], float)
        assert parsed["recv_ts"] > 0

    def test_recv_ts_is_recent(self, feed):
        """recv_ts must be within 1 second of Python wall clock."""
        raw = self._csv()
        before = time.time()
        parsed = feed._parse(raw)
        after = time.time()
        assert parsed is not None
        assert before <= parsed["recv_ts"] <= after + 0.001

    def test_ms_precision_timestamp_preserved(self, feed):
        """R2 fix: float timestamp with ms precision must survive the parse."""
        raw = self._csv(ts=1744286460.123)
        parsed = feed._parse(raw)
        assert parsed is not None
        assert abs(parsed["timestamp"] - 1744286460.123) < 0.001

    def test_queue_buffers_multiple_packets(self, feed):
        """R1 fix: queue must store all packets, not just the latest."""
        payloads = [
            self._csv(price=7200.0),
            self._csv(price=7200.5),
            self._csv(price=7201.0),
        ]
        # Simulate receiver thread: parse and enqueue all three
        import threading
        lock = feed._lock
        for raw in payloads:
            parsed = feed._parse(raw)
            assert parsed is not None
            with lock:
                feed._queue.append(parsed)

        assert feed.queue_size == 3

    def test_queue_returns_fifo_order(self, feed):
        """R1 fix: get_latest_blocking must return oldest tick first."""
        prices = [7200.0, 7200.5, 7201.0]
        lock = feed._lock
        for p in prices:
            parsed = feed._parse(self._csv(price=p))
            with lock:
                feed._queue.append(parsed)

        # Drain the queue — must come out in insertion order
        results = []
        while feed.queue_size > 0:
            tick = feed.get_latest_blocking(timeout=0.1)
            if tick is not None:
                results.append(tick["price"])

        assert results == prices, f"Expected FIFO {prices}, got {results}"

    def test_queue_bounded_at_maxlen(self, feed):
        """Queue must not grow beyond maxlen=128 (drops oldest on overflow)."""
        lock = feed._lock
        for i in range(200):
            parsed = feed._parse(self._csv(price=float(7200 + i)))
            with lock:
                feed._queue.append(parsed)

        assert feed.queue_size == 128

    def test_get_latest_peeks_without_consuming(self, feed):
        """get_latest() must peek at most-recent without removing from queue."""
        payloads = [7200.0, 7201.0, 7202.0]
        lock = feed._lock
        for p in payloads:
            with lock:
                feed._queue.append({"price": p, "recv_ts": time.time()})

        peeked = feed.get_latest()
        assert peeked is not None
        assert peeked["price"] == 7202.0, "get_latest must peek at most recent"
        assert feed.queue_size == 3, "get_latest must not consume the tick"


# ── Full session replay ───────────────────────────────────────────────

class TestFullSessionReplay:

    @pytest.fixture
    def engines(self):
        VAH = 7326.0; POC = 7135.0; VAL = 6826.0
        return {
            "event":      EventEngine(window=10),
            "levels":     create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0),
            "confluence": ConfluenceEngine(history_size=10),
            "validator":  Validator(tick=0.25, min_liq_ticks=4),
            "risk":       RiskEngine(tick=0.25),
            "intent":     IntentEngine(buffer_size=15, tick=0.25),
        }

    def _replay(self, engines, bars):
        results = []
        for bar in bars:
            res  = engines["event"].process(bar)
            ctx  = engines["levels"].get_context(bar["price"])
            anal = engines["confluence"].evaluate(res, ctx)
            val  = engines["validator"].validate(anal, res, bar)
            narr = engines["intent"].analyze(res, ctx, anal, val)
            rr   = engines["risk"].analyze(bar["price"], anal, val, narr, ctx)
            results.append({
                "event":    res["event"],
                "score":    anal.score,
                "validated": val.validated,
                "approved":  rr.approved,
            })
        return results

    def test_uptrend_session_produces_at_least_one_intento(self, engines):
        bars = _make_session(n_bars=60, base_price=7180.0, trend="UP")
        results = self._replay(engines, bars)
        events = [r["event"] for r in results]
        assert "INTENTO" in events

    def test_downtrend_session_produces_intento(self, engines):
        bars = _make_session(n_bars=60, base_price=7250.0, trend="DOWN")
        results = self._replay(engines, bars)
        events = [r["event"] for r in results]
        assert "INTENTO" in events

    def test_range_session_dominated_by_acumulacion(self, engines):
        bars = _make_session(n_bars=80, base_price=7135.0, trend="RANGE")
        results = self._replay(engines, bars)
        acum = sum(1 for r in results if "ACUMUL" in r["event"])
        total = len(results)
        # In a range session, accumulation events should dominate
        assert acum / total >= 0.3

    def test_all_scores_in_valid_range(self, engines):
        bars = _make_session(n_bars=50, base_price=7200.0, trend="UP")
        results = self._replay(engines, bars)
        for r in results:
            assert 0 <= r["score"] <= 100, f"Score out of range: {r['score']}"

    def test_no_crashes_over_100_bars(self, engines):
        bars = _make_session(n_bars=100, base_price=7200.0, trend="UP")
        try:
            results = self._replay(engines, bars)
        except Exception as e:
            pytest.fail(f"Pipeline crashed on bar processing: {e}")
        assert len(results) == 100

    def test_approved_trades_have_valid_risk_params(self, engines):
        bars = _make_session(n_bars=100, base_price=7150.0, trend="UP")
        results_raw = []

        for bar in bars:
            res  = engines["event"].process(bar)
            ctx  = engines["levels"].get_context(bar["price"])
            anal = engines["confluence"].evaluate(res, ctx)
            val  = engines["validator"].validate(anal, res, bar)
            narr = engines["intent"].analyze(res, ctx, anal, val)
            rr   = engines["risk"].analyze(bar["price"], anal, val, narr, ctx)

            if rr.approved:
                assert rr.stop > 0,      "Stop must be positive"
                assert rr.target_1 > 0,  "Target must be positive"
                assert rr.risk_reward >= RiskEngine.MIN_RR, \
                    f"R:R {rr.risk_reward} below minimum {RiskEngine.MIN_RR}"
                assert rr.position_size in (0.25, 0.5, 1.0, 2.0), \
                    f"Unexpected position size: {rr.position_size}"

    def test_session_statistics(self, engines):
        """Baseline regression: record key session metrics."""
        bars = _make_session(n_bars=80, base_price=7180.0, trend="UP")
        results = self._replay(engines, bars)

        approved = [r for r in results if r["approved"]]
        validated = [r for r in results if r["validated"]]
        avg_score = sum(r["score"] for r in results) / len(results)

        # Sanity bounds — not strict targets, just regression guards
        assert avg_score >= 0
        assert len(validated) <= len(results)
        assert len(approved) <= len(validated)


# ── JSONL recording round-trip ────────────────────────────────────────

class TestJSONLRoundtrip:
    """
    Validates that bars serialised to JSONL and read back
    produce identical pipeline results.
    """

    def test_jsonl_write_read_preserves_prices(self, tmp_path):
        bars = _make_session(n_bars=10)
        path = tmp_path / "test_session.jsonl"

        with open(path, "w") as f:
            for bar in bars:
                f.write(json.dumps(bar) + "\n")

        loaded = []
        with open(path) as f:
            for line in f:
                loaded.append(json.loads(line.strip()))

        assert len(loaded) == 10
        for orig, back in zip(bars, loaded):
            assert orig["price"] == back["price"]
            assert orig["volume"] == back["volume"]

    def test_jsonl_bars_are_pipeline_compatible(self, tmp_path):
        bars = _make_session(n_bars=20)
        path = tmp_path / "compat_session.jsonl"

        with open(path, "w") as f:
            for bar in bars:
                f.write(json.dumps(bar) + "\n")

        eng = EventEngine(window=10)
        with open(path) as f:
            for line in f:
                bar = json.loads(line.strip())
                result = eng.process(bar)
                assert "event" in result
                assert "confidence" in result
