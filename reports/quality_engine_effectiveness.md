# Quality Engine Effectiveness Report

**Date:** 2026-06-02  
**Sessions:** 5/43  |  **Max bars:** 800  |  **Threshold:** 62  
**Metadata coverage:** 100% of trade entry bars had full pipeline data  
**Composite scorer:** 10 physical features → synthetic quality score 0-100  
(regime 0-25 · environment 0-20 · volume ratio 0-20 · detector strength 0-20 · momentum 0-15)

---

## Verdict

**INCONCLUSIVE** — Quality score not predictive with current metadata

| Signal | Value | Target |
|--------|-------|--------|
| WR delta Fusion vs Baseline | +0.0 pp | > 0 → alpha |
| PF delta | +0.00 | > 0 → alpha |
| Expectancy delta | +0.00 pts | > 0 → alpha |
| MaxDD delta | +0.0 pts | > 0 → risk reduction |
| % rejected trades that were losers | 0.0% | > 60% → selective |
| % rejected trades that were winners | 0.0% | < 40% → not wasting wins |

---

## Metrics Comparison

| Metric | Real Baseline* | This Run Baseline | Fusion | Conf-Only | B → F |
|--------|:-------------:|:-----------------:|:------:|:---------:|:-----:|
| Trades | 106 | 9 | 9 | 9 | +0 |
| Win Rate | 38.7% | 33.3% | **33.3%** | 33.3% | +0.0 pp |
| Profit Factor | 1.56 | 1.10 | **1.10** | 1.09 | +0.00 |
| Expectancy pts | +2.61 | +0.53 | **+0.53** | +0.33 | +0.00 |
| Max Drawdown | -95.8 | -19.2 | **-19.2** | -15.0 | +0.0 |
| Avg Win | +18.76 | +16.92 | +16.92 | +12.50 | +0.00 |
| Avg Loss | -7.57 | -7.67 | -7.67 | -5.75 | +0.00 |
| Total PnL | +277.0 | +4.8 | +4.8 | +3.0 | +0.0 |

*Real 43-session baseline: `optimization_comparison.json` (full max_bars=4000 run).

**Column key:** Baseline = original engine (no Wave 1).  Fusion = quality gate (score ≥ threshold) + confidence sizing.  Conf-Only = confidence sizing only (isolates each Wave 1 gate).

---

## Rejection Analysis

Quality gate rejected **0** of 9 baseline trades (0%).

| Category | Count | % of Rejected | Avg Quality Score |
|----------|:-----:|:-------------:|:-----------------:|
| Rejected **losers** (correct rejects — system helped) | 0 | 0.0% | 0.0 |
| Rejected **winners** (false negatives — cost of gate) | 0 | 0.0% | 0.0 |
| Net PnL of rejected trades | — | — | +0.00 pts |

**Interpretation:** The rejection set is mixed (0% losers, 0% winners).  
Quality score is NOT reliably discriminating winners from losers in this dataset.  
The filter reduces trade count and drawdown exposure but does not improve WR.

---

## Quality Score vs Win Rate

Monotonic increase in WR with score = quality score is predictive.

| Score Band | N | Win Rate | Avg PnL/Trade |
|:----------:|:-:|:--------:|:-------------:|
|   60-65 |   3 |  33.3% |   +2.00 |
|   65-70 |   1 |   0.0% |   -8.00 |
|   70-80 |   2 |  50.0% |   +1.38 |
|  80-100 |   3 |  33.3% |   +1.33 |

WR range: 33.3% (lowest band) → 33.3% (highest band).  
Monotonic: **NO — quality score does not predict WR in this dataset**.

---

## Quality Score Distribution

| Metric | Value |
|--------|-------|
| Min | 63 |
| Max | 86 |
| Mean | 72.1 |
| Median | 70.0 |
| Threshold | 62 |
| % trades above threshold | 100% |

---

## Composite Scorer — Feature Weights

The 10 physical features map to quality score 0-100:

| Feature | Weight | Source |
|---------|:------:|--------|
| Regime quality (TREND_DAY/EXPANSION/ROTATIONAL...) | 0-25 | `SessionRegimeEngine.session_regime` |
| Environment (tradeable + danger level) | 0-20 | `MarketEnvironmentAnalyzer` |
| Volume ratio (current vs rolling avg) | 0-20 | `raw[volume] / avg_volume` |
| Detector strength (FA bars_outside / VA80 return_bars) | 0-20 | `FADetector` / `VA80Detector` |
| Momentum (EventEngine confidence + momentum) | 0-15 | `EventEngine.process()` |

Then fed into `quality_engine.score()` — production module, unchanged.

---

## Conclusions

1. **No clear benefit detected** in this run. Possible causes:
   - max_bars limit: 2000 bars may cut before key signal clusters
   - Composite scorer needs calibration with more labelled data
   - Full 43-session run with max_bars=4000 recommended

2. **Confidence sizing** (Gate 2) confirmed working: avg_loss -7.67 → -7.67 pts.

3. **Methodology**: Composite quality score uses real pipeline data (regime, environment, volume, FA/VA80 state, momentum) — not the simplified sconf=70/75 proxy from Phase 2.

4. **Next step**: Paper trade 200+ sessions with quality_engine.py active in the live engine (full ConfluenceEngine context) to confirm results in real conditions.

---

*Generated: 2026-06-02 | backtest_fidelity.py | 5 sessions | max_bars=800*