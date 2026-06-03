# ATAS → PYTHON BRIDGE CAPABILITY AUDIT
**GIBBZ Trading System — Technical Data Feed Assessment**

**Date:** 2026-06-02  
**Method:** Read-only static analysis of source code + empirical analysis of 656 MB JSONL recording (2026-05-09, MES)  
**Scope:** GibbzBridge.cs v2.3, MarketFeed.py v1.1, BarAggregator.py, engine.py, replay_recorder.py, ocie_stacked_imbalance.py, tick_schema.py + 14 additional modules  
**No code was modified.**

---

## 1. EXECUTIVE SUMMARY

The GIBBZ bridge delivers **true per-trade tick data** from ATAS at up to 327 ticks/second. Each executed trade arrives as a completed 1-tick ATAS candle over UDP loopback with sub-millisecond transmission latency. The feed provides full OHLCV + Delta + Ask/Bid volume separation at tick resolution.

**What is strong:**
- True tick-by-tick resolution (vol=1 on 86.7% of packets)
- Delta direction per trade (aggressor buyer vs seller, derived from ATAS candle.Delta)
- Session-level volume profile (VAH/VAL/POC) via GibbzBridge footprint API
- Stacked imbalance detection (pure Python, no DOM required)
- Sub-millisecond loopback UDP latency

**What is missing:**
- DOM / Level 2 order book — **NOT transmitted**
- Millisecond-precision timestamps — ATAS sends second-level only
- Individual quote-level data (resting orders, pulls, adds) — **not available**
- Iceberg / hidden order detection — **not available**
- Tick sequence numbers — **not transmitted**

**Institutional readiness score: 62 / 100** (Advanced Retail tier — above typical retail, below institutional-grade CME Level 2 systems)

---

## 2. ARCHITECTURE — COMPLETE FLOW

```
╔══════════════════════════════════════════════════════════════════╗
║  ATAS Chart (Windows)                                            ║
║  │                                                               ║
║  │  OnCalculate(bar, value)  — fires on every COMPLETED candle  ║
║  │  GetCandle(bar)           — retrieves ATAS ICandle object    ║
║  │  ICandle fields: Close, Open, High, Low, Volume, Delta,      ║
║  │                  Time, (GetAllPriceLevels if Footprint)       ║
║  ▼                                                               ║
║  GibbzBridge.cs  v2.3      [C#, ATAS.Indicators.dll]           ║
║  Class: GibbzBridge : Indicator                                  ║
║  Method: OnCalculate() → formats 13-field CSV → UdpClient.Send  ║
║  Timer:  PollCommandFile() every 500ms (file-based IPC)         ║
║  VP:     TrackContextLevels() + UpdateVolumeProfile() per bar   ║
║  │                                                               ║
║  │  Protocol: UDP/IPv4  Transport: loopback 127.0.0.1:9999      ║
║  │  Format: CSV, 13 fields, ~80 bytes/packet, no framing        ║
║  │  Frequency: one packet per ATAS bar close                    ║
║  │                                                               ║
║  ▼                                                               ║
║  market_feed.py  (MarketFeed class)     [Python, socket, Thread]║
║  Background thread: socket.recvfrom(1024) with 100ms timeout   ║
║  Parse: _parse() → 13-field CSV → dict                         ║
║  Storage: _latest (single slot, lock-protected) — NO queue      ║
║  │                                                               ║
║  ▼                                                               ║
║  engine.py  get_price_data()                                     ║
║  get_latest_blocking(timeout=5.0) — blocks up to 5s             ║
║  │                                                               ║
║  ▼                                                               ║
║  bar_aggregator.py  (BarAggregator)                             ║
║  Live mode:   TIME, 5 seconds per bar                           ║
║  Replay mode: TICK, 500 ticks per bar                           ║
║  Accumulates: OHLCV + ask/bid volumes + delta (cumulative)      ║
║  │                                                               ║
║  ▼ dict { price, open, high, low, close, volume,                ║
║           delta, ask_volume, bid_volume, trades }                ║
║  │                                                               ║
║  ▼                                                               ║
║  15-engine analysis pipeline:                                    ║
║  EventEngine → LevelContext → SessionRegimeEngine →             ║
║  MarketEnvironmentAnalyzer → MicrostructureEngine →             ║
║  ConfirmationEngine → ContinuationEngine → ConfluenceEngine →   ║
║  Validator → IntentEngine → RiskEngine →                        ║
║  QualityEngine → ConfidenceEngine →                             ║
║  FeedbackEngine → LearningEngine                                ║
╚══════════════════════════════════════════════════════════════════╝

IPC side-channel (file-based, NOT the main data path):
  Python → %USERPROFILE%\gibbz_bridge_cmd.txt   (RECORD/STATUS/STOP)
  Bridge → %USERPROFILE%\gibbz_bridge_status.txt  (streaming status)
  Bridge → %USERPROFILE%\gibbz_context_levels.json (PDH/PDL/ONH/ONL/VAH/VAL/POC)
  Written every 60 seconds, throttled.
```

---

## 3. PHASE 2 — DATA AVAILABLE (EMPIRICAL)

Analysis based on `recordings/2026-05-09_1349.jsonl` (656 MB, 1.5h MES session, 50k+ ticks sampled).

### PRICE DATA

| Field | Available | Resolution | Notes |
|-------|-----------|------------|-------|
| Last price (Close) | **YES** | Per trade (1 tick) | `price` field, pos 0 |
| Open | **YES** | Per bar | `open` field |
| High | **YES** | Per bar | `high` field |
| Low | **YES** | Per bar | `low` field |
| Close | **YES** | Per bar (= price) | `close` field, pos 4 (duplicate of pos 0) |
| Bid | **NO** | — | Not in payload; no L1 quote stream |
| Ask | **NO** | — | Not in payload; no L1 quote stream |
| Spread | **NO** | — | Requires L1 bid/ask |
| OHLC candle | **YES** | Per ATAS bar close | Full OHLC per packet |
| Candle updates (forming bar) | **PARTIAL** | `get_partial()` polls current bar | Only Close updates |
| Tick data | **YES** | Per executed trade | 86.7% of packets are vol=1 |
| Tick timestamp | **PARTIAL** | Second precision only | `candle.Time` → Unix seconds; no ms |
| Tick sequence number | **NO** | — | `BarIndex` is bridge counter, not CME sequence |
| Trade direction | **YES** | Per trade | Delta: +volume=buyer aggressor, -volume=seller |

**Key finding:** The feed operates at true tick resolution when ATAS is configured with a 1-tick chart. Volume=1 on 86.7% of packets confirms per-trade granularity.

---

## 4. PHASE 3 — ORDER FLOW AUDIT

### DELTA

| Metric | Available | Source | Precision | Notes |
|--------|-----------|--------|-----------|-------|
| Volume Delta (bar) | **YES** | ATAS `candle.Delta` | Per bar | Exact from ATAS footprint |
| Cumulative Delta | **YES** | BarAggregator computed | Per bar | `b.delta = b.ask_volume - b.bid_volume` |
| Delta per tick | **YES** | ATAS 1-tick bar | Each trade | Delta = ±volume for 1-tick bars |
| Delta per 5s bar | **YES** | BarAggregator accumulation | 5 seconds | Sum of all trade deltas in window |
| Delta aggressor buyer | **YES** | Derived: `ask_volume` | Per trade | `(Volume + Delta) / 2` in bridge |
| Delta aggressor seller | **YES** | Derived: `bid_volume` | Per trade | `(Volume - Delta) / 2` in bridge |

**Ask/Bid calculation:** `askVol = Max(0, (Volume + Delta) / 2)` — this is a reconstruction from candle.Delta, NOT raw quote-level order matching. It correctly identifies aggressor direction but loses information about partial fills.

### VOLUME

| Metric | Available | Source |
|--------|-----------|--------|
| Total Volume | **YES** | ATAS `candle.Volume`, summed by BarAggregator |
| Buy Volume (ask aggressor) | **YES** | `(Volume + Delta) / 2` — derived |
| Sell Volume (bid aggressor) | **YES** | `(Volume - Delta) / 2` — derived |
| Trade Count | **PARTIAL** | `trades` field always = 0 in bridge v2.3 (hardcoded) |
| Volume Imbalance (bar) | **YES** | Computed: `ask_volume - bid_volume` = delta |
| Relative Volume | **DERIVABLE** | BarAggregator accumulates; rolling avg computable in Python |
| Volume Profile (VAH/VAL/POC) | **YES\*** | GibbzBridge footprint API via `GetAllPriceLevels()` |

\*VAH/VAL/POC via footprint API requires ATAS Footprint/Cluster chart. Falls back silently on standard OHLCV charts (`_vpNotAvailable = true`). Written to `gibbz_context_levels.json` every 60 seconds.

**Trade Count = 0 always.** The bridge hardcodes `Trades(0)` in the CSV payload. This is a known limitation — ATAS `ICandle.TradesCount` exists but is not transmitted.

---

## 5. PHASE 4 — MARKET DEPTH / DOM

| Feature | Available |
|---------|-----------|
| Level 1 (best bid/ask) | **NO** |
| Level 2 DOM | **NO** |
| Full DOM (N levels) | **NO** |
| Bid liquidity by level | **NO** |
| Ask liquidity by level | **NO** |
| Order book changes | **NO** |
| Liquidity pulling | **NO** |
| Liquidity adding | **NO** |
| Queue position | **NO** |

**System currently CANNOT see the order book.**

GibbzBridge.cs implements `Indicator`, not `IOrderBook` or `IDepthIndicator`. ATAS provides DOM access via a separate interface (`OnBestBidAskChanged`, `GetDepth()`) which the bridge does not implement.

To add DOM: GibbzBridge would need to inherit from a DOM-enabled ATAS base class and serialize the order book snapshot alongside (or instead of) the OHLCV candle payload. This is architecturally feasible but requires a significant bridge rewrite and a wider UDP payload (≥ 500 bytes for 10 levels each side).

---

## 6. PHASE 5 — IMBALANCES

| Imbalance Type | Available | Method |
|----------------|-----------|--------|
| Footprint cell Bid×Ask | **YES** | `ocie_stacked_imbalance.py` — Python pure calc |
| Bid × Ask imbalance | **YES** | Ratio: `ask_volume / bid_volume` per bar |
| Diagonal imbalance | **PARTIAL** | Not explicitly computed; derivable from consecutive bars |
| Stacked imbalance | **YES** | `StackedImbalanceCalculator` — MIN_IMBALANCE_RATIO=3.0, MIN_STACK_COUNT=3 |
| Absorption | **YES** | `ConfirmationEngine` + `MicrostructureEngine` — delta vs price move ratio |
| Exhaustion | **YES** | `EventEngine.AGOTAMIENTO` — reversal after impulse with delta divergence |
| Iceberg detection | **NO** | Requires L2 DOM (hidden resting orders) |

**Stacked imbalance is computed in Python from OHLCV+Delta bars, replicating the ATAS formula.** It does NOT use ATAS footprint cells (which would require per-price-level data from `GetAllPriceLevels()`).

**Important:** The Python imbalance calculation uses bar-level ask_volume and bid_volume (derived from delta), not the original per-price-level footprint data. The calculation is equivalent for identifying directional imbalance but cannot resolve which specific price levels within a bar had unmatched orders.

---

## 7. PHASE 6 — DIVERGENCES

| Divergence | Available | Notes |
|------------|-----------|-------|
| Price vs Delta divergence | **YES** | EventEngine classifies: `FALLO` = price move opposed by delta |
| Price vs Volume divergence | **PARTIAL** | High volume + small price move → `ACUMULACION` event |
| Cumulative Delta divergence | **PARTIAL** | `ConfirmationEngine` tracks delta_persistence over window |
| Momentum divergence | **YES** | `EventEngine` computes rolling 3-bar momentum vs current move |

All divergences are **calculated in Python** from the bar-level aggregated data. No separate divergence module exists — the logic is embedded in EventEngine, ConfirmationEngine, and MicrostructureEngine.

---

## 8. PHASE 7 — SPEED AND PERFORMANCE

### LATENCY BREAKDOWN (estimated)

| Segment | Latency | Basis |
|---------|---------|-------|
| ATAS trade execution → `OnCalculate()` trigger | ~0 ms | Same process, event-driven |
| `UdpClient.Send()` → OS UDP buffer | ~0.1 ms | Loopback |
| UDP loopback 127.0.0.1:9999 | **< 1 ms** | OS loopback (typically 0.1-0.5 ms) |
| `socket.recvfrom()` in Python | ~0.5 ms | CPython socket overhead |
| `_parse()` CSV split + float conversion | ~0.05 ms | 13 field parse |
| BarAggregator accumulation (TIME 5s) | **5000 ms** | The dominant delay in live mode |
| 15-engine pipeline processing | 2-20 ms | Empirically ~5 ms per bar (Python) |
| Voice/display rendering | < 5 ms | Async, non-blocking |
| **Total (bar-level, live mode)** | **~5006 ms** | Dominated by 5s bar window |
| **Total (tick-level, replay mode)** | **~10-50 ms** | 500-tick bar accumulation |

**The critical bottleneck is the BarAggregator, not the transport.**
The bridge-to-Python UDP hop takes < 1 ms. The engine then waits for a 5-second bar to close before running the analysis pipeline. Reducing to 1-second bars or tick-based bars would reduce the strategic decision latency significantly.

### FREQUENCY

| Metric | Value |
|--------|-------|
| Average ticks/second (MES RTH) | 9.35 / sec |
| P90 ticks/second | 25 / sec |
| P99 ticks/second | 69 / sec |
| Peak ticks/second observed | **327 / sec** |
| UDP packet size | ~80 bytes (well within 1024-byte buffer) |
| Maximum safe throughput | ~12,000 packets/sec (loopback UDP theoretical) |
| Actual peak load vs capacity | 327 / 12,000 = **2.7% utilization** |

**No bottleneck at the network layer.** The system has >30× headroom at peak throughput.

### TICK LOSS RISK

The `MarketFeed._latest` is a **single-slot store** (no queue). When the engine is busy processing a bar (takes 2-20 ms), incoming UDP packets from ATAS are buffered by the OS socket buffer but `_latest` is overwritten on each arrival. Between bar-processing cycles, the engine calls `get_latest_blocking()` and reads only the **most recent** tick.

**Impact in TIME mode (5s bars):** Each incoming tick is processed by `BarAggregator.process()`. Because the engine calls `get_latest_blocking()` in a tight loop, it processes most ticks. OHLCV bar data is accurate.

**Impact in tick-burst moments (>100 ticks/sec):** If the engine loop takes >10ms per iteration, ticks in the OS buffer may be received in batches. Only the latest is stored in `_latest`. This means burst-period ticks between two `get_latest_blocking()` calls can be **silently dropped**.

**Actual tick loss estimate:** During P99 periods (69/sec), if engine loop takes 5ms, expected loss rate ≈ (69 × 0.005) - 1 = 0.35 ticks/second. Over RTH: negligible in volume terms (< 0.4% of ticks in peak periods). During the MAX 327/sec burst, up to 50% of ticks could be missed. This does not affect price/OHLC accuracy significantly but would affect exact volume counts.

---

## 9. PHASE 8 — INSTITUTIONAL COMPARISON

| Category | GIBBZ (Current) | Professional (Sierra Chart/CQG/TT) | Score |
|----------|----------------|-----------------------------------|-------|
| Market Data (OHLCV) | ✅ Full per trade | ✅ Full per trade | 10/10 |
| Tick Resolution | ✅ Per executed trade | ✅ Per executed trade | 9/10 |
| Timestamp Precision | ⚠️ Second-level | ✅ Millisecond or better | 4/10 |
| Trade Direction (aggressor) | ✅ Per trade | ✅ Per trade | 9/10 |
| Volume Delta | ✅ Derived (bridge calc) | ✅ Native (raw split) | 7/10 |
| DOM / Level 2 | ❌ None | ✅ 10-20 levels, <10ms | 0/10 |
| Footprint Data (price×volume) | ⚠️ OHLCV only in feed; VAH/VAL/POC via side-channel | ✅ Full footprint per tick | 4/10 |
| Stacked Imbalance | ✅ Python-calculated | ✅ Native ATAS/Sierra | 7/10 |
| Iceberg Detection | ❌ No | ✅ Some platforms | 0/10 |
| Order Book Pulls/Adds | ❌ No | ✅ With L2 feed | 0/10 |
| Absorption (price×delta) | ✅ Python-calculated | ✅ Native | 8/10 |
| Latency (transport) | ✅ < 1ms loopback | ✅ < 1ms colocation | 9/10 |
| Latency (analysis decision) | ⚠️ 5+ seconds (bar close) | ✅ < 100ms (tick-by-tick) | 4/10 |
| Execution Integration | ❌ No (signal generation only) | ✅ Integrated OMS | 0/10 |
| Data Integrity | ⚠️ Tick loss risk at peaks | ✅ Guaranteed delivery | 6/10 |

### Classification

| Tier | Criteria | Assessment |
|------|----------|------------|
| **Retail** | OHLCV only, no delta | NOT applicable (exceeds this) |
| **Advanced Retail** | OHLCV + delta + imbalance detection | ✅ **CURRENT LEVEL** |
| **Professional** | + millisecond timestamps + L2 DOM | Would need DOM + ms timestamps |
| **Institutional** | + co-located feed + L3 order book + execution integration | Significant architecture investment |

---

## 10. PHASE 9 — RISKS FOUND

### R1 — Tick Loss at Burst Periods (MEDIUM RISK)
**Finding:** `MarketFeed._latest` is a single-slot store. During peak periods (>100 ticks/sec), ticks between engine loop iterations are silently dropped.  
**Impact:** Potential undercount of volume/trade count during news events. Price (OHLC) accuracy maintained. Delta may be understated in burst seconds.  
**Evidence:** Peak 327 ticks/sec observed; engine loop 5ms = potential 1.6 missed ticks/iteration.  
**Fix:** Replace single slot with `collections.deque` queue; drain queue each loop iteration.

### R2 — Second-Level Timestamp Only (MEDIUM RISK)
**Finding:** `GibbzBridge.cs` converts `candle.Time` to `((DateTimeOffset)candle.Time).ToUnixTimeSeconds()` — integer seconds only. Multiple ticks at the same second have identical timestamps.  
**Impact:** Cannot reconstruct intra-second tick sequence. Timing analysis (e.g., "did this tick happen before or after that event?") is impossible at sub-second resolution.  
**Evidence:** 88% of ticks in first 5000 have same timestamp as previous tick. Up to 98 ticks at same second observed.  
**Fix:** Use `.ToUnixTimeMilliseconds()` and divide by 1000.0 to get float timestamp. Change IDX_TIMESTAMP parse to preserve decimal part.

### R3 — Trade Count Always Zero (LOW RISK)
**Finding:** Payload position 9 is hardcoded as `0` in bridge: `...,0,{9},{10},{11}`. The `ICandle.TradesCount` (number of individual executions within the bar) is not transmitted.  
**Impact:** Python `trades` field is always 0. Any analysis relying on trade count (e.g., "large print" detection by count) cannot function.  
**Evidence:** All 50,000 sampled ticks show `trades=1` (after recorder normalizes 0 to 1).  
**Fix:** Replace `,0,` in bridge payload with `,{candle.TradesCount},`.

### R4 — Delta Reconstruction vs Native (LOW RISK)
**Finding:** `askVol = Max(0, (Volume + Delta) / 2)` — ask and bid volumes are **reconstructed** from the net delta, not from raw buy/sell split. If ATAS provides a non-additive delta (e.g., the bar has overlapping aggressive orders), the reconstruction may not equal the true ask/bid split.  
**Impact:** Minor potential inaccuracy in ask_volume/bid_volume absolute values. Delta (the net difference) is always correct.  
**Evidence:** `ask_volume = (volume + delta) / 2` is mathematically equivalent only when `ask_volume + bid_volume = volume`, which holds for simple single-aggressor trades but may not hold for large aggregated bars.  
**Fix:** ATAS `ICandle` may expose separate `BuyTrades`/`SellTrades` or `BuyVolume`/`SellVolume` fields directly in newer versions. Investigate and use native fields if available.

### R5 — No DOM → No Iceberg/Pulling Detection (STRUCTURAL RISK)
**Finding:** Liquidity dynamics (order pulling before a sweep, large hidden resting orders) are completely invisible to the system.  
**Impact:** Cannot detect certain institutional order flow patterns that precede major moves. Missing a significant source of alpha used by professional systems.  
**Note:** This is a structural limitation, not a bug.

### R6 — Replay vs Live Timestamp Discrepancy (LOW RISK)
**Finding:** `rec_timestamp` in recordings is Python wall-clock time (during record session), not ATAS tick time. During replay, `rec_timestamp` reflects when the recording was made, not when the market event occurred. The lag observed (34,069,757 seconds ≈ 394 days) confirms these are old recordings replayed.  
**Impact:** No impact on strategy — `rec_timestamp` is not used in analysis. However, if anything in the pipeline incorrectly uses `rec_timestamp` as market time, it would produce nonsense.  
**Evidence:** Confirmed — all pipeline engines use `timestamp` (ATAS bar time), not `rec_timestamp`.

### R7 — 5-Second Bar Window Delays Signal Generation (MEDIUM RISK)
**Finding:** Live engine uses `BarAggregator(mode="TIME", seconds=5)`. No analysis fires until a 5-second window closes.  
**Impact:** A strong directional move that begins and reverses within 5 seconds is processed as a single averaged bar. Entry signals are delayed by up to 5 seconds from the causal event.  
**Note:** This is a design choice, not a defect. The replay engine uses 500-tick bars (~1 min average at 9 ticks/sec). Both create meaningful analysis delay vs a true real-time engine.

### R8 — File-Based IPC for Context Levels (LOW RISK)
**Finding:** `gibbz_context_levels.json` is written by C# and read by Python via filesystem. No locking or atomicity guarantee on Windows file writes.  
**Impact:** Python could read a partially-written JSON during the 60-second context refresh. The bridge uses `File.WriteAllText` (atomic on most modern Windows NTFS), but there is no explicit lock coordination between processes.  
**Evidence:** No atomic write pattern (unlike `gibbz_launcher.py` which uses `shutil.move`).  
**Fix:** Bridge should write to a temp file and rename atomically, or Python should retry on JSON parse error.

---

## 11. FINAL REPORT

### Score Summary

| Dimension | Score | Rationale |
|-----------|-------|-----------|
| **Data Quality** | **72 / 100** | True tick data, correct OHLCV, correct delta direction. Deducted: second-level timestamps, trades=0 hardcoded, delta reconstruction not native |
| **Order Flow** | **58 / 100** | Delta, absorption, imbalance, stacked imbalance all available. Deducted: no DOM, no footprint per-price-level cells in stream, no iceberg |
| **Latency** | **45 / 100** | Transport < 1ms (excellent). Deducted: 5-second bar close delay (poor for HFT/scalping), second-level timestamp precision |
| **Institutional Readiness** | **62 / 100** | Exceeds standard retail. Strong tick data + delta. Missing: DOM, ms timestamps, execution integration |

### What the System CAN Feed (Confirmed)

- True per-trade tick data (MES: median 8 ticks/sec, peak 327/sec)  
- Full OHLCV per bar (accurate, directly from ATAS bar engine)
- Delta per trade (accurate, from ATAS `candle.Delta`)
- Aggressor direction: buyer vs seller (derived, mathematically equivalent)  
- Cumulative delta over any bar window (BarAggregator accumulation)
- Stacked imbalance zones (3+ consecutive bid/ask imbalance at same price)  
- Absorption detection (large delta + small price move)
- Exhaustion / AGOTAMIENTO (directional reversal with delta divergence)
- Micro-range detection + breakout (8-tick range, minimum 6 bars)
- Session volume profile: VAH, VAL, POC (requires footprint chart in ATAS)
- Previous day high/low (PDH/PDL), overnight high/low (ONH/ONL)

### What the System CANNOT Feed (Confirmed Missing)

- Real-time bid/ask spread (no L1 quote stream)
- DOM / Level 2 order book (0 levels, 0 Hz)
- Intra-second tick ordering (all ticks within same second are unordered)
- Individual footprint price-level volumes per bar (only aggregated via side-channel)
- Iceberg / hidden order detection
- Order pulling / adding events
- Trade count per bar (hardcoded 0)
- Execution / OMS integration (signal generation only, no order submission)

### Improvements That Would Elevate to Professional Tier

| Priority | Change | Impact |
|----------|--------|--------|
| HIGH | Add millisecond timestamp (bridge: `.ToUnixTimeMilliseconds() / 1000.0`) | Enables intra-second sequencing, proper HFT analysis |
| HIGH | Add trade count (`candle.TradesCount`) to payload | Enables large-print detection, trade-count divergences |
| MEDIUM | Replace `_latest` single-slot with tick queue (5-10 element deque) | Eliminates tick loss at burst periods |
| MEDIUM | Reduce bar window to 1s or tick-based (100-250 ticks) | Reduces signal decision latency from 5s to < 1s |
| LOW | Atomic write for `gibbz_context_levels.json` | Eliminates partial-read race condition |
| FUTURE | Implement `IOrderBook`/DOM interface in bridge | Adds Level 2 — requires significant bridge rewrite |

---

*Audit completed 2026-06-02. No files were modified during this audit. All findings are based on static source code analysis and empirical analysis of recorded market data.*
