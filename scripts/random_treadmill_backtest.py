"""
scripts/random_treadmill_backtest.py
Bootstrap Treadmill de Validacion Estadistica.

Objetivo: medir la DISTRIBUCION de PF/Exp/MaxDD via muestreo aleatorio
de las 43 sesiones disponibles, cuantificando intervalos de confianza.

Diferencia vs replay_treadmill.py (simulation/):
  replay_treadmill.py  — valida estabilidad de edge via ETIL/GTAL/shadow metrics
  este script          — valida PF/Exp/MaxDD via bootstrap de trades reales

Metodologia:
  Bootstrap con reemplazo: cada "run" samplera N sesiones con reemplazo
  de las 43 disponibles (mismo pool que el backtest real).
  La distribucion de resultados revela si el edge es estadisticamente
  robusto o dependiente de pocas sesiones especificas.

Uso:
    python scripts/random_treadmill_backtest.py
    python scripts/random_treadmill_backtest.py --runs 200 --sessions-per-run 20
    python scripts/random_treadmill_backtest.py --seed 42  # reproducible
"""

import sys
import os
import json
import random
import argparse
import statistics
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


def session_metrics(trades: list) -> dict:
    """Session-level maxDD + trade metrics."""
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "exp": 0.0, "pnl": 0.0, "max_dd": 0.0}
    wins   = [t for t in trades if t.result == "WIN"]
    losses = [t for t in trades if t.result == "LOSS"]
    n, nw, nl = len(trades), len(wins), len(losses)
    wr  = 100.0 * nw / n
    sw  = sum(t.pnl for t in wins)
    sl  = sum(t.pnl for t in losses)
    pf  = abs(sw / sl) if sl != 0 else float("inf")
    aw  = sw / max(nw, 1)
    al  = sl / max(nl, 1)
    exp = round(wr / 100 * aw + (1 - wr / 100) * al, 2)
    total = sw + sl

    # Session-level drawdown
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

    return {"n": n, "wr": round(wr, 1), "pf": round(pf, 2),
            "exp": exp, "pnl": round(total, 2), "max_dd": round(max_dd, 2)}


def run_single_bootstrap(
    pool: list,
    n_sessions: int,
    cf: ContextFilter,
    cache: dict,
) -> dict:
    """
    Un bootstrap run: samplear n_sessions de pool con reemplazo,
    correr backtest con ContextFilter, retornar metricas.
    El cache evita repetir run_session() para la misma sesion.
    """
    sampled = random.choices(pool, k=n_sessions)
    all_trades: list[Trade] = []
    session_counter: dict = defaultdict(int)

    for s in sampled:
        key = s["date"]
        session_counter[key] += 1

        if key not in cache:
            stype = s["session_type"]
            if cf.is_session_filtered(stype):
                cache[key] = []
            else:
                bars  = run_session(s["date"], s["recording"], MAX_BARS, TARGET_CAP,
                                    context_filter=cf, session_type=stype)
                cache[key] = run_backtest(bars, s["date"], TARGET_CAP) if bars else []

        # Cada vez que una sesion aparece en el sample, sus trades se duplican
        trades_for_session = cache[key]
        if trades_for_session and session_counter[key] > 1:
            # Para duplicados en bootstrap: re-etiquetar session para no confundir
            # el session-level MaxDD (usamos session+rep como clave)
            from dataclasses import replace
            rep = session_counter[key]
            for t in trades_for_session:
                all_trades.append(replace(t, session=f"{t.session}_b{rep}"))
        else:
            all_trades.extend(trades_for_session)

    return session_metrics(all_trades)


def percentile(data: list, p: float) -> float:
    if not data:
        return 0.0
    s = sorted(data)
    i = (len(s) - 1) * p / 100.0
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (i - lo), 3)


def _pf(pf): return f"{pf:.2f}" if pf != float("inf") else "inf"
def _sep(n=72): print("  " + "-" * n)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs",             type=int,   default=200,
                        help="Bootstrap runs (default: 200)")
    parser.add_argument("--sessions-per-run", type=int,   default=43,
                        help="Sessions per run (default: 43 = same pool size)")
    parser.add_argument("--seed",             type=int,   default=None,
                        help="Random seed for reproducibility")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    print()
    print("=" * 72)
    print("  GIBBZ Bootstrap Treadmill — Validacion Estadistica")
    print(f"  {args.runs} runs x {args.sessions_per_run} sesiones (bootstrap con reemplazo)")
    print("=" * 72)

    pool = load_sessions()
    print(f"\n  Pool: {len(pool)} sesiones")

    cf = ContextFilter(enable_vol_release=True,
                       enable_destructive_regime=False,
                       enable_session_kill_switch=False)

    n_vol_release = sum(1 for s in pool if cf.is_session_filtered(s["session_type"]))
    print(f"  VOL_RELEASE (filtrado): {n_vol_release}")
    print(f"  Elegibles: {len(pool) - n_vol_release}")
    print(f"\n  Ejecutando {args.runs} runs...")
    print(f"  (Cachea sesiones individuales — primera ejecucion tarda mas)")
    print()

    # Cache de resultados por sesion (evita correr run_session multiple veces)
    session_cache: dict = {}

    results_pf    = []
    results_wr    = []
    results_exp   = []
    results_maxdd = []
    results_pnl   = []
    profitable_runs = 0

    for run_i in range(args.runs):
        if run_i % 20 == 0:
            print(f"  run {run_i:>3}/{args.runs}...", flush=True)
        m = run_single_bootstrap(pool, args.sessions_per_run, cf, session_cache)
        if m["n"] > 0:
            if m["pf"] != float("inf"):
                results_pf.append(m["pf"])
            results_wr.append(m["wr"])
            results_exp.append(m["exp"])
            results_maxdd.append(m["max_dd"])
            results_pnl.append(m["pnl"])
            if m["pf"] > 1.0:
                profitable_runs += 1

    valid_runs = len(results_pf)
    print(f"\n  Runs completados: {args.runs}  |  Con trades: {valid_runs}")
    print(f"  Sesiones unicas usadas: {len(session_cache)}")
    print()

    # ── Estadisticas ─────────────────────────────────────────────────────
    print("=" * 72)
    print("  DISTRIBUCION DE RESULTADOS")
    _sep()

    def stats_row(label: str, data: list, fmt: str = ".2f") -> None:
        if not data:
            print(f"  {label:<20}  (sin datos)")
            return
        mean = statistics.mean(data)
        std  = statistics.stdev(data) if len(data) > 1 else 0.0
        p5   = percentile(data, 5)
        p50  = percentile(data, 50)
        p95  = percentile(data, 95)
        f    = f"{{:{fmt}}}"
        print(f"  {label:<20}  "
              f"media={f.format(mean):>8}  "
              f"std={f.format(std):>7}  "
              f"p5={f.format(p5):>8}  "
              f"p50={f.format(p50):>8}  "
              f"p95={f.format(p95):>8}")

    print(f"  {'Metrica':<20}  {'media':>9}  {'std':>8}  {'p5':>9}  {'p50':>9}  {'p95':>9}")
    _sep()
    stats_row("Profit Factor",  results_pf,    ".3f")
    stats_row("Win Rate (%)",   results_wr,    ".1f")
    stats_row("Expectancy",     results_exp,   "+.2f")
    stats_row("MaxDD (pts)",    results_maxdd, ".2f")
    stats_row("PnL (pts)",      results_pnl,   "+.2f")

    pf_positive = sum(1 for x in results_pf if x > 1.0)
    pf_above_2  = sum(1 for x in results_pf if x >= 2.0)
    pf_above_25 = sum(1 for x in results_pf if x >= 2.5)

    print()
    print(f"  Runs con PF > 1.0:   {pf_positive}/{valid_runs}  "
          f"({100*pf_positive/max(valid_runs,1):.1f}%)")
    print(f"  Runs con PF >= 2.0:  {pf_above_2}/{valid_runs}  "
          f"({100*pf_above_2/max(valid_runs,1):.1f}%)")
    print(f"  Runs con PF >= 2.5:  {pf_above_25}/{valid_runs}  "
          f"({100*pf_above_25/max(valid_runs,1):.1f}%)")

    # Interpretacion de robustez
    pf_p5  = percentile(results_pf, 5)
    pf_med = percentile(results_pf, 50)
    print()
    print("  INTERPRETACION DE ROBUSTEZ:")
    print(f"  PF mediano:           {pf_med:.2f}  "
          f"({'>=2.3 solido' if pf_med >= 2.3 else '<2.3 debil'})")
    print(f"  PF percentil 5%:      {pf_p5:.2f}  "
          f"({'>=2.0 robusto' if pf_p5 >= 2.0 else '>=1.0 positivo' if pf_p5 >= 1.0 else '<1.0 negativo'})")
    exp_p5 = percentile(results_exp, 5)
    print(f"  Expectancy p5%:       {exp_p5:+.2f} pts/trade  "
          f"({'positivo' if exp_p5 > 0 else 'negativo'})")
    dd_p95 = percentile(results_maxdd, 95)
    print(f"  MaxDD percentil 95%:  {dd_p95:.2f} pts  "
          f"({'<=30 controlado' if dd_p95 <= 30 else '>30 revisar'})")

    # ── Criterios de aceptacion ────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  CRITERIOS DE ACEPTACION")
    _sep()

    pct_profitable = 100 * pf_positive / max(valid_runs, 1)
    checks = [
        ("PF mediano >= 2.3",        pf_med   >= 2.3,   f"{pf_med:.2f}",    ">= 2.3"),
        ("PF p5% >= 2.0",            pf_p5    >= 2.0,   f"{pf_p5:.2f}",     ">= 2.0"),
        ("Exp p5% > 0",              exp_p5   >  0,     f"{exp_p5:+.2f}",   "> 0.0"),
        ("MaxDD p95% <= 30 pts",     dd_p95   <= 30.0,  f"{dd_p95:.2f}",    "<= 30"),
        ("Runs PF>1 >= 90%",         pct_profitable >= 90.0,
                                     f"{pct_profitable:.1f}%",   ">= 90%"),
    ]
    all_pass = True
    for label, ok, got, expected in checks:
        icon = "[OK]  " if ok else "[FAIL]"
        print(f"  {icon}  {label:<32}  got={got:<10}  expected={expected}")
        if not ok:
            all_pass = False

    print()
    if all_pass:
        print("  BOOTSTRAP TREADMILL: PASS")
        print("  El edge se mantiene estadisticamente robusto a traves")
        print(f"  de {args.runs} permutaciones aleatorias del pool de sesiones.")
    else:
        print("  BOOTSTRAP TREADMILL: REVISAR")
        print("  Uno o mas criterios no se cumplen. Analizar distribucion.")
    print()
    print("=" * 72)
    print()


if __name__ == "__main__":
    main()
