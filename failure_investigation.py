"""
failure_investigation.py — GIBBZ Edge Failure Investigation (Modo Cientifico)
Sin modificar codigo. Sin ajustar parametros. Solo analisis.

Responde: donde desaparece el edge?
¿En que condiciones el sistema deja de tener ventaja?

Fases:
  1 — Periodos negativos (10 peores sesiones, 5 peores rachas, peor mes)
  2 — Ganadores vs perdedores (comparacion objetiva)
  3 — Autopsia de Marzo 2026
  4 — Regimenes (donde vive el edge, donde se destruye)
  5 — Distribucion de senales (sconf, R:R, stop — W vs L)
  6 — Analisis de horario (apertura/manana/mediodia/tarde)
  7 — Edge Decay Ranking (factores ordenados por PF — peor a mejor)
  8 — Failure Signatures (patrones antes de perdidas)
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

def compute_metrics(trades: list) -> dict:
    if not trades:
        return {"n": 0, "wins": 0, "losses": 0, "wr": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0, "profit_factor": 0.0,
                "expectancy": 0.0, "total_pnl": 0.0}
    wins   = [t for t in trades if t.result == "WIN"]
    losses = [t for t in trades if t.result == "LOSS"]
    n, nw, nl = len(trades), len(wins), len(losses)
    wr      = 100.0 * nw / n
    avg_win = sum(t.pnl for t in wins)   / max(nw, 1)
    avg_los = sum(t.pnl for t in losses) / max(nl, 1)
    sum_w   = sum(t.pnl for t in wins)
    sum_l   = sum(t.pnl for t in losses)
    pf      = abs(sum_w / sum_l) if sum_l != 0 else float("inf")
    exp     = round(wr / 100 * avg_win + (1 - wr / 100) * avg_los, 2)
    return {
        "n": n, "wins": nw, "losses": nl, "wr": round(wr, 1),
        "avg_win": round(avg_win, 2), "avg_loss": round(avg_los, 2),
        "profit_factor": round(pf, 2), "expectancy": exp,
        "total_pnl": round(sum(t.pnl for t in trades), 2),
    }


def _sep(n: int = 70) -> None:
    print("  " + "-" * n)


def _pf_str(pf: float) -> str:
    return f"{pf:.2f}" if pf != float("inf") else "inf"


def get_bar_timestamps(recording_path: Path, max_bar: int) -> dict:
    """Read every 500th tick to get bar_number -> Unix timestamp mapping."""
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
    if 9 <= hour_et < 11:   return "APERTURA (9-11 ET)"
    elif 11 <= hour_et < 13: return "MANANA (11-13 ET)"
    elif 13 <= hour_et < 15: return "MEDIODIA (13-15 ET)"
    elif 15 <= hour_et < 17: return "CIERRE (15-17 ET)"
    else:                    return "OVERNIGHT (<9 / >17 ET)"


# ── Load Sessions ───────────────────────────────────────────────────────────────

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
            "date":           sdate,
            "recording":      rf,
            "rec_path":       rpath,
            "valid":          valid,
            "session_type":   exp.get("session_type", "UNKNOWN"),
            "ep_score":       exp.get("ep_score", 0),
            "total_bars":     exp.get("total_bars", 0),
            "od_score":       exp.get("opening_drive_score", 0),
            "vol_exp_score":  exp.get("volatility_expansion_score", 0),
            "hb_rate":        exp.get("hb_rate", 0.0),
            "gtal_valid":     exp.get("gtal_valid_count", 0),
            "ets_max":        exp.get("ets_max", 0),
            "delta_persist":  exp.get("delta_persistence_score", 0.0),
        })
    return [s for s in sessions if s["valid"]]


# ── Run Backtest (extended: also capture BarData for sconf) ─────────────────────

def run_full_backtest(sessions: list) -> tuple:
    all_trades:      list[Trade] = []
    bars_by_session: dict        = {}  # date -> {bar_num: BarData}
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


# ── Enrich Trades ───────────────────────────────────────────────────────────────

def enrich_trades(all_trades: list, bars_by_session: dict,
                  session_meta: dict, bar_timestamps: dict) -> list:
    enriched = []
    for t in all_trades:
        bd     = bars_by_session.get(t.session, {}).get(t.entry_bar)
        sconf  = bd.sconf if bd else 0
        max_bar = max(bars_by_session.get(t.session, {0: None}).keys(), default=1)

        # R:R ratio
        risk   = abs(t.entry_price - t.stop_level)
        reward = abs(t.tgt_level   - t.entry_price)
        rratio = round(reward / risk, 2) if risk > 0 else 0.0

        # Time period: use timestamp if available, else bar position
        ts_map = bar_timestamps.get(t.session, {})
        ts     = ts_map.get(t.entry_bar, 0)
        if ts:
            hour   = et_hour(ts)
            period = time_period(hour)
        else:
            # fallback: relative position
            pos = t.entry_bar / max(max_bar, 1)
            hour = -1
            if pos <= 0.25:    period = "APERTURA (0-25%)"
            elif pos <= 0.50:  period = "MANANA (25-50%)"
            elif pos <= 0.75:  period = "MEDIODIA (50-75%)"
            else:              period = "TARDE (75-100%)"

        meta = session_meta.get(t.session, {})
        enriched.append({
            "session":       t.session,
            "month":         t.session[:7],
            "entry_bar":     t.entry_bar,
            "max_bar":       max_bar,
            "stype":         t.stype,
            "direction":     t.direction,
            "pnl":           t.pnl,
            "result":        t.result,
            "sconf":         sconf,
            "rratio":        rratio,
            "stop_dist":     round(risk, 2),
            "entry_price":   t.entry_price,
            "session_type":  meta.get("session_type", "UNKNOWN"),
            "ep_score":      meta.get("ep_score", 0),
            "od_score":      meta.get("od_score", 0),
            "vol_exp_score": meta.get("vol_exp_score", 0),
            "hb_rate":       meta.get("hb_rate", 0.0),
            "ets_max":       meta.get("ets_max", 0),
            "delta_persist": meta.get("delta_persist", 0.0),
            "hour_et":       hour,
            "period":        period,
        })
    return enriched


# ── Phase 1: Worst Periods ──────────────────────────────────────────────────────

def phase1_worst_periods(all_trades: list, session_meta: dict) -> dict:
    by_sess: dict = defaultdict(list)
    for t in all_trades:
        by_sess[t.session].append(t)

    sess_data = []
    for d, ts in sorted(by_sess.items()):
        m = compute_metrics(ts)
        sess_data.append({"date": d, **m,
                          "session_type": session_meta.get(d, {}).get("session_type", "?")})

    # Sort by total_pnl ascending (worst first)
    sorted_worst = sorted(sess_data, key=lambda x: x["total_pnl"])

    # Worst 5 consecutive session streaks by cumulative PnL drop
    sorted_chron = sorted(sess_data, key=lambda x: x["date"])
    pnls = [s["total_pnl"] for s in sorted_chron]
    dates = [s["date"] for s in sorted_chron]

    # Find all maximal negative-run sequences
    streaks = []
    i = 0
    while i < len(pnls):
        if pnls[i] <= 0:
            j = i
            while j < len(pnls) and pnls[j] <= 0:
                j += 1
            streak_pnl = sum(pnls[i:j])
            streaks.append({
                "start": dates[i], "end": dates[j-1],
                "sessions": j - i, "pnl": round(streak_pnl, 2),
                "dates": dates[i:j], "pnls": pnls[i:j],
            })
            i = j
        else:
            i += 1

    streaks_sorted = sorted(streaks, key=lambda x: x["pnl"])[:5]

    # Worst month
    by_month: dict = defaultdict(list)
    for t in all_trades:
        by_month[t.session[:7]].append(t)
    month_metrics = {m: compute_metrics(ts) for m, ts in by_month.items()}
    worst_month = min(month_metrics.items(), key=lambda x: x[1]["total_pnl"])
    best_month  = max(month_metrics.items(), key=lambda x: x[1]["total_pnl"])

    return {
        "sessions_worst10": sorted_worst[:10],
        "sessions_best10":  sorted(sess_data, key=lambda x: x["total_pnl"], reverse=True)[:10],
        "streaks":          streaks_sorted,
        "month_metrics":    month_metrics,
        "worst_month":      worst_month,
        "best_month":       best_month,
    }


# ── Phase 2: Winners vs Losers (session level) ─────────────────────────────────

def phase2_winners_vs_losers(enriched: list, p1: dict) -> dict:
    best10  = {s["date"] for s in p1["sessions_best10"]}
    worst10 = {s["date"] for s in p1["sessions_worst10"]}

    group_w = [e for e in enriched if e["session"] in best10]
    group_l = [e for e in enriched if e["session"] in worst10]

    def profile(grp: list, label: str) -> dict:
        if not grp:
            return {}
        setup_dist  = defaultdict(int)
        regime_dist = defaultdict(int)
        dir_dist    = defaultdict(int)
        for e in grp:
            setup_dist[e["stype"]]        += 1
            regime_dist[e["session_type"]] += 1
            dir_dist[e["direction"]]       += 1
        confs   = [e["sconf"]     for e in grp if e["sconf"]   > 0]
        rrs     = [e["rratio"]    for e in grp if e["rratio"]  > 0]
        stops   = [e["stop_dist"] for e in grp if e["stop_dist"] > 0]
        eps     = [e["ep_score"]  for e in grp]
        hbs     = [e["hb_rate"]   for e in grp]
        ets     = [e["ets_max"]   for e in grp]
        deltas  = [e["delta_persist"] for e in grp]
        sessions = len(set(e["session"] for e in grp))
        return {
            "label":       label,
            "sessions":    sessions,
            "trades":      len(grp),
            "avg_trades":  round(len(grp) / max(sessions, 1), 1),
            "setup_dist":  dict(setup_dist),
            "regime_dist": dict(regime_dist),
            "dir_dist":    dict(dir_dist),
            "avg_sconf":   round(statistics.mean(confs), 1)   if confs  else 0,
            "avg_rr":      round(statistics.mean(rrs), 2)     if rrs    else 0,
            "avg_stop":    round(statistics.mean(stops), 2)   if stops  else 0,
            "avg_ep":      round(statistics.mean(eps), 1)     if eps    else 0,
            "avg_hb_rate": round(statistics.mean(hbs), 3)     if hbs    else 0,
            "avg_ets_max": round(statistics.mean(ets), 1)     if ets    else 0,
            "avg_delta":   round(statistics.mean(deltas), 1)  if deltas else 0,
        }

    return {
        "winners": profile(group_w, "TOP 10 GANADORAS"),
        "losers":  profile(group_l, "TOP 10 PERDEDORAS"),
    }


# ── Phase 3: March 2026 Autopsy ─────────────────────────────────────────────────

def phase3_march_autopsy(enriched: list, session_meta: dict,
                         all_trades: list) -> dict:
    march_sessions = [d for d in set(e["session"] for e in enriched)
                      if d.startswith("2026-03")]

    by_sess_march: dict = defaultdict(list)
    for t in all_trades:
        if t.session.startswith("2026-03"):
            by_sess_march[t.session].append(t)

    all_march_sessions = sorted(
        {e.get("session_date", "") for ef in sorted(OUTCOMES_DIR.glob("2026-03*_expansion.json"))
         for e in [json.load(open(ef, encoding="utf-8"))]}
    )

    march_enriched = [e for e in enriched if e["session"].startswith("2026-03")]

    # Worst session deep dive: 2026-03-24
    worst_march = sorted(by_sess_march.keys(),
                         key=lambda d: sum(t.pnl for t in by_sess_march[d]))[0] \
        if by_sess_march else None

    session_breakdown = {}
    for d in sorted(by_sess_march.keys()):
        ts  = by_sess_march[d]
        m   = compute_metrics(ts)
        meta = session_meta.get(d, {})
        trade_details = [
            {"bar": t.entry_bar, "stype": t.stype, "dir": t.direction,
             "pnl": t.pnl, "result": t.result,
             "stop_dist": round(abs(t.entry_price - t.stop_level), 2),
             "rratio": round(abs(t.tgt_level - t.entry_price) /
                             max(abs(t.entry_price - t.stop_level), 0.01), 2)}
            for t in sorted(ts, key=lambda x: x.entry_bar)
        ]
        session_breakdown[d] = {
            "metrics": m, "meta": meta, "trades": trade_details
        }

    return {
        "sessions_with_trades": sorted(by_sess_march.keys()),
        "breakdown":            session_breakdown,
        "worst_session":        worst_march,
        "march_enriched":       march_enriched,
    }


# ── Phase 4: Regime Analysis ─────────────────────────────────────────────────────

def phase4_regime(all_trades: list, session_meta: dict) -> dict:
    by_regime: dict = defaultdict(list)
    for t in all_trades:
        regime = session_meta.get(t.session, {}).get("session_type", "UNKNOWN")
        by_regime[regime].append(t)

    result = {}
    for regime, trades in by_regime.items():
        m = compute_metrics(trades)
        by_sess: dict = defaultdict(list)
        for t in trades:
            by_sess[t.session].append(t)
        sess_pnls = [sum(ts.pnl for ts in v) for v in by_sess.values()]
        max_dd = 0.0
        cum = peak = 0.0
        for pnl in sess_pnls:
            cum += pnl
            if cum > peak: peak = cum
            dd = peak - cum
            if dd > max_dd: max_dd = dd
        result[regime] = {**m, "max_dd": round(max_dd, 2),
                          "sess_count": len(by_sess)}
    return result


# ── Phase 5: Signal Distribution (W vs L) ──────────────────────────────────────

def phase5_signals(enriched: list) -> dict:
    wins   = [e for e in enriched if e["result"] == "WIN"]
    losses = [e for e in enriched if e["result"] == "LOSS"]

    def stats_for(grp: list, label: str) -> dict:
        if not grp: return {"label": label}
        confs  = [e["sconf"]     for e in grp if e["sconf"]    > 0]
        rrs    = [e["rratio"]    for e in grp if e["rratio"]   > 0]
        stops  = [e["stop_dist"] for e in grp if e["stop_dist"]> 0]
        eps    = [e["ep_score"]  for e in grp]
        longs  = sum(1 for e in grp if e["direction"] == "LONG")
        shorts = len(grp) - longs
        setup_dist = defaultdict(int)
        for e in grp: setup_dist[e["stype"]] += 1

        return {
            "label":       label,
            "n":           len(grp),
            "avg_sconf":   round(statistics.mean(confs), 1)  if confs  else 0,
            "med_sconf":   round(statistics.median(confs), 1) if confs else 0,
            "min_sconf":   min(confs) if confs else 0,
            "avg_rr":      round(statistics.mean(rrs), 2)    if rrs    else 0,
            "med_rr":      round(statistics.median(rrs), 2)  if rrs    else 0,
            "avg_stop":    round(statistics.mean(stops), 2)  if stops  else 0,
            "med_stop":    round(statistics.median(stops), 2) if stops else 0,
            "long_pct":    round(100*longs/len(grp), 1),
            "setup_dist":  dict(setup_dist),
            "avg_ep":      round(statistics.mean(eps), 1)    if eps    else 0,
        }

    # sconf distribution buckets for W and L
    def bucket_sconf(grp: list) -> dict:
        buckets = defaultdict(int)
        for e in grp:
            c = e["sconf"]
            if c == 0:         buckets["0"] += 1
            elif c < 40:       buckets["1-39"] += 1
            elif c < 60:       buckets["40-59"] += 1
            elif c < 80:       buckets["60-79"] += 1
            else:              buckets["80+"] += 1
        return dict(buckets)

    # R:R distribution
    def bucket_rr(grp: list) -> dict:
        buckets = defaultdict(int)
        for e in grp:
            rr = e["rratio"]
            if rr == 0:       buckets["0"] += 1
            elif rr < 1.0:    buckets["<1.0"] += 1
            elif rr < 1.5:    buckets["1.0-1.5"] += 1
            elif rr < 2.0:    buckets["1.5-2.0"] += 1
            elif rr < 3.0:    buckets["2.0-3.0"] += 1
            else:             buckets["3.0+"] += 1
        return dict(buckets)

    return {
        "wins":          stats_for(wins,   "GANADORES"),
        "losses":        stats_for(losses, "PERDEDORES"),
        "win_sconf_dist": bucket_sconf(wins),
        "los_sconf_dist": bucket_sconf(losses),
        "win_rr_dist":   bucket_rr(wins),
        "los_rr_dist":   bucket_rr(losses),
    }


# ── Phase 6: Time of Day ────────────────────────────────────────────────────────

def phase6_time(enriched: list) -> dict:
    by_period: dict = defaultdict(list)
    by_hour:   dict = defaultdict(list)

    for e in enriched:
        by_period[e["period"]].append(e)
        if e["hour_et"] >= 0:
            by_hour[e["hour_et"]].append(e)

    period_order = ["APERTURA (9-11 ET)", "MANANA (11-13 ET)",
                    "MEDIODIA (13-15 ET)", "CIERRE (15-17 ET)",
                    "OVERNIGHT (<9 / >17 ET)"]

    period_metrics = {}
    for p in period_order:
        grp = by_period.get(p, [])
        if not grp: continue
        wins = sum(1 for e in grp if e["result"] == "WIN")
        pnl  = round(sum(e["pnl"] for e in grp), 2)
        exp  = round(pnl / len(grp), 2)
        wr   = round(100.0 * wins / len(grp), 1)
        sum_wins = sum(e["pnl"] for e in grp if e["result"] == "WIN")
        sum_los  = sum(e["pnl"] for e in grp if e["result"] == "LOSS")
        pf = round(abs(sum_wins / sum_los), 2) if sum_los != 0 else float("inf")
        period_metrics[p] = {"n": len(grp), "wr": wr, "pf": pf, "exp": exp, "pnl": pnl}

    # By hour
    hour_metrics = {}
    for h in sorted(by_hour.keys()):
        grp = by_hour[h]
        wins = sum(1 for e in grp if e["result"] == "WIN")
        pnl  = round(sum(e["pnl"] for e in grp), 2)
        wr   = round(100.0 * wins / len(grp), 1)
        exp  = round(pnl / len(grp), 2)
        sum_wins = sum(e["pnl"] for e in grp if e["result"] == "WIN")
        sum_los  = sum(e["pnl"] for e in grp if e["result"] == "LOSS")
        pf = round(abs(sum_wins / sum_los), 2) if sum_los != 0 else float("inf")
        hour_metrics[h] = {"n": len(grp), "wr": wr, "pf": pf, "exp": exp, "pnl": pnl}

    return {"by_period": period_metrics, "by_hour": hour_metrics}


# ── Phase 7: Edge Decay Ranking ─────────────────────────────────────────────────

def phase7_decay_ranking(enriched: list, session_meta: dict) -> list:
    """Rank all factor-values by PF (ascending = worst first)."""
    factors = []

    def add_factor(name: str, grp: list) -> None:
        if not grp:
            return
        wins   = [e for e in grp if e["result"] == "WIN"]
        losses = [e for e in grp if e["result"] == "LOSS"]
        sw = sum(e["pnl"] for e in wins)
        sl = sum(e["pnl"] for e in losses)
        pf  = round(abs(sw / sl), 2) if sl != 0 else float("inf")
        exp = round(sum(e["pnl"] for e in grp) / len(grp), 2)
        wr  = round(100 * len(wins) / len(grp), 1)
        factors.append({"name": name, "n": len(grp), "wr": wr, "pf": pf, "exp": exp,
                        "pnl": round(sum(e["pnl"] for e in grp), 2)})

    # By regime
    by_regime: dict = defaultdict(list)
    for e in enriched: by_regime[e["session_type"]].append(e)
    for r, g in by_regime.items(): add_factor(f"Regimen: {r}", g)

    # By period
    by_period: dict = defaultdict(list)
    for e in enriched: by_period[e["period"]].append(e)
    for p, g in by_period.items(): add_factor(f"Periodo: {p}", g)

    # By setup
    by_setup: dict = defaultdict(list)
    for e in enriched: by_setup[e["stype"]].append(e)
    for s, g in by_setup.items(): add_factor(f"Setup: {s}", g)

    # By direction
    by_dir: dict = defaultdict(list)
    for e in enriched: by_dir[e["direction"]].append(e)
    for d, g in by_dir.items(): add_factor(f"Direccion: {d}", g)

    # By month
    by_month: dict = defaultdict(list)
    for e in enriched: by_month[e["month"]].append(e)
    for m, g in by_month.items(): add_factor(f"Mes: {m}", g)

    # Sort by PF ascending (worst first), inf at end
    factors_finite  = [f for f in factors if f["pf"] != float("inf")]
    factors_inf     = [f for f in factors if f["pf"] == float("inf")]
    return sorted(factors_finite, key=lambda x: x["pf"]) + factors_inf


# ── Phase 8: Failure Signatures ─────────────────────────────────────────────────

def phase8_failure_signatures(enriched: list, all_trades: list) -> dict:
    # 1. Within-session momentum: prev trade result -> current trade result
    by_sess_seq: dict = defaultdict(list)
    for e in sorted(enriched, key=lambda x: (x["session"], x["entry_bar"])):
        by_sess_seq[e["session"]].append(e)

    after_win_wins   = after_win_losses   = 0
    after_loss_wins  = after_loss_losses  = 0
    for seq in by_sess_seq.values():
        for i in range(1, len(seq)):
            prev_r = seq[i-1]["result"]
            curr_r = seq[i]["result"]
            if prev_r == "WIN":
                if curr_r == "WIN":   after_win_wins  += 1
                else:                 after_win_losses += 1
            else:
                if curr_r == "WIN":   after_loss_wins  += 1
                else:                 after_loss_losses += 1

    total_after_win  = after_win_wins  + after_win_losses
    total_after_loss = after_loss_wins + after_loss_losses
    wr_after_win  = round(100 * after_win_wins  / max(total_after_win,  1), 1)
    wr_after_loss = round(100 * after_loss_wins / max(total_after_loss, 1), 1)

    # 2. Stop distance: winners vs losers
    wins_stop   = [e["stop_dist"] for e in enriched
                   if e["result"] == "WIN"   and e["stop_dist"] > 0]
    losses_stop = [e["stop_dist"] for e in enriched
                   if e["result"] == "LOSS"  and e["stop_dist"] > 0]
    avg_stop_win = round(statistics.mean(wins_stop),   2) if wins_stop   else 0
    avg_stop_los = round(statistics.mean(losses_stop), 2) if losses_stop else 0

    # 3. R:R: winners vs losers
    wins_rr   = [e["rratio"] for e in enriched
                 if e["result"] == "WIN"  and e["rratio"] > 0]
    losses_rr = [e["rratio"] for e in enriched
                 if e["result"] == "LOSS" and e["rratio"] > 0]
    avg_rr_win = round(statistics.mean(wins_rr),   2) if wins_rr   else 0
    avg_rr_los = round(statistics.mean(losses_rr), 2) if losses_rr else 0

    # 4. Overtrading flag: sessions with many trades and low WR
    by_sess_m: dict = defaultdict(list)
    for t in all_trades: by_sess_m[t.session].append(t)
    overtraded = []
    for d, ts in by_sess_m.items():
        if len(ts) >= 7:
            m = compute_metrics(ts)
            if m["wr"] < 35:
                overtraded.append({"date": d, **m})
    overtraded.sort(key=lambda x: x["total_pnl"])

    # 5. Direction dominance in losing sessions
    losing_sessions = set(sorted(by_sess_m.keys(),
                                 key=lambda d: sum(t.pnl for t in by_sess_m[d]))[:10])
    losing_enriched = [e for e in enriched if e["session"] in losing_sessions]
    dir_in_losers = defaultdict(int)
    for e in losing_enriched: dir_in_losers[e["direction"]] += 1

    # 6. sconf of losing trades: are they lower?
    wins_conf   = [e["sconf"] for e in enriched
                   if e["result"] == "WIN"  and e["sconf"] > 0]
    losses_conf = [e["sconf"] for e in enriched
                   if e["result"] == "LOSS" and e["sconf"] > 0]
    avg_conf_win = round(statistics.mean(wins_conf),   1) if wins_conf   else 0
    avg_conf_los = round(statistics.mean(losses_conf), 1) if losses_conf else 0

    # 7. Session-level: does a bad session follow another bad session?
    sess_pnls_chron = []
    for d in sorted(by_sess_m.keys()):
        pnl = sum(t.pnl for t in by_sess_m[d])
        sess_pnls_chron.append({"date": d, "pnl": pnl})

    after_bad_win  = after_bad_loss  = 0
    after_good_win = after_good_loss = 0
    for i in range(1, len(sess_pnls_chron)):
        prev = sess_pnls_chron[i-1]["pnl"]
        curr = sess_pnls_chron[i]["pnl"]
        if prev < 0:
            if curr >= 0: after_bad_win  += 1
            else:         after_bad_loss += 1
        else:
            if curr >= 0: after_good_win  += 1
            else:         after_good_loss += 1

    total_after_bad  = after_bad_win  + after_bad_loss
    total_after_good = after_good_win + after_good_loss
    pos_after_bad  = round(100 * after_bad_win  / max(total_after_bad,  1), 1)
    pos_after_good = round(100 * after_good_win / max(total_after_good, 1), 1)

    return {
        "momentum": {
            "wr_after_win":       wr_after_win,
            "wr_after_loss":      wr_after_loss,
            "n_after_win":        total_after_win,
            "n_after_loss":       total_after_loss,
            "after_win_wins":     after_win_wins,
            "after_win_losses":   after_win_losses,
            "after_loss_wins":    after_loss_wins,
            "after_loss_losses":  after_loss_losses,
        },
        "stop_comparison": {
            "avg_stop_win":  avg_stop_win,
            "avg_stop_loss": avg_stop_los,
        },
        "rr_comparison": {
            "avg_rr_win":  avg_rr_win,
            "avg_rr_loss": avg_rr_los,
        },
        "conf_comparison": {
            "avg_conf_win":  avg_conf_win,
            "avg_conf_loss": avg_conf_los,
        },
        "overtraded_sessions": overtraded,
        "dir_in_losing_sessions": dict(dir_in_losers),
        "session_momentum": {
            "pct_positive_after_bad":  pos_after_bad,
            "pct_positive_after_good": pos_after_good,
            "n_after_bad":  total_after_bad,
            "n_after_good": total_after_good,
        },
    }


# ── Phase 9: Final Report ───────────────────────────────────────────────────────

def phase9_report(p1, p2, p3, p4, p5, p6, p7, p8) -> None:
    print()
    print("=" * 72)
    print("  # GIBBZ EDGE FAILURE REPORT")
    print(f"  Fecha analisis: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 72)

    # 1. Peores sesiones
    print()
    print("  1. PEORES SESIONES")
    _sep()
    print(f"  {'#':>2}  {'Sesion':<12}  {'PnL':>9}  {'WR':>6}  {'PF':>6}  "
          f"{'Exp':>7}  {'N':>3}  Tipo")
    _sep()
    for i, s in enumerate(p1["sessions_worst10"], 1):
        print(f"  {i:>2}. {s['date']:<12}  {s['total_pnl']:>+9.2f}  "
              f"{s['wr']:>5.1f}%  {_pf_str(s['profit_factor']):>6}  "
              f"{s['expectancy']:>+7.2f}  {s['n']:>3}  {s['session_type']}")

    # 2. Peores regimenes
    print()
    print("  2. PEORES REGIMENES")
    _sep()
    print(f"  {'REGIMEN':<22}  {'N':>3}  {'WR':>6}  {'PF':>6}  {'Exp':>7}  "
          f"{'PnL':>9}  {'MaxDD':>8}  {'Sess':>5}")
    _sep()
    for regime, m in sorted(p4.items(), key=lambda x: x[1]["expectancy"]):
        print(f"  {regime:<22}  {m['n']:>3}  {m['wr']:>5.1f}%  "
              f"{_pf_str(m['profit_factor']):>6}  {m['expectancy']:>+7.2f}  "
              f"{m['total_pnl']:>+9.2f}  {m['max_dd']:>8.2f}  {m['sess_count']:>5}")

    # 3. Peores horarios
    print()
    print("  3. PEORES HORARIOS")
    _sep()
    bp = p6.get("by_period", {})
    print(f"  {'PERIODO':<30}  {'N':>3}  {'WR':>6}  {'PF':>6}  {'Exp':>7}  {'PnL':>9}")
    _sep()
    for period in sorted(bp, key=lambda x: bp[x]["exp"]):
        m = bp[period]
        print(f"  {period:<30}  {m['n']:>3}  {m['wr']:>5.1f}%  "
              f"{_pf_str(m['pf']):>6}  {m['exp']:>+7.2f}  {m['pnl']:>+9.2f}")

    # 4. Peores contextos (comparacion W vs L sessions)
    print()
    print("  4. CONTEXTO — GANADORAS vs PERDEDORAS")
    _sep()
    w = p2.get("winners", {})
    l = p2.get("losers",  {})
    if w and l:
        print(f"  {'Metrica':<28}  {'GANADORAS':>12}  {'PERDEDORAS':>12}  {'Delta':>10}")
        _sep(60)
        rows = [
            ("Trades/sesion",   w["avg_trades"],    l["avg_trades"],    ""),
            ("Avg sconf",       w["avg_sconf"],      l["avg_sconf"],     ""),
            ("Avg R:R",         w["avg_rr"],         l["avg_rr"],        ""),
            ("Avg stop dist",   w["avg_stop"],       l["avg_stop"],      "pts"),
            ("Avg EP score",    w["avg_ep"],         l["avg_ep"],        ""),
            ("Avg HB rate",     w["avg_hb_rate"],    l["avg_hb_rate"],   ""),
            ("Avg ETS max",     w["avg_ets_max"],    l["avg_ets_max"],   ""),
            ("Avg delta_persist", w["avg_delta"],    l["avg_delta"],     ""),
        ]
        for name, wval, lval, unit in rows:
            try:
                delta = round(float(wval) - float(lval), 2)
                sign  = "+" if delta >= 0 else ""
                print(f"  {name:<28}  {str(wval):>12}  {str(lval):>12}  "
                      f"{sign}{delta:>9.2f}")
            except Exception:
                print(f"  {name:<28}  {str(wval):>12}  {str(lval):>12}")
        print()
        print(f"  Regimen GANADORAS: {sorted(w['regime_dist'].items(), key=lambda x:-x[1])}")
        print(f"  Regimen PERDEDORAS: {sorted(l['regime_dist'].items(), key=lambda x:-x[1])}")
        print(f"  Setup GANADORAS:  {sorted(w['setup_dist'].items(), key=lambda x:-x[1])}")
        print(f"  Setup PERDEDORAS: {sorted(l['setup_dist'].items(), key=lambda x:-x[1])}")
        print(f"  Dir GANADORAS:    {w['dir_dist']}")
        print(f"  Dir PERDEDORAS:   {l['dir_dist']}")

    # 5. Ganadores vs perdedores (trade level)
    print()
    print("  5. GANADORES vs PERDEDORES — nivel trade")
    _sep()
    ws = p5.get("wins", {})
    ls = p5.get("losses", {})
    if ws and ls:
        print(f"  {'Metrica':<28}  {'WINNERS ({})'.format(ws['n']):>14}  "
              f"{'LOSERS ({})'.format(ls['n']):>14}")
        _sep(60)
        for name, wk, lk in [
            ("Avg sconf",       "avg_sconf",  "avg_sconf"),
            ("Median sconf",    "med_sconf",  "med_sconf"),
            ("Min sconf",       "min_sconf",  "min_sconf"),
            ("Avg R:R",         "avg_rr",     "avg_rr"),
            ("Median R:R",      "med_rr",     "med_rr"),
            ("Avg stop dist",   "avg_stop",   "avg_stop"),
            ("Median stop dist","med_stop",   "med_stop"),
            ("LONG %",          "long_pct",   "long_pct"),
            ("Avg EP score",    "avg_ep",     "avg_ep"),
        ]:
            print(f"  {name:<28}  {str(ws.get(wk,'-')):>14}  {str(ls.get(lk,'-')):>14}")
        print()
        print("  sconf distribution (WINNERS):")
        for b, c in sorted(p5["win_sconf_dist"].items()):
            pct = round(100*c/max(ws["n"],1), 1)
            print(f"    {b:<10}: {c:>4} ({pct:.1f}%)")
        print("  sconf distribution (LOSERS):")
        for b, c in sorted(p5["los_sconf_dist"].items()):
            pct = round(100*c/max(ls["n"],1), 1)
            print(f"    {b:<10}: {c:>4} ({pct:.1f}%)")
        print("  R:R distribution (WINNERS):")
        for b, c in sorted(p5["win_rr_dist"].items()):
            pct = round(100*c/max(ws["n"],1), 1)
            print(f"    {b:<10}: {c:>4} ({pct:.1f}%)")
        print("  R:R distribution (LOSERS):")
        for b, c in sorted(p5["los_rr_dist"].items()):
            pct = round(100*c/max(ls["n"],1), 1)
            print(f"    {b:<10}: {c:>4} ({pct:.1f}%)")

    # 6. Autopsia marzo 2026
    print()
    print("  6. AUTOPSIA MARZO 2026")
    _sep()
    for d in sorted(p3["breakdown"].keys()):
        bd   = p3["breakdown"][d]
        m    = bd["metrics"]
        meta = bd["meta"]
        print(f"  {d}  n={m['n']:>3}  WR={m['wr']:>5.1f}%  "
              f"Exp={m['expectancy']:>+6.2f}  PnL={m['total_pnl']:>+8.2f}  "
              f"tipo={meta.get('session_type','?')}  "
              f"ep={meta.get('ep_score',0)}  "
              f"ets={meta.get('ets_max',0)}")
        for td in bd["trades"]:
            tag = "[W]" if td["result"] == "WIN" else "[L]"
            print(f"    bar={td['bar']:>4}  {td['stype']:<22}  {td['dir']:<6}  "
                  f"pnl={td['pnl']:>+7.2f}  stop={td['stop_dist']:.2f}  "
                  f"R:R={td['rratio']:.2f}  {tag}")
    print()

    # Context comparison: march vs best month
    best_m, best_data = p1["best_month"]
    worst_m, worst_data = p1["worst_month"]
    print(f"  Peor mes:  {worst_m}  WR={worst_data['wr']:.1f}%  "
          f"PF={_pf_str(worst_data['profit_factor'])}  "
          f"Exp={worst_data['expectancy']:+.2f}  n={worst_data['n']}")
    print(f"  Mejor mes: {best_m}  WR={best_data['wr']:.1f}%  "
          f"PF={_pf_str(best_data['profit_factor'])}  "
          f"Exp={best_data['expectancy']:+.2f}  n={best_data['n']}")

    # 7. Edge Decay Ranking
    print()
    print("  7. EDGE DECAY RANKING (peor -> mejor por PF)")
    _sep()
    print(f"  {'#':>2}  {'Factor':<38}  {'N':>3}  {'WR':>6}  {'PF':>6}  {'Exp':>7}  {'PnL':>9}")
    _sep()
    for i, f in enumerate(p7[:20], 1):
        pf_s = _pf_str(f["pf"])
        print(f"  {i:>2}. {f['name']:<38}  {f['n']:>3}  {f['wr']:>5.1f}%  "
              f"{pf_s:>6}  {f['exp']:>+7.2f}  {f['pnl']:>+9.2f}")

    # 8. Failure Signatures
    print()
    print("  8. FAILURE SIGNATURES")
    _sep()
    mom   = p8["momentum"]
    scomp = p8["stop_comparison"]
    rcomp = p8["rr_comparison"]
    ccomp = p8["conf_comparison"]
    smom  = p8["session_momentum"]

    print("  [A] Momentum intra-sesion (resultado previo -> WR siguiente):")
    print(f"      Despues de WIN:   WR={mom['wr_after_win']:.1f}%  "
          f"(n={mom['n_after_win']},  {mom['after_win_wins']}W/{mom['after_win_losses']}L)")
    print(f"      Despues de LOSS:  WR={mom['wr_after_loss']:.1f}%  "
          f"(n={mom['n_after_loss']}, {mom['after_loss_wins']}W/{mom['after_loss_losses']}L)")
    print()
    print("  [B] Comparacion stop distance:")
    print(f"      Winners: avg stop = {scomp['avg_stop_win']:.2f} pts")
    print(f"      Losers:  avg stop = {scomp['avg_stop_loss']:.2f} pts")
    delta_stop = round(scomp['avg_stop_win'] - scomp['avg_stop_loss'], 2)
    print(f"      Delta: {delta_stop:+.2f} pts  "
          f"({'Winners tienen stops mas amplios' if delta_stop > 0 else 'Losers tienen stops mas amplios'})")
    print()
    print("  [C] Comparacion R:R:")
    print(f"      Winners: avg R:R = {rcomp['avg_rr_win']:.2f}")
    print(f"      Losers:  avg R:R = {rcomp['avg_rr_loss']:.2f}")
    print()
    print("  [D] Comparacion sconf (setup confidence):")
    print(f"      Winners: avg sconf = {ccomp['avg_conf_win']:.1f}")
    print(f"      Losers:  avg sconf = {ccomp['avg_conf_loss']:.1f}")
    print()
    print("  [E] Momentum entre sesiones:")
    print(f"      Sesiones positivas DESPUES de sesion negativa:  "
          f"{smom['pct_positive_after_bad']:.1f}%  (n={smom['n_after_bad']})")
    print(f"      Sesiones positivas DESPUES de sesion positiva:  "
          f"{smom['pct_positive_after_good']:.1f}%  (n={smom['n_after_good']})")
    print()
    print("  [F] Sesiones con sobreoperacion (n>=7, WR<35%):")
    if p8["overtraded_sessions"]:
        for s in p8["overtraded_sessions"]:
            print(f"      {s['date']}  n={s['n']:>3}  WR={s['wr']:>5.1f}%  "
                  f"PnL={s['total_pnl']:>+8.2f}")
    else:
        print("      Ninguna.")
    print()
    print("  [G] Distribucion de direccion en sesiones perdedoras (Top 10):")
    for d, c in sorted(p8["dir_in_losing_sessions"].items()):
        total = sum(p8["dir_in_losing_sessions"].values())
        print(f"      {d}: {c} trades ({100*c/max(total,1):.1f}%)")

    # Final answer
    print()
    print("=" * 72)
    print("  DONDE DESAPARECE EL EDGE")
    print("=" * 72)
    print()
    print("  El edge desaparece bajo las siguientes condiciones observadas:")
    print()

    # Worst 3 factors from p7
    for i, f in enumerate(p7[:3], 1):
        print(f"  {i}. {f['name']}")
        print(f"     PF={_pf_str(f['pf'])}  WR={f['wr']:.1f}%  "
              f"Exp={f['exp']:+.2f}  n={f['n']}")
        print()

    print("  Patrones adicionales identificados:")
    print()

    # Overtrading
    if p8["overtraded_sessions"]:
        ot = p8["overtraded_sessions"]
        total_ot_pnl = round(sum(s["total_pnl"] for s in ot), 2)
        print(f"  * Sobreoperacion: {len(ot)} sesiones con n>=7 y WR<35%  "
              f"(PnL total: {total_ot_pnl:+.2f} pts)")

    # Momentum effect
    if abs(mom["wr_after_win"] - mom["wr_after_loss"]) > 5:
        print(f"  * Efecto momentum: WR despues de WIN={mom['wr_after_win']:.1f}% "
              f"vs LOSS={mom['wr_after_loss']:.1f}%")
        print(f"    (diferencia de {abs(mom['wr_after_win']-mom['wr_after_loss']):.1f} pp)")

    # Stop comparison
    if abs(delta_stop) > 1:
        print(f"  * Stops en losers son {'mas grandes' if delta_stop < 0 else 'mas chicos'} "
              f"que en winners ({abs(delta_stop):.2f} pts diferencia)")

    # sconf comparison
    conf_delta = ccomp["avg_conf_win"] - ccomp["avg_conf_loss"]
    if abs(conf_delta) > 2:
        print(f"  * sconf de perdedores es {abs(conf_delta):.1f} puntos "
              f"{'menor' if conf_delta > 0 else 'mayor'} que ganadores")

    # March autopsy summary
    print(f"  * Marzo 2026: {worst_data['n']} trades, WR={worst_data['wr']:.1f}%, "
          f"Exp={worst_data['expectancy']:+.2f} — contextos con HB_RATE elevado")

    print()
    print("  CONDICIONES EN QUE EL SISTEMA NO TIENE VENTAJA (evidencia):")
    for f in p7:
        if f["pf"] < 1.0 and f["n"] >= 5:
            print(f"  * {f['name']}  PF={_pf_str(f['pf'])}  "
                  f"Exp={f['exp']:+.2f}  n={f['n']}")
    print()
    print("=" * 72)


# ── Main ────────────────────────────────────────────────────────────────────────

def main() -> None:
    print()
    print("=" * 72)
    print("  GIBBZ EDGE FAILURE INVESTIGATION -- Modo Cientifico")
    print("  Sin modificar codigo. Sin ajustar parametros. Solo analisis.")
    print("=" * 72)
    print()

    sessions    = load_sessions()
    session_meta = {s["date"]: s for s in sessions}
    print(f"  Sesiones con grabacion valida: {len(sessions)}")
    print(f"  Ejecutando backtest completo...")
    print()

    all_trades, bars_by_session, sessions_run = run_full_backtest(sessions)

    if not all_trades:
        print("  ERROR: 0 trades generados.")
        return

    gm = compute_metrics(all_trades)
    print()
    print(f"  Backtest: {sessions_run} sesiones | {gm['n']} trades | "
          f"WR={gm['wr']:.1f}% | PF={gm['profit_factor']:.2f} | "
          f"Exp={gm['expectancy']:+.2f} | PnL={gm['total_pnl']:+.2f}")
    print()

    # Build bar timestamps for sessions with trades
    sessions_with_trades = set(t.session for t in all_trades)
    print(f"  Leyendo timestamps de {len(sessions_with_trades)} sesiones con trades...")
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
        print(f"  {s['date']}: leyendo hasta bar {max_bar}...", end=" ", flush=True)
        ts_map = get_bar_timestamps(s["rec_path"], max_bar)
        bar_timestamps[s["date"]] = ts_map
        print(f"{len(ts_map)} timestamps OK")
    print()

    # Enrich
    enriched = enrich_trades(all_trades, bars_by_session, session_meta, bar_timestamps)

    # Phases
    print("FASE 1 -- Periodos negativos")
    _sep()
    p1 = phase1_worst_periods(all_trades, session_meta)
    print(f"  {'#':>2}  {'Sesion':<12}  {'PnL':>9}  WR     PF     Exp      N  Tipo")
    _sep()
    for i, s in enumerate(p1["sessions_worst10"], 1):
        print(f"  {i:>2}. {s['date']:<12}  {s['total_pnl']:>+9.2f}  "
              f"{s['wr']:>5.1f}%  {_pf_str(s['profit_factor']):>5}  "
              f"{s['expectancy']:>+7.2f}  {s['n']:>3}  {s['session_type']}")
    print()
    print("  Peores rachas consecutivas de sesiones negativas:")
    for i, st in enumerate(p1["streaks"], 1):
        print(f"  {i}. {st['start']} -> {st['end']}  "
              f"({st['sessions']} sesiones)  PnL={st['pnl']:+.2f}  "
              f"({', '.join(f'{d}:{v:+.1f}' for d,v in zip(st['dates'], st['pnls']))})")
    print()
    print("  Meses — peor a mejor:")
    for month, m in sorted(p1["month_metrics"].items(), key=lambda x: x[1]["total_pnl"]):
        if m["n"] == 0: continue
        print(f"  {month}  n={m['n']:>3}  WR={m['wr']:>5.1f}%  "
              f"PF={_pf_str(m['profit_factor']):>5}  "
              f"Exp={m['expectancy']:>+6.2f}  PnL={m['total_pnl']:>+8.2f}")
    print()

    print("FASE 2 -- Ganadoras vs Perdedoras (nivel sesion)")
    _sep()
    p2 = phase2_winners_vs_losers(enriched, p1)
    w, l = p2.get("winners", {}), p2.get("losers", {})
    if w and l:
        print(f"  {'Metrica':<28}  {'GANADORAS':>14}  {'PERDEDORAS':>14}")
        _sep(60)
        for name, wk, lk in [
            ("Trades / sesion",   "avg_trades",  "avg_trades"),
            ("Avg sconf",         "avg_sconf",   "avg_sconf"),
            ("Avg R:R",           "avg_rr",      "avg_rr"),
            ("Avg stop dist",     "avg_stop",    "avg_stop"),
            ("Avg EP score",      "avg_ep",      "avg_ep"),
            ("Avg HB rate",       "avg_hb_rate", "avg_hb_rate"),
            ("Avg ETS max",       "avg_ets_max", "avg_ets_max"),
            ("Avg delta_persist", "avg_delta",   "avg_delta"),
        ]:
            print(f"  {name:<28}  {str(w.get(wk,'-')):>14}  {str(l.get(lk,'-')):>14}")
        print()
        print(f"  Regimen GANADORAS:  {sorted(w['regime_dist'].items(), key=lambda x:-x[1])}")
        print(f"  Regimen PERDEDORAS: {sorted(l['regime_dist'].items(), key=lambda x:-x[1])}")
        print(f"  Setup GANADORAS:    {sorted(w['setup_dist'].items(), key=lambda x:-x[1])}")
        print(f"  Setup PERDEDORAS:   {sorted(l['setup_dist'].items(), key=lambda x:-x[1])}")
        print(f"  Dir GANADORAS:      {w['dir_dist']}")
        print(f"  Dir PERDEDORAS:     {l['dir_dist']}")
    print()

    print("FASE 3 -- Autopsia Marzo 2026")
    _sep()
    p3 = phase3_march_autopsy(enriched, session_meta, all_trades)
    for d in sorted(p3["breakdown"].keys()):
        bd   = p3["breakdown"][d]
        m    = bd["metrics"]
        meta = bd["meta"]
        print(f"  {d}  n={m['n']:>3}  WR={m['wr']:>5.1f}%  "
              f"Exp={m['expectancy']:>+6.2f}  PnL={m['total_pnl']:>+8.2f}  "
              f"tipo={meta.get('session_type','?')}  "
              f"ep={meta.get('ep_score',0)}  "
              f"od={meta.get('od_score',0)}  "
              f"ets={meta.get('ets_max',0)}  "
              f"hb={meta.get('hb_rate',0):.3f}")
        for td in bd["trades"]:
            tag = "[W]" if td["result"] == "WIN" else "[L]"
            print(f"    bar={td['bar']:>4}  {td['stype']:<22}  {td['dir']:<6}  "
                  f"pnl={td['pnl']:>+7.2f}  stop={td['stop_dist']:.2f}  "
                  f"R:R={td['rratio']:.2f}  {tag}")
    print()

    print("FASE 4 -- Regimenes (mejor a peor)")
    _sep()
    p4 = phase4_regime(all_trades, session_meta)
    print(f"  {'REGIMEN':<22}  {'N':>3}  {'WR':>6}  {'PF':>6}  "
          f"{'Exp':>7}  {'PnL':>9}  {'MaxDD':>8}")
    _sep()
    for regime, m in sorted(p4.items(), key=lambda x: x[1]["expectancy"], reverse=True):
        print(f"  {regime:<22}  {m['n']:>3}  {m['wr']:>5.1f}%  "
              f"{_pf_str(m['profit_factor']):>6}  {m['expectancy']:>+7.2f}  "
              f"{m['total_pnl']:>+9.2f}  {m['max_dd']:>8.2f}")
    print()

    print("FASE 5 -- Distribucion de senales (W vs L)")
    _sep()
    p5 = phase5_signals(enriched)
    ws = p5.get("wins", {})
    ls = p5.get("losses", {})
    if ws and ls:
        print(f"  {'Metrica':<28}  {'WINNERS ({})'.format(ws.get('n',0)):>16}  "
              f"{'LOSERS ({})'.format(ls.get('n',0)):>16}")
        _sep(65)
        for name, wk, lk in [
            ("Avg sconf",       "avg_sconf",  "avg_sconf"),
            ("Median sconf",    "med_sconf",  "med_sconf"),
            ("Min sconf",       "min_sconf",  "min_sconf"),
            ("Avg R:R",         "avg_rr",     "avg_rr"),
            ("Median R:R",      "med_rr",     "med_rr"),
            ("Avg stop dist",   "avg_stop",   "avg_stop"),
            ("LONG %",          "long_pct",   "long_pct"),
        ]:
            print(f"  {name:<28}  {str(ws.get(wk,'-')):>16}  "
                  f"{str(ls.get(lk,'-')):>16}")
        print()
        print(f"  sconf dist WINNERS: {dict(sorted(p5['win_sconf_dist'].items()))}")
        print(f"  sconf dist LOSERS:  {dict(sorted(p5['los_sconf_dist'].items()))}")
        print(f"  R:R dist WINNERS:   {dict(sorted(p5['win_rr_dist'].items()))}")
        print(f"  R:R dist LOSERS:    {dict(sorted(p5['los_rr_dist'].items()))}")
    print()

    print("FASE 6 -- Horario (ET)")
    _sep()
    p6 = phase6_time(enriched)
    bp = p6.get("by_period", {})
    print(f"  {'PERIODO':<30}  {'N':>3}  {'WR':>6}  {'PF':>6}  {'Exp':>7}  {'PnL':>9}")
    _sep()
    period_order = ["APERTURA (9-11 ET)", "MANANA (11-13 ET)",
                    "MEDIODIA (13-15 ET)", "CIERRE (15-17 ET)",
                    "OVERNIGHT (<9 / >17 ET)"]
    for period in period_order:
        m = bp.get(period)
        if not m: continue
        print(f"  {period:<30}  {m['n']:>3}  {m['wr']:>5.1f}%  "
              f"{_pf_str(m['pf']):>6}  {m['exp']:>+7.2f}  {m['pnl']:>+9.2f}")
    print()
    bh = p6.get("by_hour", {})
    if bh:
        print("  Por hora ET:")
        print(f"  {'Hora ET':<10}  {'N':>3}  {'WR':>6}  {'PF':>6}  {'Exp':>7}  {'PnL':>9}")
        _sep(55)
        for h in sorted(bh.keys()):
            m = bh[h]
            print(f"  {h:02d}:00 ET    {m['n']:>3}  {m['wr']:>5.1f}%  "
                  f"{_pf_str(m['pf']):>6}  {m['exp']:>+7.2f}  {m['pnl']:>+9.2f}")
    print()

    print("FASE 7 -- Edge Decay Ranking (peor -> mejor por PF)")
    _sep()
    p7 = phase7_decay_ranking(enriched, session_meta)
    print(f"  {'#':>2}  {'Factor':<38}  {'N':>3}  {'WR':>6}  {'PF':>6}  {'Exp':>7}  {'PnL':>9}")
    _sep()
    for i, f in enumerate(p7, 1):
        pf_s = _pf_str(f["pf"])
        print(f"  {i:>2}. {f['name']:<38}  {f['n']:>3}  "
              f"{f['wr']:>5.1f}%  {pf_s:>6}  {f['exp']:>+7.2f}  {f['pnl']:>+9.2f}")
    print()

    print("FASE 8 -- Failure Signatures")
    _sep()
    p8 = phase8_failure_signatures(enriched, all_trades)
    mom   = p8["momentum"]
    scomp = p8["stop_comparison"]
    rcomp = p8["rr_comparison"]
    ccomp = p8["conf_comparison"]
    smom  = p8["session_momentum"]
    delta_stop = round(scomp["avg_stop_win"] - scomp["avg_stop_loss"], 2)

    print("  [A] Momentum intra-sesion:")
    print(f"      Despues de WIN:   WR={mom['wr_after_win']:.1f}%  "
          f"n={mom['n_after_win']}  ({mom['after_win_wins']}W / {mom['after_win_losses']}L)")
    print(f"      Despues de LOSS:  WR={mom['wr_after_loss']:.1f}%  "
          f"n={mom['n_after_loss']}  ({mom['after_loss_wins']}W / {mom['after_loss_losses']}L)")
    print()
    print("  [B] Stop distance (W vs L):")
    print(f"      Winners: {scomp['avg_stop_win']:.2f} pts  |  "
          f"Losers: {scomp['avg_stop_loss']:.2f} pts  |  Delta: {delta_stop:+.2f}")
    print("  [C] R:R (W vs L):")
    print(f"      Winners: {rcomp['avg_rr_win']:.2f}  |  "
          f"Losers: {rcomp['avg_rr_loss']:.2f}")
    print("  [D] sconf (W vs L):")
    print(f"      Winners: {ccomp['avg_conf_win']:.1f}  |  "
          f"Losers: {ccomp['avg_conf_loss']:.1f}")
    print()
    print("  [E] Momentum entre sesiones:")
    print(f"      Positivas despues de sesion negativa:  "
          f"{smom['pct_positive_after_bad']:.1f}%  (n={smom['n_after_bad']})")
    print(f"      Positivas despues de sesion positiva:  "
          f"{smom['pct_positive_after_good']:.1f}%  (n={smom['n_after_good']})")
    print()
    print("  [F] Sesiones con sobreoperacion (n>=7, WR<35%):")
    if p8["overtraded_sessions"]:
        for s in p8["overtraded_sessions"]:
            print(f"      {s['date']}  n={s['n']:>3}  WR={s['wr']:>5.1f}%  "
                  f"PnL={s['total_pnl']:>+8.2f}")
    else:
        print("      Ninguna.")
    print()
    print("  [G] Direccion en sesiones perdedoras:")
    dir_total = sum(p8["dir_in_losing_sessions"].values())
    for d, c in sorted(p8["dir_in_losing_sessions"].items()):
        print(f"      {d}: {c} ({100*c/max(dir_total,1):.1f}%)")
    print()

    print("FASE 9 -- Reporte final")
    _sep()
    phase9_report(p1, p2, p3, p4, p5, p6, p7, p8)


if __name__ == "__main__":
    main()
