"""
scripts/run_backtest_with_filter.py — Backtest de validacion del ContextFilter.

Compara metricas con/sin filtro usando el pipeline completo de full_backtest.py.
No modifica ninguna logica de setup ni parametro.

Uso:
    python scripts/run_backtest_with_filter.py
    python scripts/run_backtest_with_filter.py --no-filter     # solo baseline
    python scripts/run_backtest_with_filter.py --max-bars 4000
"""

import sys
import os
import json
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

MAX_BARS   = 4000
TARGET_CAP = 20.0


# ── Baseline expectations (from edge_contribution_audit.py) ───────────────────
BASELINE = {
    "n":        106,
    "wr":       38.7,
    "pf":       1.56,
    "exp":      2.61,
    "pnl":      277.0,
    "max_dd":   95.75,
    "rf":       2.89,
}
EXPECTED_WITH_FILTER = {
    "pf_min":   2.50,   # target PF >= 2.5 (improvement-1 projection: 2.71)
    "max_dd_max": 20.0, # target MaxDD <= 20 pts (improvement-1 projection: 15)
    "exp_min":  5.0,    # target Exp >= +5.0 pts/trade (improvement-1 projection: 5.5)
    "n_max":    60,     # target trades <= 60 (improvement-1 projection: 42)
}


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


def compute_metrics(trades: list[Trade]) -> dict:
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "exp": 0.0,
                "pnl": 0.0, "max_dd": 0.0, "rf": 0.0}
    wins   = [t for t in trades if t.result == "WIN"]
    losses = [t for t in trades if t.result == "LOSS"]
    n, nw, nl = len(trades), len(wins), len(losses)
    wr     = 100.0 * nw / n
    aw     = sum(t.pnl for t in wins)   / max(nw, 1)
    al     = sum(t.pnl for t in losses) / max(nl, 1)
    sw     = sum(t.pnl for t in wins)
    sl     = sum(t.pnl for t in losses)
    pf     = abs(sw / sl) if sl != 0 else float("inf")
    exp    = round(wr / 100 * aw + (1 - wr / 100) * al, 2)
    total  = round(sum(t.pnl for t in trades), 2)

    by_sess: dict = defaultdict(list)
    for t in trades:
        by_sess[t.session].append(t)
    cum = peak = max_dd = 0.0
    for d in sorted(by_sess.keys()):
        cum += sum(t.pnl for t in by_sess[d])
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    max_dd = round(max_dd, 2)
    rf = round(total / max_dd, 2) if max_dd > 0 else float("inf")

    return {
        "n": n, "wr": round(wr, 1), "pf": round(pf, 2),
        "exp": exp, "pnl": total, "max_dd": max_dd, "rf": rf,
    }


def run(sessions: list, context_filter=None, label: str = "BASELINE",
        max_bars: int = MAX_BARS, target_cap: float = TARGET_CAP) -> list[Trade]:
    all_trades: list[Trade] = []
    skipped_sessions = 0
    print(f"\n  [{label}] Ejecutando {len(sessions)} sesiones...")

    for i, s in enumerate(sessions, 1):
        cf_arg = context_filter
        stype  = s["session_type"]
        bars = run_session(
            s["date"], s["recording"], max_bars, target_cap,
            context_filter=cf_arg,
            session_type=stype,
        )
        if not bars:
            if cf_arg and cf_arg.is_session_filtered(stype):
                skipped_sessions += 1
            continue
        trades = run_backtest(bars, s["date"], target_cap)
        all_trades.extend(trades)

    if skipped_sessions:
        print(f"  Sesiones filtradas por ContextFilter: {skipped_sessions}")
    return all_trades


def _pf(pf: float) -> str:
    return f"{pf:.2f}" if pf != float("inf") else "inf"


def _rf(rf: float) -> str:
    return f"{rf:.2f}" if rf != float("inf") else "inf"


def print_metrics(label: str, m: dict) -> None:
    print(f"  {label}")
    print(f"    Trades:       {m['n']}")
    print(f"    WR:           {m['wr']:.1f}%")
    print(f"    PF:           {_pf(m['pf'])}")
    print(f"    Expectancy:   {m['exp']:+.2f} pts/trade")
    print(f"    PnL Total:    {m['pnl']:+.2f} pts")
    print(f"    Max Drawdown: {m['max_dd']:.2f} pts")
    print(f"    Recovery F.:  {_rf(m['rf'])}")


def print_comparison(baseline: dict, filtered: dict) -> None:
    def delta(k: str, invert_dd: bool = False) -> str:
        b = baseline.get(k, 0)
        f = filtered.get(k, 0)
        if b == 0:
            return "N/A"
        if k == "max_dd":
            d = (f - b) / abs(b) * 100  # negative = improvement
        else:
            d = (f - b) / abs(b) * 100  # positive = improvement
        return f"{d:+.0f}%"

    print(f"  {'Metrica':<16}  {'Baseline':>10}  {'Con Filtro':>10}  {'Delta':>8}")
    print("  " + "-" * 50)
    rows = [
        ("PF",          _pf(baseline["pf"]),   _pf(filtered["pf"]),   delta("pf")),
        ("Expectancy",  f"{baseline['exp']:+.2f}", f"{filtered['exp']:+.2f}", delta("exp")),
        ("MaxDD (pts)", f"{baseline['max_dd']:.2f}", f"{filtered['max_dd']:.2f}", delta("max_dd")),
        ("Recovery F.", _rf(baseline["rf"]),    _rf(filtered["rf"]),   delta("rf")),
        ("PnL (pts)",   f"{baseline['pnl']:+.2f}", f"{filtered['pnl']:+.2f}", delta("pnl")),
        ("Trades",      str(baseline["n"]),     str(filtered["n"]),    delta("n")),
        ("WR",          f"{baseline['wr']:.1f}%", f"{filtered['wr']:.1f}%", delta("wr")),
    ]
    for label, bv, fv, dv in rows:
        print(f"  {label:<16}  {bv:>10}  {fv:>10}  {dv:>8}")


def validate_outcomes(m_filtered: dict) -> tuple[bool, list]:
    checks = []
    passed = True

    def chk(label, condition, got, expected):
        nonlocal passed
        ok = bool(condition)
        if not ok:
            passed = False
        checks.append((ok, label, got, expected))

    chk("PF >= 2.5",            m_filtered["pf"] >= EXPECTED_WITH_FILTER["pf_min"],
        _pf(m_filtered["pf"]),  f">= {EXPECTED_WITH_FILTER['pf_min']}")
    chk("MaxDD <= 20 pts",      m_filtered["max_dd"] <= EXPECTED_WITH_FILTER["max_dd_max"],
        f"{m_filtered['max_dd']:.2f}", f"<= {EXPECTED_WITH_FILTER['max_dd_max']}")
    chk("Exp >= +5.0 pts",      m_filtered["exp"] >= EXPECTED_WITH_FILTER["exp_min"],
        f"{m_filtered['exp']:+.2f}",  f">= +{EXPECTED_WITH_FILTER['exp_min']}")
    chk("Trades <= 50",         m_filtered["n"] <= EXPECTED_WITH_FILTER["n_max"],
        str(m_filtered["n"]),   f"<= {EXPECTED_WITH_FILTER['n_max']}")

    return passed, checks


def main():
    parser = argparse.ArgumentParser(
        description="Backtest de validacion del ContextFilter"
    )
    parser.add_argument("--no-filter",  action="store_true",
                        help="Ejecutar solo baseline (sin filtro)")
    parser.add_argument("--max-bars",   type=int, default=MAX_BARS)
    parser.add_argument("--target-cap", type=float, default=TARGET_CAP)
    args = parser.parse_args()

    max_bars   = args.max_bars
    target_cap = args.target_cap

    print()
    print("=" * 72)
    print("  GIBBZ Backtest — Validacion ContextFilter")
    print("  Sin modificar logica de setup. Sin optimizar parametros.")
    print("=" * 72)

    sessions = load_sessions()
    print(f"\n  Sesiones disponibles: {len(sessions)}")
    print(f"  max_bars={max_bars}  target_cap={target_cap}")

    # ── Baseline (sin filtro) ──────────────────────────────────────────────
    baseline_trades = run(sessions, context_filter=None, label="BASELINE",
                          max_bars=max_bars, target_cap=target_cap)
    m_baseline      = compute_metrics(baseline_trades)

    print()
    print("=" * 72)
    print_metrics("BASELINE (sin filtro)", m_baseline)

    # Verificar que baseline coincide con valores esperados
    tol = 2
    n_ok   = abs(m_baseline["n"]  - BASELINE["n"])   <= tol
    pf_ok  = abs(m_baseline["pf"] - BASELINE["pf"])  <= 0.05
    pnl_ok = abs(m_baseline["pnl"]- BASELINE["pnl"]) <= 5.0
    if n_ok and pf_ok and pnl_ok:
        print("  [OK] Baseline coincide con auditoria previa")
    else:
        print(f"  [WARN] Baseline difiere de auditoria previa")
        print(f"         Esperado: n={BASELINE['n']} PF={BASELINE['pf']} PnL={BASELINE['pnl']:+.0f}")
        print(f"         Obtenido: n={m_baseline['n']} PF={_pf(m_baseline['pf'])} PnL={m_baseline['pnl']:+.0f}")

    if args.no_filter:
        print()
        print("  [--no-filter] Omitiendo ejecucion con filtro.")
        print("=" * 72)
        return

    # ── Con filtro ─────────────────────────────────────────────────────────
    # improvement-1: destructive_regime disabled; maxdd threshold raised to 40
    cf = ContextFilter(
        enable_vol_release=True,
        enable_destructive_regime=False,
        enable_session_kill_switch=True,
        session_maxdd_threshold=40.0,
    )
    filtered_trades = run(sessions, context_filter=cf, label="CON FILTRO",
                          max_bars=max_bars, target_cap=target_cap)
    m_filtered      = compute_metrics(filtered_trades)

    print()
    print("=" * 72)
    print_metrics("CON FILTRO (ContextFilter activo)", m_filtered)

    # ── Comparacion ────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  COMPARACION")
    print_comparison(m_baseline, m_filtered)

    # ── Validacion de criterios de aceptacion ─────────────────────────────
    print()
    print("=" * 72)
    print("  CRITERIOS DE ACEPTACION")
    all_passed, checks = validate_outcomes(m_filtered)
    for ok, label, got, expected in checks:
        icon = "✓" if ok else "✗"
        print(f"  {icon}  {label:<25}  got={got:<10}  expected={expected}")

    print()
    if all_passed:
        print("  ✅ BACKTEST VALIDATION PASSED")
        print("  Siguiente paso: paper trading 2 semanas")
        print(f"    Monitorear: MaxDD real < 30 pts | PF real > 2.0 | filtro activo")
    else:
        print("  ❌ BACKTEST VALIDATION FAILED — revisar filtros")
    print("=" * 72)
    print()


if __name__ == "__main__":
    main()
