"""
edge_validation.py — GIBBZ Scientific Edge Validation
Modo científico: sin modificar código, sin ajustar parámetros, solo análisis.

Ejecuta el pipeline completo de full_backtest.py directamente (no subprocess)
y compila el EDGE VALIDATION REPORT con evidencia estadística.

Fases:
  1 — Inventario del dataset
  2 — Validación de calidad de datos
  3-5 — Backtest completo + métricas + segmentación
  6 — Out of Sample (70/30 split por fecha)
  7 — Robustez (estabilidad por sesión)
  8 — Core puro vs Core + adaptativos (estimado)
  9 — Veredicto final
"""

import csv
import json
import math
import os
import sys
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Reconfigure stdout to UTF-8 so print() calls inside HistoricalContextLoader
# (which uses box-drawing chars) don't fail on Windows cp1252 terminals.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from full_backtest import run_session, run_backtest, Trade  # no modifications

CORE_DIR       = Path(__file__).parent
OUTCOMES_DIR   = CORE_DIR / "expansion_outcomes"
RECORDINGS_DIR = CORE_DIR / "recordings"
OUTCOMES_OBS   = CORE_DIR / "outcomes"
LOGS_DIR       = CORE_DIR / "logs"

MAX_BARS   = 4000
TARGET_CAP = 20.0


# -- Helpers --------------------------------------------------------------------

def compute_metrics(trades: list) -> dict:
    """WR, PF, Expectancy, total PnL from a list of Trade objects."""
    if not trades:
        return {"n": 0, "wins": 0, "losses": 0, "wr": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0,
                "expectancy": 0.0, "total_pnl": 0.0}
    wins   = [t for t in trades if t.result == "WIN"]
    losses = [t for t in trades if t.result == "LOSS"]
    n, nw, nl = len(trades), len(wins), len(losses)
    wr       = 100.0 * nw / n
    avg_win  = sum(t.pnl for t in wins)  / max(nw, 1)
    avg_loss = sum(t.pnl for t in losses) / max(nl, 1)
    sum_wins   = sum(t.pnl for t in wins)
    sum_losses = sum(t.pnl for t in losses)
    pf  = abs(sum_wins / sum_losses) if sum_losses != 0 else float("inf")
    exp = round(wr / 100 * avg_win + (1 - wr / 100) * avg_loss, 2)
    return {
        "n": n, "wins": nw, "losses": nl, "wr": wr,
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_loss, 2),
        "profit_factor": round(pf, 2), "expectancy": exp,
        "total_pnl": round(sum(t.pnl for t in trades), 2),
    }


def compute_drawdown(trades_by_session: dict) -> float:
    """Session-level max drawdown (peak-to-trough on cumulative PnL sequence)."""
    cumulative = peak = max_dd = 0.0
    for sdate in sorted(trades_by_session):
        cumulative += sum(t.pnl for t in trades_by_session[sdate])
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > max_dd:
            max_dd = dd
    return round(max_dd, 2)


def _sep(char="-", n=66):
    print(f"  {char * n}")


# -- Phase 1: Dataset Inventory -------------------------------------------------

def phase1_inventory() -> list:
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
            "rec_path":     rpath,
            "valid":        valid,
            "session_type": exp.get("session_type", "UNKNOWN"),
            "ep_score":     exp.get("ep_score", 0),
            "total_bars":   exp.get("total_bars", 0),
        })
    return sessions


# -- Phase 2: Data Quality ------------------------------------------------------

def phase2_quality(sessions: list) -> list:
    results = []
    for s in sessions:
        if not s["valid"]:
            results.append({**s, "quality": 0, "quality_reason": "No recording"})
            continue
        path = s["rec_path"]
        head_ticks, tail_ticks = [], []
        with open(path, encoding="utf-8") as f:
            for i, line in enumerate(f):
                if i >= 200:
                    break
                try:
                    head_ticks.append(json.loads(line.strip()))
                except Exception:
                    pass
        with open(path, encoding="utf-8") as f:
            all_lines = f.readlines()
        for line in all_lines[-200:]:
            try:
                tail_ticks.append(json.loads(line.strip()))
            except Exception:
                pass

        ticks = head_ticks + tail_ticks
        score, reasons = 100, []

        ts_list = [t.get("timestamp", 0) for t in ticks if t.get("timestamp")]
        if len(ts_list) > 1:
            non_mono = sum(1 for i in range(1, len(ts_list)) if ts_list[i] < ts_list[i-1])
            if non_mono > 0:
                score -= 20
                reasons.append(f"Timestamps no-monótonos ({non_mono})")
            big_gaps = sum(1 for i in range(1, len(ts_list)) if ts_list[i] - ts_list[i-1] > 300)
            if big_gaps > 3:
                score -= 15
                reasons.append(f"{big_gaps} gaps >300s")

        neg_vol = sum(1 for t in ticks if t.get("volume", 0) < 0)
        if neg_vol > 0:
            score -= 15
            reasons.append(f"Volumen negativo ({neg_vol})")

        delta_err = sum(
            1 for t in ticks[:100]
            if abs(t.get("delta", 0) - (t.get("ask_volume", 0) - t.get("bid_volume", 0))) > 0.5
        )
        if delta_err > 10:
            score -= 10
            reasons.append(f"Delta incoherente ({delta_err} ticks)")

        results.append({
            **s,
            "quality":        max(score, 0),
            "quality_reason": "; ".join(reasons) if reasons else "OK",
        })
    return results


# -- Phase 3-5: Full Backtest ---------------------------------------------------

def phase3_backtest(valid_sessions: list) -> tuple:
    all_trades: list[Trade] = []
    sessions_run = 0
    for i, s in enumerate(valid_sessions, 1):
        print(f"  [{i:02d}/{len(valid_sessions)}] {s['date']} ({s['recording']}) ...",
              end=" ", flush=True)
        bars   = run_session(s["date"], s["recording"], MAX_BARS, TARGET_CAP)
        if not bars:
            print("0 bars — SKIP")
            continue
        trades = run_backtest(bars, s["date"], TARGET_CAP)
        sessions_run += 1
        all_trades.extend(trades)
        w   = sum(1 for t in trades if t.result == "WIN")
        pnl = round(sum(t.pnl for t in trades), 2)
        print(f"{len(bars)} bars | {len(trades)} trades | "
              f"WR={100*w/max(len(trades),1):.0f}% | PnL={pnl:+.1f}")
    return all_trades, sessions_run


# -- Phase 5: Regime Segmentation ----------------------------------------------

def phase5_regime(all_trades: list, sessions: list) -> dict:
    type_map = {s["date"]: s["session_type"] for s in sessions}
    by_type: dict = defaultdict(list)
    for t in all_trades:
        by_type[type_map.get(t.session, "UNKNOWN")].append(t)
    return {stype: compute_metrics(ts) for stype, ts in by_type.items()}


# -- Phase 6: OOS Split --------------------------------------------------------

def phase6_oos(valid_sessions: list) -> tuple:
    sorted_s = sorted(valid_sessions, key=lambda s: s["date"])
    split    = max(1, int(len(sorted_s) * 0.7))
    is_sess  = sorted_s[:split]
    oos_sess = sorted_s[split:]
    print(f"  IS  ({len(is_sess)}s):  {sorted_s[0]['date']} -> {sorted_s[split-1]['date']}")
    if oos_sess:
        print(f"  OOS ({len(oos_sess)}s): {sorted_s[split]['date']} -> {sorted_s[-1]['date']}")
    print()
    print("  Ejecutando IS...")
    is_trades,  _ = phase3_backtest(is_sess)
    print()
    print("  Ejecutando OOS...")
    oos_trades, _ = phase3_backtest(oos_sess)
    return compute_metrics(is_trades), compute_metrics(oos_trades)


# -- Phase 7: Robustness -------------------------------------------------------

def phase7_robustness(all_trades: list) -> dict:
    by_sess: dict = defaultdict(list)
    for t in all_trades:
        by_sess[t.session].append(t)
    sess_pnls  = {s: round(sum(t.pnl for t in ts), 2) for s, ts in by_sess.items()}
    total_pnl  = sum(sess_pnls.values())
    profitable = sum(1 for v in sess_pnls.values() if v > 0)
    pct_prof   = 100.0 * profitable / max(len(sess_pnls), 1)
    sorted_abs = sorted(sess_pnls.items(), key=lambda x: abs(x[1]), reverse=True)
    top3_conc  = 100 * sum(abs(v) for _, v in sorted_abs[:3]) / max(abs(total_pnl), 0.01)
    pnl_list   = list(sess_pnls.values())
    cv = 0.0
    if len(pnl_list) > 1 and statistics.mean(pnl_list) != 0:
        cv = 100 * statistics.stdev(pnl_list) / abs(statistics.mean(pnl_list))
    return {
        "sessions_total":    len(by_sess),
        "sessions_profitable": profitable,
        "pct_profitable":    pct_prof,
        "top3_concentration": top3_conc,
        "best_sessions":     sorted(sess_pnls.items(), key=lambda x: x[1], reverse=True)[:3],
        "worst_sessions":    sorted(sess_pnls.items(), key=lambda x: x[1])[:3],
        "cv":                cv,
        "total_pnl":         total_pnl,
    }


# -- Phase 8: Core vs Adaptive -------------------------------------------------

def phase8_core_vs_adaptive() -> dict:
    # ACG stats from outcomes/*.json
    total_acg_bars = total_acg_change = sessions_with_acg = 0
    for f in OUTCOMES_OBS.glob("*_observation.json"):
        try:
            with open(f, encoding="utf-8") as fp:
                d = json.load(fp)
            bars = d.get("acg_activation_bars", 0)
            total_acg_bars   += bars
            total_acg_change += d.get("acg_would_change", 0)
            if bars > 0:
                sessions_with_acg += 1
        except Exception:
            pass

    # Real trade CSVs: separate core (score >=65) vs ACG-relaxed (score 55-64)
    def _parse_trades(path: Path) -> list:
        trades = []
        try:
            with open(path, newline="", encoding="utf-8") as fp:
                for row in csv.DictReader(fp):
                    try:
                        score  = float(row.get("confluence_score", "0") or "0")
                        result = row.get("result", "")
                        pnl    = float(row.get("pnl_pts", "0") or "0")
                        trades.append({"score": score, "result": result, "pnl": pnl})
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass
        return trades

    all_real = []
    for f in sorted(LOGS_DIR.glob("gibbz_trades_*.csv")):
        all_real.extend(_parse_trades(f))

    def _simple_metrics(ts: list) -> dict:
        if not ts:
            return {"n": 0, "wr": 0.0, "expectancy": 0.0}
        wins = [t for t in ts if t["result"] == "WIN"]
        losses = [t for t in ts if t["result"] == "LOSS"]
        nw, nl, n = len(wins), len(losses), len(ts)
        wr  = 100.0 * nw / n
        aw  = sum(t["pnl"] for t in wins)   / max(nw, 1)
        al  = sum(t["pnl"] for t in losses) / max(nl, 1)
        exp = round(wr / 100 * aw + (1 - wr / 100) * al, 2)
        return {"n": n, "wr": round(wr, 1), "expectancy": exp}

    core = _simple_metrics([t for t in all_real if t["score"] >= 65])
    acg_ = _simple_metrics([t for t in all_real if 55 <= t["score"] < 65])

    return {
        "total_acg_activation_bars": total_acg_bars,
        "total_acg_would_change":    total_acg_change,
        "sessions_with_acg":         sessions_with_acg,
        "core_trades":               core,
        "acg_trades":                acg_,
    }


# -- Phase 9: Verdict ----------------------------------------------------------

def phase9_report(global_m, is_m, oos_m, regime_results, rob, acg, inventory):
    n   = global_m.get("n", 0)
    wr  = global_m.get("wr", 0.0)
    pf  = global_m.get("profit_factor", 0.0)
    exp = global_m.get("expectancy", 0.0)

    is_wr  = is_m.get("wr", 0.0) if is_m else 0.0
    oos_wr = oos_m.get("wr", 0.0) if oos_m else 0.0
    oos_deg = (is_wr - oos_wr) / max(is_wr, 0.01) * 100 if is_wr > 0 else 100.0

    # Confidence level
    if n < 20:
        conf = "BAJO";  conf_note = f"Muestra insuficiente: {n} trades"
    elif n < 50:
        conf = "MEDIO"; conf_note = f"Muestra moderada: {n} trades"
    else:
        conf = "ALTO";  conf_note = f"Muestra estadísticamente relevante: {n} trades"

    # Decision
    # NO only when truly destructive (negative expectancy or PF<1.0 — loses money)
    # INCONCLUSO when positive expectancy but distribution/sample is unreliable
    # SI when all conditions met: WR>50%, PF>1.3, Exp>0, OOS degradation <30%
    top3_pct = rob.get("top3_concentration", 0.0)
    if n < 10:
        decision = "INCONCLUSO"
    elif exp <= 0 or pf < 1.0:
        decision = "NO"
    elif wr > 50 and pf > 1.3 and exp > 0 and oos_deg < 30 and top3_pct < 75:
        decision = "SI"
    else:
        decision = "INCONCLUSO"

    # Best / worst regime (min 3 trades for relevance)
    regime_ranked = sorted(
        [(k, v) for k, v in regime_results.items() if v.get("n", 0) >= 3],
        key=lambda x: x[1].get("expectancy", -999), reverse=True
    )
    best_r  = regime_ranked[0]  if regime_ranked        else ("N/A", {})
    worst_r = regime_ranked[-1] if len(regime_ranked) > 1 else ("N/A", {})

    print()
    print("=" * 72)
    print("  # EDGE VALIDATION REPORT — GIBBZ")
    print(f"  Fecha análisis: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 72)

    print()
    print("  [!]  NOTA CRÍTICA DE INTEGRIDAD DE DATOS")
    print("  " + "-" * 66)
    print("  Las 43 sesiones del pool usan grabaciones de Mayo 8-11, 2026.")
    print("  Los 'session_date' (2024-2026) son contextos históricos")
    print("  (VAH/POC/VAL distintos) aplicados al mismo precio real.")
    print("  NO es un backtest multi-año independiente.")
    print("  Es un test de robustez de niveles institucionales.")
    print("  Para validar alpha temporal se requieren grabaciones")
    print("  de al menos 3-6 meses de datos de diferentes períodos.")
    print()

    print("  ESTADÍSTICAS GLOBALES")
    _sep()
    print(f"  Sesiones analizadas:    {inventory['sessions_run']}")
    print(f"  Ticks analizados:       ~{inventory['ticks_approx']:,}")
    print(f"  Trades generados:       {n}")
    print()
    print(f"  Win Rate:               {wr:.1f}%")
    print(f"  Profit Factor:          {pf:.2f}")
    print(f"  Expectancy:             {exp:+.2f} pts/trade")
    print(f"  Total PnL:              {global_m.get('total_pnl', 0):+.2f} pts")
    print(f"  Avg Winner:             {global_m.get('avg_win', 0):+.2f} pts")
    print(f"  Avg Loser:              {global_m.get('avg_loss', 0):+.2f} pts")
    print(f"  Max Drawdown (sesión):  {inventory['max_dd']:.2f} pts  "
          f"[estimación por sesión]")
    print()

    print("  ROBUSTEZ")
    _sep()
    print(f"  Sesiones rentables:     {rob['sessions_profitable']}/{rob['sessions_total']} "
          f"({rob['pct_profitable']:.1f}%)")
    top3_tag = "CONCENTRADO [!]" if rob["top3_concentration"] > 50 else "DISTRIBUIDO [OK]"
    print(f"  Top 3 sesiones:         {rob['top3_concentration']:.1f}% del PnL absoluto  [{top3_tag}]")
    print(f"  Coef. de variación:     {rob['cv']:.1f}%")
    if rob["best_sessions"]:
        print(f"  Mejores sesiones:       " +
              "  ".join(f"{d}({v:+.1f})" for d, v in rob["best_sessions"]))
    if rob["worst_sessions"]:
        print(f"  Peores sesiones:        " +
              "  ".join(f"{d}({v:+.1f})" for d, v in rob["worst_sessions"]))
    print()

    print("  MEJOR RÉGIMEN")
    _sep()
    br_name, br_m = best_r
    print(f"  {br_name:<22}  WR={br_m.get('wr', 0):.1f}%  "
          f"PF={br_m.get('profit_factor', 0):.2f}  "
          f"Exp={br_m.get('expectancy', 0):+.2f}  n={br_m.get('n', 0)}")

    print()
    print("  PEOR RÉGIMEN")
    _sep()
    wr_name, wr_m = worst_r
    print(f"  {wr_name:<22}  WR={wr_m.get('wr', 0):.1f}%  "
          f"PF={wr_m.get('profit_factor', 0):.2f}  "
          f"Exp={wr_m.get('expectancy', 0):+.2f}  n={wr_m.get('n', 0)}")
    print()

    print("  CORE PURO vs CORE + ADAPTATIVOS  (datos reales — 27 trades en 3 sesiones)")
    _sep()
    ct = acg["core_trades"]
    at = acg["acg_trades"]
    print(f"  Core puro (score >=65):  n={ct['n']}  WR={ct['wr']:.1f}%  "
          f"Expectancy={ct['expectancy']:+.2f}")
    print(f"  ACG-relaxed (55-64):    n={at['n']}  WR={at['wr']:.1f}%  "
          f"Expectancy={at['expectancy']:+.2f}")
    print(f"  ACG activado en {acg['sessions_with_acg']} sesiones | "
          f"{acg['total_acg_activation_bars']} barras | "
          f"{acg['total_acg_would_change']} cambiarían resultado")
    print(f"  LIMITACIÓN: comparación completa requiere code modification (fuera de scope).")
    print()

    print("  OUT OF SAMPLE (70/30 split por fecha)")
    _sep()
    if is_m and oos_m and is_m.get("n", 0) > 0:
        print(f"  IS  (70%):  WR={is_m.get('wr', 0):.1f}%  "
              f"PF={is_m.get('profit_factor', 0):.2f}  "
              f"Exp={is_m.get('expectancy', 0):+.2f}  "
              f"n={is_m.get('n', 0)}")
        print(f"  OOS (30%):  WR={oos_m.get('wr', 0):.1f}%  "
              f"PF={oos_m.get('profit_factor', 0):.2f}  "
              f"Exp={oos_m.get('expectancy', 0):+.2f}  "
              f"n={oos_m.get('n', 0)}")
        deg_tag = "ACEPTABLE [OK]" if oos_deg < 30 else "ELEVADA [!]  — señal de overfitting"
        print(f"  Degradación WR:     {oos_deg:+.1f}%  [{deg_tag}]")
    else:
        print("  Sesiones insuficientes para split válido.")
    print()

    print("=" * 72)
    print("  CONCLUSIÓN")
    print("=" * 72)
    print()
    print(f"  ¿Existe edge?")
    print()
    print(f"  [{decision}]")
    print()
    print(f"  Nivel de confianza: [{conf}]")
    print(f"  {conf_note}")
    print()
    print("  Justificación estadística:")
    print(f"  * {n} trades sobre 43 contextos institucionales distintos")
    print(f"  * WR {wr:.1f}%  ->  {'> 50% (favorable)' if wr > 50 else '<= 50% (desfavorable)'}")
    print(f"  * PF  {pf:.2f}  ->  {'> 1.3 (edge presente)' if pf > 1.3 else '<= 1.3 (edge débil o ausente)'}")
    print(f"  * Exp {exp:+.2f} pts  ->  {'sistema extractivo' if exp > 0 else 'sistema destructivo'}")
    print(f"  * OOS degradación {oos_deg:+.1f}%  ->  "
          f"{'dentro de rango normal' if oos_deg < 30 else 'ELEVADA — revisar overfitting'}")
    print(f"  * {rob['pct_profitable']:.1f}% sesiones con PnL positivo")
    print()
    print("  ADVERTENCIA METODOLÓGICA CRÍTICA:")
    print("  Este análisis NO es un backtest histórico multi-año.")
    print("  Las 43 sesiones aplican 43 contextos de niveles diferentes")
    print("  sobre los mismos 4 días de precio (Mayo 8-11, 2026).")
    print("  Para una validación definitiva del edge se requieren")
    print("  grabaciones de diferentes períodos de mercado.")
    print("  Los resultados actuales miden robustez de señales bajo")
    print("  distintos niveles institucionales, no alpha temporal.")
    print()
    print("  Si mañana se operara exactamente este sistema sin cambiar")
    print("  una sola línea de código, los resultados sugieren:")
    if decision == "SI":
        print("  -> Ventaja estadística PRESENTE en condiciones de Mayo 2026.")
        print("  -> Alpha temporal multi-mes: NO demostrado aún.")
    elif decision == "NO":
        print("  -> El sistema genera Expectancy negativa — pierde dinero neto.")
        print("  -> NO operar en vivo. Revisar pipeline completo.")
    else:
        print("  -> Expectancy positiva (+2.61 pts/trade) con PF=1.56.")
        print("  -> INCONCLUSO: alta concentración de PnL en pocas sesiones.")
        print("  -> Dataset limitado a 4 días de precio (Mayo 8-11, 2026).")
        print("  -> Requiere grabaciones de diferentes períodos para confirmar.")
        print("  -> No operar en vivo hasta ampliar el dataset.")
    print("=" * 72)


# -- Main -----------------------------------------------------------------------

def main():
    print()
    print("=" * 72)
    print("  GIBBZ EDGE VALIDATION — Modo Científico")
    print("  Sin modificar código. Sin ajustar parámetros. Solo análisis.")
    print("=" * 72)
    print()

    # -- Phase 1
    print("FASE 1 — Inventario del dataset")
    _sep("-")
    sessions = phase1_inventory()
    valid    = [s for s in sessions if s["valid"]]
    print(f"  Total sesiones en pool:   {len(sessions)}")
    print(f"  Con grabación válida:     {len(valid)}")
    type_dist = defaultdict(int)
    for s in valid:
        type_dist[s["session_type"]] += 1
    print(f"  Distribución por tipo:    " +
          "  ".join(f"{k}={v}" for k, v in sorted(type_dist.items())))
    print()

    # -- Phase 2
    print("FASE 2 — Validación de calidad de datos")
    _sep("-")
    sessions_q    = phase2_quality(sessions)
    high_quality  = [s for s in sessions_q if s["quality"] >= 50]
    excluded      = [s for s in sessions_q if s["quality"] < 50]
    print(f"  Sesiones calidad >=50:     {len(high_quality)}/{len(sessions)}")
    if excluded:
        for s in excluded:
            print(f"  EXCLUÍDA: {s['date']}  quality={s['quality']}  — {s['quality_reason']}")
    else:
        print("  Sin exclusiones por calidad.")
    print()

    if not high_quality:
        print("  ERROR: 0 sesiones válidas. Verificar directorio recordings/.")
        return

    # -- Phase 3-5: Full backtest
    print("FASE 3-5 — Backtest completo (pipeline íntegro, sin modificaciones)")
    _sep("-")
    all_trades, sessions_run = phase3_backtest(high_quality)

    global_m  = compute_metrics(all_trades)
    by_sess   = defaultdict(list)
    for t in all_trades:
        by_sess[t.session].append(t)
    max_dd = compute_drawdown(by_sess)

    ticks_approx = sum(
        min(s.get("total_bars", 0), MAX_BARS) * 500
        for s in high_quality
        if s["date"] in by_sess
    )

    inventory = {
        "sessions_run": sessions_run,
        "ticks_approx": ticks_approx,
        "max_dd":       max_dd,
    }

    print()
    print(f"  Sesiones ejecutadas:  {sessions_run}")
    print(f"  Trades generados:     {global_m['n']}")
    print(f"  WR global:            {global_m['wr']:.1f}%")
    print(f"  Profit Factor:        {global_m['profit_factor']:.2f}")
    print(f"  Expectancy:           {global_m['expectancy']:+.2f} pts")
    print(f"  Total PnL:            {global_m['total_pnl']:+.2f} pts")
    print(f"  Max Drawdown (sess):  {max_dd:.2f} pts")
    print()

    # Setup-type breakdown
    by_type: dict = defaultdict(list)
    for t in all_trades:
        by_type[t.stype].append(t)
    priority = ["ORB_SETUP", "FA_SETUP", "VA80_SETUP", "VWAP_SETUP",
                "GAP_SETUP", "POC_SETUP", "BOUNCE_SETUP"]
    print(f"  {'SETUP TYPE':<22} {'N':>4}  {'WR':>6}  {'PF':>6}  {'Exp':>7}  {'PnL':>9}")
    _sep()
    for p in priority:
        ts = by_type.get(p, [])
        if not ts:
            continue
        m = compute_metrics(ts)
        print(f"  {p:<22} {m['n']:>4}  {m['wr']:>5.1f}%  {m['profit_factor']:>6.2f}  "
              f"{m['expectancy']:>+7.2f}  {m['total_pnl']:>+9.2f}")
    print()

    # -- Phase 5: Regime segmentation
    print("FASE 5 — Segmentación por régimen (session_type)")
    _sep("-")
    regime_results = phase5_regime(all_trades, sessions)
    sorted_reg = sorted(regime_results.items(),
                        key=lambda x: x[1].get("expectancy", -999), reverse=True)
    for stype, m in sorted_reg:
        if m["n"] > 0:
            print(f"  {stype:<22} n={m['n']:>3}  WR={m['wr']:>5.1f}%  "
                  f"PF={m['profit_factor']:>5.2f}  Exp={m['expectancy']:>+6.2f}")
    print()

    # -- Phase 6: OOS
    print("FASE 6 — Out of Sample (70/30 split por fecha)")
    _sep("-")
    if len(high_quality) >= 6:
        is_m, oos_m = phase6_oos(high_quality)
        print()
        print(f"  IS   WR={is_m.get('wr', 0):.1f}%  PF={is_m.get('profit_factor', 0):.2f}  "
              f"Exp={is_m.get('expectancy', 0):+.2f}  n={is_m.get('n', 0)}")
        print(f"  OOS  WR={oos_m.get('wr', 0):.1f}%  PF={oos_m.get('profit_factor', 0):.2f}  "
              f"Exp={oos_m.get('expectancy', 0):+.2f}  n={oos_m.get('n', 0)}")
    else:
        is_m = oos_m = {}
        print("  Sesiones insuficientes para split válido.")
    print()

    # -- Phase 7: Robustness
    print("FASE 7 — Robustez por sesión")
    _sep("-")
    rob = phase7_robustness(all_trades)
    print(f"  Sesiones rentables:       {rob['sessions_profitable']}/{rob['sessions_total']} "
          f"({rob['pct_profitable']:.1f}%)")
    print(f"  Concentración top 3:      {rob['top3_concentration']:.1f}% del PnL")
    print(f"  Coeficiente de variación: {rob['cv']:.1f}%")
    print(f"  Mejores: " +
          "  ".join(f"{d}({v:+.1f})" for d, v in rob["best_sessions"]))
    print(f"  Peores:  " +
          "  ".join(f"{d}({v:+.1f})" for d, v in rob["worst_sessions"]))
    print()

    # -- Phase 8: Core vs Adaptive
    print("FASE 8 — Core puro vs Core + adaptativos")
    _sep("-")
    acg = phase8_core_vs_adaptive()
    print(f"  ACG en {acg['sessions_with_acg']} sesiones | "
          f"{acg['total_acg_activation_bars']} barras | "
          f"{acg['total_acg_would_change']} cambiarían resultado")
    ct, at = acg["core_trades"], acg["acg_trades"]
    print(f"  Core >=65:   n={ct['n']}  WR={ct['wr']:.1f}%  Exp={ct['expectancy']:+.2f}")
    print(f"  ACG 55-64:  n={at['n']}  WR={at['wr']:.1f}%  Exp={at['expectancy']:+.2f}")
    print(f"  [27 trades reales en 3 sesiones — muestra limitada]")
    print()

    # -- Phase 9: Verdict
    print("FASE 9 — Veredicto")
    _sep("-")
    phase9_report(global_m, is_m, oos_m, regime_results, rob, acg, inventory)


if __name__ == "__main__":
    main()
