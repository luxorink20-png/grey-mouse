"""
scripts/validate_go_live_readiness.py
Score de readiness para live trading.

Combina:
  1. Backtest completo 43 sesiones con ContextFilter
  2. Bootstrap Treadmill (50 runs rapidos)
  3. Metricas de calidad del edge

Emite un GO / NO-GO con justificacion cuantitativa.

Uso:
    python scripts/validate_go_live_readiness.py
    python scripts/validate_go_live_readiness.py --treadmill-runs 100
"""

import sys
import os
import json
import random
import statistics
import argparse
from collections import defaultdict
from pathlib import Path

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
MAX_BARS     = 4000
TARGET_CAP   = 20.0


def load_sessions() -> list:
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


def calc_metrics(trades: list) -> dict:
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "exp": 0.0,
                "pnl": 0.0, "max_dd": 0.0, "rf": 0.0}
    wins   = [t for t in trades if t.result == "WIN"]
    losses = [t for t in trades if t.result == "LOSS"]
    n, nw, nl = len(trades), len(wins), len(losses)
    wr = 100.0 * nw / n
    sw = sum(t.pnl for t in wins)
    sl = sum(t.pnl for t in losses)
    pf = abs(sw / sl) if sl != 0 else float("inf")
    aw = sw / max(nw, 1)
    al = sl / max(nl, 1)
    exp = round(wr / 100 * aw + (1 - wr / 100) * al, 2)
    total = round(sw + sl, 2)
    by_sess: dict = defaultdict(float)
    for t in trades:
        by_sess[t.session] += t.pnl
    cum = peak = max_dd = 0.0
    for d in sorted(by_sess.keys()):
        cum += by_sess[d]
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    max_dd = round(max_dd, 2)
    rf = round(total / max_dd, 2) if max_dd > 0 else float("inf")
    return {"n": n, "wr": round(wr, 1), "pf": round(pf, 2),
            "exp": exp, "pnl": total, "max_dd": max_dd, "rf": rf}


def percentile(data: list, p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    i = (len(s) - 1) * p / 100.0
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (i - lo), 3)


def _pf(pf): return f"{pf:.2f}" if pf != float("inf") else "inf"
def _rf(rf): return f"{rf:.2f}" if rf != float("inf") else "inf"
def _sep(n=72): print("  " + "-" * n)


def phase1_full_backtest(sessions: list, cf: ContextFilter) -> dict:
    """Backtest completo de las 43 sesiones con ContextFilter."""
    all_trades: list[Trade] = []
    skipped = session_with_trades = 0
    for s in sessions:
        stype = s["session_type"]
        if cf.is_session_filtered(stype):
            skipped += 1
            continue
        bars = run_session(s["date"], s["recording"], MAX_BARS, TARGET_CAP,
                           context_filter=cf, session_type=stype)
        trades = run_backtest(bars, s["date"], TARGET_CAP) if bars else []
        if trades:
            session_with_trades += 1
        all_trades.extend(trades)
    m = calc_metrics(all_trades)
    m["sessions_total"]      = len(sessions)
    m["sessions_filtered"]   = skipped
    m["sessions_with_trades"]= session_with_trades
    return m


def phase2_bootstrap(sessions: list, cf: ContextFilter,
                     n_runs: int, sessions_per_run: int,
                     cache: dict) -> dict:
    """Bootstrap treadmill para intervalos de confianza."""
    pf_list = []
    exp_list = []
    dd_list  = []
    for _ in range(n_runs):
        sampled = random.choices(sessions, k=sessions_per_run)
        counter: dict = defaultdict(int)
        all_trades: list[Trade] = []
        for s in sampled:
            key = s["date"]
            counter[key] += 1
            if key not in cache:
                stype = s["session_type"]
                if cf.is_session_filtered(stype):
                    cache[key] = []
                else:
                    bars = run_session(s["date"], s["recording"], MAX_BARS, TARGET_CAP,
                                       context_filter=cf, session_type=stype)
                    cache[key] = run_backtest(bars, s["date"], TARGET_CAP) if bars else []
            trades = cache[key]
            if trades and counter[key] > 1:
                from dataclasses import replace
                rep = counter[key]
                all_trades.extend(replace(t, session=f"{t.session}_b{rep}") for t in trades)
            else:
                all_trades.extend(trades)

        m = calc_metrics(all_trades)
        if m["n"] > 0 and m["pf"] != float("inf"):
            pf_list.append(m["pf"])
            exp_list.append(m["exp"])
            dd_list.append(m["max_dd"])

    if not pf_list:
        return {"pf_median": 0.0, "pf_p5": 0.0, "exp_p5": 0.0,
                "dd_p95": 999.0, "pct_profitable": 0.0}
    pct_prof = 100 * sum(1 for x in pf_list if x > 1.0) / max(len(pf_list), 1)
    return {
        "pf_median": percentile(pf_list,  50),
        "pf_p5":     percentile(pf_list,  5),
        "exp_p5":    percentile(exp_list, 5),
        "dd_p95":    percentile(dd_list,  95),
        "pct_profitable": pct_prof,
        "n_runs":    len(pf_list),
    }


def go_live_score(p1: dict, p2: dict) -> tuple[float, str, list]:
    """
    Calcula el Go-Live Score (0-100) y la decision.

    Componentes:
      A (30 pts): PF baseline >= 2.3
      B (25 pts): MaxDD baseline <= 20 pts
      C (20 pts): Bootstrap PF mediano >= 2.0
      D (15 pts): Bootstrap PF p5% >= 1.5
      E (10 pts): Bootstrap % runs profitable >= 90%
    """
    score = 0.0
    details = []

    # A: PF baseline
    pf_b = p1["pf"]
    if pf_b >= 2.5:
        score += 30.0; details.append(("A  PF baseline >= 2.5",       30, 30, _pf(pf_b)))
    elif pf_b >= 2.3:
        score += 22.0; details.append(("A  PF baseline >= 2.3",       22, 30, _pf(pf_b)))
    elif pf_b >= 2.0:
        score += 15.0; details.append(("A  PF baseline 2.0-2.3",      15, 30, _pf(pf_b)))
    elif pf_b >= 1.3:
        score +=  8.0; details.append(("A  PF baseline 1.3-2.0",       8, 30, _pf(pf_b)))
    else:
        details.append(("A  PF baseline < 1.3",                         0, 30, _pf(pf_b)))

    # B: MaxDD baseline
    dd = p1["max_dd"]
    if dd <= 15.0:
        score += 25.0; details.append(("B  MaxDD <= 15 pts",          25, 25, f"{dd:.2f}"))
    elif dd <= 20.0:
        score += 20.0; details.append(("B  MaxDD <= 20 pts",          20, 25, f"{dd:.2f}"))
    elif dd <= 30.0:
        score += 12.0; details.append(("B  MaxDD <= 30 pts",          12, 25, f"{dd:.2f}"))
    else:
        details.append(("B  MaxDD > 30 pts",                            0, 25, f"{dd:.2f}"))

    # C: Bootstrap PF mediano
    pf_med = p2["pf_median"]
    if pf_med >= 2.3:
        score += 20.0; details.append(("C  Bootstrap PF mediano >= 2.3", 20, 20, f"{pf_med:.2f}"))
    elif pf_med >= 2.0:
        score += 15.0; details.append(("C  Bootstrap PF mediano >= 2.0", 15, 20, f"{pf_med:.2f}"))
    elif pf_med >= 1.5:
        score +=  8.0; details.append(("C  Bootstrap PF mediano >= 1.5",  8, 20, f"{pf_med:.2f}"))
    else:
        details.append(("C  Bootstrap PF mediano < 1.5",                  0, 20, f"{pf_med:.2f}"))

    # D: Bootstrap PF p5%
    pf_p5 = p2["pf_p5"]
    if pf_p5 >= 2.0:
        score += 15.0; details.append(("D  Bootstrap PF p5% >= 2.0",  15, 15, f"{pf_p5:.2f}"))
    elif pf_p5 >= 1.5:
        score += 10.0; details.append(("D  Bootstrap PF p5% >= 1.5",  10, 15, f"{pf_p5:.2f}"))
    elif pf_p5 >= 1.0:
        score +=  5.0; details.append(("D  Bootstrap PF p5% >= 1.0",   5, 15, f"{pf_p5:.2f}"))
    else:
        details.append(("D  Bootstrap PF p5% < 1.0",                    0, 15, f"{pf_p5:.2f}"))

    # E: % runs profitable
    pct = p2["pct_profitable"]
    if pct >= 95.0:
        score += 10.0; details.append(("E  Runs profitable >= 95%",   10, 10, f"{pct:.1f}%"))
    elif pct >= 90.0:
        score +=  8.0; details.append(("E  Runs profitable >= 90%",    8, 10, f"{pct:.1f}%"))
    elif pct >= 80.0:
        score +=  4.0; details.append(("E  Runs profitable >= 80%",    4, 10, f"{pct:.1f}%"))
    else:
        details.append(("E  Runs profitable < 80%",                     0, 10, f"{pct:.1f}%"))

    score = round(score, 1)

    if score >= 80:
        verdict = "GO  — Edge validado para live trading (size reducido)"
    elif score >= 65:
        verdict = "GO CONDICIONAL — Considerar paper trading 2 semanas adicionales"
    elif score >= 50:
        verdict = "NO-GO — Edge prometedor pero insuficiente para live"
    else:
        verdict = "NO-GO — Edge no demostrado estadisticamente"

    return score, verdict, details


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--treadmill-runs", type=int, default=50,
                        help="Bootstrap runs (default: 50, rapido)")
    parser.add_argument("--seed", type=int, default=None)
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    print()
    print("=" * 72)
    print("  GIBBZ Go-Live Readiness Score")
    print("=" * 72)

    sessions = load_sessions()
    cf = ContextFilter(enable_vol_release=True,
                       enable_destructive_regime=False,
                       enable_session_kill_switch=False)

    # ── FASE 1: Backtest completo ──────────────────────────────────────────
    print()
    print("  FASE 1 — Backtest 43 sesiones con ContextFilter")
    _sep()
    p1 = phase1_full_backtest(sessions, cf)
    print(f"  Sesiones total:         {p1['sessions_total']}")
    print(f"  Filtradas (VOL_RELEASE):{p1['sessions_filtered']}")
    print(f"  Con trades:             {p1['sessions_with_trades']}")
    print(f"  Trades generados:       {p1['n']}")
    print(f"  WR:                     {p1['wr']:.1f}%")
    print(f"  PF:                     {_pf(p1['pf'])}")
    print(f"  Expectancy:             {p1['exp']:+.2f} pts/trade")
    print(f"  PnL:                    {p1['pnl']:+.2f} pts")
    print(f"  MaxDD:                  {p1['max_dd']:.2f} pts")
    print(f"  Recovery Factor:        {_rf(p1['rf'])}")

    p1_ok = (p1["pf"] >= 2.3 and p1["max_dd"] <= 30.0 and p1["n"] >= 20)
    print(f"\n  Fase 1: {'PASS' if p1_ok else 'REVISAR'}")

    # ── FASE 2: Bootstrap treadmill ─────────────────────────────────────
    print()
    print(f"  FASE 2 — Bootstrap Treadmill ({args.treadmill_runs} runs)")
    _sep()
    print(f"  Ejecutando {args.treadmill_runs} permutaciones aleatorias...", flush=True)
    cache: dict = {}
    p2 = phase2_bootstrap(sessions, cf, args.treadmill_runs, 43, cache)
    print(f"  Runs completados:       {p2['n_runs']}")
    print(f"  PF mediano:             {p2['pf_median']:.3f}")
    print(f"  PF percentil 5%:        {p2['pf_p5']:.3f}")
    print(f"  Exp percentil 5%:       {p2['exp_p5']:+.2f} pts/trade")
    print(f"  MaxDD percentil 95%:    {p2['dd_p95']:.2f} pts")
    print(f"  Runs PF > 1.0:          {p2['pct_profitable']:.1f}%")

    p2_ok = (p2["pf_median"] >= 2.0 and p2["pf_p5"] >= 1.5 and p2["pct_profitable"] >= 90.0)
    print(f"\n  Fase 2: {'PASS' if p2_ok else 'REVISAR'}")

    # ── Score final ────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  GO-LIVE READINESS SCORE")
    _sep()
    score, verdict, details = go_live_score(p1, p2)

    print(f"  {'Componente':<42}  {'Pts':>5}  {'Max':>5}  {'Valor':>10}")
    _sep(65)
    for label, pts, mx, val in details:
        print(f"  {label:<42}  {pts:>5}  {mx:>5}  {val:>10}")
    _sep(65)
    print(f"  {'TOTAL':<42}  {score:>5.1f}  {100:>5}")
    print()
    print(f"  SCORE: {score:.1f} / 100")
    print()
    print(f"  VEREDICTO: {verdict}")
    print()

    # ── Contexto ──────────────────────────────────────────────────────────
    print("=" * 72)
    print("  LIMITACIONES DEL DATASET")
    _sep()
    print("  * 43 sesiones aplican 43 contextos historicos (VAH/POC/VAL distintos)")
    print("    sobre los mismos 4 dias de precio real (Mayo 8-11, 2026).")
    print("  * NO es un backtest multi-mes independiente.")
    print("  * El Bootstrap Treadmill permuta esas mismas 43 sesiones —")
    print("    no genera datos nuevos.")
    print("  * Para validacion definitiva: se requieren grabaciones de")
    print("    diferentes periodos de mercado (3-6 meses de datos nuevos).")
    print()
    print("  Con las limitaciones actuales, el score de",
          f"{score:.1f}/100 es el maximo")
    print("  alcanzable con el dataset disponible.")
    print()
    print("=" * 72)
    print()


if __name__ == "__main__":
    main()
