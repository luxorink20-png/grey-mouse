# Optimization Research

Exploratory analysis artifacts. **Do not implement without validation.**

## Protocol

1. Any scenario here is **in-sample only** — same 43 sessions used for backtest.
2. Before implementing a recommendation: run `full_backtest.py` with the change
   on the full 43-session pool and verify PF ≥ 1.7, MaxDD ≤ 25 pts out-of-sample.
3. Commit the implementation as a separate branch, not directly to master.

## Files

| File | Description |
|------|-------------|
| `optimization_comparison.json` | 10 parameter scenarios vs baseline (106 trades). Top pick: SL Tight 6.0 pts. **Projection only — max_dd unchanged across all scenarios = not a real backtest.** |
| `va80_optimization_simulation.json` | VA80 setup expansion scenarios (conservative/moderate/aggressive). VA80 = 38% PnL from 17 trades — high concentration risk. |

## Warning

The `max_dd` field is identical across all scenarios in `optimization_comparison.json`.
This confirms these are mathematical projections, not real backtests.
Run `full_backtest.py` before trusting any recommendation here.
