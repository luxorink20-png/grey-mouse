"""
edge_contribution_audit.py — GIBBZ Edge Contribution Audit (Modo Cientifico)
Sin modificar codigo. Sin ajustar parametros. Solo medicion.

Responde: ¿Los contextos "malos" destruyen edge o solo concentran varianza?

Fases:
  1 — Baseline
  2 — Edge Contribution (quien genera el dinero)
  3 — Loss Contribution (quien genera el dano)
  4 — Net Edge Score (ranking composite)
  5 — Remove Test (simulacion de ausencia)
  6 — Edge Dependency (dependencia estructural de VOL_RELEASE)
  7 — Risk-Adjusted Contribution (eficiencia beneficio/riesgo)
  8 — Statistical Reliability (confianza estadistica por contexto)
  9 — Final Verdict
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

CONTEXTS = [
    "VOL_RELEASE",
    "Marzo 2026",
    "Mediodia ET",
    "Apertura ET",
    "Sesiones >=7 trades",
]


# ── Helpers ────────────────────────────────────────────────────────────────────

def _sep(n: int = 72) -> None:
    print("  " + "-" * n)


def _pf(pf: float) -> str:
    return f"{pf:.2f}" if pf != float("inf") else "inf"


def _rf(rf: float) -> str:
    return f"{rf:.2f}" if rf != float("inf") else "inf"


def compute_metrics_e(enriched: list) -> dict:
    if not enriched:
        return {
            "n": 0, "wins": 0, "losses": 0, "wr": 0.0,
            "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0,
            "expectancy": 0.0, "total_pnl": 0.0,
            "gross_wins": 0.0, "gross_losses": 0.0,
            "max_dd": 0.0, "recovery_factor": 0.0,
        }
    wins   = [e for e in enriched if e["result"] == "WIN"]
    losses = [e for e in enriched if e["result"] == "LOSS"]
    n, nw, nl  = len(enriched), len(wins), len(losses)
    wr         = 100.0 * nw / n
    avg_win    = sum(e["pnl"] for e in wins)   / max(nw, 1)
    avg_los    = sum(e["pnl"] for e in losses) / max(nl, 1)
    gross_w    = sum(e["pnl"] for e in wins)
    gross_l    = sum(e["pnl"] for e in losses)
    pf         = abs(gross_w / gross_l) if gross_l != 0 else float("inf")
    exp        = round(wr / 100 * avg_win + (1 - wr / 100) * avg_los, 2)
    total      = round(gross_w + gross_l, 2)

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
        "total_pnl": total, "gross_wins": round(gross_w, 2),
        "gross_losses": round(gross_l, 2),
        "max_dd": max_dd, "recovery_factor": rf,
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
            "date":         sdate,
            "recording":    rf,
            "rec_path":     rpath,
            "valid":        valid,
            "session_type": exp.get("session_type", "UNKNOWN"),
            "ep_score":     exp.get("ep_score", 0),
            "total_bars":   exp.get("total_bars", 0),
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
        max_bar = max(bars_by_session.get(t.session, {0: None}).keys(), default=1)
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
            "session_type": meta.get("session_type", "UNKNOWN"),
            "period":       period,
            "hour_et":      hour,
        })
    return enriched


# ── Context Membership ─────────────────────────────────────────────────────────

def build_context_sets(enriched: list) -> dict:
    by_sess: dict = defaultdict(list)
    for e in enriched:
        by_sess[e["session"]].append(e)
    sessions_ge7 = {d for d, ts in by_sess.items() if len(ts) >= 7}
    return {"sessions_ge7": sessions_ge7}


def in_context(e: dict, ctx: str, ctx_sets: dict) -> bool:
    if ctx == "VOL_RELEASE":
        return e["session_type"] == "VOL_RELEASE"
    if ctx == "Marzo 2026":
        return e["month"] == "2026-03"
    if ctx == "Mediodia ET":
        return "MEDIODIA" in e["period"]
    if ctx == "Apertura ET":
        return "APERTURA" in e["period"]
    if ctx == "Sesiones >=7 trades":
        return e["session"] in ctx_sets["sessions_ge7"]
    return False


def split_context(enriched: list, ctx: str,
                  ctx_sets: dict) -> tuple:
    inside  = [e for e in enriched if     in_context(e, ctx, ctx_sets)]
    outside = [e for e in enriched if not in_context(e, ctx, ctx_sets)]
    return inside, outside


# ── Phase 1: Baseline ──────────────────────────────────────────────────────────

def phase1_baseline(enriched: list) -> dict:
    m = compute_metrics_e(enriched)
    n_sess = len(set(e["session"] for e in enriched))
    by_sess: dict = defaultdict(list)
    for e in enriched:
        by_sess[e["session"]].append(e)
    neg_sess = sum(1 for ts in by_sess.values()
                   if sum(e["pnl"] for e in ts) < 0)
    return {**m, "sessions": n_sess, "negative_sessions": neg_sess}


# ── Phase 2: Edge Contribution ─────────────────────────────────────────────────

def phase2_edge_contribution(enriched: list, baseline: dict,
                             ctx_sets: dict) -> dict:
    total_pnl  = baseline["total_pnl"]
    total_wins = baseline["gross_wins"]
    total_n    = baseline["n"]

    results = {}
    for ctx in CONTEXTS:
        inside, _ = split_context(enriched, ctx, ctx_sets)
        if not inside:
            results[ctx] = {"n": 0}
            continue
        m   = compute_metrics_e(inside)
        n_s = len(set(e["session"] for e in inside))

        pnl_share    = round(m["total_pnl"]  / max(abs(total_pnl),  0.01) * 100, 1)
        wins_share   = round(m["gross_wins"] / max(abs(total_wins), 0.01) * 100, 1)
        trade_share  = round(m["n"]          / max(total_n, 1) * 100, 1)

        results[ctx] = {
            **m,
            "sessions":    n_s,
            "pnl_share":   pnl_share,
            "wins_share":  wins_share,
            "trade_share": trade_share,
        }
    return results


# ── Phase 3: Loss Contribution ─────────────────────────────────────────────────

def phase3_loss_contribution(enriched: list, baseline: dict,
                              ctx_sets: dict) -> dict:
    total_loss_pts    = abs(baseline["gross_losses"])
    total_loss_trades = baseline["losses"]
    n_sessions        = baseline["sessions"]

    # Total drawdown (session-level cumulative)
    total_dd          = baseline["max_dd"]

    results = {}
    for ctx in CONTEXTS:
        inside, outside = split_context(enriched, ctx, ctx_sets)
        if not inside:
            results[ctx] = {"n": 0}
            continue

        ctx_loss_pts    = abs(sum(e["pnl"] for e in inside if e["result"] == "LOSS"))
        ctx_loss_trades = sum(1 for e in inside if e["result"] == "LOSS")
        ctx_sessions    = set(e["session"] for e in inside)
        by_sess: dict   = defaultdict(list)
        for e in inside:
            by_sess[e["session"]].append(e)
        ctx_neg_sess = sum(1 for ts in by_sess.values()
                           if sum(e["pnl"] for e in ts) < 0)

        # DD contribution: MaxDD of outside (what remains without this context)
        m_out = compute_metrics_e(outside)
        dd_removed = round(total_dd - m_out["max_dd"], 2)

        results[ctx] = {
            "n_sessions":       len(ctx_sessions),
            "n_trades":         len(inside),
            "loss_pts":         round(ctx_loss_pts, 2),
            "loss_trades":      ctx_loss_trades,
            "neg_sessions":     ctx_neg_sess,
            "pct_loss_pts":     round(ctx_loss_pts    / max(total_loss_pts, 0.01) * 100, 1),
            "pct_loss_trades":  round(ctx_loss_trades / max(total_loss_trades, 1) * 100, 1),
            "pct_neg_sessions": round(ctx_neg_sess    / max(n_sessions, 1) * 100, 1),
            "dd_removed":       dd_removed,
            "pct_dd":           round(dd_removed / max(total_dd, 0.01) * 100, 1),
        }
    return results


# ── Phase 4: Net Edge Score ────────────────────────────────────────────────────

def phase4_net_edge_score(enriched: list, baseline: dict,
                           p2: dict, p3: dict, ctx_sets: dict) -> list:
    """
    Net Edge Contribution (NEC) — composite metric per context.

    Components (each normalized to comparable scale):
      A — PnL contribution share (% of total PnL):   weight 0.35
      B — PF quality vs baseline (relative):         weight 0.35
      C — Expectancy quality vs baseline (relative): weight 0.20
      D — Drawdown cost (% of total DD removed):     weight 0.10  (penalty)

    Positive NEC = net positive contributor to system edge.
    Negative NEC = net negative contributor.
    """
    b_pf  = baseline["profit_factor"]
    b_exp = baseline["expectancy"]
    b_dd  = max(baseline["max_dd"], 0.01)

    ranking = []
    for ctx in CONTEXTS:
        m2 = p2.get(ctx, {})
        m3 = p3.get(ctx, {})
        if not m2 or m2.get("n", 0) == 0:
            continue

        ctx_pf  = m2["profit_factor"]
        ctx_exp = m2["expectancy"]
        pnl_sh  = m2["pnl_share"]   # signed %
        dd_pct  = m3.get("pct_dd", 0.0)

        # A: PnL share (already in %)
        f_a = pnl_sh * 0.35

        # B: PF relative to baseline
        if ctx_pf == float("inf"):
            f_b = 100.0 * 0.35
        elif b_pf > 0:
            f_b = ((ctx_pf / b_pf) - 1.0) * 100.0 * 0.35
        else:
            f_b = 0.0

        # C: Expectancy relative to baseline
        if b_exp != 0:
            f_c = ((ctx_exp / b_exp) - 1.0) * 100.0 * 0.20
        else:
            f_c = 0.0

        # D: DD cost (penalty — context contributes this % of total DD)
        f_d = -(dd_pct * 0.10)

        nec = round(f_a + f_b + f_c + f_d, 1)
        ranking.append({
            "context": ctx,
            "nec":     nec,
            "f_pnl":   round(f_a, 1),
            "f_pf":    round(f_b, 1),
            "f_exp":   round(f_c, 1),
            "f_dd":    round(f_d, 1),
            "pnl_share":  pnl_sh,
            "pf":      ctx_pf,
            "exp":     ctx_exp,
            "n":       m2["n"],
        })

    ranking.sort(key=lambda x: x["nec"], reverse=True)
    return ranking


# ── Phase 5: Remove Test ───────────────────────────────────────────────────────

def phase5_remove_test(enriched: list, baseline: dict, ctx_sets: dict) -> list:
    results = []
    for ctx in CONTEXTS:
        _, outside = split_context(enriched, ctx, ctx_sets)
        m_out = compute_metrics_e(outside)
        n_exc = len(enriched) - len(outside)

        d_pnl = round(m_out["total_pnl"] - baseline["total_pnl"], 2)
        d_pf  = round(m_out["profit_factor"] - baseline["profit_factor"], 3) \
                if m_out["profit_factor"] != float("inf") else float("inf")
        d_wr  = round(m_out["wr"] - baseline["wr"], 1)
        d_exp = round(m_out["expectancy"] - baseline["expectancy"], 2)
        d_dd  = round(baseline["max_dd"] - m_out["max_dd"], 2)

        # Classification: is this context profitable or destructive?
        inside, _ = split_context(enriched, ctx, ctx_sets)
        m_in  = compute_metrics_e(inside)
        ctx_pnl = m_in["total_pnl"]

        if ctx_pnl > 0 and m_in["profit_factor"] >= baseline["profit_factor"]:
            verdict = "RENTABLE Y EFICIENTE"
        elif ctx_pnl > 0 and m_in["profit_factor"] < baseline["profit_factor"]:
            verdict = "RENTABLE PERO INEFICIENTE"
        elif ctx_pnl > 0 and m_in["profit_factor"] < 1.0:
            verdict = "RENTABLE MARGINAL"
        elif ctx_pnl <= 0:
            verdict = "DESTRUCTIVO NETO"
        else:
            verdict = "INDEFINIDO"

        results.append({
            "context":    ctx,
            "excluded":   n_exc,
            "remaining":  len(outside),
            "wr_after":   m_out["wr"],
            "pf_after":   m_out["profit_factor"],
            "exp_after":  m_out["expectancy"],
            "pnl_after":  m_out["total_pnl"],
            "dd_after":   m_out["max_dd"],
            "rf_after":   m_out["recovery_factor"],
            "d_pnl":      d_pnl,
            "d_pf":       d_pf,
            "d_wr":       d_wr,
            "d_exp":      d_exp,
            "d_dd":       d_dd,
            "ctx_pnl":    ctx_pnl,
            "ctx_pf":     m_in["profit_factor"],
            "verdict":    verdict,
        })
    return results


# ── Phase 6: Edge Dependency (VOL_RELEASE) ─────────────────────────────────────

def phase6_dependency(enriched: list, baseline: dict, ctx_sets: dict) -> dict:
    inside, outside = split_context(enriched, "VOL_RELEASE", ctx_sets)
    m_in  = compute_metrics_e(inside)
    m_out = compute_metrics_e(outside)

    total_pnl     = baseline["total_pnl"]
    total_winners = baseline["wins"]
    total_exp_pts = baseline["expectancy"] * baseline["n"]  # total expectancy mass

    pnl_pct     = round(m_in["total_pnl"]  / max(abs(total_pnl), 0.01) * 100, 1)
    winners_pct = round(m_in["wins"]        / max(total_winners, 1) * 100, 1)
    exp_mass_in = m_in["expectancy"]  * m_in["n"]
    exp_pct     = round(exp_mass_in   / max(abs(total_exp_pts), 0.01) * 100, 1)
    trade_pct   = round(m_in["n"]     / max(baseline["n"], 1) * 100, 1)

    # Dependency classification (based on PnL share and what-if)
    # If removing VOL_RELEASE destroys PnL → critical dependency
    # If removing improves metrics but loses absolute PnL → medium dependency
    d_pnl = m_out["total_pnl"] - total_pnl
    if pnl_pct > 70:
        dep_label = "DEPENDENCIA CRITICA"
    elif pnl_pct > 40:
        dep_label = "DEPENDENCIA ALTA"
    elif pnl_pct > 20:
        dep_label = "DEPENDENCIA MEDIA"
    else:
        dep_label = "DEPENDENCIA BAJA"

    # Can the system survive without VOL_RELEASE?
    survives = m_out["profit_factor"] > 1.0 and m_out["expectancy"] > 0

    return {
        "n_trades":    m_in["n"],
        "n_sessions":  len(set(e["session"] for e in inside)),
        "pnl":         m_in["total_pnl"],
        "pnl_pct":     pnl_pct,
        "winners_pct": winners_pct,
        "exp_pct":     exp_pct,
        "trade_pct":   trade_pct,
        "pf_internal": m_in["profit_factor"],
        "exp_internal":m_in["expectancy"],
        "wr_internal": m_in["wr"],
        "dep_label":   dep_label,
        "survives_without": survives,
        "without_pf":  m_out["profit_factor"],
        "without_exp": m_out["expectancy"],
        "without_pnl": m_out["total_pnl"],
        "without_dd":  m_out["max_dd"],
        "d_pnl":       round(d_pnl, 2),
    }


# ── Phase 7: Risk-Adjusted Contribution ───────────────────────────────────────

def phase7_risk_adjusted(enriched: list, ctx_sets: dict) -> list:
    """
    Contribution Efficiency = gross_wins / abs(gross_losses).
    This is equivalent to internal PF, but framed as: for every point lost,
    how many points were gained?
    Also compute: PnL per session and PnL per trade.
    """
    results = []
    for ctx in CONTEXTS:
        inside, _ = split_context(enriched, ctx, ctx_sets)
        if not inside:
            results.append({"context": ctx, "n": 0, "efficiency": 0.0})
            continue
        m   = compute_metrics_e(inside)
        n_s = len(set(e["session"] for e in inside))

        gross_w = m["gross_wins"]
        gross_l = abs(m["gross_losses"])

        efficiency = round(gross_w / max(gross_l, 0.01), 3)  # = PF
        pnl_per_sess  = round(m["total_pnl"] / max(n_s, 1), 2)
        pnl_per_trade = m["expectancy"]

        results.append({
            "context":       ctx,
            "n":             m["n"],
            "sessions":      n_s,
            "gross_wins":    gross_w,
            "gross_losses":  gross_l,
            "efficiency":    efficiency,
            "pnl_per_sess":  pnl_per_sess,
            "pnl_per_trade": pnl_per_trade,
            "wr":            m["wr"],
        })

    results.sort(key=lambda x: x["efficiency"], reverse=True)
    return results


# ── Phase 8: Statistical Reliability ──────────────────────────────────────────

def phase8_reliability(enriched: list, baseline: dict, ctx_sets: dict) -> list:
    """
    Classify each context by statistical reliability of its metrics.
    Based on N trades (primary) and N sessions (secondary).
    """
    def classify(n_trades: int, n_sess: int) -> tuple:
        if n_trades < 15:
            label = "INSUFICIENTE"
            conf  = max(5, n_trades * 3)
        elif n_trades < 30:
            label = "DEBIL"
            conf  = 35 + n_trades
        elif n_trades < 70:
            label = "MODERADO"
            conf  = min(74, 50 + n_trades // 3)
        else:
            label = "FUERTE"
            conf  = min(95, 75 + n_trades // 20)

        # Penalize if fewer than 3 sessions (anecdotal)
        if n_sess < 3:
            conf = max(5, conf - 20)
            if label not in ("INSUFICIENTE",):
                label = "DEBIL"
        return label, min(conf, 95)

    results = []
    for ctx in CONTEXTS:
        inside, _ = split_context(enriched, ctx, ctx_sets)
        n_t = len(inside)
        n_s = len(set(e["session"] for e in inside))
        label, conf = classify(n_t, n_s)
        m   = compute_metrics_e(inside)

        # PF confidence interval estimate (Wilson-like approximation for PF)
        # Very rough: larger n reduces uncertainty
        if n_t >= 30 and m["profit_factor"] > 0 and m["profit_factor"] != float("inf"):
            pf_margin = round(m["profit_factor"] * (1.5 / (n_t ** 0.5)), 2)
        else:
            pf_margin = None

        results.append({
            "context":   ctx,
            "n_trades":  n_t,
            "n_sessions":n_s,
            "label":     label,
            "confidence":conf,
            "pf":        m["profit_factor"],
            "pf_margin": pf_margin,
            "wr":        m["wr"],
        })
    return results


# ── Phase 9: Final Verdict ─────────────────────────────────────────────────────

def phase9_verdict(
    baseline: dict, p2: dict, p3: dict, p4: list, p5: list,
    p6: dict, p7: list, p8: list
) -> None:
    print()
    print("=" * 72)
    print("  FINAL VERDICT")
    print("=" * 72)
    print()

    # Q1: Quien genera mas dinero
    top_pnl = max(p2.items(), key=lambda x: x[1].get("total_pnl", -9999))
    print(f"  1. Contexto que genera MAS dinero:")
    print(f"     -> {top_pnl[0]}")
    print(f"     PnL={top_pnl[1]['total_pnl']:+.2f} pts  "
          f"({top_pnl[1].get('pnl_share', 0):.1f}% del total)  "
          f"n={top_pnl[1]['n']}")

    # Q2: Quien genera mas perdidas
    top_loss = max(p3.items(), key=lambda x: x[1].get("loss_pts", 0))
    print()
    print(f"  2. Contexto que genera MAS perdidas:")
    print(f"     -> {top_loss[0]}")
    print(f"     Perdidas={top_loss[1]['loss_pts']:.2f} pts  "
          f"({top_loss[1].get('pct_loss_pts', 0):.1f}% del total)")

    # Q3: Mejor relacion beneficio/riesgo
    best_eff = p7[0] if p7 else {}
    print()
    print(f"  3. Mejor relacion beneficio/riesgo (Contribution Efficiency):")
    print(f"     -> {best_eff.get('context', '?')}")
    print(f"     Efficiency={best_eff.get('efficiency', 0):.3f}  "
          f"PnL/trade={best_eff.get('pnl_per_trade', 0):+.2f}  "
          f"WR={best_eff.get('wr', 0):.1f}%  n={best_eff.get('n', 0)}")

    # Q4: Quien sostiene el edge
    top_nec = p4[0] if p4 else {}
    print()
    print(f"  4. Contexto que SOSTIENE el edge del sistema:")
    print(f"     -> {top_nec.get('context', '?')}")
    print(f"     NEC={top_nec.get('nec', 0):+.1f}  "
          f"PnL_share={top_nec.get('pnl_share', 0):.1f}%  "
          f"PF={_pf(top_nec.get('pf', 0))}  "
          f"Exp={top_nec.get('exp', 0):+.2f}")

    # Q5: Quien destruye edge real (negative PnL net AND below-baseline PF)
    destructive = [r for r in p5 if r["ctx_pnl"] <= 0 or r["ctx_pf"] < 1.0]
    destructive.sort(key=lambda x: x["ctx_pnl"])
    print()
    print(f"  5. Contextos que destruyen edge real (PnL neto <= 0 o PF < 1.0):")
    if destructive:
        for d in destructive:
            print(f"     -> {d['context']:<25}  PnL={d['ctx_pnl']:+.2f}  "
                  f"PF={_pf(d['ctx_pf'])}  Verdict: {d['verdict']}")
    else:
        print("     Ninguno. Todos los contextos tienen PnL neto positivo.")

    # Q6: Solo aumenta varianza (positive PnL but below-baseline PF, high DD)
    variance_adders = [r for r in p5 if r["ctx_pnl"] > 0 and r["ctx_pf"] < baseline["profit_factor"]]
    variance_adders.sort(key=lambda x: x["d_dd"], reverse=True)
    print()
    print(f"  6. Contextos que SOLO aumentan varianza (PnL>0 pero PF < baseline, alto DD):")
    if variance_adders:
        for v in variance_adders:
            print(f"     -> {v['context']:<25}  PnL={v['ctx_pnl']:+.2f}  "
                  f"PF={_pf(v['ctx_pf'])}  DD eliminado si se quita: {v['d_dd']:+.2f} pts")
    else:
        print("     Ninguno identificado bajo ese criterio.")

    # Q7: VOL_RELEASE structural dependency
    dep = p6
    print()
    print(f"  7. Dependencia estructural de VOL_RELEASE:")
    print(f"     [{dep['dep_label']}]")
    print(f"     PnL de VOL_RELEASE:     {dep['pnl']:+.2f} pts  ({dep['pnl_pct']:.1f}% del total)")
    print(f"     % Winners de VOL_RELEASE: {dep['winners_pct']:.1f}%")
    print(f"     % Trades de VOL_RELEASE:  {dep['trade_pct']:.1f}%")
    print(f"     PF interno VOL_RELEASE:   {_pf(dep['pf_internal'])}")
    print(f"     Sistema sin VOL_RELEASE:  PF={_pf(dep['without_pf'])}  "
          f"Exp={dep['without_exp']:+.2f}  PnL={dep['without_pnl']:+.2f}")
    if dep["survives_without"]:
        print(f"     -> El sistema sigue siendo rentable sin VOL_RELEASE.")
    else:
        print(f"     -> El sistema NO es rentable sin VOL_RELEASE.")

    print()
    print("=" * 72)
    print("  NOTA: Este reporte es puramente observacional.")
    print("  Sin recomendaciones. Sin modificaciones. Solo evidencia.")
    print("=" * 72)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("=" * 72)
    print("  GIBBZ EDGE CONTRIBUTION AUDIT -- Modo Cientifico")
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

    sessions_with_trades = set(t.session for t in all_trades)
    print()
    print(f"  Leyendo timestamps ({len(sessions_with_trades)} sesiones)...")
    bar_timestamps: dict = {}
    for s in sessions:
        if s["date"] not in sessions_with_trades or not s["rec_path"]:
            continue
        max_bar = max(
            (t.entry_bar for t in all_trades if t.session == s["date"]),
            default=100
        ) + 50
        bar_timestamps[s["date"]] = get_bar_timestamps(s["rec_path"], max_bar)
    print("  Timestamps OK.")
    print()

    enriched = enrich_trades(
        all_trades, bars_by_session, session_meta, bar_timestamps
    )
    ctx_sets = build_context_sets(enriched)

    # ── FASE 1
    print("FASE 1 -- Baseline")
    _sep()
    p1 = phase1_baseline(enriched)
    print(f"  Sesiones:     {p1['sessions']}  "
          f"({p1['negative_sessions']} negativas)")
    print(f"  Trades:       {p1['n']}  ({p1['wins']}W / {p1['losses']}L)")
    print(f"  WR:           {p1['wr']:.1f}%")
    print(f"  PF:           {_pf(p1['profit_factor'])}")
    print(f"  Expectancy:   {p1['expectancy']:+.2f} pts/trade")
    print(f"  Gross Wins:   {p1['gross_wins']:+.2f} pts")
    print(f"  Gross Losses: {p1['gross_losses']:+.2f} pts")
    print(f"  PnL Total:    {p1['total_pnl']:+.2f} pts")
    print(f"  Max Drawdown: {p1['max_dd']:.2f} pts")
    print(f"  Recovery F.:  {_rf(p1['recovery_factor'])}")
    print()

    # ── FASE 2
    print("FASE 2 -- Edge Contribution (quien genera el dinero)")
    _sep()
    p2 = phase2_edge_contribution(enriched, p1, ctx_sets)
    print(f"  {'Contexto':<26}  {'N':>4}  {'Sess':>5}  "
          f"{'WR':>6}  {'PF':>6}  {'Exp':>7}  "
          f"{'PnL':>9}  {'%PnL':>6}  "
          f"{'GrossW':>8}  {'GrossL':>8}  {'%Winners':>9}  {'%Trades':>8}")
    _sep()
    for ctx in CONTEXTS:
        m = p2.get(ctx, {})
        if not m or m.get("n", 0) == 0:
            print(f"  {ctx:<26}  {'0':>4}  --  -- (sin trades)")
            continue
        print(f"  {ctx:<26}  {m['n']:>4}  {m['sessions']:>5}  "
              f"{m['wr']:>5.1f}%  {_pf(m['profit_factor']):>6}  "
              f"{m['expectancy']:>+7.2f}  "
              f"{m['total_pnl']:>+9.2f}  {m['pnl_share']:>+5.1f}%  "
              f"{m['gross_wins']:>+8.2f}  {m['gross_losses']:>+8.2f}  "
              f"{m['wins_share']:>8.1f}%  {m['trade_share']:>7.1f}%")
    print()

    # ── FASE 3
    print("FASE 3 -- Loss Contribution (quien genera el dano)")
    _sep()
    print(f"  Total perdidas: {abs(p1['gross_losses']):.2f} pts  "
          f"| Total DD: {p1['max_dd']:.2f} pts")
    print()
    print(f"  {'Contexto':<26}  {'N':>4}  "
          f"{'Loss pts':>9}  {'%Loss pts':>10}  "
          f"{'Loss trades':>12}  {'%Loss trades':>13}  "
          f"{'Neg sess':>9}  {'%Neg sess':>10}  "
          f"{'DD remov.':>10}  {'%DD':>6}")
    _sep()
    p3 = phase3_loss_contribution(enriched, p1, ctx_sets)
    for ctx in CONTEXTS:
        m = p3.get(ctx, {})
        if not m or m.get("n_trades", 0) == 0:
            print(f"  {ctx:<26}  --")
            continue
        print(f"  {ctx:<26}  {m['n_trades']:>4}  "
              f"{m['loss_pts']:>9.2f}  {m['pct_loss_pts']:>9.1f}%  "
              f"{m['loss_trades']:>12}  {m['pct_loss_trades']:>12.1f}%  "
              f"{m['neg_sessions']:>9}  {m['pct_neg_sessions']:>9.1f}%  "
              f"{m['dd_removed']:>+10.2f}  {m['pct_dd']:>5.1f}%")
    print()

    # ── FASE 4
    print("FASE 4 -- Net Edge Score (mayor contribuyente positivo -> negativo)")
    _sep()
    p4 = phase4_net_edge_score(enriched, p1, p2, p3, ctx_sets)
    print(f"  {'Contexto':<26}  {'NEC':>7}  "
          f"{'F_PnL':>7}  {'F_PF':>7}  {'F_Exp':>7}  {'F_DD':>7}  "
          f"{'PF':>6}  {'Exp':>7}  {'N':>4}")
    _sep()
    for r in p4:
        print(f"  {r['context']:<26}  {r['nec']:>+7.1f}  "
              f"{r['f_pnl']:>+7.1f}  {r['f_pf']:>+7.1f}  "
              f"{r['f_exp']:>+7.1f}  {r['f_dd']:>+7.1f}  "
              f"{_pf(r['pf']):>6}  {r['exp']:>+7.2f}  {r['n']:>4}")
    print()

    # ── FASE 5
    print("FASE 5 -- Remove Test (¿que pasa si nunca hubiera existido?)")
    _sep()
    p5 = phase5_remove_test(enriched, p1, ctx_sets)
    print(f"  {'Contexto':<26}  {'Excl':>5}  "
          f"{'WR':>6}  {'PF':>6}  {'Exp':>7}  "
          f"{'PnL':>9}  {'DD':>7}  "
          f"{'ΔExp':>7}  {'ΔPF':>7}  {'ΔWR':>7}  {'ΔPnL':>9}  {'ΔDD':>7}  "
          f"Clasificacion")
    _sep()
    for r in p5:
        dpf = f"{r['d_pf']:+.3f}" if r["d_pf"] != float("inf") else "+inf"
        print(f"  {r['context']:<26}  {r['excluded']:>5}  "
              f"{r['wr_after']:>5.1f}%  {_pf(r['pf_after']):>6}  "
              f"{r['exp_after']:>+7.2f}  "
              f"{r['pnl_after']:>+9.2f}  {r['dd_after']:>7.2f}  "
              f"{r['d_exp']:>+7.2f}  {dpf:>7}  {r['d_wr']:>+6.1f}%  "
              f"{r['d_pnl']:>+9.2f}  {r['d_dd']:>+7.2f}  "
              f"{r['verdict']}")
    print()

    # ── FASE 6
    print("FASE 6 -- Edge Dependency (dependencia de VOL_RELEASE)")
    _sep()
    p6 = phase6_dependency(enriched, p1, ctx_sets)
    print(f"  Trades en VOL_RELEASE:    {p6['n_trades']} / {p1['n']}  "
          f"({p6['trade_pct']:.1f}% del total)")
    print(f"  Sesiones VOL_RELEASE:     {p6['n_sessions']}")
    print()
    print(f"  PnL generado:             {p6['pnl']:+.2f} pts  ({p6['pnl_pct']:.1f}% del total)")
    print(f"  % Winners del sistema:    {p6['winners_pct']:.1f}%")
    print(f"  % Expectancy mass:        {p6['exp_pct']:.1f}%")
    print()
    print(f"  PF interno VOL_RELEASE:   {_pf(p6['pf_internal'])}")
    print(f"  WR interno VOL_RELEASE:   {p6['wr_internal']:.1f}%")
    print(f"  Exp interna VOL_RELEASE:  {p6['exp_internal']:+.2f} pts/trade")
    print()
    print(f"  Sistema SIN VOL_RELEASE:")
    print(f"    PF:           {_pf(p6['without_pf'])}")
    print(f"    Expectancy:   {p6['without_exp']:+.2f} pts/trade")
    print(f"    PnL:          {p6['without_pnl']:+.2f} pts  (delta: {p6['d_pnl']:+.2f})")
    print(f"    Max Drawdown: {p6['without_dd']:.2f} pts")
    print()
    print(f"  CLASIFICACION: [{p6['dep_label']}]")
    surviving = "SI" if p6["survives_without"] else "NO"
    print(f"  Sistema rentable sin VOL_RELEASE: {surviving}  "
          f"(PF={_pf(p6['without_pf'])}, Exp={p6['without_exp']:+.2f})")
    print()

    # ── FASE 7
    print("FASE 7 -- Risk-Adjusted Contribution (Contribution Efficiency)")
    _sep()
    p7 = phase7_risk_adjusted(enriched, ctx_sets)
    print(f"  {'#':>2}  {'Contexto':<26}  {'N':>4}  {'Sess':>5}  "
          f"{'GrossW':>8}  {'GrossL':>8}  "
          f"{'Efficiency':>11}  {'PnL/trade':>10}  {'PnL/sess':>10}  {'WR':>6}")
    _sep()
    for i, r in enumerate(p7, 1):
        if r["n"] == 0:
            continue
        print(f"  {i:>2}. {r['context']:<26}  {r['n']:>4}  {r['sessions']:>5}  "
              f"{r['gross_wins']:>+8.2f}  {r['gross_losses']:>+8.2f}  "
              f"{r['efficiency']:>11.3f}  {r['pnl_per_trade']:>+10.2f}  "
              f"{r['pnl_per_sess']:>+10.2f}  {r['wr']:>5.1f}%")
    print()

    # ── FASE 8
    print("FASE 8 -- Statistical Reliability")
    _sep()
    p8 = phase8_reliability(enriched, p1, ctx_sets)
    print(f"  {'Contexto':<26}  {'N trades':>9}  {'N sess':>7}  "
          f"{'Nivel':>15}  {'Conf':>6}  {'PF':>6}  {'±PF':>8}")
    _sep()
    for r in p8:
        margin = f"±{r['pf_margin']:.2f}" if r["pf_margin"] is not None else "N/A"
        print(f"  {r['context']:<26}  {r['n_trades']:>9}  {r['n_sessions']:>7}  "
              f"{r['label']:>15}  {r['confidence']:>5}%  "
              f"{_pf(r['pf']):>6}  {margin:>8}")
    print()

    # ── FASE 9
    print("FASE 9 -- Final Verdict")
    _sep()
    phase9_verdict(p1, p2, p3, p4, p5, p6, p7, p8)


if __name__ == "__main__":
    main()
