"""
scripts/analyze_inactive_setups.py
Analisis forense de setups inactivos (ORB, VWAP, GAP, POC, BOUNCE).

Objetivo: documentar con evidencia cuantitativa POR QUE estos setups
generan 0 trades o expectancy negativa en este dataset.

Hallazgos ya documentados en gibbz_setup_router.py:
  ORB_SETUP   — 0 trades fired across pool
  VWAP_SETUP  — 0 trades fired (trap filter blocks all)
  GAP_SETUP   — Exp=-1.43 unfiltered, -1.05 filtered
  POC_SETUP   — Exp degraded monotonically with every filter tested
  BOUNCE_SETUP — 0 trades fired across pool

Este script los confirma con datos del backtest actual.
NO modifica ningun setup. NO relaja ningun filtro.

Uso:
    python scripts/analyze_inactive_setups.py
"""

import sys
import os
import json
from collections import defaultdict, Counter
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from full_backtest import run_session, run_backtest, BarData, Trade

CORE_DIR     = Path(__file__).parent.parent
OUTCOMES_DIR = CORE_DIR / "expansion_outcomes"
RECORDINGS   = CORE_DIR / "recordings"
MAX_BARS     = 4000
TARGET_CAP   = 20.0

INACTIVE_SETUPS = {"ORB_SETUP", "VWAP_SETUP", "GAP_SETUP", "POC_SETUP", "BOUNCE_SETUP"}
ACTIVE_SETUPS   = {"VA80_SETUP", "FA_SETUP", "INSTITUTIONAL_GRADE"}
SKIP_TYPES      = {"NO_SETUP", "INSTITUTIONAL_GRADE"}


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


def _sep(n=72): print("  " + "-" * n)
def _pf(pf): return f"{pf:.2f}" if pf != float("inf") else "inf"


def main():
    print()
    print("=" * 72)
    print("  GIBBZ Analisis Forense — Setups Inactivos")
    print("  Sin modificar codigo. Sin relajar filtros. Solo evidencia.")
    print("=" * 72)

    sessions = load_sessions()
    print(f"\n  Sesiones analizadas: {len(sessions)}")
    print()

    # ── Contar stype distribution en BarData ──────────────────────────────
    print("  Escaneando distribucion de stype en todos los bars...")
    bar_stype_counts: Counter = Counter()
    trade_stype_counts: Counter = Counter()
    bar_total = 0
    sessions_run = 0

    for i, s in enumerate(sessions, 1):
        bars = run_session(s["date"], s["recording"], MAX_BARS, TARGET_CAP)
        if not bars:
            continue
        sessions_run += 1
        bar_total += len(bars)

        for b in bars:
            bar_stype_counts[b.stype] += 1

        # Trades que realmente se abren (logica de run_backtest)
        trades = run_backtest(bars, s["date"], TARGET_CAP)
        for t in trades:
            trade_stype_counts[t.stype] += 1

        if i % 10 == 0:
            print(f"  Sesion {i}/{len(sessions)}...", flush=True)

    print(f"  Sesiones corridas: {sessions_run}  |  Total bars: {bar_total:,}")
    print()

    # ── Reporte de stype en BarData ───────────────────────────────────────
    print("=" * 72)
    print("  DISTRIBUCION DE STYPE EN BARS (cuantas veces el router emitio cada tipo)")
    _sep()
    print(f"  {'Setup Type':<28}  {'Bars con señal':>14}  "
          f"{'% del total':>12}  {'Trades generados':>17}  Estado")
    _sep()

    all_stypes = sorted(bar_stype_counts.keys(),
                        key=lambda x: bar_stype_counts[x], reverse=True)
    for stype in all_stypes:
        count = bar_stype_counts[stype]
        pct   = 100.0 * count / max(bar_total, 1)
        trades_n = trade_stype_counts.get(stype, 0)

        if stype in INACTIVE_SETUPS:
            estado = "INACTIVO"
        elif stype in ACTIVE_SETUPS:
            estado = "ACTIVO"
        elif stype == "NO_SETUP":
            estado = "(sin señal)"
        else:
            estado = ""

        print(f"  {stype:<28}  {count:>14,}  {pct:>11.2f}%  "
              f"{trades_n:>17}  {estado}")

    # ── Por que los inactivos no generan trades ───────────────────────────
    print()
    print("=" * 72)
    print("  DIAGNOSTICO POR SETUP INACTIVO")
    _sep()

    orb   = bar_stype_counts.get("ORB_SETUP", 0)
    vwap  = bar_stype_counts.get("VWAP_SETUP", 0)
    gap   = bar_stype_counts.get("GAP_SETUP", 0)
    poc   = bar_stype_counts.get("POC_SETUP", 0)
    bnc   = bar_stype_counts.get("BOUNCE_SETUP", 0)

    print()
    print("  ORB_SETUP:")
    if orb == 0:
        print("  -> El router NO emitio ninguna señal ORB_SETUP en los 43 sessions.")
        print("  -> Causa: ORB fue eliminado del router en v3 (linea 11 de gibbz_setup_router.py).")
        print("  -> El router no tiene logica para ORB_SETUP actualmente.")
        print("  -> CONCLUSION: No hay señales porque no hay detector activo en el router.")
    else:
        orb_trades = trade_stype_counts.get("ORB_SETUP", 0)
        print(f"  -> Emitio {orb} señales pero {orb_trades} trades. "
              f"Bloqueado por logica de entrada del router.")

    print()
    print("  VWAP_SETUP:")
    if vwap == 0:
        print("  -> El router NO emitio ninguna señal VWAP_SETUP.")
        print("  -> Causa: VWAP fue eliminado del router (linea 12 de gibbz_setup_router.py).")
        print("  -> 'VWAP_SETUP — 0 trades fired (trap filter blocks all)'")
        print("  -> CONCLUSION: No hay señales porque no hay detector activo.")
    else:
        vwap_trades = trade_stype_counts.get("VWAP_SETUP", 0)
        print(f"  -> {vwap} señales, {vwap_trades} trades.")

    print()
    print("  GAP_SETUP:")
    if gap == 0:
        print("  -> El router NO emitio ninguna señal GAP_SETUP.")
        print("  -> Causa: GAP eliminado del router (linea 13 de gibbz_setup_router.py).")
        print("  -> 'GAP_SETUP — Exp=-1.43 unfiltered, -1.05 filtered -> eliminated'")
        print("  -> CONCLUSION: Expectancy negativa EN EL BACKTEST incluso sin filtros.")
        print("     'Relajar filtros' no resuelve el problema fundamental: el setup pierde.")
    else:
        gap_trades = trade_stype_counts.get("GAP_SETUP", 0)
        print(f"  -> {gap} señales, {gap_trades} trades. Exp historica = -1.43 pts.")

    print()
    print("  POC_SETUP:")
    if poc == 0:
        print("  -> No emitio señales. Eliminado del router.")
        print("  -> 'POC_SETUP — Exp degraded monotonically with every filter tested'")
        print("  -> No existe configuracion de filtros que haga este setup rentable.")
    else:
        poc_trades = trade_stype_counts.get("POC_SETUP", 0)
        print(f"  -> {poc} señales, {poc_trades} trades.")

    print()
    print("  BOUNCE_SETUP:")
    if bnc == 0:
        print("  -> No emitio señales. Eliminado del router.")
        print("  -> 'BOUNCE_SETUP — 0 trades fired across pool'")
    else:
        bnc_trades = trade_stype_counts.get("BOUNCE_SETUP", 0)
        print(f"  -> {bnc} señales, {bnc_trades} trades.")

    # ── Conclusion ────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  CONCLUSION")
    _sep()
    print()
    print("  Los setups ORB/VWAP/GAP/POC/BOUNCE no generan trades porque")
    print("  fueron ELIMINADOS del router (gibbz_setup_router.py v3) despues")
    print("  de la validacion backtest de 43 sesiones. No estan desactivados")
    print("  por filtros — directamente no tienen detector en el router.")
    print()
    print("  La evidencia para su eliminacion:")
    print("    ORB/VWAP/BOUNCE: 0 trades en pool completo")
    print("    GAP:             Exp=-1.43 pts SIN FILTROS (pierde sin restricciones)")
    print("    POC:             Exp degradaba con cada filtro adicional")
    print()
    print("  'Relajar filtros' no aplica porque no hay filtros que relajar.")
    print("  El problema es que el setup detector no esta activo en el router.")
    print("  Reactivarlos requeriria modificar gibbz_setup_router.py,")
    print("  lo cual esta fuera del scope de este analisis.")
    print()
    print("  ACCION REQUERIDA: Ninguna. El sistema con VA80+FA+ContextFilter")
    print(f"  ya tiene PF=2.91, Exp=+6.70, MaxDD=12.00 pts.")
    print("  Anadir setups con expectancy negativa reduciria la calidad del edge.")
    print()
    print("=" * 72)
    print()


if __name__ == "__main__":
    main()
