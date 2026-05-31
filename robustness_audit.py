"""
robustness_audit.py — GIBBZ Edge Robustness Audit (Modo Cientifico)
Sin modificar codigo. Sin ajustar parametros. Solo analisis.

Responde: el edge encontrado en edge_validation.py es robusto,
distribuido, o dependiente de pocas sesiones/setups?

Fases:
  1 — Concentracion del PnL (Top 1/3/5/10 sesiones)
  2 — Test de resiliencia (escenarios Base/A/B/C/D)
  3 — Analisis por regimen
  4 — Analisis por setup
  5 — Dependencia de setups
  6 — Estabilidad temporal (por mes de contexto)
  7 — Robustez estadistica (PF, Sharpe, Recovery Factor)
  8 — Edge Survival Score (0-100)
  9 — Veredicto final
"""

import json
import os
import sys
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Reconfigure stdout to UTF-8 so HistoricalContextLoader box-drawing chars
# don't fail on Windows cp1252 terminals.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from full_backtest import run_session, run_backtest, Trade  # no modifications

CORE_DIR       = Path(__file__).parent
OUTCOMES_DIR   = CORE_DIR / "expansion_outcomes"
RECORDINGS_DIR = CORE_DIR / "recordings"

MAX_BARS   = 4000
TARGET_CAP = 20.0


# ── Helpers ────────────────────────────────────────────────────────────────────

def compute_metrics(trades: list) -> dict:
    if not trades:
        return {"n": 0, "wins": 0, "losses": 0, "wr": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0,
                "expectancy": 0.0, "total_pnl": 0.0}
    wins   = [t for t in trades if t.result == "WIN"]
    losses = [t for t in trades if t.result == "LOSS"]
    n, nw, nl = len(trades), len(wins), len(losses)
    wr       = 100.0 * nw / n
    avg_win  = sum(t.pnl for t in wins)   / max(nw, 1)
    avg_loss = sum(t.pnl for t in losses) / max(nl, 1)
    sum_wins   = sum(t.pnl for t in wins)
    sum_losses = sum(t.pnl for t in losses)
    pf  = abs(sum_wins / sum_losses) if sum_losses != 0 else float("inf")
    exp = round(wr / 100 * avg_win + (1 - wr / 100) * avg_loss, 2)
    return {
        "n": n, "wins": nw, "losses": nl, "wr": round(wr, 1),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "profit_factor": round(pf, 2), "expectancy": exp,
        "total_pnl": round(sum(t.pnl for t in trades), 2),
    }


def _sep(n: int = 70) -> None:
    print("  " + "-" * n)


def load_sessions() -> list:
    sessions = []
    for ef in sorted(OUTCOMES_DIR.glob("*_expansion.json")):
        with open(ef, encoding="utf-8") as f:
            exp = json.load(f)
        sdate = exp.get("session_date", ef.stem.replace("_expansion", ""))
        rf    = exp.get("recording_file", "")
        rpath = RECORDINGS_DIR / rf if rf else None
        valid = bool(rpath and rpath.exists() and rpath.stat().st_size > 0)
        sessions.append({
            "date":         sdate,
            "recording":    rf,
            "valid":        valid,
            "session_type": exp.get("session_type", "UNKNOWN"),
        })
    return [s for s in sessions if s["valid"]]


def run_full_backtest(sessions: list) -> tuple:
    all_trades: list[Trade] = []
    sessions_run = 0
    for i, s in enumerate(sessions, 1):
        print(f"  [{i:02d}/{len(sessions)}] {s['date']} ({s['recording']}) ...",
              end=" ", flush=True)
        bars   = run_session(s["date"], s["recording"], MAX_BARS, TARGET_CAP)
        if not bars:
            print("0 bars -- SKIP")
            continue
        trades = run_backtest(bars, s["date"], TARGET_CAP)
        sessions_run += 1
        all_trades.extend(trades)
        w   = sum(1 for t in trades if t.result == "WIN")
        pnl = round(sum(t.pnl for t in trades), 2)
        print(f"{len(bars)} bars | {len(trades)} trades | "
              f"WR={100*w/max(len(trades),1):.0f}% | PnL={pnl:+.1f}")
    return all_trades, sessions_run


# ── Phase 1: PnL Concentration ─────────────────────────────────────────────────

def phase1_concentration(all_trades: list, type_map: dict) -> dict:
    by_sess: dict = defaultdict(list)
    for t in all_trades:
        by_sess[t.session].append(t)

    sess_pnls = {s: round(sum(t.pnl for t in ts), 2) for s, ts in by_sess.items()}
    total_pnl = sum(sess_pnls.values())
    total_abs = sum(abs(v) for v in sess_pnls.values())

    sorted_by_pnl = sorted(sess_pnls.items(), key=lambda x: x[1], reverse=True)

    def top_pct(n: int) -> float:
        top_abs = sum(abs(v) for _, v in sorted_by_pnl[:n])
        return round(100.0 * top_abs / max(total_abs, 0.01), 1)

    return {
        "sorted_sessions": sorted_by_pnl,
        "total_pnl":       round(total_pnl, 2),
        "total_abs":       round(total_abs, 2),
        "profitable":      sum(1 for v in sess_pnls.values() if v > 0),
        "total_sessions":  len(sess_pnls),
        "top1_pct":        top_pct(1),
        "top3_pct":        top_pct(3),
        "top5_pct":        top_pct(5),
        "top10_pct":       top_pct(min(10, len(sorted_by_pnl))),
    }


# ── Phase 2: Resilience Scenarios ──────────────────────────────────────────────

def phase2_resilience(all_trades: list, conc: dict) -> list:
    sorted_sessions = conc["sorted_sessions"]

    def scenario(n_exclude: int, label: str) -> dict:
        excl = {s for s, _ in sorted_sessions[:n_exclude]}
        filtered = [t for t in all_trades if t.session not in excl]
        m = compute_metrics(filtered)
        tag = "POSITIVO" if m["expectancy"] > 0 else "NEGATIVO"
        return {"label": label, "excluded": excl, "metrics": m, "tag": tag}

    return [
        scenario(0,  "BASE (todas las sesiones)"),
        scenario(1,  "Escenario A (sin Top 1)"),
        scenario(3,  "Escenario B (sin Top 3)"),
        scenario(5,  "Escenario C (sin Top 5)"),
        scenario(min(10, len(sorted_sessions)), "Escenario D (sin Top 10)"),
    ]


# ── Phase 3: Regime Analysis ────────────────────────────────────────────────────

def phase3_regime(all_trades: list, type_map: dict) -> dict:
    by_regime: dict = defaultdict(list)
    for t in all_trades:
        by_regime[type_map.get(t.session, "UNKNOWN")].append(t)

    result = {}
    for regime, trades in by_regime.items():
        m = compute_metrics(trades)
        result[regime] = {**m, "sess_count": len(set(t.session for t in trades))}
    return result


# ── Phase 4: Setup Analysis ─────────────────────────────────────────────────────

def phase4_setup(all_trades: list) -> dict:
    by_setup: dict = defaultdict(list)
    for t in all_trades:
        by_setup[t.stype].append(t)
    return {stype: compute_metrics(ts) for stype, ts in by_setup.items()}


# ── Phase 5: Setup Dependency ───────────────────────────────────────────────────

def phase5_setup_dependency(all_trades: list, setup_metrics: dict) -> dict:
    sorted_setups = sorted(
        [(k, v) for k, v in setup_metrics.items() if v["n"] > 0],
        key=lambda x: x[1]["expectancy"], reverse=True
    )

    def scenario(n_excl: int, label: str) -> dict:
        excl = {s for s, _ in sorted_setups[:n_excl]}
        filtered = [t for t in all_trades if t.stype not in excl]
        m = compute_metrics(filtered)
        tag = "SOBREVIVE" if m["expectancy"] > 0 else "DESTRUIDO"
        return {"label": label, "excluded": excl, "metrics": m, "tag": tag}

    return {
        "sorted_setups": sorted_setups,
        "scenarios": [
            scenario(0, "BASE"),
            scenario(1, "Sin mejor setup"),
            scenario(2, "Sin top 2 setups"),
            scenario(3, "Sin top 3 setups"),
        ],
    }


# ── Phase 6: Temporal Stability ─────────────────────────────────────────────────

def phase6_temporal(all_trades: list) -> dict:
    by_date: dict = defaultdict(list)
    by_month: dict = defaultdict(list)
    for t in all_trades:
        by_date[t.session].append(t)
        month = t.session[:7] if len(t.session) >= 7 else "UNKNOWN"
        by_month[month].append(t)

    date_metrics  = {d: compute_metrics(ts) for d, ts in sorted(by_date.items())}
    month_metrics = {m: compute_metrics(ts) for m, ts in sorted(by_month.items())}

    sess_pnls = [m["total_pnl"] for m in date_metrics.values()]
    if len(sess_pnls) > 1 and statistics.mean(sess_pnls) != 0:
        mean_pnl = statistics.mean(sess_pnls)
        std_pnl  = statistics.stdev(sess_pnls)
        cv = round(100 * std_pnl / abs(mean_pnl), 1)
    else:
        mean_pnl = sess_pnls[0] if sess_pnls else 0.0
        std_pnl  = 0.0
        cv = 0.0

    return {
        "by_date":   date_metrics,
        "by_month":  month_metrics,
        "mean_pnl":  round(mean_pnl, 2),
        "std_pnl":   round(std_pnl,  2),
        "cv":        cv,
    }


# ── Phase 7: Statistical Robustness ────────────────────────────────────────────

def phase7_stats(all_trades: list) -> dict:
    m = compute_metrics(all_trades)
    trade_pnls = [t.pnl for t in all_trades]

    if len(trade_pnls) > 1:
        std_t = statistics.stdev(trade_pnls)
        sharpe = round(statistics.mean(trade_pnls) / std_t, 3) if std_t > 0 else 0.0
    else:
        sharpe = 0.0

    # Session-level cumulative drawdown
    by_sess: dict = defaultdict(list)
    for t in all_trades:
        by_sess[t.session].append(t)
    cum = peak = max_dd = 0.0
    for sdate in sorted(by_sess):
        cum += sum(t.pnl for t in by_sess[sdate])
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    rf = round(m["total_pnl"] / max_dd, 2) if max_dd > 0 else float("inf")

    return {
        "pf":          m["profit_factor"],
        "expectancy":  m["expectancy"],
        "wr":          m["wr"],
        "n":           m["n"],
        "total_pnl":   m["total_pnl"],
        "avg_win":     m["avg_win"],
        "avg_loss":    m["avg_loss"],
        "sharpe":      sharpe,
        "max_dd":      round(max_dd, 2),
        "recovery_f":  rf,
    }


# ── Phase 8: Edge Survival Score ────────────────────────────────────────────────

def phase8_ess(stats: dict, conc: dict, resilience: list,
               setup_dep: dict, oos_deg: float) -> dict:
    score = 0
    breakdown = {}

    # Profit Factor (0-20)
    pf = stats["pf"]
    if pf >= 2.0:   pf_pts = 20
    elif pf >= 1.5: pf_pts = 15
    elif pf >= 1.3: pf_pts = 10
    elif pf >= 1.0: pf_pts = 5
    else:           pf_pts = 0
    score += pf_pts
    breakdown["Profit Factor"] = f"PF={pf:.2f} -> {pf_pts}/20"

    # Expectancy (0-20)
    exp = stats["expectancy"]
    if exp >= 5.0:   exp_pts = 20
    elif exp >= 2.0: exp_pts = 15
    elif exp >= 1.0: exp_pts = 10
    elif exp > 0:    exp_pts = 5
    else:            exp_pts = 0
    score += exp_pts
    breakdown["Expectancy"] = f"Exp={exp:+.2f} -> {exp_pts}/20"

    # OOS Degradation (0-20)
    if oos_deg <= 10:    oos_pts = 20
    elif oos_deg <= 20:  oos_pts = 15
    elif oos_deg <= 30:  oos_pts = 10
    elif oos_deg <= 50:  oos_pts = 5
    else:                oos_pts = 0
    score += oos_pts
    breakdown["OOS Degradation"] = f"deg={oos_deg:.1f}% -> {oos_pts}/20"

    # PnL Concentration (0-20)
    top3 = conc["top3_pct"]
    if top3 <= 30:    conc_pts = 20
    elif top3 <= 50:  conc_pts = 15
    elif top3 <= 70:  conc_pts = 10
    elif top3 <= 90:  conc_pts = 5
    else:             conc_pts = 0
    score += conc_pts
    breakdown["PnL Concentration"] = f"Top3={top3:.1f}% -> {conc_pts}/20"

    # Setup Dependency (0-10)
    ss = setup_dep["scenarios"]
    no1_exp = ss[1]["metrics"]["expectancy"] if len(ss) > 1 else 0
    no2_exp = ss[2]["metrics"]["expectancy"] if len(ss) > 2 else 0
    if no1_exp > 0 and no2_exp > 0:     setup_pts = 10
    elif no1_exp > 0:                   setup_pts = 6
    elif no1_exp > -1.0:                setup_pts = 3
    else:                               setup_pts = 0
    score += setup_pts
    breakdown["Setup Dependency"] = f"noTop1={no1_exp:+.2f}, noTop2={no2_exp:+.2f} -> {setup_pts}/10"

    # Session Dependency (0-10)
    no3_exp = resilience[2]["metrics"]["expectancy"] if len(resilience) > 2 else 0
    no5_exp = resilience[3]["metrics"]["expectancy"] if len(resilience) > 3 else 0
    if no3_exp > 0 and no5_exp > 0:     sess_pts = 10
    elif no3_exp > 0:                   sess_pts = 6
    elif no3_exp > -0.5:                sess_pts = 3
    else:                               sess_pts = 0
    score += sess_pts
    breakdown["Session Dependency"] = f"noTop3={no3_exp:+.2f}, noTop5={no5_exp:+.2f} -> {sess_pts}/10"

    if score >= 86:    interp = "Edge Institucional"
    elif score >= 71:  interp = "Edge Robusto"
    elif score >= 51:  interp = "Edge Prometedor"
    elif score >= 31:  interp = "Edge Fragil"
    else:              interp = "Edge Debil"

    return {"score": score, "interpretation": interp, "breakdown": breakdown}


# ── Phase 9: Final Report ───────────────────────────────────────────────────────

def phase9_report(all_trades: list, conc: dict, resilience: list,
                  regime_data: dict, setup_data: dict, setup_dep: dict,
                  temporal: dict, stats: dict, ess: dict, oos_deg: float) -> None:

    n   = stats["n"]
    wr  = stats["wr"]
    pf  = stats["pf"]
    exp = stats["expectancy"]

    top3_pct    = conc["top3_pct"]
    no3_exp     = resilience[2]["metrics"]["expectancy"] if len(resilience) > 2 else 0
    scen_b_pf   = resilience[2]["metrics"]["profit_factor"] if len(resilience) > 2 else 0
    no3_tag     = "SOBREVIVE" if no3_exp > 0 else "DESTRUIDO"

    regime_ranked = sorted(
        [(k, v) for k, v in regime_data.items() if v.get("n", 0) >= 3],
        key=lambda x: x[1].get("expectancy", -999), reverse=True
    )
    best_r  = regime_ranked[0]  if regime_ranked        else ("N/A", {})
    worst_r = regime_ranked[-1] if len(regime_ranked) > 1 else ("N/A", {})

    setup_ranked = sorted(
        [(k, v) for k, v in setup_data.items() if v.get("n", 0) >= 3],
        key=lambda x: x[1].get("expectancy", -999), reverse=True
    )
    best_s  = setup_ranked[0]  if setup_ranked        else ("N/A", {})
    worst_s = setup_ranked[-1] if len(setup_ranked) > 1 else ("N/A", {})

    # Verdict logic
    if exp <= 0 or pf < 1.0:
        edge   = "NO"
        robust = "BAJA"
    elif no3_exp > 0 and scen_b_pf > 1.0 and top3_pct < 75:
        edge   = "SI"
        robust = "ALTA" if top3_pct < 50 else "MEDIA"
    elif no3_exp > 0:
        edge   = "INCONCLUSO"
        robust = "MEDIA"
    else:
        edge   = "INCONCLUSO"
        robust = "BAJA"

    prod = max(0, min(100, ess["score"]
                      - (20 if n < 30 else 0)
                      - (20 if top3_pct > 75 else 0)
                      - (15 if oos_deg > 40 else 0)))

    print()
    print("=" * 72)
    print("  # GIBBZ EDGE ROBUSTNESS REPORT")
    print(f"  Fecha analisis: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 72)
    print()
    print(f"  Sesiones analizadas:     {len(set(t.session for t in all_trades))}")
    print(f"  Trades:                  {n}")
    print(f"  WR:                      {wr:.1f}%")
    print(f"  PF:                      {pf:.2f}")
    print(f"  Expectancy:              {exp:+.2f} pts/trade")
    print(f"  Max Drawdown:            {stats['max_dd']:.2f} pts")
    print()
    print(f"  TOP 1 concentracion:     {conc['top1_pct']:.1f}%")
    print(f"  TOP 3 concentracion:     {conc['top3_pct']:.1f}%")
    print(f"  TOP 5 concentracion:     {conc['top5_pct']:.1f}%")
    print(f"  TOP 10 concentracion:    {conc['top10_pct']:.1f}%")
    print()

    for sc in resilience:
        m = sc["metrics"]
        print(f"  {sc['label']:<38}  WR={m['wr']:>5.1f}%  PF={m['profit_factor']:>5.2f}  "
              f"Exp={m['expectancy']:>+6.2f}  PnL={m['total_pnl']:>+8.2f}  [{sc['tag']}]")
    print()

    br_name, br_m = best_r
    wr_name, wr_m = worst_r
    print(f"  Mejor regimen:  {br_name:<22}  WR={br_m.get('wr', 0):.1f}%  "
          f"PF={br_m.get('profit_factor', 0):.2f}  "
          f"Exp={br_m.get('expectancy', 0):+.2f}  n={br_m.get('n', 0)}")
    print(f"  Peor regimen:   {wr_name:<22}  WR={wr_m.get('wr', 0):.1f}%  "
          f"PF={wr_m.get('profit_factor', 0):.2f}  "
          f"Exp={wr_m.get('expectancy', 0):+.2f}  n={wr_m.get('n', 0)}")
    print()

    bs_name, bs_m = best_s
    ws_name, ws_m = worst_s
    print(f"  Mejor setup:    {bs_name:<22}  WR={bs_m.get('wr', 0):.1f}%  "
          f"PF={bs_m.get('profit_factor', 0):.2f}  "
          f"Exp={bs_m.get('expectancy', 0):+.2f}  n={bs_m.get('n', 0)}")
    print(f"  Peor setup:     {ws_name:<22}  WR={ws_m.get('wr', 0):.1f}%  "
          f"PF={ws_m.get('profit_factor', 0):.2f}  "
          f"Exp={ws_m.get('expectancy', 0):+.2f}  n={ws_m.get('n', 0)}")
    print()

    _sep()
    print(f"  Edge Survival Score:     {ess['score']}/100 -- {ess['interpretation']}")
    for factor, detail in ess["breakdown"].items():
        print(f"    {factor:<22}  {detail}")
    _sep()
    print()
    print("  Veredicto final:")
    print()
    print(f"  EDGE:                 [{edge}]")
    print(f"  ROBUSTEZ:             [{robust}]")
    print(f"  PRODUCTION READINESS: {prod}%")
    print()
    _sep()
    print("  Explicacion detallada:")
    _sep()
    print()

    # Q1
    print("  1. Existe edge?")
    if exp > 0 and pf > 1.0:
        print(f"     SI — Expectancy={exp:+.2f} pts/trade, PF={pf:.2f}, "
              f"Total PnL={stats['total_pnl']:+.2f} pts")
        print(f"     El sistema extrae valor sobre {len(set(t.session for t in all_trades))} "
              f"contextos institucionales distintos.")
    else:
        print(f"     NO — Expectancy={exp:+.2f} o PF={pf:.2f} indican sistema destructivo.")
    print()

    # Q2
    print("  2. Es robusto?")
    if robust == "ALTA":
        print(f"     SI — Expectancy positiva ({no3_exp:+.2f}) incluso eliminando Top 3 sesiones.")
        print(f"     Concentracion Top 3 = {top3_pct:.1f}%. El edge esta distribuido.")
    elif robust == "MEDIA":
        print(f"     PARCIALMENTE — Edge positivo sin Top 3 (Exp={no3_exp:+.2f}),")
        print(f"     pero concentracion Top 3 = {top3_pct:.1f}%. Riesgo de concentracion.")
    else:
        print(f"     NO — Sin Top 3 sesiones la expectancy cae a {no3_exp:+.2f}.")
        print(f"     El edge depende de pocas sesiones excepcionales ({top3_pct:.1f}% Top 3).")
    print()

    # Q3
    print("  3. Sobrevive sin las mejores sesiones?")
    for sc in resilience[1:]:
        m = sc["metrics"]
        tag = "[+]" if m["expectancy"] > 0 else "[-]"
        print(f"     {sc['label']:<38} Exp={m['expectancy']:>+6.2f}  "
              f"PF={m['profit_factor']:>5.2f}  {tag}")
    print()

    # Q4
    print("  4. Sobrevive sin los mejores setups?")
    for sc in setup_dep["scenarios"][1:]:
        m = sc["metrics"]
        excl_str = ", ".join(sorted(sc["excluded"])) if sc["excluded"] else "-"
        tag = "[+]" if m["expectancy"] > 0 else "[-]"
        print(f"     {sc['label']:<28} Exp={m['expectancy']:>+6.2f}  "
              f"PF={m['profit_factor']:>5.2f}  {tag}  "
              f"(excl: {excl_str})")
    print()

    # Q5
    print("  5. Que regimen produce el edge?")
    for regime, m in sorted(regime_data.items(),
                             key=lambda x: x[1].get("expectancy", -999), reverse=True):
        if m["n"] == 0:
            continue
        mark = " <- MEJOR" if regime == br_name else (" <- PEOR" if regime == wr_name else "")
        print(f"     {regime:<22}  n={m['n']:>3}  WR={m['wr']:>5.1f}%  "
              f"Exp={m['expectancy']:>+6.2f}  PnL={m['total_pnl']:>+8.2f}{mark}")
    print()

    # Q6
    priority = ["ORB_SETUP", "FA_SETUP", "VA80_SETUP", "VWAP_SETUP",
                "GAP_SETUP", "POC_SETUP", "BOUNCE_SETUP"]
    print("  6. Que setup produce el edge?")
    for stype in priority:
        m = setup_data.get(stype)
        if not m or m["n"] == 0:
            print(f"     {stype:<22}  n=  0  --")
            continue
        mark = " <- MEJOR" if stype == bs_name else (" <- PEOR" if stype == ws_name else "")
        print(f"     {stype:<22}  n={m['n']:>3}  WR={m['wr']:>5.1f}%  "
              f"Exp={m['expectancy']:>+6.2f}  PnL={m['total_pnl']:>+8.2f}{mark}")
    print()

    # Q7
    print("  7. Que tan cerca esta de ser production-ready?")
    print(f"     Edge Survival Score: {ess['score']}/100 -- {ess['interpretation']}")
    print(f"     Production Readiness: {prod}%")
    if prod >= 70:
        print("     -> Listo para operacion piloto con gestion de riesgo estricta.")
        print("        Recomendado: max 1 contrato hasta completar 100+ trades en vivo.")
    elif prod >= 50:
        print("     -> Prometedor. Ampliar dataset antes de operar en vivo.")
        print("        Objetivo: grabaciones de 3+ meses de precio distintos.")
    else:
        print("     -> No listo para produccion. Factores criticos pendientes:")
        if n < 30:
            print(f"        * Muestra insuficiente ({n} trades). Objetivo: >=100.")
        if top3_pct > 75:
            print(f"        * Concentracion critica: {top3_pct:.1f}% PnL en Top 3 sesiones.")
        if oos_deg > 40:
            print(f"        * OOS degradacion elevada: {oos_deg:.1f}%. Riesgo overfitting.")
    print()
    print("  NOTA CRITICA DE INTEGRIDAD DE DATOS:")
    print("  Las 43 sesiones aplican 43 contextos institucionales distintos")
    print("  (VAH/POC/VAL de fechas 2024-2026) sobre los mismos 4 dias de")
    print("  precio real (Mayo 8-11, 2026). Esto mide robustez de niveles,")
    print("  NO alpha temporal. Para validar produccion se requieren")
    print("  grabaciones de distintos periodos de mercado (3-6 meses).")
    print()
    print("=" * 72)


# ── Main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("=" * 72)
    print("  GIBBZ EDGE ROBUSTNESS AUDIT -- Modo Cientifico")
    print("  Sin modificar codigo. Sin ajustar parametros. Solo analisis.")
    print("=" * 72)
    print()

    sessions  = load_sessions()
    type_map  = {s["date"]: s["session_type"] for s in sessions}
    print(f"  Sesiones con grabacion valida: {len(sessions)}")
    print(f"  Ejecutando backtest completo (puede tardar 20-40 minutos)...")
    print()

    all_trades, sessions_run = run_full_backtest(sessions)

    if not all_trades:
        print("  ERROR: 0 trades generados. Verificar dataset.")
        return

    gm = compute_metrics(all_trades)
    print()
    print(f"  Backtest completo: {sessions_run} sesiones | {gm['n']} trades | "
          f"WR={gm['wr']:.1f}% | PF={gm['profit_factor']:.2f} | "
          f"Exp={gm['expectancy']:+.2f} | PnL={gm['total_pnl']:+.2f}")
    print()

    # OOS split in-memory (no re-run; sessions are independent)
    sorted_s  = sorted(sessions, key=lambda s: s["date"])
    split_idx = max(1, int(len(sorted_s) * 0.7))
    is_dates  = {s["date"] for s in sorted_s[:split_idx]}
    oos_dates = {s["date"] for s in sorted_s[split_idx:]}
    is_m      = compute_metrics([t for t in all_trades if t.session in is_dates])
    oos_m     = compute_metrics([t for t in all_trades if t.session in oos_dates])
    oos_deg   = ((is_m["wr"] - oos_m["wr"]) / max(is_m["wr"], 0.01) * 100
                 if is_m["wr"] > 0 else 100.0)

    # ── FASE 1
    print("FASE 1 -- Concentracion del PnL")
    _sep()
    conc = phase1_concentration(all_trades, type_map)
    print(f"  Total PnL:           {conc['total_pnl']:+.2f} pts")
    print(f"  Sesiones rentables:  {conc['profitable']}/{conc['total_sessions']}")
    print(f"  TOP  1: {conc['top1_pct']:>5.1f}% del PnL absoluto")
    print(f"  TOP  3: {conc['top3_pct']:>5.1f}%")
    print(f"  TOP  5: {conc['top5_pct']:>5.1f}%")
    print(f"  TOP 10: {conc['top10_pct']:>5.1f}%")
    print()
    print(f"  {'#':>2}  {'Sesion':<12}  {'PnL':>9}  {'Contrib%':>8}  {'Tipo':<22}  Estado")
    _sep()
    total_abs = conc["total_abs"]
    for i, (d, pnl) in enumerate(conc["sorted_sessions"], 1):
        pct  = 100 * abs(pnl) / max(total_abs, 0.01)
        stype = type_map.get(d, "?")
        tag   = "GANADORA" if pnl > 0 else "PERDEDORA"
        print(f"  {i:>2}. {d:<12}  {pnl:>+9.2f}  {pct:>7.1f}%  {stype:<22}  [{tag}]")
    print()

    # ── FASE 2
    print("FASE 2 -- Test de resiliencia del edge")
    _sep()
    resilience = phase2_resilience(all_trades, conc)
    print(f"  {'ESCENARIO':<40}  {'WR':>6}  {'PF':>6}  {'Exp':>7}  {'PnL':>9}  RESULTADO")
    _sep()
    for sc in resilience:
        m = sc["metrics"]
        print(f"  {sc['label']:<40}  {m['wr']:>5.1f}%  {m['profit_factor']:>6.2f}  "
              f"{m['expectancy']:>+7.2f}  {m['total_pnl']:>+9.2f}  [{sc['tag']}]")
    print()
    surviving = sum(1 for sc in resilience[1:] if sc["metrics"]["expectancy"] > 0)
    print(f"  Edge positivo en {surviving}/4 escenarios de eliminacion.")
    print(f"  {'Respuesta:':10} "
          f"{'El edge SOBREVIVE eliminaciones' if surviving >= 3 else 'El edge DEPENDE de pocas sesiones'}")
    print()

    # ── FASE 3
    print("FASE 3 -- Analisis por regimen")
    _sep()
    regime_data = phase3_regime(all_trades, type_map)
    print(f"  {'REGIMEN':<22}  {'N':>3}  {'WR':>6}  {'PF':>6}  {'Exp':>7}  {'PnL':>9}  {'Sess':>5}")
    _sep()
    for regime, m in sorted(regime_data.items(),
                             key=lambda x: x[1].get("expectancy", -999), reverse=True):
        if m["n"] == 0:
            continue
        print(f"  {regime:<22}  {m['n']:>3}  {m['wr']:>5.1f}%  "
              f"{m['profit_factor']:>6.2f}  {m['expectancy']:>+7.2f}  "
              f"{m['total_pnl']:>+9.2f}  {m.get('sess_count', 0):>5}")
    requested = {"EXPANSION","ROTATIONAL","OPENING_DRIVE","BALANCED",
                 "LOW_VOL","TREND","LIQUIDATION","SHORT_COVERING"}
    missing = sorted(requested - set(regime_data.keys()))
    if missing:
        print(f"  Regimenes solicitados sin datos: {', '.join(missing)}")
    print()

    # ── FASE 4
    print("FASE 4 -- Analisis por setup")
    _sep()
    setup_data = phase4_setup(all_trades)
    priority   = ["ORB_SETUP", "FA_SETUP", "VA80_SETUP", "VWAP_SETUP",
                  "GAP_SETUP", "POC_SETUP", "BOUNCE_SETUP"]
    print(f"  {'SETUP':<22}  {'N':>3}  {'WR':>6}  {'PF':>6}  {'Exp':>7}  {'PnL':>9}")
    _sep()
    for stype in priority:
        m = setup_data.get(stype)
        if not m:
            print(f"  {stype:<22}  {0:>3}  {'--':>6}  {'--':>6}  {'--':>7}  {'--':>9}")
            continue
        print(f"  {stype:<22}  {m['n']:>3}  {m['wr']:>5.1f}%  "
              f"{m['profit_factor']:>6.2f}  {m['expectancy']:>+7.2f}  "
              f"{m['total_pnl']:>+9.2f}")
    print()

    # ── FASE 5
    print("FASE 5 -- Dependencia de setups")
    _sep()
    setup_dep = phase5_setup_dependency(all_trades, setup_data)
    print("  Setups ordenados por Expectancy (mejor -> peor):")
    for i, (stype, m) in enumerate(setup_dep["sorted_setups"], 1):
        print(f"    {i}. {stype:<22}  Exp={m['expectancy']:>+6.2f}  "
              f"WR={m['wr']:>5.1f}%  n={m['n']}")
    print()
    print(f"  {'ESCENARIO':<32}  {'WR':>6}  {'PF':>6}  {'Exp':>7}  {'PnL':>9}  RESULTADO")
    _sep()
    for sc in setup_dep["scenarios"]:
        m    = sc["metrics"]
        excl = ", ".join(sorted(sc["excluded"])) if sc["excluded"] else "ninguno"
        print(f"  {sc['label']:<32}  {m['wr']:>5.1f}%  {m['profit_factor']:>6.2f}  "
              f"{m['expectancy']:>+7.2f}  {m['total_pnl']:>+9.2f}  [{sc['tag']}]")
        if sc["excluded"]:
            print(f"    (excluidos: {excl})")
    print()

    # ── FASE 6
    print("FASE 6 -- Estabilidad temporal")
    _sep()
    temporal = phase6_temporal(all_trades)
    print("  Por mes de contexto (session_date — NO es fecha de trading real):")
    print(f"  {'MES':<10}  {'N':>3}  {'WR':>6}  {'Exp':>7}  {'PnL':>9}")
    _sep()
    for month, m in sorted(temporal["by_month"].items()):
        if m["n"] == 0:
            continue
        print(f"  {month:<10}  {m['n']:>3}  {m['wr']:>5.1f}%  "
              f"{m['expectancy']:>+7.2f}  {m['total_pnl']:>+9.2f}")
    print()
    cv_tag = ("ERRATICO" if temporal["cv"] > 150
              else "INESTABLE" if temporal["cv"] > 100
              else "ACEPTABLE")
    print(f"  Media PnL/sesion:   {temporal['mean_pnl']:+.2f} pts")
    print(f"  Std PnL/sesion:     {temporal['std_pnl']:.2f} pts")
    print(f"  Coef. variacion:    {temporal['cv']:.1f}%  [{cv_tag}]")
    print()

    # ── FASE 7
    print("FASE 7 -- Robustez estadistica")
    _sep()
    stats = phase7_stats(all_trades)
    print(f"  Profit Factor:      {stats['pf']:.2f}")
    print(f"  Expectancy:         {stats['expectancy']:+.2f} pts/trade")
    print(f"  Sharpe (por trade): {stats['sharpe']:.3f}  "
          f"(mean/std de PnL individual)")
    print(f"  Recovery Factor:    {stats['recovery_f']}  "
          f"(PnL total / Max Drawdown)")
    print(f"  Max Drawdown:       {stats['max_dd']:.2f} pts  (nivel sesion)")
    print()
    oos_tag = ("ACEPTABLE" if oos_deg <= 20
               else "MODERADA" if oos_deg <= 30
               else "ELEVADA -- riesgo overfitting")
    print(f"  Out-of-Sample 70/30 (split en memoria, sin re-run):")
    print(f"  IS  ({len(is_dates)} sesiones, {is_m['n']} trades):  "
          f"WR={is_m['wr']:.1f}%  PF={is_m['profit_factor']:.2f}  "
          f"Exp={is_m['expectancy']:+.2f}")
    print(f"  OOS ({len(oos_dates)} sesiones, {oos_m['n']} trades): "
          f"WR={oos_m['wr']:.1f}%  PF={oos_m['profit_factor']:.2f}  "
          f"Exp={oos_m['expectancy']:+.2f}")
    print(f"  Degradacion WR:     {oos_deg:+.1f}%  [{oos_tag}]")
    print()

    # ── FASE 8
    print("FASE 8 -- Edge Survival Score")
    _sep()
    ess = phase8_ess(stats, conc, resilience, setup_dep, oos_deg)
    for factor, detail in ess["breakdown"].items():
        print(f"  {factor:<22}  {detail}")
    _sep()
    print(f"  TOTAL: {ess['score']}/100 -- {ess['interpretation']}")
    print()
    ess_interp = {
        "Edge Institucional": "86-100  Listo para produccion.",
        "Edge Robusto":       "71-85   Solido. Operacion piloto recomendada.",
        "Edge Prometedor":    "51-70   Ampliar dataset antes de produccion.",
        "Edge Fragil":        "31-50   Fragil. No operar en vivo.",
        "Edge Debil":         "0-30    Sin evidencia de ventaja real.",
    }
    print(f"  Escala: {ess_interp.get(ess['interpretation'], '')}")
    print()

    # ── FASE 9
    print("FASE 9 -- Veredicto final")
    _sep()
    phase9_report(all_trades, conc, resilience, regime_data, setup_data,
                  setup_dep, temporal, stats, ess, oos_deg)


if __name__ == "__main__":
    main()
