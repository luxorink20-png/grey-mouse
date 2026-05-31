"""
scripts/run_backtest_with_level2.py
Dry Run: Backtest comparativo con Level 2 Proxy Features.

Metodologia:
  1. Corre el backtest baseline identico al actual (run_session + run_backtest)
  2. Extrae independientemente las barras raw de cada grabacion
  3. Computa L2 proxy features (iceberg, absorcion, imbalance) por barra
  4. Aplica filtro L2 conservador a las senales baseline
  5. Compara metricas antes y despues del filtro L2

LIMITACIONES CRITICAS (leer antes de interpretar resultados):
  - Los features son PROXIES de L2, no datos de order book reales
  - n=32 trades es estadisticamente pequeno — cualquier cambio en 2-5 trades
    mueve PF significativamente; la mejora puede ser ruido
  - El filtro conservador (requiere 2 proxies convergentes) minimiza
    el riesgo de over-filter pero tambien limita el beneficio potencial
  - Veredicto de implementar SOLO si mejora es estadisticamente solida
    Y robusta en todas las sesiones con trades

USO:
    python scripts/run_backtest_with_level2.py
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
from scripts.level2_features_bar import (
    extract_raw_bars,
    compute_l2_features,
    l2_filter_decision,
    session_l2_summary,
)

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
            "rec_path":     rpath,
            "valid":        valid,
            "session_type": exp.get("session_type", "UNKNOWN"),
        })
    return [s for s in sessions if s["valid"]]


def compute_metrics(trades: list[Trade]) -> dict:
    if not trades:
        return {"n": 0, "wins": 0, "losses": 0, "wr": 0.0,
                "pf": 0.0, "exp": 0.0, "pnl": 0.0, "max_dd": 0.0}
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
    tot = round(sw + sl, 2)
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
    return {"n": n, "wins": nw, "losses": nl, "wr": round(wr, 1),
            "pf": round(pf, 2), "exp": exp, "pnl": tot, "max_dd": round(max_dd, 2)}


def _pf(pf): return f"{pf:.2f}" if pf != float("inf") else "inf"
def _sep(n=72): print("  " + "-" * n)


def main() -> None:
    print()
    print("=" * 72)
    print("  GIBBZ Dry Run — Backtest con Level 2 Proxy Features")
    print("  Sin modificar codigo existente. Solo analisis comparativo.")
    print("=" * 72)
    print()
    print("  LIMITACION: features son proxies, NO datos de order book real.")
    print("  Con n=32 trades, cambios de 2-5 trades alteran PF significativamente.")
    print("  Interpretar resultados con cautela estadistica.")
    print()

    sessions = load_sessions()
    cf = ContextFilter(enable_vol_release=True,
                       enable_destructive_regime=False,
                       enable_session_kill_switch=False)

    # ── Fase 1: Backtest baseline (identico al actual) ─────────────────────
    print("  FASE 1 — Backtest baseline (replicando resultado conocido)")
    _sep()

    baseline_trades: list[Trade] = []
    session_bars: dict   = {}   # date → list[BarData]

    for i, s in enumerate(sessions, 1):
        stype = s["session_type"]
        if cf.is_session_filtered(stype):
            continue
        bars = run_session(s["date"], s["recording"], MAX_BARS, TARGET_CAP,
                           context_filter=cf, session_type=stype)
        if not bars:
            continue
        trades = run_backtest(bars, s["date"], TARGET_CAP)
        baseline_trades.extend(trades)
        if trades:
            session_bars[s["date"]] = bars
            print(f"  {s['date']}  {stype:<20}  {len(trades)} trades")

    m_base = compute_metrics(baseline_trades)
    print()
    print(f"  Baseline: n={m_base['n']}  WR={m_base['wr']:.1f}%  "
          f"PF={_pf(m_base['pf'])}  Exp={m_base['exp']:+.2f}  "
          f"PnL={m_base['pnl']:+.2f}  MaxDD={m_base['max_dd']:.2f}")

    if m_base["n"] == 0:
        print("  ERROR: 0 trades en baseline. Verificar configuracion.")
        return

    # Validar que baseline coincide con resultado conocido (PF~2.91)
    expected_pf = 2.91
    pf_diff = abs(m_base["pf"] - expected_pf) if m_base["pf"] != float("inf") else 999
    if pf_diff < 0.05:
        print(f"  [OK] Baseline PF={_pf(m_base['pf'])} coincide con resultado conocido ({expected_pf})")
    else:
        print(f"  [WARN] Baseline PF={_pf(m_base['pf'])} difiere del esperado ({expected_pf})")
    print()

    # ── Fase 2: Extraer L2 features de grabaciones ─────────────────────────
    print("  FASE 2 — Extrayendo L2 proxy features de grabaciones")
    _sep()

    sessions_with_trades = set(t.session for t in baseline_trades)
    l2_by_session: dict = {}   # date → dict[bar_idx, BarL2Features]
    l2_summary_by_session: dict = {}

    for s in sessions:
        if s["date"] not in sessions_with_trades:
            continue
        if not s["rec_path"]:
            continue

        max_bar_needed = max(
            (t.entry_bar for t in baseline_trades if t.session == s["date"]),
            default=100
        ) + 20

        print(f"  {s['date']}: extracting raw bars...", end=" ", flush=True)
        raw_bars = extract_raw_bars(s["rec_path"], max_bars=max_bar_needed + 50)
        print(f"{len(raw_bars)} bars OK", end="  ")

        features = compute_l2_features(raw_bars)
        l2_by_session[s["date"]] = features

        summary = session_l2_summary(features)
        l2_summary_by_session[s["date"]] = summary
        print(f"icebergs={summary['icebergs']}  "
              f"absorptions={summary['absorptions']}  "
              f"imbalances={summary['imbalances']}")

    print()

    # ── Fase 3: Aplicar filtro L2 a trades baseline ─────────────────────────
    print("  FASE 3 — Aplicando filtro L2 conservador a trades baseline")
    _sep()
    print("  (Filtro requiere 2+ proxies opuestos convergentes para skip)")
    print()

    filtered_trades: list[Trade] = []
    skipped_trades: list[dict]   = []

    for t in baseline_trades:
        features = l2_by_session.get(t.session)
        if features is None:
            filtered_trades.append(t)
            continue

        skip, reason = l2_filter_decision(
            trade_direction = t.direction,
            entry_bar       = t.entry_bar,
            features        = features,
            window          = 5,
        )

        if skip:
            skipped_trades.append({
                "session":   t.session,
                "bar":       t.entry_bar,
                "stype":     t.stype,
                "direction": t.direction,
                "pnl":       t.pnl,
                "result":    t.result,
                "reason":    reason,
            })
        else:
            filtered_trades.append(t)

    m_l2 = compute_metrics(filtered_trades)

    print(f"  Trades baseline:   {m_base['n']}")
    print(f"  Trades skippeados: {len(skipped_trades)}")
    print(f"  Trades restantes:  {m_l2['n']}")
    print()

    if skipped_trades:
        print("  Trades skippeados por L2:")
        wins_skipped  = sum(1 for s in skipped_trades if s["result"] == "WIN")
        losses_skipped= sum(1 for s in skipped_trades if s["result"] == "LOSS")
        pnl_skipped   = sum(s["pnl"] for s in skipped_trades)
        print(f"  {'Sesion':<14}  {'Stype':<22}  {'Dir':<6}  "
              f"{'PnL':>7}  {'Result':<7}  Razon L2")
        _sep(80)
        for s in skipped_trades:
            print(f"  {s['session']:<14}  {s['stype']:<22}  {s['direction']:<6}  "
                  f"{s['pnl']:>+7.2f}  {s['result']:<7}  {s['reason'][:45]}")
        print()
        print(f"  PnL de trades skippeados: {pnl_skipped:+.2f} pts  "
              f"({wins_skipped}W / {losses_skipped}L)")
    print()

    # ── Fase 4: Comparacion y veredicto ─────────────────────────────────────
    print("=" * 72)
    print("  COMPARACION BASELINE vs CON FILTRO L2")
    _sep()
    print(f"  {'Metrica':<18}  {'Baseline':>12}  {'Con L2':>12}  {'Delta':>10}")
    _sep(60)

    def delta_str(a, b):
        if a == float("inf") or b == float("inf"):
            return "N/A"
        d = b - a
        return f"{d:+.3f}"

    rows = [
        ("Trades",      str(m_base["n"]),             str(m_l2["n"]),
         str(m_l2["n"] - m_base["n"])),
        ("WR",          f"{m_base['wr']:.1f}%",        f"{m_l2['wr']:.1f}%",
         f"{m_l2['wr'] - m_base['wr']:+.1f}pp"),
        ("PF",          _pf(m_base["pf"]),             _pf(m_l2["pf"]),
         delta_str(m_base["pf"], m_l2["pf"])),
        ("Expectancy",  f"{m_base['exp']:+.2f}",       f"{m_l2['exp']:+.2f}",
         f"{m_l2['exp'] - m_base['exp']:+.2f}"),
        ("PnL (pts)",   f"{m_base['pnl']:+.2f}",       f"{m_l2['pnl']:+.2f}",
         f"{m_l2['pnl'] - m_base['pnl']:+.2f}"),
        ("MaxDD",       f"{m_base['max_dd']:.2f}",     f"{m_l2['max_dd']:.2f}",
         f"{m_l2['max_dd'] - m_base['max_dd']:+.2f}"),
    ]
    for label, bv, lv, dv in rows:
        print(f"  {label:<18}  {bv:>12}  {lv:>12}  {dv:>10}")

    # ── Veredicto ─────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    print("  VEREDICTO")
    _sep()

    n_skipped = len(skipped_trades)

    # Calcular mejora
    if m_base["pf"] != float("inf") and m_l2["pf"] != float("inf"):
        pf_delta = m_l2["pf"] - m_base["pf"]
    else:
        pf_delta = 0.0
    exp_delta = m_l2["exp"] - m_base["exp"]
    dd_delta  = m_base["max_dd"] - m_l2["max_dd"]  # positive = improvement

    print()

    # Evaluacion estadistica
    n_total = m_base["n"]
    pct_skipped = n_skipped / max(n_total, 1) * 100
    if n_skipped == 0:
        print("  FILTRO L2: 0 trades filtrados.")
        print("  Los proxy features no detectaron senales opuestas convergentes")
        print("  suficientemente fuertes en el window de las entradas actuales.")
        print()
        print("  INTERPRETACION: los trades actuales NO coinciden con los patrones")
        print("  de L2 que el filtro buscaba. El edge existente opera en momentos")
        print("  que ya tienen claridad directional suficiente (sin icebergs/absorcion opuesta).")
        print()
        verdict = "NO_CHANGE"

    elif pf_delta > 0.30 and exp_delta > 1.0:
        print(f"  PF: {_pf(m_base['pf'])} → {_pf(m_l2['pf'])} ({pf_delta:+.2f})")
        print(f"  Exp: {m_base['exp']:+.2f} → {m_l2['exp']:+.2f} ({exp_delta:+.2f} pts)")
        print(f"  {n_skipped} trades filtrados ({pct_skipped:.1f}% del total)")
        print()
        print("  MEJORA SIGNIFICATIVA DETECTADA.")
        print()
        print("  ADVERTENCIA ESTADISTICA:")
        print(f"  n={n_total} trades es una muestra pequena. Una mejora de PF={pf_delta:+.2f}")
        print(f"  filtrando {n_skipped} trades ({pct_skipped:.1f}%) puede ser ruido.")
        print("  Para confirmar que L2 features aportan edge real necesitarias:")
        print(f"  - Al menos 100+ trades (actual: {n_total})")
        print("  - Validacion out-of-sample con grabaciones nuevas")
        print("  - Confirmar que la mejora es consistente por sesion")
        print()
        verdict = "POSSIBLE_IMPROVEMENT"

    elif pf_delta > 0 and exp_delta > 0:
        print(f"  Mejora marginal: PF {pf_delta:+.2f}, Exp {exp_delta:+.2f}")
        verdict = "MARGINAL"

    elif pf_delta < -0.10:
        print(f"  L2 filter empeora el sistema (PF {pf_delta:+.2f}).")
        print("  Los proxies estan filtrando trades buenos, no malos.")
        verdict = "DEGRADATION"

    else:
        print(f"  Cambio insignificante: PF {pf_delta:+.2f}, Exp {exp_delta:+.2f}")
        verdict = "NEUTRAL"

    print()
    print("  DECISION RECOMENDADA:")
    print()
    if verdict == "NO_CHANGE":
        print("  -> Proceder a paper trading SIN L2 features.")
        print("     Los proxies no encuentran conflicto con las entradas actuales.")
        print("     Agregar L2 en el futuro cuando se tengan datos de order book real.")
    elif verdict == "POSSIBLE_IMPROVEMENT":
        print("  -> INVESTIGAR la mejora antes de implementar.")
        print("     Revisar manualmente cada trade filtrado.")
        print("     Si los trades filtrados son perdedores sistematicos → considerar.")
        print("     Si son aleatorios → probable ruido en muestra pequena.")
        print("     NO implementar sin validacion adicional.")
    elif verdict == "MARGINAL":
        print("  -> NO implementar todavia. Beneficio insuficiente para justificar")
        print("     complejidad adicional. Proceder a paper trading.")
    elif verdict == "DEGRADATION":
        print("  -> NO implementar. L2 proxies degradan el sistema en este dataset.")
        print("     Proceder a paper trading con sistema actual.")
    else:
        print("  -> Cambio neutral. Proceder a paper trading con sistema actual.")

    print()

    # ── Actividad L2 por sesion ────────────────────────────────────────────
    if l2_summary_by_session:
        print("=" * 72)
        print("  ACTIVIDAD L2 POR SESION")
        _sep()
        print(f"  {'Sesion':<14}  {'Barras':>6}  {'Icebergs':>9}  "
              f"{'Absorc.':>8}  {'Imbal.':>8}  {'Buy':>5}  {'Sell':>5}")
        _sep(65)
        for date, s in sorted(l2_summary_by_session.items()):
            print(f"  {date:<14}  {s['n_bars']:>6}  "
                  f"{s['icebergs']:>5}({s['icebergs_pct']:>4.1f}%)  "
                  f"{s['absorptions']:>4}({s['absorptions_pct']:>4.1f}%)  "
                  f"{s['imbalances']:>5}  {s['buy_imbalances']:>5}  "
                  f"{s['sell_imbalances']:>5}")

    print()
    print("=" * 72)
    print()


if __name__ == "__main__":
    main()
