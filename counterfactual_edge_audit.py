"""
counterfactual_edge_audit.py — GIBBZ Counterfactual Edge Audit (Modo Científico)
Sin modificar código. Sin ajustar parámetros. Solo medición.

Responde: ¿Cuánto edge se está destruyendo?

Fases:
  1 — Baseline (dataset completo)
  2 — Auditoría contrafactual individual (A–F)
  3 — Combinaciones (1–7)
  4 — Impacto marginal (delta por contexto)
  5 — Ranking de fugas de edge
  6 — Concentración del daño
  7 — Contrafactual máximo (todos los contextos destructivos excluidos)
  8 — Edge Purity Score (0–100)
  9 — Reporte final
"""

import json
import os
import sys
import statistics
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from full_backtest import run_session, run_backtest, Trade

CORE_DIR       = Path(__file__).parent
OUTCOMES_DIR   = CORE_DIR / "expansion_outcomes"
RECORDINGS_DIR = CORE_DIR / "recordings"
ET             = timezone(timedelta(hours=-4))   # EDT

MAX_BARS   = 4000
TARGET_CAP = 20.0


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sep(n: int = 72) -> None:
    print("  " + "-" * n)


def _pf_str(pf: float) -> str:
    return f"{pf:.2f}" if pf != float("inf") else "inf"


def _rf_str(rf: float) -> str:
    return f"{rf:.2f}" if rf != float("inf") else "inf"


def compute_metrics_e(enriched: list) -> dict:
    """Compute all metrics from a list of enriched trade dicts."""
    if not enriched:
        return {
            "n": 0, "wins": 0, "losses": 0, "wr": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0,
            "expectancy": 0.0, "total_pnl": 0.0,
            "max_dd": 0.0, "recovery_factor": 0.0,
        }
    wins   = [e for e in enriched if e["result"] == "WIN"]
    losses = [e for e in enriched if e["result"] == "LOSS"]
    n, nw, nl = len(enriched), len(wins), len(losses)
    wr      = 100.0 * nw / n
    avg_win = sum(e["pnl"] for e in wins)   / max(nw, 1)
    avg_los = sum(e["pnl"] for e in losses) / max(nl, 1)
    sum_w   = sum(e["pnl"] for e in wins)
    sum_l   = sum(e["pnl"] for e in losses)
    pf      = abs(sum_w / sum_l) if sum_l != 0 else float("inf")
    exp     = round(wr / 100 * avg_win + (1 - wr / 100) * avg_los, 2)
    total   = round(sum(e["pnl"] for e in enriched), 2)

    by_sess: dict = defaultdict(float)
    for e in enriched:
        by_sess[e["session"]] += e["pnl"]
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

    return {
        "n": n, "wins": nw, "losses": nl, "wr": round(wr, 1),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_los, 2),
        "profit_factor": round(pf, 2), "expectancy": exp,
        "total_pnl": total, "max_dd": max_dd, "recovery_factor": rf,
    }


def get_bar_timestamps(recording_path: Path, max_bar: int) -> dict:
    ts_map: dict = {}
    tick_n = 0
    try:
        with open(recording_path, encoding="utf-8") as f:
            for line in f:
                tick_n += 1
                bar_n = tick_n // 500
                if bar_n > max_bar:
                    break
                if tick_n % 500 == 0:
                    try:
                        ts = json.loads(line.strip()).get("timestamp", 0)
                        if ts:
                            ts_map[bar_n] = float(ts)
                    except Exception:
                        pass
    except Exception:
        pass
    return ts_map


def et_hour(unix_ts: float) -> int:
    return datetime.fromtimestamp(unix_ts, ET).hour


def time_period(hour_et: int) -> str:
    if 9 <= hour_et < 11:    return "APERTURA (9-11 ET)"
    elif 11 <= hour_et < 13: return "MANANA (11-13 ET)"
    elif 13 <= hour_et < 15: return "MEDIODIA (13-15 ET)"
    elif 15 <= hour_et < 17: return "CIERRE (15-17 ET)"
    else:                    return "OVERNIGHT (<9 / >17 ET)"


# ── Load + Run ─────────────────────────────────────────────────────────────────

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
            "date":          sdate,
            "recording":     rf,
            "rec_path":      rpath,
            "valid":         valid,
            "session_type":  exp.get("session_type", "UNKNOWN"),
            "ep_score":      exp.get("ep_score", 0),
            "total_bars":    exp.get("total_bars", 0),
            "od_score":      exp.get("opening_drive_score", 0),
            "vol_exp_score": exp.get("volatility_expansion_score", 0),
            "hb_rate":       exp.get("hb_rate", 0.0),
            "gtal_valid":    exp.get("gtal_valid_count", 0),
            "ets_max":       exp.get("ets_max", 0),
            "delta_persist": exp.get("delta_persistence_score", 0.0),
        })
    return [s for s in sessions if s["valid"]]


def run_full_backtest(sessions: list) -> tuple:
    all_trades: list[Trade] = []
    bars_by_session: dict   = {}
    sessions_run = 0
    for i, s in enumerate(sessions, 1):
        print(f"  [{i:02d}/{len(sessions)}] {s['date']} ({s['recording']}) ...",
              end=" ", flush=True)
        bars = run_session(s["date"], s["recording"], MAX_BARS, TARGET_CAP)
        if not bars:
            print("0 bars -- SKIP")
            continue
        trades = run_backtest(bars, s["date"], TARGET_CAP)
        sessions_run += 1
        all_trades.extend(trades)
        bars_by_session[s["date"]] = {b.bar: b for b in bars}
        w   = sum(1 for t in trades if t.result == "WIN")
        pnl = round(sum(t.pnl for t in trades), 2)
        print(f"{len(bars)} bars | {len(trades)} trades | "
              f"WR={100*w/max(len(trades),1):.0f}% | PnL={pnl:+.1f}")
    return all_trades, bars_by_session, sessions_run


def enrich_trades(all_trades: list, bars_by_session: dict,
                  session_meta: dict, bar_timestamps: dict) -> list:
    enriched = []
    for t in all_trades:
        bd      = bars_by_session.get(t.session, {}).get(t.entry_bar)
        sconf   = bd.sconf if bd else 0
        max_bar = max(bars_by_session.get(t.session, {0: None}).keys(), default=1)
        risk    = abs(t.entry_price - t.stop_level)
        reward  = abs(t.tgt_level   - t.entry_price)
        rratio  = round(reward / risk, 2) if risk > 0 else 0.0
        ts_map  = bar_timestamps.get(t.session, {})
        ts      = ts_map.get(t.entry_bar, 0)
        if ts:
            hour   = et_hour(ts)
            period = time_period(hour)
        else:
            pos  = t.entry_bar / max(max_bar, 1)
            hour = -1
            if pos <= 0.25:   period = "APERTURA (0-25%)"
            elif pos <= 0.50: period = "MANANA (25-50%)"
            elif pos <= 0.75: period = "MEDIODIA (50-75%)"
            else:             period = "TARDE (75-100%)"
        meta = session_meta.get(t.session, {})
        enriched.append({
            "session":      t.session,
            "month":        t.session[:7],
            "entry_bar":    t.entry_bar,
            "stype":        t.stype,
            "direction":    t.direction,
            "pnl":          t.pnl,
            "result":       t.result,
            "sconf":        sconf,
            "rratio":       rratio,
            "stop_dist":    round(risk, 2),
            "session_type": meta.get("session_type", "UNKNOWN"),
            "ep_score":     meta.get("ep_score", 0),
            "hour_et":      hour,
            "period":       period,
        })
    return enriched


# ── Filter Masks ───────────────────────────────────────────────────────────────

def build_masks(enriched: list) -> dict:
    """Build session-level sets for context filters."""
    by_sess: dict = defaultdict(list)
    for e in enriched:
        by_sess[e["session"]].append(e)

    # Sessions with ≥7 trades
    sessions_ge7 = {d for d, ts in by_sess.items() if len(ts) >= 7}

    # Sessions with WR < 25%
    def sess_wr(ts: list) -> float:
        wins = sum(1 for e in ts if e["result"] == "WIN")
        return 100.0 * wins / len(ts) if ts else 0.0

    sessions_wr_lt25 = {d for d, ts in by_sess.items() if sess_wr(ts) < 25.0}

    return {
        "sessions_ge7":      sessions_ge7,
        "sessions_wr_lt25":  sessions_wr_lt25,
    }


def is_mediodia(e: dict) -> bool:
    return "MEDIODIA" in e["period"]


def is_apertura(e: dict) -> bool:
    return "APERTURA" in e["period"]


def is_vol_release(e: dict) -> bool:
    return e["session_type"] == "VOL_RELEASE"


def is_marzo_2026(e: dict) -> bool:
    return e["month"] == "2026-03"


def is_session_ge7(e: dict, masks: dict) -> bool:
    return e["session"] in masks["sessions_ge7"]


def is_session_wr_lt25(e: dict, masks: dict) -> bool:
    return e["session"] in masks["sessions_wr_lt25"]


def apply_exclusions(enriched: list, exclude_fns: list) -> list:
    """Return trades where NONE of the exclude predicates are True."""
    return [e for e in enriched if not any(fn(e) for fn in exclude_fns)]


# ── Phase 1: Baseline ──────────────────────────────────────────────────────────

def phase1_baseline(enriched: list) -> dict:
    m = compute_metrics_e(enriched)
    n_sessions = len(set(e["session"] for e in enriched))
    return {**m, "sessions": n_sessions}


# ── Phase 2: Individual Counterfactuals ────────────────────────────────────────

def phase2_individual(enriched: list, masks: dict) -> dict:
    scenarios = {
        "A - Sin Mediodia ET":        [lambda e: is_mediodia(e)],
        "B - Sin Apertura ET":        [lambda e: is_apertura(e)],
        "C - Sin VOL_RELEASE":        [lambda e: is_vol_release(e)],
        "D - Sin Marzo 2026":         [lambda e: is_marzo_2026(e)],
        "E - Sin Sesiones >=7 trades":[lambda e: is_session_ge7(e, masks)],
        "F - Sin Sesiones WR<25%":    [lambda e: is_session_wr_lt25(e, masks)],
    }
    results = {}
    for name, fns in scenarios.items():
        filtered = apply_exclusions(enriched, fns)
        m = compute_metrics_e(filtered)
        n_exc = len(enriched) - len(filtered)
        results[name] = {**m, "excluded_trades": n_exc,
                         "sessions": len(set(e["session"] for e in filtered))}
    return results


# ── Phase 3: Combinations ──────────────────────────────────────────────────────

def phase3_combos(enriched: list, masks: dict) -> dict:
    combos = {
        "COMBO 1: Sin Mediodia + Sin Apertura": [
            lambda e: is_mediodia(e),
            lambda e: is_apertura(e),
        ],
        "COMBO 2: Sin Mediodia + Sin VOL_RELEASE": [
            lambda e: is_mediodia(e),
            lambda e: is_vol_release(e),
        ],
        "COMBO 3: Sin Mediodia + Sin Marzo 2026": [
            lambda e: is_mediodia(e),
            lambda e: is_marzo_2026(e),
        ],
        "COMBO 4: Sin Mediodia + Sin Sesiones >=7": [
            lambda e: is_mediodia(e),
            lambda e: is_session_ge7(e, masks),
        ],
        "COMBO 5: Sin VOL_RELEASE + Sin Marzo 2026": [
            lambda e: is_vol_release(e),
            lambda e: is_marzo_2026(e),
        ],
        "COMBO 6: Sin Mediodia + Sin VOL_RELEASE + Sin Marzo 2026": [
            lambda e: is_mediodia(e),
            lambda e: is_vol_release(e),
            lambda e: is_marzo_2026(e),
        ],
        "COMBO 7: Sin Mediodia + Sin VOL_RELEASE + Sin Marzo 2026 + Sin >=7": [
            lambda e: is_mediodia(e),
            lambda e: is_vol_release(e),
            lambda e: is_marzo_2026(e),
            lambda e: is_session_ge7(e, masks),
        ],
    }
    results = {}
    for name, fns in combos.items():
        filtered = apply_exclusions(enriched, fns)
        m = compute_metrics_e(filtered)
        n_exc = len(enriched) - len(filtered)
        results[name] = {**m, "excluded_trades": n_exc,
                         "sessions": len(set(e["session"] for e in filtered))}
    return results


# ── Phase 4: Marginal Impact ───────────────────────────────────────────────────

def phase4_marginal(baseline: dict, individual: dict) -> list:
    impacts = []
    for name, m in individual.items():
        delta_pf  = round(m["profit_factor"] - baseline["profit_factor"], 3) \
                    if m["profit_factor"] != float("inf") else float("inf")
        delta_exp = round(m["expectancy"]    - baseline["expectancy"],    2)
        delta_wr  = round(m["wr"]            - baseline["wr"],            1)
        delta_dd  = round(baseline["max_dd"] - m["max_dd"],               2)  # positive = improvement
        delta_pnl = round(m["total_pnl"]     - baseline["total_pnl"],     2)
        impacts.append({
            "name":      name,
            "n":         m["n"],
            "excluded":  m["excluded_trades"],
            "wr":        m["wr"],
            "pf":        m["profit_factor"],
            "exp":       m["expectancy"],
            "pnl":       m["total_pnl"],
            "dd":        m["max_dd"],
            "rf":        m["recovery_factor"],
            "delta_pf":  delta_pf,
            "delta_exp": delta_exp,
            "delta_wr":  delta_wr,
            "delta_dd":  delta_dd,
            "delta_pnl": delta_pnl,
        })
    # Sort by delta_exp descending (biggest improvement first)
    finite = [x for x in impacts if x["delta_exp"] != float("inf")]
    finite.sort(key=lambda x: x["delta_exp"], reverse=True)
    return finite


# ── Phase 5: Leak Ranking ──────────────────────────────────────────────────────

def phase5_leak_ranking(phase4_results: list) -> list:
    """Rank contexts by how much damage they cause (worst first).
    Uses composite score: delta_exp * 0.4 + delta_pf_norm * 0.4 + delta_wr * 0.2
    """
    if not phase4_results:
        return []
    max_dexp = max(abs(r["delta_exp"]) for r in phase4_results) or 1.0
    max_dwr  = max(abs(r["delta_wr"])  for r in phase4_results) or 1.0
    max_dpf  = max(
        abs(r["delta_pf"]) for r in phase4_results
        if r["delta_pf"] != float("inf")
    ) or 1.0

    ranked = []
    for r in phase4_results:
        dpf = r["delta_pf"] if r["delta_pf"] != float("inf") else max_dpf
        score = (r["delta_exp"] / max_dexp * 40 +
                 dpf             / max_dpf  * 40 +
                 r["delta_wr"]  / max_dwr   * 20)
        ranked.append({**r, "leak_score": round(score, 1)})
    ranked.sort(key=lambda x: x["leak_score"], reverse=True)
    return ranked


# ── Phase 6: Damage Concentration ─────────────────────────────────────────────

def phase6_damage_concentration(enriched: list, masks: dict) -> dict:
    total_loss_pts = abs(sum(e["pnl"] for e in enriched if e["result"] == "LOSS"))
    total_pnl      = sum(e["pnl"] for e in enriched)

    def context_stats(filt: list, label: str) -> dict:
        losses_pts = abs(sum(e["pnl"] for e in filt if e["result"] == "LOSS"))
        pnl        = sum(e["pnl"] for e in filt)
        pct_losses = round(100.0 * losses_pts / total_loss_pts, 1) if total_loss_pts > 0 else 0.0
        pct_pnl    = round(100.0 * abs(pnl)   / max(abs(total_pnl), 0.01), 1)
        return {
            "label":           label,
            "n":               len(filt),
            "losses_pts":      round(losses_pts, 2),
            "pct_of_losses":   pct_losses,
            "pnl":             round(pnl, 2),
            "pct_of_abs_pnl":  pct_pnl,
        }

    mediodia_trades  = [e for e in enriched if is_mediodia(e)]
    apertura_trades  = [e for e in enriched if is_apertura(e)]
    volrel_trades    = [e for e in enriched if is_vol_release(e)]
    marzo_trades     = [e for e in enriched if is_marzo_2026(e)]
    ge7_trades       = [e for e in enriched if is_session_ge7(e, masks)]
    wr25_trades      = [e for e in enriched if is_session_wr_lt25(e, masks)]

    # Union of all bad context trades (deduplicated by identity)
    bad_ids = set()
    for e in mediodia_trades + volrel_trades + marzo_trades + ge7_trades:
        bad_ids.add(id(e))
    bad_all  = [e for e in enriched if id(e) in bad_ids]
    good_all = [e for e in enriched if id(e) not in bad_ids]

    return {
        "total_loss_pts":   round(total_loss_pts, 2),
        "total_pnl":        round(total_pnl, 2),
        "mediodia":         context_stats(mediodia_trades, "Mediodia ET (13-15)"),
        "apertura":         context_stats(apertura_trades, "Apertura ET (9-11)"),
        "vol_release":      context_stats(volrel_trades,   "VOL_RELEASE"),
        "marzo_2026":       context_stats(marzo_trades,    "Marzo 2026"),
        "sessions_ge7":     context_stats(ge7_trades,      "Sesiones >=7 trades"),
        "sessions_wr_lt25": context_stats(wr25_trades,     "Sesiones WR<25%"),
        "all_bad_combined": context_stats(bad_all,         "Union contextos malos"),
        "good_only":        context_stats(good_all,        "Contextos limpios"),
    }


# ── Phase 7: Maximum Counterfactual ────────────────────────────────────────────

def phase7_max_counterfactual(enriched: list, masks: dict) -> dict:
    # Exclude all destructive contexts identified by failure_investigation:
    # Mediodia ET + VOL_RELEASE + Marzo 2026 + Sesiones >=7 trades
    fns = [
        lambda e: is_mediodia(e),
        lambda e: is_vol_release(e),
        lambda e: is_marzo_2026(e),
        lambda e: is_session_ge7(e, masks),
    ]
    filtered = apply_exclusions(enriched, fns)
    m = compute_metrics_e(filtered)
    n_exc = len(enriched) - len(filtered)
    n_sess_excluded = len(set(e["session"] for e in enriched
                              if any(fn(e) for fn in fns)))
    return {
        **m,
        "excluded_trades":   n_exc,
        "remaining_trades":  len(filtered),
        "sessions_excluded": n_sess_excluded,
        "sessions_remaining": len(set(e["session"] for e in filtered)),
    }


# ── Phase 8: Edge Purity Score ────────────────────────────────────────────────

def phase8_purity_score(baseline: dict, counterfactual_max: dict,
                        damage: dict) -> dict:
    """
    Edge Purity Score 0–100.
    Measures: how much of the edge comes from clean contexts vs. how much
    is destroyed by bad contexts.

    Components:
      A (0–50): Clean-context PF quality
      B (0–30): % of total losses concentrated in bad contexts (isolated damage)
      C (0–20): Clean-context WR above 50%
    """
    clean_pf = counterfactual_max["profit_factor"]
    clean_wr = counterfactual_max["wr"]
    loss_pct = damage["all_bad_combined"]["pct_of_losses"]

    # A: PF of clean context (PF=1.0 → 0, PF=2.0 → 50, PF≥3.0 → 50)
    if clean_pf == float("inf"):
        score_a = 50.0
    elif clean_pf <= 1.0:
        score_a = 0.0
    else:
        score_a = min(50.0, (clean_pf - 1.0) / 1.0 * 50.0)

    # B: damage isolation (higher = more damage lives in bad contexts → cleaner elsewhere)
    score_b = min(30.0, loss_pct * 0.30)

    # C: clean WR above 50% (WR=50 → 0, WR=60 → 10, WR=70 → 20)
    score_c = max(0.0, min(20.0, (clean_wr - 50.0) * 2.0))

    score = round(score_a + score_b + score_c)

    if score <= 30:
        label = "EDGE CONTAMINADO"
    elif score <= 50:
        label = "EDGE INCONSISTENTE"
    elif score <= 70:
        label = "EDGE PROMETEDOR"
    elif score <= 85:
        label = "EDGE LIMPIO"
    else:
        label = "EDGE ALTAMENTE SELECTIVO"

    return {
        "score":       score,
        "label":       label,
        "score_a":     round(score_a, 1),
        "score_b":     round(score_b, 1),
        "score_c":     round(score_c, 1),
        "clean_pf":    clean_pf,
        "clean_wr":    clean_wr,
        "loss_pct_bad": loss_pct,
        "baseline_pf": baseline["profit_factor"],
        "baseline_wr": baseline["wr"],
        "delta_pf":    round(
            (clean_pf - baseline["profit_factor"])
            if clean_pf != float("inf") and baseline["profit_factor"] != float("inf")
            else 0, 3
        ),
    }


# ── Phase 9: Final Report ──────────────────────────────────────────────────────

def phase9_report(
    baseline: dict,
    individual: dict,
    combos: dict,
    marginal: list,
    leak_ranking: list,
    damage: dict,
    cf_max: dict,
    purity: dict,
) -> None:
    print()
    print("=" * 72)
    print("  # GIBBZ COUNTERFACTUAL EDGE REPORT")
    print(f"  Fecha analisis: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 72)

    # ── BASELINE
    print()
    print("  BASELINE (dataset completo)")
    _sep()
    b = baseline
    print(f"  Sesiones:     {b['sessions']}")
    print(f"  Trades:       {b['n']}  ({b['wins']}W / {b['losses']}L)")
    print(f"  Win Rate:     {b['wr']:.1f}%")
    print(f"  Profit Factor:{b['profit_factor']:.2f}")
    print(f"  Expectancy:   {b['expectancy']:+.2f} pts/trade")
    print(f"  PnL Total:    {b['total_pnl']:+.2f} pts")
    print(f"  Max Drawdown: {b['max_dd']:.2f} pts")
    print(f"  Recovery F.:  {_rf_str(b['recovery_factor'])}")

    # ── ESCENARIOS INDIVIDUALES
    print()
    print("  ESCENARIOS INDIVIDUALES")
    _sep()
    print(f"  {'Escenario':<38}  {'N':>4}  {'Excl':>5}  {'WR':>6}  "
          f"{'PF':>6}  {'Exp':>7}  {'PnL':>9}  {'DD':>7}  {'RF':>7}")
    _sep()
    for name, m in individual.items():
        label = name.split(" - ", 1)[1] if " - " in name else name
        print(f"  {label:<38}  {m['n']:>4}  {m['excluded_trades']:>5}  "
              f"{m['wr']:>5.1f}%  {_pf_str(m['profit_factor']):>6}  "
              f"{m['expectancy']:>+7.2f}  {m['total_pnl']:>+9.2f}  "
              f"{m['max_dd']:>7.2f}  {_rf_str(m['recovery_factor']):>7}")

    # ── COMBINACIONES
    print()
    print("  COMBINACIONES")
    _sep()
    print(f"  {'Combo':<50}  {'N':>4}  {'WR':>6}  {'PF':>6}  "
          f"{'Exp':>7}  {'PnL':>9}  {'DD':>7}")
    _sep()
    for name, m in combos.items():
        label = name.split(": ", 1)[1] if ": " in name else name
        print(f"  {label:<50}  {m['n']:>4}  {m['wr']:>5.1f}%  "
              f"{_pf_str(m['profit_factor']):>6}  {m['expectancy']:>+7.2f}  "
              f"{m['total_pnl']:>+9.2f}  {m['max_dd']:>7.2f}")

    # ── IMPACTO MARGINAL
    print()
    print("  IMPACTO MARGINAL (mayor mejora -> menor mejora)")
    _sep()
    print(f"  {'Exclusion':<38}  {'ΔExp':>7}  {'ΔPF':>7}  {'ΔWR':>6}  "
          f"{'ΔPnL':>9}  {'ΔDD':>7}")
    _sep()
    for r in marginal:
        label = r["name"].split(" - ", 1)[1] if " - " in r["name"] else r["name"]
        dpf = f"{r['delta_pf']:+.3f}" if r["delta_pf"] != float("inf") else "+inf"
        print(f"  {label:<38}  {r['delta_exp']:>+7.2f}  {dpf:>7}  "
              f"{r['delta_wr']:>+5.1f}%  {r['delta_pnl']:>+9.2f}  "
              f"{r['delta_dd']:>+7.2f}")
    print()
    if marginal:
        worst = marginal[0]
        label = worst["name"].split(" - ", 1)[1] if " - " in worst["name"] else worst["name"]
        print(f"  -> Contexto que destruye MAS valor: {label}")
        best  = marginal[-1]
        label_b = best["name"].split(" - ", 1)[1] if " - " in best["name"] else best["name"]
        print(f"  -> Contexto que aporta MENOS valor: {label_b}")

    # ── TOP FUGAS DE EDGE
    print()
    print("  TOP FUGAS DE EDGE")
    _sep()
    for i, r in enumerate(leak_ranking, 1):
        label = r["name"].split(" - ", 1)[1] if " - " in r["name"] else r["name"]
        dpf   = f"{r['delta_pf']:+.3f}" if r["delta_pf"] != float("inf") else "+inf"
        print(f"  {i}. {label}")
        print(f"     Excl={r['excluded']:>3}  WR sin={r['wr']:>5.1f}%  "
              f"PF sin={_pf_str(r['pf'])}  Exp sin={r['exp']:>+6.2f}  "
              f"ΔExp={r['delta_exp']:>+6.2f}  ΔPF={dpf}  "
              f"Leak Score={r['leak_score']}")

    # ── CONCENTRACION DEL DAÑO
    print()
    print("  CONCENTRACION DEL DANO")
    _sep()
    print(f"  Total perdidas (pts):  {damage['total_loss_pts']:+.2f}")
    print(f"  Total PnL (pts):       {damage['total_pnl']:+.2f}")
    print()
    print(f"  {'Contexto':<32}  {'N':>4}  {'Loss pts':>10}  "
          f"{'% Losses':>9}  {'PnL':>9}")
    _sep(65)
    for key in ["mediodia", "apertura", "vol_release", "marzo_2026",
                "sessions_ge7", "sessions_wr_lt25"]:
        d = damage[key]
        print(f"  {d['label']:<32}  {d['n']:>4}  "
              f"{d['losses_pts']:>10.2f}  {d['pct_of_losses']:>8.1f}%  "
              f"{d['pnl']:>+9.2f}")
    print()
    bad = damage["all_bad_combined"]
    good = damage["good_only"]
    print(f"  {'Union contextos malos':<32}  {bad['n']:>4}  "
          f"{bad['losses_pts']:>10.2f}  {bad['pct_of_losses']:>8.1f}%  "
          f"{bad['pnl']:>+9.2f}")
    print(f"  {'Contextos limpios':<32}  {good['n']:>4}  "
          f"{good['losses_pts']:>10.2f}  {good['pct_of_losses']:>8.1f}%  "
          f"{good['pnl']:>+9.2f}")

    # ── CONTRAFACTUAL MAXIMO
    print()
    print("  CONTRAFACTUAL MAXIMO")
    print("  (Sin Mediodia + Sin VOL_RELEASE + Sin Marzo 2026 + Sin Sesiones >=7)")
    _sep()
    cf = cf_max
    print(f"  Trades restantes:   {cf['remaining_trades']}  "
          f"(excluidos: {cf['excluded_trades']})")
    print(f"  Sesiones restantes: {cf['sessions_remaining']}  "
          f"(excluidas: {cf['sessions_excluded']})")
    print(f"  Win Rate:           {cf['wr']:.1f}%")
    print(f"  Profit Factor:      {_pf_str(cf['profit_factor'])}")
    print(f"  Expectancy:         {cf['expectancy']:+.2f} pts/trade")
    print(f"  PnL Total:          {cf['total_pnl']:+.2f} pts")
    print(f"  Max Drawdown:       {cf['max_dd']:.2f} pts")
    print(f"  Recovery Factor:    {_rf_str(cf['recovery_factor'])}")

    # ── EDGE PURITY SCORE
    print()
    print("  EDGE PURITY SCORE")
    _sep()
    p = purity
    print(f"  Score:     {p['score']} / 100")
    print(f"  Categoria: {p['label']}")
    print()
    print(f"  Componente A — PF contexto limpio "
          f"(PF={_pf_str(p['clean_pf'])}):            {p['score_a']:.1f} / 50")
    print(f"  Componente B — Concentracion daño en contextos malos "
          f"({p['loss_pct_bad']:.1f}%): {p['score_b']:.1f} / 30")
    print(f"  Componente C — WR contexto limpio "
          f"(WR={p['clean_wr']:.1f}%):               {p['score_c']:.1f} / 20")
    print()
    print(f"  Baseline:           PF={_pf_str(p['baseline_pf'])}  WR={p['baseline_wr']:.1f}%")
    print(f"  Contexto limpio:    PF={_pf_str(p['clean_pf'])}  WR={p['clean_wr']:.1f}%")
    print(f"  Delta PF:           {p['delta_pf']:+.3f}")

    # ── PREGUNTAS FINALES
    print()
    print("=" * 72)
    print("  CONCLUSIONES ESTADISTICAS")
    print("=" * 72)
    print()

    # Q1: ¿Qué contexto destruye más valor?
    q1 = leak_ranking[0] if leak_ranking else None
    if q1:
        label = q1["name"].split(" - ", 1)[1] if " - " in q1["name"] else q1["name"]
        print(f"  1. Contexto que destruye MAS valor:")
        print(f"     -> {label}")
        print(f"     Leak Score={q1['leak_score']}  ΔExp={q1['delta_exp']:+.2f}  "
              f"ΔPF={q1['delta_pf']:+.3f}  Excluye {q1['excluded']} trades")

    # Q2: ¿Qué contexto aporta menos valor?
    q2 = leak_ranking[-1] if len(leak_ranking) > 1 else None
    if q2:
        label2 = q2["name"].split(" - ", 1)[1] if " - " in q2["name"] else q2["name"]
        print()
        print(f"  2. Contexto que aporta MENOS valor (menor Leak Score):")
        print(f"     -> {label2}")
        print(f"     Leak Score={q2['leak_score']}  ΔExp={q2['delta_exp']:+.2f}  "
              f"ΔPF={q2['delta_pf']:+.3f}  Excluye {q2['excluded']} trades")

    # Q3: ¿Dónde se concentra el daño?
    sorted_by_loss = sorted(
        [damage[k] for k in ["mediodia", "apertura", "vol_release",
                              "marzo_2026", "sessions_ge7"]],
        key=lambda x: x["pct_of_losses"], reverse=True
    )
    print()
    print("  3. Concentracion del dano (ordenado por % de perdidas):")
    for d in sorted_by_loss:
        print(f"     {d['label']:<32}: {d['pct_of_losses']:>5.1f}% de todas las perdidas  "
              f"(PnL={d['pnl']:+.2f})")

    # Q4: % daño que explican los 3 peores contextos
    top3_pct = sum(d["pct_of_losses"] for d in sorted_by_loss[:3])
    top3_names = [d["label"] for d in sorted_by_loss[:3]]
    print()
    print(f"  4. Los 3 peores contextos explican el {top3_pct:.1f}% de todas las perdidas:")
    for n_ in top3_names:
        print(f"     - {n_}")

    # Q5: ¿Edge limpio o contaminado?
    print()
    print(f"  5. Estado del edge: [{purity['label']}]  (Score={purity['score']}/100)")
    if purity["score"] <= 30:
        print("     El edge esta contaminado. Los contextos malos son ubicuos.")
    elif purity["score"] <= 50:
        print("     El edge es inconsistente. Los contextos malos impactan"
              " ampliamente.")
    elif purity["score"] <= 70:
        print("     El edge es prometedor. Los contextos malos explican una "
              "porcion significativa del dano.")
    elif purity["score"] <= 85:
        print("     El edge es limpio. Los contextos malos son aislables.")
    else:
        print("     El edge es altamente selectivo. El dano esta muy concentrado.")

    # Q6: ¿Cuán selectivo es GIBBZ?
    clean_pct = round(100.0 * cf_max["remaining_trades"] /
                      max(baseline["n"], 1), 1)
    print()
    print(f"  6. Selectividad de GIBBZ:")
    print(f"     Trades en contextos limpios: {cf_max['remaining_trades']} / "
          f"{baseline['n']} ({clean_pct:.1f}% del total)")
    print(f"     PF en contextos limpios:     {_pf_str(cf_max['profit_factor'])}")
    print(f"     PF baseline (todos):         {_pf_str(baseline['profit_factor'])}")
    pf_ratio = (cf_max["profit_factor"] / baseline["profit_factor"]
                if (baseline["profit_factor"] > 0 and
                    baseline["profit_factor"] != float("inf") and
                    cf_max["profit_factor"] != float("inf"))
                else float("inf"))
    if pf_ratio != float("inf"):
        print(f"     Razon de mejora:             {pf_ratio:.2f}x")

    # Q7: Principal conclusión estadística
    print()
    print("  7. Principal conclusion estadistica:")
    base_pf   = baseline["profit_factor"]
    clean_pf  = cf_max["profit_factor"]
    delta_exp = cf_max["expectancy"] - baseline["expectancy"]
    best_leak = leak_ranking[0] if leak_ranking else {}
    best_label = (best_leak.get("name", "?").split(" - ", 1)[1]
                  if " - " in best_leak.get("name", "") else best_leak.get("name", "?"))
    print(f"     El sistema tiene edge real (PF baseline={_pf_str(base_pf)}) pero")
    print(f"     {damage['all_bad_combined']['pct_of_losses']:.1f}% de las perdidas se concentran")
    print(f"     en contextos identificados como destructivos.")
    print(f"     Excluirlos mejoraria la Expectancy en {delta_exp:+.2f} pts/trade")
    print(f"     y el PF a {_pf_str(clean_pf)} (vs {_pf_str(base_pf)} baseline).")
    print(f"     El principal destructor de edge es: {best_label}.")
    print()
    print("  NOTA: Este analisis es puramente observacional.")
    print("  NO implica recomendacion de cambios. Solo mide donde esta el dano.")
    print()
    print("=" * 72)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("=" * 72)
    print("  GIBBZ COUNTERFACTUAL EDGE AUDIT -- Modo Cientifico")
    print("  Sin modificar codigo. Sin ajustar parametros. Solo medicion.")
    print("=" * 72)
    print()

    sessions     = load_sessions()
    session_meta = {s["date"]: s for s in sessions}
    print(f"  Sesiones con grabacion valida: {len(sessions)}")
    print()

    print("  Ejecutando backtest completo...")
    print()
    all_trades, bars_by_session, sessions_run = run_full_backtest(sessions)

    if not all_trades:
        print("  ERROR: 0 trades generados.")
        return

    # Build bar timestamps for time-of-day enrichment
    sessions_with_trades = set(t.session for t in all_trades)
    print()
    print(f"  Leyendo timestamps ({len(sessions_with_trades)} sesiones)...")
    bar_timestamps: dict = {}
    for s in sessions:
        if s["date"] not in sessions_with_trades:
            continue
        if not s["rec_path"]:
            continue
        max_bar = max(
            (t.entry_bar for t in all_trades if t.session == s["date"]),
            default=100
        ) + 50
        ts_map = get_bar_timestamps(s["rec_path"], max_bar)
        bar_timestamps[s["date"]] = ts_map

    print(f"  Timestamps listos.")
    print()

    enriched = enrich_trades(all_trades, bars_by_session, session_meta, bar_timestamps)
    masks    = build_masks(enriched)

    n_ge7    = len(masks["sessions_ge7"])
    n_wr25   = len(masks["sessions_wr_lt25"])
    print(f"  Sesiones con >=7 trades:  {n_ge7}  ({sorted(masks['sessions_ge7'])})")
    print(f"  Sesiones con WR<25%:      {n_wr25}  ({sorted(masks['sessions_wr_lt25'])})")
    print()

    # ── Phase 1
    print("FASE 1 -- Baseline")
    _sep()
    p1 = phase1_baseline(enriched)
    print(f"  Sesiones: {p1['sessions']}  Trades: {p1['n']} ({p1['wins']}W/{p1['losses']}L)")
    print(f"  WR: {p1['wr']:.1f}%  PF: {p1['profit_factor']:.2f}  "
          f"Exp: {p1['expectancy']:+.2f}  PnL: {p1['total_pnl']:+.2f}  "
          f"MaxDD: {p1['max_dd']:.2f}  RF: {_rf_str(p1['recovery_factor'])}")
    print()

    # ── Phase 2
    print("FASE 2 -- Escenarios individuales")
    _sep()
    p2 = phase2_individual(enriched, masks)
    print(f"  {'Escenario':<40}  {'N':>4}  {'Excl':>5}  {'WR':>6}  "
          f"{'PF':>6}  {'Exp':>7}  {'PnL':>9}")
    _sep()
    for name, m in p2.items():
        label = name.split(" - ", 1)[1] if " - " in name else name
        print(f"  {label:<40}  {m['n']:>4}  {m['excluded_trades']:>5}  "
              f"{m['wr']:>5.1f}%  {_pf_str(m['profit_factor']):>6}  "
              f"{m['expectancy']:>+7.2f}  {m['total_pnl']:>+9.2f}")
    print()

    # ── Phase 3
    print("FASE 3 -- Combinaciones")
    _sep()
    p3 = phase3_combos(enriched, masks)
    print(f"  {'Combo':<52}  {'N':>4}  {'WR':>6}  {'PF':>6}  "
          f"{'Exp':>7}  {'PnL':>9}  {'DD':>7}")
    _sep()
    for name, m in p3.items():
        label = name.split(": ", 1)[1] if ": " in name else name
        print(f"  {label:<52}  {m['n']:>4}  {m['wr']:>5.1f}%  "
              f"{_pf_str(m['profit_factor']):>6}  {m['expectancy']:>+7.2f}  "
              f"{m['total_pnl']:>+9.2f}  {m['max_dd']:>7.2f}")
    print()

    # ── Phase 4
    print("FASE 4 -- Impacto marginal")
    _sep()
    p4 = phase4_marginal(p1, p2)
    print(f"  {'Exclusion':<40}  {'ΔExp':>7}  {'ΔPF':>7}  {'ΔWR':>7}  "
          f"{'ΔPnL':>9}  {'ΔDD':>7}")
    _sep()
    for r in p4:
        label = r["name"].split(" - ", 1)[1] if " - " in r["name"] else r["name"]
        dpf   = f"{r['delta_pf']:+.3f}" if r["delta_pf"] != float("inf") else "+inf"
        print(f"  {label:<40}  {r['delta_exp']:>+7.2f}  {dpf:>7}  "
              f"{r['delta_wr']:>+6.1f}%  {r['delta_pnl']:>+9.2f}  "
              f"{r['delta_dd']:>+7.2f}")
    print()

    # ── Phase 5
    print("FASE 5 -- Ranking de fugas de edge")
    _sep()
    p5 = phase5_leak_ranking(p4)
    for i, r in enumerate(p5, 1):
        label = r["name"].split(" - ", 1)[1] if " - " in r["name"] else r["name"]
        print(f"  {i}. {label:<38}  Leak Score={r['leak_score']:.1f}  "
              f"ΔExp={r['delta_exp']:>+6.2f}  ΔPF={r['delta_pf']:>+.3f}")
    print()

    # ── Phase 6
    print("FASE 6 -- Concentracion del dano")
    _sep()
    p6 = phase6_damage_concentration(enriched, masks)
    print(f"  Total perdidas (pts): {p6['total_loss_pts']:+.2f}")
    print()
    print(f"  {'Contexto':<32}  {'N':>4}  {'Loss pts':>9}  "
          f"{'% Losses':>9}  {'PnL':>9}")
    _sep(65)
    for key in ["mediodia", "apertura", "vol_release", "marzo_2026",
                "sessions_ge7", "sessions_wr_lt25", "all_bad_combined", "good_only"]:
        d = p6[key]
        print(f"  {d['label']:<32}  {d['n']:>4}  {d['losses_pts']:>9.2f}  "
              f"{d['pct_of_losses']:>8.1f}%  {d['pnl']:>+9.2f}")
    print()

    # ── Phase 7
    print("FASE 7 -- Contrafactual maximo")
    _sep()
    p7 = phase7_max_counterfactual(enriched, masks)
    print(f"  Trades restantes:   {p7['remaining_trades']} / {p1['n']}  "
          f"(excluidos: {p7['excluded_trades']})")
    print(f"  Sesiones restantes: {p7['sessions_remaining']}  "
          f"(excluidas: {p7['sessions_excluded']})")
    print(f"  WR:           {p7['wr']:.1f}%  (baseline: {p1['wr']:.1f}%)")
    print(f"  PF:           {_pf_str(p7['profit_factor'])}  (baseline: {_pf_str(p1['profit_factor'])})")
    print(f"  Expectancy:   {p7['expectancy']:+.2f}  (baseline: {p1['expectancy']:+.2f})")
    print(f"  PnL:          {p7['total_pnl']:+.2f}  (baseline: {p1['total_pnl']:+.2f})")
    print(f"  MaxDD:        {p7['max_dd']:.2f}  (baseline: {p1['max_dd']:.2f})")
    print(f"  Recovery F.:  {_rf_str(p7['recovery_factor'])}")
    print()

    # ── Phase 8
    print("FASE 8 -- Edge Purity Score")
    _sep()
    p8 = phase8_purity_score(p1, p7, p6)
    print(f"  SCORE: {p8['score']} / 100  —  {p8['label']}")
    print()
    print(f"  A (PF limpio):      {p8['score_a']:.1f} / 50  "
          f"(PF={_pf_str(p8['clean_pf'])})")
    print(f"  B (dano aislado):   {p8['score_b']:.1f} / 30  "
          f"({p8['loss_pct_bad']:.1f}% de perdidas en contextos malos)")
    print(f"  C (WR limpia):      {p8['score_c']:.1f} / 20  "
          f"(WR={p8['clean_wr']:.1f}%)")
    print()

    # ── Phase 9
    print("FASE 9 -- Reporte final")
    _sep()
    phase9_report(p1, p2, p3, p4, p5, p6, p7, p8)


if __name__ == "__main__":
    main()
