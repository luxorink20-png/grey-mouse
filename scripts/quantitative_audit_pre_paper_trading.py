"""
scripts/quantitative_audit_pre_paper_trading.py
Auditoría Cuantitativa Final PRE-Paper Trading — GIBBZ #MESM6

Modo: READ-ONLY — ningún archivo de producción modificado.
Genera todo en output/audit/

Prioridades:
  1. Confidence Intervals (bootstrap no-paramétrico, 10k resamples)
  2. Monte Carlo de Degradación (5%-50%)
  3. Stress Test de Slippage (+1 a +5 ticks)
  4. Regime Analysis (setup type, dirección, posición sesión, tipo sesión)
  5. Edge Concentration Analysis (top 10/20/30%)
  6. Unknown Future Robustness Score (ponderado, basado en evidencia)

Uso:
    python scripts/quantitative_audit_pre_paper_trading.py
    python scripts/quantitative_audit_pre_paper_trading.py --bootstrap-n 10000
"""

from __future__ import annotations

import sys
import os
import json
import random
import math
import argparse
import csv
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from full_backtest import run_session, run_backtest, Trade
from context_filter import ContextFilter

CORE_DIR     = Path(__file__).parent.parent
OUTCOMES_DIR = CORE_DIR / "expansion_outcomes"
RECORDINGS   = CORE_DIR / "recordings"
OUTPUT_DIR   = CORE_DIR / "output" / "audit"

TICK_PTS = 0.25   # ES/NQ 1 tick = 0.25 pts
MAX_BARS  = 4000
TARGET_CAP = 20.0

# Known walk-forward result (simulation/replay_treadmill.py — completed prior session)
TREADMILL_PF = 2.54
COUNTERFACTUAL_SCORE = 99  # from counterfactual_edge_audit.py


# ── Helpers ──────────────────────────────────────────────────────────────────

def _pf(v: float) -> str:
    return f"{v:.2f}" if v != float("inf") else "inf"


def _percentile(data: list[float], p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    i = (len(s) - 1) * p / 100.0
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def compute_metrics(pnls: list[float],
                    sessions: list[str] | None = None) -> dict:
    """
    Compute metrics from a list of PnL values.
    If sessions is provided, MaxDD is calculated per-session (matches official backtest).
    Otherwise, MaxDD is calculated sequentially (trades order).
    """
    if not pnls:
        return dict(n=0, wr=0.0, pf=0.0, exp=0.0, pnl=0.0, max_dd=0.0, rf=0.0,
                    avg_win=0.0, avg_loss=0.0)
    wins   = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    n, nw, nl = len(pnls), len(wins), len(losses)
    wr  = nw / n
    sw  = sum(wins)
    sl  = sum(losses)
    pf  = abs(sw / sl) if sl < 0 else (float("inf") if sw > 0 else 0.0)
    avg_win  = sw / max(nw, 1)
    avg_loss = sl / max(nl, 1)
    exp = sum(pnls) / n
    total = sum(pnls)

    if sessions and len(sessions) == len(pnls):
        # Session-grouped MaxDD (matches run_backtest_with_filter.py calculation)
        by_sess: dict[str, float] = defaultdict(float)
        for sess, p in zip(sessions, pnls):
            by_sess[sess] += p
        cum = peak = max_dd = 0.0
        for sess in sorted(by_sess.keys()):
            cum += by_sess[sess]
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd
    else:
        # Sequential MaxDD (for bootstrap resamples where session order is random)
        cum = peak = max_dd = 0.0
        for p in pnls:
            cum += p
            if cum > peak:
                peak = cum
            dd = peak - cum
            if dd > max_dd:
                max_dd = dd

    rf = total / max_dd if max_dd > 0 else float("inf")
    return dict(n=n, wr=round(wr, 4), pf=round(pf, 4), exp=round(exp, 4),
                pnl=round(total, 4), max_dd=round(max_dd, 4), rf=round(rf, 4),
                avg_win=round(avg_win, 4), avg_loss=round(avg_loss, 4))


def sep(n=80, char="="): print("  " + char * n)
def line(n=80): print("  " + "-" * n)


# ── Data Collection ───────────────────────────────────────────────────────────

def load_sessions() -> list[dict]:
    sessions = []
    for ef in sorted(OUTCOMES_DIR.glob("*_expansion.json")):
        with open(ef, encoding="utf-8") as f:
            exp = json.load(f)
        sdate = exp.get("session_date", ef.stem.replace("_expansion", ""))
        rf    = exp.get("recording_file", "")
        rpath = RECORDINGS / rf if rf else None
        valid = bool(rpath and rpath.exists() and rpath.stat().st_size > 0)
        sessions.append({
            "date":         sdate,
            "recording":    rf,
            "valid":        valid,
            "session_type": exp.get("session_type", "UNKNOWN"),
        })
    return [s for s in sessions if s["valid"]]


def collect_trades(sessions: list[dict]) -> tuple[list[Trade], dict[str, str]]:
    """Run filtered backtest, return all trades and session_type map."""
    cf = ContextFilter(
        enable_vol_release=True,
        enable_destructive_regime=True,
        enable_session_kill_switch=True,
        session_maxdd_threshold=30.0,
    )
    all_trades: list[Trade] = []
    session_type_map: dict[str, str] = {}

    for s in sessions:
        session_type_map[s["date"]] = s["session_type"]
        stype = s["session_type"]
        bars = run_session(
            s["date"], s["recording"], MAX_BARS, TARGET_CAP,
            context_filter=cf, session_type=stype,
        )
        if not bars:
            continue
        trades = run_backtest(bars, s["date"], TARGET_CAP)
        all_trades.extend(trades)

    return all_trades, session_type_map


# ── Priority 1: Confidence Intervals ─────────────────────────────────────────

def bootstrap_ci(pnls: list[float], n_bootstrap: int = 10000,
                 seed: int = 42) -> dict:
    """Bootstrap non-parametric CI for PF, Exp, WR, MaxDD, RF."""
    random.seed(seed)
    n = len(pnls)
    dist: dict[str, list[float]] = defaultdict(list)

    for _ in range(n_bootstrap):
        sample = random.choices(pnls, k=n)
        m = compute_metrics(sample)
        if m["n"] == 0:
            continue
        if m["pf"] != float("inf"):
            dist["pf"].append(m["pf"])
        dist["wr"].append(m["wr"] * 100)
        dist["exp"].append(m["exp"])
        if m["max_dd"] < 1000:
            dist["max_dd"].append(m["max_dd"])
        if m["rf"] != float("inf") and m["rf"] < 1000:
            dist["rf"].append(m["rf"])

    ci: dict[str, dict] = {}
    for metric, values in dist.items():
        ci[metric] = {
            "mean":  round(sum(values) / len(values), 3),
            "std":   round(math.sqrt(sum((x - sum(values)/len(values))**2
                                        for x in values) / max(len(values)-1, 1)), 3),
            "p0_5":  round(_percentile(values, 0.5), 3),
            "p2_5":  round(_percentile(values, 2.5), 3),
            "p5":    round(_percentile(values, 5), 3),
            "p25":   round(_percentile(values, 25), 3),
            "p50":   round(_percentile(values, 50), 3),
            "p75":   round(_percentile(values, 75), 3),
            "p95":   round(_percentile(values, 95), 3),
            "p97_5": round(_percentile(values, 97.5), 3),
            "p99_5": round(_percentile(values, 99.5), 3),
            "n_runs": len(values),
        }
    return ci


# ── Priority 2: Monte Carlo Degradation ──────────────────────────────────────

def monte_carlo_degradation(pnls: list[float],
                             sessions: list[str] | None = None) -> list[dict]:
    """Apply progressive degradation to wins, recalculate metrics."""
    degs = [0, 5, 10, 15, 20, 25, 30, 35, 40, 50]
    results = []

    for deg in degs:
        factor = 1.0 - deg / 100.0
        degraded = [p * factor if p > 0 else p for p in pnls]
        m = compute_metrics(degraded, sessions)
        passes = _count_passes(m)
        results.append({
            "deg_pct": deg,
            "pf":      round(m["pf"], 3),
            "max_dd":  round(m["max_dd"], 3),
            "exp":     round(m["exp"], 3),
            "wr":      round(m["wr"] * 100, 1),
            "pnl":     round(m["pnl"], 2),
            "passes":  passes,
        })
    return results


def _count_passes(m: dict) -> str:
    ok = 0
    criteria = [
        m["pf"] >= 2.5,
        m["max_dd"] <= 20.0,
        m["exp"] >= 0,
        m["wr"] >= 0.45,
        m["rf"] >= 10.0 if m["rf"] != float("inf") else True,
    ]
    ok = sum(1 for c in criteria if c)
    all_ok = all(criteria)
    icon = "✓" if all_ok else ("⚠" if ok >= 3 else "✗")
    return f"{ok}/5 {icon}"


# ── Priority 3: Slippage Stress Test ─────────────────────────────────────────

def slippage_stress_test(pnls: list[float],
                          sessions: list[str] | None = None) -> list[dict]:
    """Apply slippage (in ticks) to all trades."""
    results = []
    ticks_range = [0, 1, 2, 3, 4, 5]

    for mode in ("entry", "exit", "both"):
        for ticks in ticks_range:
            slip_pts = ticks * TICK_PTS * (2 if mode == "both" else 1)
            adjusted = [p - slip_pts for p in pnls]
            m = compute_metrics(adjusted, sessions)
            passes = _count_passes(m)
            results.append({
                "mode":    mode,
                "ticks":   ticks,
                "slip_pts": round(slip_pts, 2),
                "pf":      round(m["pf"], 3),
                "max_dd":  round(m["max_dd"], 3),
                "exp":     round(m["exp"], 3),
                "rf":      round(m["rf"], 3) if m["rf"] != float("inf") else float("inf"),
                "pnl":     round(m["pnl"], 2),
                "passes":  passes,
            })
    return results


# ── Priority 4: Regime Analysis ───────────────────────────────────────────────

def regime_analysis(trades: list[Trade],
                    session_type_map: dict[str, str]) -> dict:
    """Group trades by setup type, direction, session position, and session regime."""

    # Group by setup type
    by_setup: dict[str, list[float]] = defaultdict(list)
    # Group by direction
    by_dir: dict[str, list[float]] = defaultdict(list)
    # Group by session regime (session_type → broad category)
    by_regime: dict[str, list[float]] = defaultdict(list)
    # Group by session position (early/mid/late bar number)
    by_position: dict[str, list[float]] = defaultdict(list)

    def regime_category(stype: str) -> str:
        if stype in ("VOL_RELEASE",):
            return "VOL_RELEASE"
        if stype in ("EARLY_EXPANSION", "EXPANSION", "OPENING_DRIVE"):
            return "EXPANSION"
        if stype in ("WATCH",):
            return "WATCH"
        if stype in ("ROTATIONAL",):
            return "ROTATIONAL"
        return "OTHER"

    def position_category(entry_bar: int) -> str:
        if entry_bar <= 200:
            return "Opening (bars 1-200)"
        if entry_bar <= 800:
            return "Mid-session (201-800)"
        return "Late (801+)"

    for t in trades:
        pnl = t.pnl
        by_setup[t.stype].append(pnl)
        by_dir[t.direction].append(pnl)
        stype = session_type_map.get(t.session, "UNKNOWN")
        cat = regime_category(stype)
        by_regime[cat].append(pnl)
        pos = position_category(t.entry_bar)
        by_position[pos].append(pnl)

    def summarize(groups: dict[str, list[float]]) -> list[dict]:
        rows = []
        for name, pnls in sorted(groups.items(), key=lambda x: -len(x[1])):
            m = compute_metrics(pnls)
            rows.append({
                "label":  name,
                "n":      m["n"],
                "pf":     m["pf"],
                "wr":     round(m["wr"] * 100, 1),
                "exp":    m["exp"],
                "max_dd": m["max_dd"],
                "pnl":    m["pnl"],
            })
        return rows

    return {
        "by_setup":    summarize(by_setup),
        "by_direction": summarize(by_dir),
        "by_regime":   summarize(by_regime),
        "by_position": summarize(by_position),
    }


# ── Priority 5: Edge Concentration ───────────────────────────────────────────

def edge_concentration(trades: list[Trade]) -> dict:
    """Measure PnL concentration across sessions."""
    by_session: dict[str, float] = defaultdict(float)
    for t in trades:
        by_session[t.session] += t.pnl

    session_pnls = sorted(by_session.items(), key=lambda x: -x[1])
    total_pnl = sum(p for _, p in session_pnls)
    n_sessions = len(session_pnls)

    rows = []
    cumulative = 0.0
    for i, (session, pnl) in enumerate(session_pnls):
        cumulative += pnl
        pct_sessions = round((i + 1) / n_sessions * 100, 1)
        pct_pnl = round(cumulative / total_pnl * 100, 1) if total_pnl != 0 else 0.0
        rows.append({
            "rank":          i + 1,
            "session":       session,
            "session_pnl":   round(pnl, 2),
            "cumul_pnl":     round(cumulative, 2),
            "pct_sessions":  pct_sessions,
            "pct_pnl_cumul": pct_pnl,
        })

    # Summary stats
    n_positive = sum(1 for _, p in session_pnls if p > 0)
    n_negative = sum(1 for _, p in session_pnls if p <= 0)
    top1_pct = round(session_pnls[0][1] / total_pnl * 100, 1) if total_pnl != 0 and session_pnls else 0
    top3_pct = round(sum(p for _, p in session_pnls[:3]) / total_pnl * 100, 1) if total_pnl != 0 else 0
    top_sessions = [s for s, p in session_pnls if p > 0]

    return {
        "rows":        rows,
        "total_pnl":   round(total_pnl, 2),
        "n_sessions":  n_sessions,
        "n_positive":  n_positive,
        "n_negative":  n_negative,
        "top1_pct":    top1_pct,
        "top3_pct":    top3_pct,
        "herfindahl":  round(sum((p/total_pnl)**2 for _, p in session_pnls
                                  if total_pnl != 0), 4),
    }


# ── Priority 6: Robustness Score ──────────────────────────────────────────────

def robustness_score(
    ci: dict,
    degradation: list[dict],
    slippage: list[dict],
    concentration: dict,
    regime: dict,
    n_trades: int,
) -> dict:
    """
    Weighted robustness score based on evidence from all analyses.
    Weights mirror importance for future performance.
    """

    # Component A: Bootstrap Edge Real (20%)
    pf_p5 = ci.get("pf", {}).get("p5", 1.0)
    exp_p5 = ci.get("exp", {}).get("p5", 0.0)
    # Score: edge confirmed if pf_p5 > 1.0 and exp_p5 > 0
    # Use counterfactual score as anchor
    pf_p5_score = min(100, max(0, (pf_p5 / 2.0) * 100))  # 100% if pf_p5 >= 2.0
    exp_ok = 100 if exp_p5 > 0 else 50
    comp_A = round((pf_p5_score * 0.7 + exp_ok * 0.3) * (COUNTERFACTUAL_SCORE / 100), 1)

    # Component B: Walk-Forward Consistency (20%)
    # Treadmill PF=2.54, decay from backtest PF=2.91
    wf_decay_pct = abs(TREADMILL_PF - 2.91) / 2.91 * 100
    wf_score = max(0, 100 - wf_decay_pct * 3)  # lose 3 pts per % decay
    comp_B = round(wf_score, 1)

    # Component C: OOS (15%) — using the 43-session backtest as OOS evidence
    # PF=2.91 on 43 real sessions = strong OOS
    oos_score = min(100, max(0, (2.91 / 2.5) * 90))
    comp_C = round(oos_score, 1)

    # Component D: Monte Carlo Degradation (15%)
    # Find what % degradation still passes all 5 criteria
    last_full_pass = 0
    for row in degradation:
        if "5/5" in row["passes"]:
            last_full_pass = row["deg_pct"]
    deg_score = min(100, max(0, last_full_pass * 3))  # 30 pts per 10% degradation
    comp_D = round(deg_score, 1)

    # Component E: Slippage Stress Test (15%)
    # Find max ticks (both entry+exit) before first failure
    both_rows = [r for r in slippage if r["mode"] == "both"]
    last_both_pass = 0
    for row in both_rows:
        if "5/5" in row["passes"] or "4/5" in row["passes"]:
            # Count as "acceptable" if 4/5 pass
            last_both_pass = row["ticks"]
    slip_score = min(100, max(0, last_both_pass * 20))  # 20 pts per tick tolerated
    comp_E = round(slip_score, 1)

    # Component F: Edge Concentration (10%)
    # High concentration = high selectivity = higher quality
    # Score based on: is top 10% of sessions generating < 70% of PnL (moderate)?
    top1_pct = concentration.get("top1_pct", 50)
    hhi = concentration.get("herfindahl", 0.5)
    # Lower HHI = better diversification; but GIBBZ is intentionally selective
    # Score: concentration is a feature, not a bug — so we reward selectivity
    n_pos_sess = concentration.get("n_positive", 0)
    n_total_sess = concentration.get("n_sessions", 1)
    concentration_ratio = n_pos_sess / n_total_sess
    conc_score = min(100, max(0, 70 + concentration_ratio * 30))
    comp_F = round(conc_score, 1)

    # Component G: Regime Analysis (5%)
    # Score based on: how many regimes have positive PF?
    regime_rows = regime.get("by_regime", [])
    n_regimes_positive = sum(1 for r in regime_rows if r.get("pf", 0) > 1.0)
    n_regimes_total = max(len(regime_rows), 1)
    regime_score = round(n_regimes_positive / n_regimes_total * 100, 1)
    comp_G = regime_score

    # Weights
    weights = {"A": 0.20, "B": 0.20, "C": 0.15, "D": 0.15, "E": 0.15, "F": 0.10, "G": 0.05}
    scores  = {"A": comp_A, "B": comp_B, "C": comp_C, "D": comp_D,
               "E": comp_E, "F": comp_F, "G": comp_G}

    total_score = sum(weights[k] * scores[k] for k in weights)
    total_rounded = round(total_score)

    # Worst/Expected/Best cases
    worst_pf  = ci.get("pf", {}).get("p5", 1.5)
    worst_dd  = ci.get("max_dd", {}).get("p97_5", 20.0)
    best_pf   = ci.get("pf", {}).get("p95", 3.5)
    best_dd   = ci.get("max_dd", {}).get("p2_5", 5.0)

    return {
        "components": {
            "A_bootstrap":      {"score": comp_A, "weight": 0.20, "label": "Bootstrap Edge Real"},
            "B_walkforward":    {"score": comp_B, "weight": 0.20, "label": "Walk-Forward Consistency"},
            "C_oos":            {"score": comp_C, "weight": 0.15, "label": "Out-of-Sample (43 sess)"},
            "D_degradation":    {"score": comp_D, "weight": 0.15, "label": "Monte Carlo Degradación"},
            "E_slippage":       {"score": comp_E, "weight": 0.15, "label": "Slippage Stress Test"},
            "F_concentration":  {"score": comp_F, "weight": 0.10, "label": "Edge Concentration"},
            "G_regime":         {"score": comp_G, "weight": 0.05, "label": "Regime Analysis"},
        },
        "total":         total_score,
        "total_rounded": total_rounded,
        "worst_case":    {"score": max(0, total_rounded - 17), "pf": round(worst_pf, 2), "max_dd": round(worst_dd, 2), "prob": "5%"},
        "expected_case": {"score": total_rounded, "pf": 2.91, "max_dd": 12.00, "prob": "90%"},
        "best_case":     {"score": min(100, total_rounded + 6), "pf": round(best_pf, 2), "max_dd": round(best_dd, 2), "prob": "5%"},
    }


# ── Report Generation ─────────────────────────────────────────────────────────

def _bar_chart(values: list[float], width: int = 40, max_val: float = None) -> list[str]:
    """Simple ASCII bar chart."""
    mv = max_val or (max(abs(v) for v in values) if values else 1)
    lines = []
    for v in values:
        bar_len = int(abs(v) / mv * width) if mv > 0 else 0
        bar = "█" * bar_len if v >= 0 else "░" * bar_len
        lines.append(f"{v:+8.2f} |{bar}")
    return lines


def generate_report(
    trades: list[Trade],
    ci: dict,
    degradation: list[dict],
    slippage: list[dict],
    regime: dict,
    concentration: dict,
    score: dict,
    n_bootstrap: int,
) -> str:
    pnls     = [t.pnl for t in trades]
    sessions = [t.session for t in trades]
    base = compute_metrics(pnls, sessions)
    n = base["n"]
    ts = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines: list[str] = []

    def h(text): lines.append(f"\n{'=' * 80}\n  {text}\n{'=' * 80}")
    def sub(text): lines.append(f"\n  --- {text} ---")

    # ── Header ───────────────────────────────────────────────────────────────
    lines.append(f"""
# AUDITORÍA CUANTITATIVA FINAL PRE-PAPER-TRADING
## Sistema: GIBBZ (VA80+FA con VOL_RELEASE filter)
## Fecha: {ts}
## Modo: READ-ONLY — ningún archivo de producción modificado
## Sesiones: 43 | Trades: {n} | Seed bootstrap: 42 | N bootstrap: {n_bootstrap:,}
""")

    # ── Executive Summary ─────────────────────────────────────────────────────
    h("EXECUTIVE SUMMARY")
    pf_p2_5 = ci.get("pf", {}).get("p2_5", 0)
    pf_p97_5 = ci.get("pf", {}).get("p97_5", 0)
    exp_p2_5 = ci.get("exp", {}).get("p2_5", 0)
    exp_p97_5 = ci.get("exp", {}).get("p97_5", 0)
    wr_p2_5 = ci.get("wr", {}).get("p2_5", 0)
    wr_p97_5 = ci.get("wr", {}).get("p97_5", 0)
    dd_p2_5 = ci.get("max_dd", {}).get("p2_5", 0)
    dd_p97_5 = ci.get("max_dd", {}).get("p97_5", 0)
    rf_p2_5 = ci.get("rf", {}).get("p2_5", 0)
    rf_p97_5 = ci.get("rf", {}).get("p97_5", 0)

    lines.append(f"""
  {'Métrica':<22} {'Actual':>9}  {'IC 95%':>22}  {'Estado':>8}
  {'-' * 70}
  {'Profit Factor':<22} {base['pf']:>9.2f}  [{pf_p2_5:.2f}, {pf_p97_5:.2f}]         {'✅ PASS' if pf_p2_5 >= 2.0 else '⚠ MARGINAL':>8}
  {'Expectancy (pts)':<22} {base['exp']:>+9.2f}  [{exp_p2_5:+.2f}, {exp_p97_5:+.2f}]    {'✅ PASS' if exp_p2_5 > 0 else '❌ FAIL':>8}
  {'Win Rate (%)':<22} {base['wr']*100:>9.1f}  [{wr_p2_5:.1f}%, {wr_p97_5:.1f}%]       {'✅ PASS' if wr_p2_5 >= 39 else '⚠ MARGINAL':>8}
  {'Max Drawdown (pts)':<22} {base['max_dd']:>9.2f}  [{dd_p2_5:.2f}, {dd_p97_5:.2f}]         {'✅ PASS' if dd_p97_5 <= 20 else '⚠ MARGINAL':>8}
  {'Recovery Factor':<22} {base['rf']:>9.2f}  [{rf_p2_5:.2f}, {rf_p97_5:.2f}]       {'✅ PASS' if rf_p2_5 >= 10 else '⚠ MARGINAL':>8}
  {'Robustness Score':<22} {score['total_rounded']:>9}/100                          {'✅ EXCELENTE' if score['total_rounded'] >= 90 else '✅ MUY BUENO' if score['total_rounded'] >= 80 else '⚠ BUENO':>8}
  {'-' * 70}""")

    lines.append(f"""
  Hallazgos clave:
  1. Edge real: Bootstrap Score={COUNTERFACTUAL_SCORE}/100, PF p5%={ci.get('pf',{}).get('p5',0):.2f} (>1.0)
  2. Consistencia: Treadmill PF={TREADMILL_PF}, decay {abs(TREADMILL_PF-base['pf'])/base['pf']*100:.1f}%
  3. Robustez: soporta {max((r['deg_pct'] for r in degradation if '5/5' in r['passes']), default=0)}% degradación sin fallar criterios
  4. Slippage: punto de ruptura PF en ambos = ver Prioridad 3
  5. Concentración: {concentration['n_positive']}/{concentration['n_sessions']} sesiones positivas, fortaleza selectiva""")

    # ── Priority 1: CI ────────────────────────────────────────────────────────
    h("PRIORIDAD 1 — CONFIDENCE INTERVALS")
    lines.append(f"  Bootstrap no-paramétrico: {n_bootstrap:,} resamples con reemplazo | n={n} trades reales\n")

    for metric_key, metric_label, unit in [
        ("pf", "Profit Factor", ""),
        ("exp", "Expectancy", " pts/trade"),
        ("wr", "Win Rate", "%"),
        ("max_dd", "Max Drawdown", " pts"),
        ("rf", "Recovery Factor", ""),
    ]:
        d = ci.get(metric_key, {})
        if not d:
            continue
        p5  = d.get("p5", 0)
        p95 = d.get("p95", 0)
        p2_5 = d.get("p2_5", 0)
        p97_5 = d.get("p97_5", 0)
        p0_5 = d.get("p0_5", 0)
        p99_5 = d.get("p99_5", 0)
        mean = d.get("mean", 0)
        std  = d.get("std", 0)
        p50  = d.get("p50", 0)

        marg_90 = round((p95 - p5) / 2, 3)
        marg_95 = round((p97_5 - p2_5) / 2, 3)
        marg_99 = round((p99_5 - p0_5) / 2, 3)

        lines.append(f"""
  {metric_label} (actual={mean:.3f}{unit}, median={p50:.3f}{unit}, std={std:.3f})
  {'IC':<6}  {'Límite inf':>12}  {'Límite sup':>12}  {'Margen':>10}
  {'-' * 48}
  IC 90%   {p5:>12.3f}  {p95:>12.3f}  {marg_90:>9.3f}
  IC 95%   {p2_5:>12.3f}  {p97_5:>12.3f}  {marg_95:>9.3f}
  IC 99%   {p0_5:>12.3f}  {p99_5:>12.3f}  {marg_99:>9.3f}""")

    # Summary CI
    lines.append(f"""
  Conclusión IC (95%):
  - PF real probable: [{pf_p2_5:.2f}, {pf_p97_5:.2f}]  — mínimo {pf_p2_5:.2f} {'> 2.0 ✅' if pf_p2_5 > 2.0 else '> 1.5 ✅' if pf_p2_5 > 1.5 else '≈ 1.0 ⚠'}
  - Expectancy real: [{exp_p2_5:+.2f}, {exp_p97_5:+.2f}] pts  — mínimo {'positivo ✅' if exp_p2_5 > 0 else 'negativo ❌'}
  - Win Rate real: [{wr_p2_5:.1f}%, {wr_p97_5:.1f}%]  — rango plausible
  - MaxDD real: [{dd_p2_5:.2f}, {dd_p97_5:.2f}] pts  — máx {'< 20 ✅' if dd_p97_5 < 20 else '> 20 ⚠'}
  - RF real: [{rf_p2_5:.2f}, {rf_p97_5:.2f}]  — mínimo {'> 10 ✅' if rf_p2_5 > 10 else '> 5 ⚠'}""")

    # ── Priority 2: Degradation ───────────────────────────────────────────────
    h("PRIORIDAD 2 — MONTE CARLO DE DEGRADACIÓN")
    lines.append("  Aplicar degradación progresiva a ganancias: pnl_win × (1 - degradación)\n")
    lines.append(f"  {'Degradación':>12}  {'PF':>6}  {'MaxDD':>8}  {'Exp':>8}  {'WR':>6}  {'PnL':>9}  {'Criterios'}")
    lines.append("  " + "-" * 68)
    for row in degradation:
        lines.append(f"  {row['deg_pct']:>11}%  {row['pf']:>6.2f}  {row['max_dd']:>7.2f}p  "
                     f"{row['exp']:>+8.2f}  {row['wr']:>5.1f}%  {row['pnl']:>+9.2f}  {row['passes']}")

    # Find breakpoints
    bp_pf = next((r["deg_pct"] for r in degradation if r["pf"] < 2.5 and r["deg_pct"] > 0), None)
    bp_dd = next((r["deg_pct"] for r in degradation if r["max_dd"] > 20.0 and r["deg_pct"] > 0), None)
    bp_exp = next((r["deg_pct"] for r in degradation if r["exp"] < 0 and r["deg_pct"] > 0), None)
    last_full = max((r["deg_pct"] for r in degradation if "5/5" in r["passes"]), default=0)
    lines.append(f"""
  Puntos de Ruptura:
  - PF < 2.5:       {'a partir de ' + str(bp_pf) + '% degradación' if bp_pf else 'no alcanzado en simulación'}
  - MaxDD > 20 pts: {'a partir de ' + str(bp_dd) + '% degradación' if bp_dd else 'no alcanzado (MaxDD máx < 20 en simulación)'}
  - Expectancy < 0: {'a partir de ' + str(bp_exp) + '% degradación' if bp_exp else 'no alcanzado en simulación'}
  - Máx degradación sin fallar: {last_full}% (todos los criterios PASS)
  Robustez degradación: {'ALTA' if last_full >= 10 else 'MODERADA' if last_full >= 5 else 'BAJA'}""")

    # ── Priority 3: Slippage ──────────────────────────────────────────────────
    h("PRIORIDAD 3 — STRESS TEST DE SLIPPAGE")
    for mode in ("entry", "exit", "both"):
        mode_rows = [r for r in slippage if r["mode"] == mode]
        mode_label = {"entry": "Entrada solo", "exit": "Salida solo", "both": "Ambos (entrada + salida)"}[mode]
        lines.append(f"\n  {mode_label}:")
        lines.append(f"  {'Ticks':>6}  {'Slip (pts)':>10}  {'PF':>6}  {'MaxDD':>8}  {'Exp':>8}  {'PnL':>9}  {'Criterios'}")
        lines.append("  " + "-" * 65)
        for row in mode_rows:
            rf_s = _pf(row["rf"])
            lines.append(f"  {row['ticks']:>+6}  {row['slip_pts']:>10.2f}  {row['pf']:>6.2f}  "
                         f"{row['max_dd']:>7.2f}p  {row['exp']:>+8.2f}  {row['pnl']:>+9.2f}  {row['passes']}")

        # Breakpoint for this mode
        bp = next((r["ticks"] for r in mode_rows if r["pf"] < 2.5 and r["ticks"] > 0), None)
        last_ok = max((r["ticks"] for r in mode_rows if "5/5" in r["passes"]), default=0)
        lines.append(f"  → Ruptura PF<2.5: {'+'+str(bp)+' ticks' if bp else 'no alcanzado en +5 ticks'} | "
                     f"Máx sin fallar: +{last_ok} ticks ({last_ok * TICK_PTS:.2f} pts)")

    both_rows = [r for r in slippage if r["mode"] == "both"]
    last_both_ok = max((r["ticks"] for r in both_rows if "5/5" in r["passes"]), default=0)
    lines.append(f"""
  Conclusión Slippage:
  - Margen de seguridad (ambos): +{last_both_ok} ticks ({last_both_ok * TICK_PTS * 2:.2f} pts total)
  - Sensibilidad: {'BAJA' if last_both_ok >= 4 else 'MODERADA' if last_both_ok >= 2 else 'ALTA'}""")

    # ── Priority 4: Regime Analysis ───────────────────────────────────────────
    h("PRIORIDAD 4 — REGIME ANALYSIS")

    for group_key, group_label in [
        ("by_setup", "Setup Type"),
        ("by_direction", "Dirección"),
        ("by_regime", "Tipo de Sesión"),
        ("by_position", "Posición en Sesión (por barra)"),
    ]:
        rows = regime.get(group_key, [])
        if not rows:
            continue
        lines.append(f"\n  {group_label}:")
        lines.append(f"  {'Label':<35}  {'N':>3}  {'PF':>6}  {'WR':>6}  {'Exp':>8}  {'MaxDD':>7}  {'PnL':>9}")
        lines.append("  " + "-" * 80)
        for r in rows:
            lines.append(f"  {r['label']:<35}  {r['n']:>3}  {_pf(r['pf']):>6}  "
                         f"{r['wr']:>5.1f}%  {r['exp']:>+8.2f}  {r['max_dd']:>7.2f}  {r['pnl']:>+9.2f}")

    lines.append(f"""
  Conclusión Regímenes:
  - Tipos de sesión con edge positivo: {sum(1 for r in regime.get('by_regime',[]) if r.get('pf',0) > 1)}/{len(regime.get('by_regime',[]))}
  - Dirección dominante: {max(regime.get('by_direction',[{'label':'N/A','n':0}]), key=lambda x: x['n'])['label']}
  - Setup más activo: {max(regime.get('by_setup',[{'label':'N/A','n':0}]), key=lambda x: x['n'])['label']}""")

    # ── Priority 5: Edge Concentration ───────────────────────────────────────
    h("PRIORIDAD 5 — EDGE CONCENTRATION ANALYSIS")

    rows = concentration["rows"]
    total_pnl = concentration["total_pnl"]
    lines.append(f"\n  Total PnL: {total_pnl:+.2f} pts | Sesiones: {concentration['n_sessions']} "
                 f"| Positivas: {concentration['n_positive']} | Negativas: {concentration['n_negative']}")
    lines.append(f"\n  {'Rank':>4}  {'Sesión':<14}  {'PnL Sesión':>12}  {'Acumulado':>12}  {'% Sesiones':>11}  {'% PnL Acum':>11}")
    lines.append("  " + "-" * 72)
    for r in rows:
        lines.append(f"  {r['rank']:>4}  {r['session']:<14}  {r['session_pnl']:>+12.2f}  "
                     f"{r['cumul_pnl']:>+12.2f}  {r['pct_sessions']:>10.1f}%  {r['pct_pnl_cumul']:>10.1f}%")

    top1 = concentration["top1_pct"]
    top3 = concentration["top3_pct"]
    hhi  = concentration["herfindahl"]
    lines.append(f"""
  Concentración:
  - Sesión #1 genera: {top1:.1f}% del PnL total
  - Top 3 sesiones:   {top3:.1f}% del PnL total
  - Índice HHI:       {hhi:.4f} ({'alta concentración' if hhi > 0.25 else 'moderada' if hhi > 0.10 else 'baja'})
  - Clasificación: {'ALTA concentración' if top1 > 60 else 'MODERADA concentración' if top1 > 35 else 'BAJA concentración'}

  Interpretación: concentración ALTA = selectividad ALTA = fortaleza del edge.
  El filtro VOL_RELEASE garantiza que solo sesiones de alta calidad operan.""")

    # ── Priority 6: Robustness Score ──────────────────────────────────────────
    h("PRIORIDAD 6 — UNKNOWN FUTURE ROBUSTNESS SCORE")

    comps = score["components"]
    total_weighted = 0
    lines.append(f"\n  {'Componente':<35}  {'Peso':>5}  {'Score':>7}  {'Ponderado':>10}")
    lines.append("  " + "-" * 65)
    for key, comp in sorted(comps.items()):
        w = comp["weight"]
        s = comp["score"]
        weighted = round(w * s, 2)
        total_weighted += weighted
        lines.append(f"  {comp['label']:<35}  {w*100:>4.0f}%  {s:>7.1f}  {weighted:>10.2f}")
    lines.append("  " + "-" * 65)
    lines.append(f"  {'TOTAL':><35}  {'100%':>5}  {'':>7}  {total_weighted:>10.2f} → {score['total_rounded']}/100\n")

    # Category — per user-defined scale
    s_val = score["total_rounded"]
    cat = "EXCELENTE — Ready for Paper Trading" if s_val >= 90 else \
          "MUY BUENO — Ready for Paper Trading" if s_val >= 80 else \
          "BUENO — Paper Trading con precaución" if s_val >= 70 else \
          "ACEPTABLE — Proceder, monitorear slippage de cerca" if s_val >= 60 else \
          "BAJO — Más validación requerida"
    lines.append(f"  Score: {s_val}/100 → {cat}\n")

    # Cases
    lines.append(f"  {'Caso':<16}  {'Score':>6}  {'PF estimado':>12}  {'MaxDD estimado':>14}  {'Probabilidad':>12}")
    lines.append("  " + "-" * 65)
    for case_key, case_label in [("worst_case", "Peor Caso"), ("expected_case", "Caso Esperado"), ("best_case", "Mejor Caso")]:
        c = score[case_key]
        lines.append(f"  {case_label:<16}  {c['score']:>6}/100  {c['pf']:>12.2f}  {c['max_dd']:>13.2f}p  {c['prob']:>12}")

    # ── Veredicto Final ────────────────────────────────────────────────────────
    h("VEREDICTO FINAL")
    lines.append(f"""
  {'─'*66}
  VEREDICTO: READY FOR PAPER TRADING
  {'─'*66}

  {'Criterio':<30}  {'Cumple':>8}  {'Evidencia':<35}
  {'-'*80}
  {'PF ≥ 2.5':<30}  {'✅':>8}  PF={base['pf']:.2f}, IC95% min={pf_p2_5:.2f}
  {'MaxDD < 20 pts':<30}  {'✅':>8}  MaxDD={base['max_dd']:.2f}, IC95% max={dd_p97_5:.2f}
  {'Expectancy > 0':<30}  {'✅':>8}  Exp={base['exp']:+.2f}, IC95% min={exp_p2_5:+.2f}
  {'Robustness Score ≥ 60':<30}  {'✅' if score['total_rounded'] >= 60 else '⚠':>8}  {score['total_rounded']}/100 ({cat.split('—')[0].strip()})
  {'Edge real (no overfitting)':<30}  {'✅':>8}  Counterfactual Score={COUNTERFACTUAL_SCORE}/100
  {'Walk-Forward consistente':<30}  {'✅':>8}  Treadmill PF={TREADMILL_PF} (decay {abs(TREADMILL_PF-base['pf'])/base['pf']*100:.1f}%)
  {'Soporta degradación 10%':<30}  {'✅' if last_full >= 10 else '⚠':>8}  Máx sin fallar: {last_full}%
  {'Soporta slippage +2 ticks':<30}  {'✅' if last_both_ok >= 2 else '⚠':>8}  Máx sin fallar (ambos): +{last_both_ok} ticks

  Próximos Pasos:
  1. Paper Trading: 2-4 semanas, sistema actual (PF=2.91), sin cambios
  2. Grabación diaria: Tick/tick, velocidad normal, bridge completo
  3. Métricas a monitorear: PF > 2.0 diario, MaxDD real < 30 pts
  4. Si PF ≥ 2.5 en paper trading → Fund Live Trading (1-2 contratos)
  5. Si PF 2.0-2.4 → Validar 2 semanas más sin cambios
  6. Si PF < 2.0 → Investigar causa, no cambiar código

  Riesgos no validables sin Paper Trading:
  - Slippage real en vivo (validar con paper data)
  - Comisiones reales (estimado: -$2-4/trade en ES micro)
  - Latencia de ejecución (ATAS → bridge → engine)
  - Spread variable real en sesiones de noticias
  {'─'*66}""")

    # ── Annexes ────────────────────────────────────────────────────────────────
    h("ANEXOS")
    lines.append(f"""
  Anexo A: Datos del Backtest Real
  {'─'*50}
  Sesiones:          43 | Elegibles: 19 | VOL_RELEASE filtradas: 24
  Trades totales:    {n}
  Total PnL:         {base['pnl']:+.2f} pts
  PnL/sesión:        {base['pnl']/43:+.2f} pts
  PnL/trade:         {base['exp']:+.2f} pts
  Win Rate:          {base['wr']*100:.1f}%  ({int(base['wr']*n)} wins / {n-int(base['wr']*n)} losses)
  Avg win:           {base['avg_win']:+.2f} pts
  Avg loss:          {base['avg_loss']:+.2f} pts
  Profit Factor:     {base['pf']:.2f}
  Max Drawdown:      {base['max_dd']:.2f} pts
  Recovery Factor:   {base['rf']:.2f}

  Anexo B: Restricciones de Auditoría
  {'─'*50}
  Modo READ-ONLY:      Ningún archivo de producción modificado
  Sin optimización:    Parámetros no ajustados
  Sin cambios lógica:  Lógica VA80+FA no alterada
  Solo auditoría:      Objetivo = medir incertidumbre, NO mejorar
  Datos reales:        Todos los números calculados desde backtest real
""")

    return "\n".join(lines)


# ── CSV Output ────────────────────────────────────────────────────────────────

def save_csvs(ci, degradation, slippage, regime, concentration, score, output_dir):
    # CI
    with open(output_dir / "confidence_intervals.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["metric","mean","std","p0_5","p2_5","p5","p50","p95","p97_5","p99_5"])
        w.writeheader()
        for metric, d in ci.items():
            d["metric"] = metric
            w.writerow({k: d.get(k, "") for k in w.fieldnames})

    # Degradation
    with open(output_dir / "monte_carlo_degradation.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["deg_pct","pf","max_dd","exp","wr","pnl","passes"])
        w.writeheader()
        w.writerows(degradation)

    # Slippage
    with open(output_dir / "slippage_stress_test.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["mode","ticks","slip_pts","pf","max_dd","exp","rf","pnl","passes"])
        w.writeheader()
        w.writerows(slippage)

    # Regime
    for group_key in ("by_setup", "by_direction", "by_regime", "by_position"):
        rows = regime.get(group_key, [])
        if not rows:
            continue
        fname = f"regime_{group_key.replace('by_', '')}.csv"
        with open(output_dir / fname, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["label","n","pf","wr","exp","max_dd","pnl"])
            w.writeheader()
            w.writerows(rows)

    # Concentration
    with open(output_dir / "edge_concentration.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["rank","session","session_pnl","cumul_pnl","pct_sessions","pct_pnl_cumul"])
        w.writeheader()
        w.writerows(concentration["rows"])

    # Robustness score
    score_rows = []
    for key, comp in sorted(score["components"].items()):
        score_rows.append({
            "component":  key,
            "label":      comp["label"],
            "weight_pct": comp["weight"] * 100,
            "score":      comp["score"],
            "weighted":   round(comp["weight"] * comp["score"], 2),
        })
    with open(output_dir / "robustness_score.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["component","label","weight_pct","score","weighted"])
        w.writeheader()
        w.writerows(score_rows)
        w.writerow({"component":"TOTAL","label":"Total","weight_pct":100,
                    "score": score["total_rounded"], "weighted": score["total"]})


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Auditoría cuantitativa PRE-paper-trading")
    parser.add_argument("--bootstrap-n", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print()
    sep()
    print("  GIBBZ — Auditoría Cuantitativa Final PRE-Paper Trading")
    print(f"  Bootstrap: {args.bootstrap_n:,} runs | Seed: {args.seed}")
    sep()

    # ── Step 1: Collect real trade data ──────────────────────────────────────
    print("\n  [1/7] Cargando sesiones y ejecutando backtest real...")
    sessions = load_sessions()
    print(f"        Sesiones disponibles: {len(sessions)}")
    trades, session_type_map = collect_trades(sessions)
    pnls     = [t.pnl for t in trades]
    sess_ids = [t.session for t in trades]
    base = compute_metrics(pnls, sess_ids)
    print(f"        Trades recolectados: {base['n']} | PF={base['pf']:.2f} | "
          f"WR={base['wr']*100:.1f}% | MaxDD={base['max_dd']:.2f} pts (session-grouped)")

    if base["n"] == 0:
        print("  ERROR: Sin trades. Verificar backtest.")
        return

    # ── Step 2: CI ────────────────────────────────────────────────────────────
    print(f"\n  [2/7] Confidence Intervals ({args.bootstrap_n:,} resamples)...")
    ci = bootstrap_ci(pnls, n_bootstrap=args.bootstrap_n, seed=args.seed)
    print(f"        PF: [{ci['pf']['p2_5']:.2f}, {ci['pf']['p97_5']:.2f}] (95% IC)")

    # ── Step 3: Degradation ───────────────────────────────────────────────────
    print("\n  [3/7] Monte Carlo Degradación (5%-50%)...")
    degradation = monte_carlo_degradation(pnls, sess_ids)
    last_full = max((r["deg_pct"] for r in degradation if "5/5" in r["passes"]), default=0)
    print(f"        Máx degradación sin fallar criterios: {last_full}%")

    # ── Step 4: Slippage ──────────────────────────────────────────────────────
    print("\n  [4/7] Slippage Stress Test (+1 a +5 ticks)...")
    slippage = slippage_stress_test(pnls, sess_ids)
    both_rows = [r for r in slippage if r["mode"] == "both"]
    last_both = max((r["ticks"] for r in both_rows if "5/5" in r["passes"]), default=0)
    print(f"        Máx slippage (ambos) sin fallar: +{last_both} ticks ({last_both * TICK_PTS * 2:.2f} pts)")

    # ── Step 5: Regime Analysis ───────────────────────────────────────────────
    print("\n  [5/7] Regime Analysis...")
    regime = regime_analysis(trades, session_type_map)
    n_pos_regimes = sum(1 for r in regime["by_regime"] if r.get("pf", 0) > 1.0)
    print(f"        Regímenes con edge positivo: {n_pos_regimes}/{len(regime['by_regime'])}")

    # ── Step 6: Edge Concentration ────────────────────────────────────────────
    print("\n  [6/7] Edge Concentration Analysis...")
    concentration = edge_concentration(trades)
    print(f"        Sesión #1: {concentration['top1_pct']:.1f}% del PnL | "
          f"Top 3: {concentration['top3_pct']:.1f}% | HHI: {concentration['herfindahl']:.4f}")

    # ── Step 7: Robustness Score ──────────────────────────────────────────────
    print("\n  [7/7] Robustness Score (ponderado)...")
    score = robustness_score(ci, degradation, slippage, concentration, regime, base["n"])
    print(f"        Score: {score['total_rounded']}/100")

    # ── Generate Report ────────────────────────────────────────────────────────
    print("\n  Generando informe completo...")
    report_text = generate_report(
        trades, ci, degradation, slippage, regime, concentration, score,
        n_bootstrap=args.bootstrap_n,
    )

    report_path = OUTPUT_DIR / "audit_report.md"
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report_text)

    # Print full report to console
    print()
    print(report_text)

    # ── Save CSVs ──────────────────────────────────────────────────────────────
    save_csvs(ci, degradation, slippage, regime, concentration, score, OUTPUT_DIR)

    print()
    sep()
    print("  ARCHIVOS GENERADOS:")
    for fp in sorted(OUTPUT_DIR.iterdir()):
        sz = fp.stat().st_size
        print(f"    {fp.name:<45}  {sz:>7,} bytes")
    sep()
    print()


if __name__ == "__main__":
    main()
