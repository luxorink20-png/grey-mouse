"""
GIBBZ — export_shadow_expectancy.py
Corre BacktestEngine y guarda shadow readiness + expectancy a JSON.
"""

import json
import os
import sys
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from backtest_engine import (
    BacktestEngine,
    _calc_shadow_readiness,
    _calc_predictive_stability,
    _calc_outlier_adjusted_expectancy,
    TARGET_TRADES,
)

OUTPUT_FILE = os.path.join("logs", "shadow_expectancy.json")


def run_and_export():
    print("Running BacktestEngine simulation...", flush=True)

    engine = BacktestEngine()

    import io, contextlib
    # Run silently (suppress the heavy ANSI output)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        engine.run()

    trades = engine.trades
    if not trades:
        print("ERROR: no trades generated.")
        return

    wins   = [t for t in trades if t.result == "WIN"]
    losses = [t for t in trades if t.result == "LOSS"]
    tos    = [t for t in trades if t.result == "TIMEOUT"]
    total  = len(trades)

    avg_win  = round(sum(t.pnl_pts for t in wins)   / len(wins),   2) if wins   else 0.0
    avg_loss = round(sum(t.pnl_pts for t in losses) / len(losses), 2) if losses else 0.0
    wr       = round(len(wins) / total * 100, 1)
    exp      = round((wr/100 * avg_win) + ((1 - wr/100) * avg_loss), 2)
    total_pnl= round(sum(t.pnl_pts for t in trades), 2)

    adj_exp, outlier_msgs = _calc_outlier_adjusted_expectancy(trades, avg_win, avg_loss)
    stab, stab_stats      = _calc_predictive_stability(trades, wins, losses)
    shadow_score, shadow_stats, shadow_ready = _calc_shadow_readiness(
        stab, trades, avg_win, total_pnl
    )

    # Per-regime stats
    def group_stats(field):
        grp = defaultdict(lambda: {"t": 0, "w": 0, "pnl": 0.0})
        for t in trades:
            k = getattr(t, field, "UNKNOWN") or "UNKNOWN"
            grp[k]["t"] += 1
            if t.result == "WIN": grp[k]["w"] += 1
            grp[k]["pnl"] += t.pnl_pts
        return {
            k: {
                "n":       d["t"],
                "wr":      round(d["w"] / d["t"] * 100, 1) if d["t"] else 0,
                "avg_pnl": round(d["pnl"] / d["t"], 2)     if d["t"] else 0,
            }
            for k, d in grp.items()
        }

    result = {
        "generated_at":   datetime.now().isoformat(),
        "total_trades":   total,
        "wins":           len(wins),
        "losses":         len(losses),
        "timeouts":       len(tos),
        "win_rate_pct":   wr,
        "avg_win_pts":    avg_win,
        "avg_loss_pts":   avg_loss,
        "expectancy_pts": exp,
        "expectancy_adj": adj_exp,
        "total_pnl_pts":  total_pnl,
        "outliers_detected": len(outlier_msgs),
        "shadow_readiness": {
            "score":          round(shadow_score, 3),
            "ready":          shadow_ready,
            "threshold":      0.80,
            "friction_score": round(shadow_stats.get("friction_score",   0), 3),
            "regime_purity":  round(shadow_stats.get("regime_purity",    0), 3),
            "outlier_stab":   round(shadow_stats.get("outlier_stability", 0), 3),
            "consistency":    round(shadow_stats.get("stability",        0), 3),
        },
        "predictive_stability": {
            "score":        round(stab, 3),
            "consistency":  round(stab_stats.get("consistency",  0), 3),
            "regime_align": round(stab_stats.get("regime_align", 0), 3),
            "edge_balance": round(stab_stats.get("edge_balance", 0), 3),
            "weak_pnl_pct": stab_stats.get("weak_pnl_pct", 0),
            "score_diff":   stab_stats.get("score_diff",   0),
        },
        "by_regime":    group_stats("session_regime"),
        "by_breakout":  group_stats("breakout_type"),
        "by_narrative": group_stats("narrative"),
    }

    os.makedirs("logs", exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)

    print(f"Saved to: {OUTPUT_FILE}")
    print(f"  Trades          : {total}")
    print(f"  Win rate        : {wr}%")
    print(f"  Expectancy      : {exp} pts/trade")
    print(f"  Expectancy (adj): {adj_exp} pts/trade")
    print(f"  Shadow score    : {round(shadow_score, 3)}  {'OK READY' if shadow_ready else 'NOT READY'}")
    print(f"    friction={shadow_stats.get('friction_score',0):.3f}  "
          f"regime_purity={shadow_stats.get('regime_purity',0):.3f}  "
          f"outlier_stab={shadow_stats.get('outlier_stability',0):.3f}  "
          f"consistency={shadow_stats.get('stability',0):.3f}")
    print(f"  Stability       : {round(stab, 3)}")


if __name__ == "__main__":
    run_and_export()
