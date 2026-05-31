"""
scripts/expand_backtest_all_sessions.py
Reporte completo de TODAS las 43 sesiones con ContextFilter.

Responde:
  - Cuantas sesiones hay realmente (no "21 analizadas, 22 pendientes" — son 43 total)
  - Que genera cada tipo de sesion en trades y PnL
  - Por que WATCH sessions no generan mas trades
  - Breakdown por session_type con y sin filtro

Uso:
    python scripts/expand_backtest_all_sessions.py
"""

import sys
import os
import json
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


def load_all_sessions() -> list:
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
            "ep_score":     exp.get("ep_score", 0),
            "ets_max":      exp.get("ets_max", 0),
        })
    return [s for s in sessions if s["valid"]]


def metrics(trades: list) -> dict:
    if not trades:
        return {"n": 0, "wr": 0.0, "pf": 0.0, "exp": 0.0, "pnl": 0.0}
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
    return {
        "n": n, "wr": round(wr, 1), "pf": round(pf, 2),
        "exp": exp, "pnl": round(sw + sl, 2),
    }


def _pf(pf): return f"{pf:.2f}" if pf != float("inf") else "inf"
def _sep(n=72): print("  " + "-" * n)


def main():
    print()
    print("=" * 72)
    print("  GIBBZ — Backtest Completo 43 Sesiones")
    print("  Con y Sin ContextFilter")
    print("=" * 72)

    sessions = load_all_sessions()
    cf = ContextFilter(enable_vol_release=True,
                       enable_destructive_regime=False,
                       enable_session_kill_switch=False)

    print(f"\n  Total sesiones con grabacion: {len(sessions)}")
    from collections import Counter
    type_dist = Counter(s["session_type"] for s in sessions)
    print(f"  Distribucion por tipo:")
    for k, v in sorted(type_dist.items()):
        filtered = "→ FILTRADO" if cf.is_session_filtered(k) else ""
        print(f"    {k:<20}: {v}  {filtered}")

    # ── Correr backtest sin filtro ─────────────────────────────────────────
    print()
    print("  [1/2] Ejecutando SIN filtro...")
    trades_no_filter: list[Trade] = []
    session_results_no = {}
    for s in sessions:
        bars = run_session(s["date"], s["recording"], MAX_BARS, TARGET_CAP)
        trs  = run_backtest(bars, s["date"], TARGET_CAP) if bars else []
        trades_no_filter.extend(trs)
        session_results_no[s["date"]] = {"n": len(trs), "pnl": round(sum(t.pnl for t in trs), 2),
                                          "type": s["session_type"]}

    # ── Correr backtest con filtro ─────────────────────────────────────────
    print()
    print("  [2/2] Ejecutando CON ContextFilter (VOL_RELEASE)...")
    trades_filtered: list[Trade] = []
    session_results_cf = {}
    skipped = 0
    for s in sessions:
        stype = s["session_type"]
        if cf.is_session_filtered(stype):
            skipped += 1
            session_results_cf[s["date"]] = {"n": 0, "pnl": 0.0, "type": stype, "filtered": True}
            continue
        bars = run_session(s["date"], s["recording"], MAX_BARS, TARGET_CAP,
                           context_filter=cf, session_type=stype)
        trs  = run_backtest(bars, s["date"], TARGET_CAP) if bars else []
        trades_filtered.extend(trs)
        session_results_cf[s["date"]] = {"n": len(trs), "pnl": round(sum(t.pnl for t in trs), 2),
                                          "type": stype, "filtered": False}

    # ── Metricas globales ─────────────────────────────────────────────────
    m_no = metrics(trades_no_filter)
    m_cf = metrics(trades_filtered)

    print()
    print("=" * 72)
    print("  METRICAS GLOBALES")
    _sep()
    print(f"  {'Metrica':<18}  {'Sin Filtro':>12}  {'Con Filtro':>12}  {'Delta':>8}")
    _sep(55)
    rows = [
        ("Trades",     str(m_no["n"]),            str(m_cf["n"]),            ""),
        ("WR",         f"{m_no['wr']:.1f}%",       f"{m_cf['wr']:.1f}%",      ""),
        ("PF",         _pf(m_no["pf"]),            _pf(m_cf["pf"]),           ""),
        ("Expectancy", f"{m_no['exp']:+.2f}",      f"{m_cf['exp']:+.2f}",     ""),
        ("PnL (pts)",  f"{m_no['pnl']:+.2f}",      f"{m_cf['pnl']:+.2f}",     ""),
        ("Filtradas",  "0",                         str(skipped),              ""),
    ]
    for label, v1, v2, _ in rows:
        print(f"  {label:<18}  {v1:>12}  {v2:>12}")

    # ── Breakdown por session type (con filtro) ────────────────────────────
    print()
    print("  BREAKDOWN POR SESSION TYPE (con ContextFilter)")
    _sep()
    by_type: dict = defaultdict(list)
    for t in trades_filtered:
        stype = session_results_cf.get(t.session, {}).get("type", "?")
        by_type[stype].append(t)

    print(f"  {'Tipo':<22}  {'N sess':>6}  {'Trades':>7}  "
          f"{'WR':>6}  {'PF':>6}  {'Exp':>7}  {'PnL':>9}  Estado")
    _sep()
    for stype in sorted(type_dist.keys()):
        n_sess = type_dist[stype]
        if cf.is_session_filtered(stype):
            print(f"  {stype:<22}  {n_sess:>6}  {'--':>7}  "
                  f"{'--':>6}  {'--':>6}  {'--':>7}  {'--':>9}  FILTRADO")
            continue
        trs = by_type.get(stype, [])
        m   = metrics(trs)
        print(f"  {stype:<22}  {n_sess:>6}  {m['n']:>7}  "
              f"{m['wr']:>5.1f}%  {_pf(m['pf']):>6}  "
              f"{m['exp']:>+7.2f}  {m['pnl']:>+9.2f}  --")

    # ── Sesiones que generan 0 trades (no filtradas) ───────────────────────
    zero_trade_sessions = [
        (d, r) for d, r in session_results_cf.items()
        if not r.get("filtered") and r["n"] == 0
    ]
    if zero_trade_sessions:
        print()
        print(f"  SESIONES SIN TRADES (elegibles pero 0 señales) — {len(zero_trade_sessions)}")
        _sep()
        for d, r in sorted(zero_trade_sessions):
            print(f"  {d}  tipo={r['type']}")

    # ── Sesiones que si generan trades ────────────────────────────────────
    trade_sessions = [
        (d, r) for d, r in session_results_cf.items()
        if not r.get("filtered") and r["n"] > 0
    ]
    print()
    print(f"  SESIONES CON TRADES — {len(trade_sessions)}")
    _sep()
    print(f"  {'Fecha':<14}  {'Tipo':<22}  {'Trades':>7}  {'PnL':>9}")
    _sep(55)
    for d, r in sorted(trade_sessions):
        print(f"  {d:<14}  {r['type']:<22}  {r['n']:>7}  {r['pnl']:>+9.2f}")

    # ── Criterios de aceptacion ────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  CRITERIOS DE ACEPTACION")
    _sep()
    checks = [
        ("PF con filtro >= 2.3",   m_cf["pf"] >= 2.3,   _pf(m_cf["pf"]),   ">= 2.3"),
        ("Trades con filtro >= 25", m_cf["n"]  >= 25,    str(m_cf["n"]),    ">= 25"),
        ("Exp con filtro >= +4.0",  m_cf["exp"] >= 4.0,  f"{m_cf['exp']:+.2f}", ">= +4.0"),
    ]
    all_pass = True
    for label, ok, got, exp_val in checks:
        icon = "[OK]" if ok else "[FAIL]"
        print(f"  {icon}  {label:<32}  got={got:<10}  expected={exp_val}")
        if not ok:
            all_pass = False
    print()
    if all_pass:
        print("  TODOS LOS CRITERIOS CUMPLIDOS")
    else:
        print("  ALGUNOS CRITERIOS FALLARON — revisar distribucion de sesiones")
    print()
    print("=" * 72)
    print()


if __name__ == "__main__":
    main()
