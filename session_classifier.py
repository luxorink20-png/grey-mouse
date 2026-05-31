"""
GIBBZ V3 — session_classifier.py
Session Type Classifier + Edge Potential Scorer

Analiza todos los outcomes/*.json y produce:
- SESSION TYPE (ROTATIONAL / EXPANSION / OPENING_DRIVE / etc.)
- EDGE POTENTIAL SCORE (0-100)
- RANKING institucional
- Detección de timing: cuándo aparece ETIL vs market_env
- Análisis de HB contamination
- Top 10 sesiones para Outcome Engine preparation

USO:
  python session_classifier.py
  python session_classifier.py --detail 2026-03-11
"""

import json
import os
import glob
import argparse
from dataclasses import dataclass, field
from typing import List, Dict, Optional


# ── SESSION TYPES ─────────────────────────────────────────────────
SESSION_TYPES = {
    "EXPANSION":             "ETS>=65 sostenido + EFFICIENT_TREND temprano",
    "OPENING_DRIVE":         "ETS>=65 en bars 1-20 + trend temprano",
    "OVERNIGHT_CONTINUATION":"EFFICIENT_TREND en bars 1-10",
    "TREND_DAY":             "EFFICIENT_TREND >= 5% de la sesión",
    "FAILED_TREND":          "ETS alto pero HB domina rechazos",
    "VOL_RELEASE":           "ETS cluster >= 3 barras consecutivas",
    "BALANCED":              "conf alta pero sin ETS ni trend",
    "ROTATIONAL":            "85%+ barras ROTATIONAL, ETS bajo",
    "DEAD_SESSION":          "ETS=0 toda la sesión, sin señales",
}


@dataclass
class SessionClassification:
    date:               str   = ""
    session_type:       str   = "ROTATIONAL"
    session_subtype:    str   = ""
    edge_potential:     int   = 0      # 0-100
    edge_potential_lbl: str   = "LOW"  # LOW / MED / HIGH / ELITE

    # Timing metrics
    first_et_bar:       int   = 0     # primera barra EFFICIENT_TREND
    first_ets65_bar:    int   = 0     # primera barra ETS >= 65
    etil_env_lag:       int   = 0     # cuántas barras entre ETS65 y ET

    # Quality metrics
    et_pct:             float = 0.0
    ets65_count:        int   = 0
    ets_max:            int   = 0
    conf_max:           int   = 0
    rt_max:             int   = 0
    hb_rate:            float = 0.0
    gtal_valid_count:   int   = 0
    over_rejection_pct: float = 0.0
    micro_or_count:     int   = 0
    acg_count:          int   = 0

    # Starvation
    starvation_score:   float = 0.0
    main_blocker:       str   = ""

    # Notable
    best_bar:           int   = 0
    best_bar_ets:       int   = 0
    best_bar_conf:      int   = 0

    def label(self) -> str:
        stars = {
            "ELITE": "★★★★",
            "HIGH":  "★★★☆",
            "MED":   "★★☆☆",
            "LOW":   "★☆☆☆",
        }.get(self.edge_potential_lbl, "☆☆☆☆")
        return (f"{self.date:<12} {self.session_type:<24} "
                f"EP={self.edge_potential:3d}/100 {stars}  "
                f"ET={self.et_pct:.1f}%  ETS_max={self.ets_max:3d}  "
                f"conf_max={self.conf_max:3d}")


def classify_session(obs: dict) -> SessionClassification:
    """Clasifica una sesión basado en su observación."""
    date   = obs.get("session_date", "")
    bars   = max(obs.get("total_bars", 1), 1)
    et     = obs.get("efficient_trend_bars", 0)
    ets65  = obs.get("ets_cluster_bars", 0)  # NOTE: stored as cluster (>=50)
    ets_active = sum(1 for nb in obs.get("notable_bars", []) if nb.get("ets", 0) >= 65)
    gv     = obs.get("gtal_valid_bars", 0)
    a_s    = obs.get("a_setup_bars", 0)
    micro  = obs.get("micro_or_bars", 0)
    acg    = obs.get("acg_activation_bars", 0)
    starv  = obs.get("signal_starvation_score", 100)
    hb_blk = obs.get("hb_block_count", 0)
    cont_b = obs.get("cont_block_count", 0)
    conf_b = obs.get("conf_block_count", 0)
    gtal_r = obs.get("gtal_rejection_count", 0)
    over_r = obs.get("over_rejection_bars", 0)
    max_et = obs.get("max_ets", 0)
    max_cf = obs.get("max_conf", 0)
    max_rt = obs.get("max_rt", 0)

    et_pct  = et   / bars * 100
    ets_pct = ets65 / bars * 100

    # ── TIMING ANALYSIS ───────────────────────────────────────────
    notable = obs.get("notable_bars", [])

    first_et_bar  = 0
    first_ets_bar = 0
    best_bar = 0
    best_ets = 0
    best_cf  = 0

    for nb in notable:
        b   = nb.get("bar", 9999)
        ets = nb.get("ets", 0)
        cf  = nb.get("conf", 0)
        env = nb.get("env", "")

        if env == "EFFICIENT_TREND" and first_et_bar == 0:
            first_et_bar = b
        if ets >= 65 and first_ets_bar == 0:
            first_ets_bar = b
        if ets > best_ets or (ets == best_ets and cf > best_cf):
            best_ets = ets
            best_cf  = cf
            best_bar = b

    etil_env_lag = 0
    if first_ets_bar > 0 and first_et_bar > 0:
        etil_env_lag = abs(first_et_bar - first_ets_bar)
    elif first_ets_bar > 0 and first_et_bar == 0:
        etil_env_lag = 999  # ETIL detectó pero env nunca cambió

    # ── HB RATE ───────────────────────────────────────────────────
    total_notable = len(notable)
    hb_rate = hb_blk / max(total_notable, 1)

    # ── SESSION TYPE ──────────────────────────────────────────────
    session_type    = "ROTATIONAL"
    session_subtype = ""

    if max_et == 0 and et == 0:
        session_type = "DEAD_SESSION"

    elif et > 0 and first_et_bar > 0 and first_et_bar <= 20:
        session_type = "OPENING_DRIVE" if first_ets_bar <= 10 else "OVERNIGHT_CONTINUATION"

    elif et_pct >= 5.0:
        session_type = "TREND_DAY"

    elif ets_active >= 3 and max_et >= 65:
        if hb_blk >= ets_active * 0.5:
            session_type = "FAILED_TREND"
        else:
            session_type = "EXPANSION"

    elif ets_active >= 2 and et == 0:
        session_type = "VOL_RELEASE"

    elif max_cf >= 65 and max_et < 65:
        session_type = "BALANCED"

    elif et_pct < 1.0 and max_et < 65:
        session_type = "ROTATIONAL"

    # Subtype
    if hb_blk >= 3:
        session_subtype = "HB_DOMINATED"
    elif over_r >= 50:
        session_subtype = "OVER_FILTERED"
    elif gv > 0:
        session_subtype = "HAD_VALID_SIGNAL"

    # ── EDGE POTENTIAL SCORE (0-100) ──────────────────────────────
    ep = 0

    # ET presence (max 25)
    if et_pct >= 5.0:      ep += 25
    elif et_pct >= 2.0:    ep += 18
    elif et_pct >= 0.5:    ep += 10
    elif et > 0:           ep += 5

    # Early ET (max 15) — ET en primeras 20 barras
    if first_et_bar > 0 and first_et_bar <= 10:  ep += 15
    elif first_et_bar > 0 and first_et_bar <= 20: ep += 10
    elif first_et_bar > 0 and first_et_bar <= 50: ep += 5

    # ETS strength (max 20)
    if max_et >= 80:       ep += 20
    elif max_et >= 65:     ep += 15
    elif max_et >= 50:     ep += 8
    elif max_et >= 35:     ep += 3

    # ETS active count (max 10)
    ep += min(ets_active * 2, 10)

    # GTAL_VALID (max 15)
    ep += min(gv * 15, 15)

    # Low HB contamination (max 10)
    if hb_rate < 0.1:      ep += 10
    elif hb_rate < 0.3:    ep += 5
    elif hb_rate < 0.5:    ep += 2

    # conf quality (max 10)
    if max_cf >= 65:       ep += 10
    elif max_cf >= 55:     ep += 6
    elif max_cf >= 45:     ep += 2

    # ACG / MICRO_OR signals (max 5)
    ep += min(micro * 2 + acg * 3, 5)

    ep = max(0, min(ep, 100))

    # Label
    if ep >= 70:   lbl = "ELITE"
    elif ep >= 45: lbl = "HIGH"
    elif ep >= 20: lbl = "MED"
    else:          lbl = "LOW"

    # Main blocker
    if hb_blk >= cont_b and hb_blk >= conf_b:
        main_blocker = f"HB={hb_blk}"
    elif cont_b >= conf_b:
        main_blocker = f"cont_block={cont_b}"
    elif conf_b > 0:
        main_blocker = f"conf_block={conf_b}"
    elif gtal_r > 0:
        main_blocker = f"GTAL_rejection={gtal_r}"
    else:
        main_blocker = "none"

    return SessionClassification(
        date               = date,
        session_type       = session_type,
        session_subtype    = session_subtype,
        edge_potential     = ep,
        edge_potential_lbl = lbl,
        first_et_bar       = first_et_bar,
        first_ets65_bar    = first_ets_bar,
        etil_env_lag       = etil_env_lag,
        et_pct             = et_pct,
        ets65_count        = ets_active,
        ets_max            = max_et,
        conf_max           = max_cf,
        rt_max             = max_rt,
        hb_rate            = round(hb_rate, 2),
        gtal_valid_count   = gv,
        over_rejection_pct = round(over_r / bars * 100, 1),
        micro_or_count     = micro,
        acg_count          = acg,
        starvation_score   = starv,
        main_blocker       = main_blocker,
        best_bar           = best_bar,
        best_bar_ets       = best_ets,
        best_bar_conf      = best_cf,
    )


def print_full_report(sessions: List[SessionClassification]):
    """Reporte completo del dataset."""

    print("\n" + "="*110)
    print("  GIBBZ V3 — EXPANSION SESSION ACQUISITION REPORT")
    print("  Session Type Classifier + Edge Potential Scorer")
    print("="*110)

    # ── RANKING ───────────────────────────────────────────────────
    ranked = sorted(sessions, key=lambda s: -s.edge_potential)

    print(f"\n  {'RANKING INSTITUCIONAL — EDGE POTENTIAL':}")
    print(f"  {'─'*108}")
    print(f"  {'DATE':<12} {'SESSION TYPE':<24} {'EP':>7} {'STARS':<6} "
          f"{'ET%':>6} {'ETS_max':>8} {'conf_max':>9} {'HB_rate':>8} {'BLOCKER':<20}")
    print(f"  {'─'*108}")

    for s in ranked:
        stars = {"ELITE":"★★★★","HIGH":"★★★☆","MED":"★★☆☆","LOW":"★☆☆☆"}.get(s.edge_potential_lbl,"☆☆☆☆")
        sub = f" ({s.session_subtype})" if s.session_subtype else ""
        print(f"  {s.date:<12} {s.session_type+sub:<24} {s.edge_potential:>5}/100 {stars}  "
              f"{s.et_pct:>5.1f}%  {s.ets_max:>7}  {s.conf_max:>8}  "
              f"{s.hb_rate:>7.0%}  {s.main_blocker:<20}")

    # ── TOP 10 ────────────────────────────────────────────────────
    print(f"\n  {'TOP SESIONES PARA OUTCOME ENGINE PREPARATION':}")
    print(f"  {'─'*108}")
    top = [s for s in ranked if s.edge_potential >= 10][:10]
    if not top:
        print("  (ninguna sesión supera EP=10 — dataset sin expansion sessions)")
    else:
        for i, s in enumerate(top, 1):
            timing = ""
            if s.first_et_bar > 0:
                timing = f"| first_ET=bar{s.first_et_bar}"
            if s.first_ets65_bar > 0:
                timing += f" first_ETS65=bar{s.first_ets65_bar}"
            if s.etil_env_lag == 999:
                timing += " [ETIL sin ET confirmación]"
            elif s.etil_env_lag > 0:
                timing += f" lag={s.etil_env_lag}bars"
            print(f"  #{i:2d}  {s.date}  EP={s.edge_potential:3d}  "
                  f"{s.session_type:<22} {timing}")
            if s.best_bar > 0:
                print(f"       Best bar: {s.best_bar} | ETS={s.best_bar_ets} conf={s.best_bar_conf}")

    # ── TIMING ANALYSIS ───────────────────────────────────────────
    print(f"\n  {'TIMING ANALYSIS — ETIL vs MARKET_ENV LAG':}")
    print(f"  {'─'*108}")
    lag_sessions = [s for s in sessions if s.etil_env_lag > 0]
    if lag_sessions:
        for s in sorted(lag_sessions, key=lambda x: x.etil_env_lag):
            if s.etil_env_lag == 999:
                print(f"  {s.date}  ETIL detectó ETS>=65 pero market_env NUNCA llegó a EFFICIENT_TREND")
            else:
                print(f"  {s.date}  lag={s.etil_env_lag} barras entre ETS>=65 y EFFICIENT_TREND")
    else:
        print("  (sin datos de lag — ninguna sesión tuvo ETS>=65)")

    # ── SESSION TYPE DISTRIBUTION ─────────────────────────────────
    print(f"\n  {'SESSION TYPE DISTRIBUTION':}")
    print(f"  {'─'*108}")
    type_counts = {}
    for s in sessions:
        type_counts[s.session_type] = type_counts.get(s.session_type, 0) + 1
    for stype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
        pct = count / len(sessions) * 100
        bar_vis = "█" * int(pct / 5)
        desc = SESSION_TYPES.get(stype, "")
        print(f"  {stype:<24} {count:2d} sesiones  ({pct:4.0f}%)  {bar_vis}  {desc}")

    # ── MICRO_OR / ACG RELEVANCE ──────────────────────────────────
    print(f"\n  {'MICRO_OR / ACG RELEVANCE':}")
    print(f"  {'─'*108}")
    micro_sessions = [s for s in sessions if s.micro_or_count > 0]
    acg_sessions   = [s for s in sessions if s.acg_count > 0]
    print(f"  Sesiones con MICRO_OR activo: {len(micro_sessions)}")
    print(f"  Sesiones con ACG activado:    {len(acg_sessions)}")
    if micro_sessions:
        for s in micro_sessions:
            print(f"    {s.date}  micro_or={s.micro_or_count}  EP={s.edge_potential}")
    if not micro_sessions and not acg_sessions:
        print("  → Ninguna sesión del dataset activó MICRO_OR ni ACG.")
        print("  → Esperar EXPANSION SESSIONS donde ETS>=65 + GTAL=VALID coexistan.")

    # ── CONCLUSIÓN INSTITUCIONAL ──────────────────────────────────
    print(f"\n  {'CONCLUSION INSTITUCIONAL':}")
    print(f"  {'─'*108}")

    avg_ep   = sum(s.edge_potential for s in sessions) / len(sessions)
    elite    = [s for s in sessions if s.edge_potential_lbl == "ELITE"]
    high     = [s for s in sessions if s.edge_potential_lbl == "HIGH"]
    expansion= [s for s in sessions if s.session_type in
                ("EXPANSION","OPENING_DRIVE","TREND_DAY","OVERNIGHT_CONTINUATION")]
    et_total = sum(s.et_pct for s in sessions) / len(sessions)

    print(f"  Dataset: {len(sessions)} sesiones  avg_EP={avg_ep:.0f}/100")
    print(f"  ELITE sessions:     {len(elite)}")
    print(f"  HIGH sessions:      {len(high)}")
    print(f"  Expansion sessions: {len(expansion)}")
    print(f"  avg EFFICIENT_TREND: {et_total:.1f}% del tiempo")
    print()

    if len(expansion) == 0:
        print("  [CRITICAL] CERO expansion sessions en el dataset.")
        print("             El edge institucional NO puede validarse con este dataset.")
        print("             ACCION: Buscar/grabar sesiones CPI/FOMC/NFP/Gap days.")
    elif len(expansion) < 3:
        print(f"  [WARNING]  Solo {len(expansion)} expansion session(s).")
        print("             Insuficiente para validación estadística (mínimo 10).")
        print("             ACCION: Expandir dataset con días de alta volatilidad.")
    else:
        print(f"  [OK]       {len(expansion)} expansion sessions disponibles.")
        print("             Suficiente para análisis preliminar de timing y HB.")

    best = ranked[0]
    print()
    print(f"  MEJOR SESION DEL DATASET: {best.date}")
    print(f"    Type: {best.session_type}  EP={best.edge_potential}/100")
    print(f"    ETS_max={best.ets_max}  conf_max={best.conf_max}  RT_max={best.rt_max}")
    if best.best_bar > 0:
        print(f"    Bar más prometedora: bar {best.best_bar} "
              f"(ETS={best.best_bar_ets} conf={best.best_bar_conf})")
    print()
    print("  PRÓXIMOS PASOS:")
    print("  1. Grabar sesiones CPI/FOMC/NFP/Gap >10pts")
    print("  2. Correr --save-outcomes en cada sesión nueva")
    print("  3. Re-ejecutar session_classifier.py para actualizar ranking")
    print("  4. Cuando EP >= 70 aparezca: análisis profundo de timing")
    print()
    print("="*110 + "\n")


def print_session_detail(sessions: List[SessionClassification],
                         target_date: str):
    """Detalle de una sesión específica."""
    match = [s for s in sessions if s.date == target_date]
    if not match:
        print(f"Sesión {target_date} no encontrada en outcomes/")
        return
    s = match[0]
    print(f"\n{'='*70}")
    print(f"  DETALLE: {s.date}")
    print(f"{'─'*70}")
    print(f"  session_type:       {s.session_type}")
    print(f"  session_subtype:    {s.session_subtype or '(none)'}")
    print(f"  edge_potential:     {s.edge_potential}/100  [{s.edge_potential_lbl}]")
    print(f"  et_pct:             {s.et_pct:.1f}%")
    print(f"  first_et_bar:       {s.first_et_bar or '(nunca)'}")
    print(f"  first_ets65_bar:    {s.first_ets65_bar or '(nunca)'}")
    print(f"  etil_env_lag:       {s.etil_env_lag if s.etil_env_lag != 999 else 'ETIL sin confirmación ET'}")
    print(f"  ets_max:            {s.ets_max}")
    print(f"  conf_max:           {s.conf_max}")
    print(f"  rt_max:             {s.rt_max}")
    print(f"  hb_rate:            {s.hb_rate:.0%}")
    print(f"  gtal_valid_count:   {s.gtal_valid_count}")
    print(f"  micro_or_count:     {s.micro_or_count}")
    print(f"  acg_count:          {s.acg_count}")
    print(f"  starvation_score:   {s.starvation_score}/100")
    print(f"  over_rejection_pct: {s.over_rejection_pct}%")
    print(f"  main_blocker:       {s.main_blocker}")
    if s.best_bar > 0:
        print(f"  best_bar:           bar {s.best_bar} "
              f"(ETS={s.best_bar_ets} conf={s.best_bar_conf})")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--detail", type=str, default="",
                        help="Mostrar detalle de una sesión (YYYY-MM-DD)")
    args = parser.parse_args()

    files = sorted(glob.glob("outcomes/*_observation.json"))
    if not files:
        print("No hay archivos en outcomes/ — corre los replays con --save-outcomes primero.")
        exit()

    sessions = []
    for f in files:
        obs = json.load(open(f, encoding="utf-8"))
        sessions.append(classify_session(obs))

    if args.detail:
        print_session_detail(sessions, args.detail)
    else:
        print_full_report(sessions)