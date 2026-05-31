"""
GIBBZ V3 — training_camp_engine.py
Institutional Training Camp — Auditor Module v1.0

FUNCIÓN: auditor institucional 100% observacional.
NO ejecuta trades.
NO modifica nada del core.
NO cambia thresholds.
NO hace overfitting.

Lee todos los expansion_outcomes/ y outcomes/ JSONs
y genera el Training Camp Report estadístico completo.

USO:
  python training_camp_engine.py
  python training_camp_engine.py --verbose
  python training_camp_engine.py --session 2026-03-11
"""

import json
import os
import glob
import argparse
from dataclasses import dataclass, field
from collections import defaultdict
from typing import List, Dict, Optional


# ── FAILURE TYPES ─────────────────────────────────────────────────
FAILURE_TYPES = {
    "HB_SPIKE":        "Hindsight bias spike — precio sin contexto histórico",
    "CONT_WARMUP":     "Continuation engine sin warmup suficiente",
    "ETS_UNSUSTAINED": "ETS alto pero no sostenido (< 2 barras)",
    "ROTATIONAL_ENV":  "market_env permaneció ROTATIONAL sin confirmar ET",
    "LOW_CONF":        "conf < 65 cuando GTAL=VALID requería conf >= 65",
    "TRAP_DENSITY":    "trap_density alta bloqueó validación",
    "EDGE_EXPIRED":    "Edge expiró antes de alineación completa",
    "LATE_DETECTION":  "Recording comenzó tarde — pre-open insuficiente",
    "NO_SIGNAL":       "Sin señales en toda la sesión",
}


# ── SESSION PROFILE ───────────────────────────────────────────────

@dataclass
class SessionProfile:
    """Perfil estadístico de una sesión."""
    date:              str   = ""
    source:            str   = ""   # "expansion" o "observation"

    # Clasificación
    session_type:      str   = "ROTATIONAL"
    ep_score:          int   = 0
    ep_label:          str   = "LOW"

    # Edge quality
    ets_max:           int   = 0
    ets_active_count:  int   = 0
    ets_cluster_max:   int   = 0
    gtal_valid_count:  int   = 0
    hb_rate:           float = 0.0
    hb_count:          int   = 0
    a_setup_count:     int   = 0

    # Timing
    first_ets65_bar:   int   = 0
    first_et_bar:      int   = 0
    etil_env_lag:      int   = 999

    # Opening drive
    od_score:          int   = 0
    od_eff_peak:       int   = 0

    # Persistence
    ets_lifespan:      int   = 0    # max consecutive ETS>=65 bars
    et_lifespan:       int   = 0    # max consecutive ET bars
    edge_duration:     int   = 0    # bars where ETS>=65 AND eff>=50

    # Failures
    failure_map:       Dict  = field(default_factory=dict)
    warmup_loss:       bool  = False
    warmup_loss_bars:  int   = 0
    primary_failure:   str   = "NO_SIGNAL"

    # Starvation
    starvation_score:  float = 0.0
    hb_blocks:         int   = 0
    cont_blocks:       int   = 0
    conf_blocks:       int   = 0
    gtal_rejections:   int   = 0
    over_rejection:    int   = 0

    # Regime
    rotational_pct:    float = 0.0
    et_pct:            float = 0.0
    trappy_pct:        float = 0.0

    # Stability
    edge_stability_score: float = 0.0  # 0-100

    def ep_label_str(self) -> str:
        if self.ep_score >= 70: return "ELITE"
        if self.ep_score >= 50: return "HIGH"
        if self.ep_score >= 30: return "MED"
        return "LOW"


# ── DATA LOADER ───────────────────────────────────────────────────

def load_session_profiles() -> List[SessionProfile]:
    """Carga y fusiona datos de expansion_outcomes/ y outcomes/."""
    profiles: Dict[str, SessionProfile] = {}

    # 1. Cargar expansion outcomes (más completos)
    for f in sorted(glob.glob("expansion_outcomes/*_expansion.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
            date = d.get("session_date", "")
            if not date:
                continue
            sp = SessionProfile(date=date, source="expansion")
            sp.session_type      = d.get("session_type", "ROTATIONAL")
            sp.ep_score          = d.get("ep_score", 0)
            sp.ets_max           = d.get("ets_max", 0)
            sp.ets_active_count  = d.get("ets65_count", 0)
            sp.ets_cluster_max   = d.get("ets_cluster_max", 0)
            sp.gtal_valid_count  = d.get("gtal_valid_count", 0)
            sp.hb_rate           = d.get("hb_rate", 0.0)
            sp.hb_count          = d.get("hb_count", 0)
            sp.a_setup_count     = d.get("a_setup_count", 0)
            sp.first_ets65_bar   = d.get("first_ets65_bar", 0)
            sp.first_et_bar      = d.get("first_et_bar", 0)
            sp.etil_env_lag      = d.get("etil_to_env_delay", 999)
            sp.od_score          = d.get("opening_drive_score", 0)
            sp.od_eff_peak       = d.get("od_eff_peak", 0)
            sp.ets_lifespan      = d.get("ets_cluster_max", 0)
            sp.et_lifespan       = d.get("od_et_bars", 0)
            sp.edge_duration     = d.get("early_edge_duration", 0)
            sp.warmup_loss       = (d.get("etil_to_env_delay", 0) == 999 and
                                    d.get("ets65_count", 0) > 0)
            profiles[date] = sp
        except Exception as e:
            print(f"  [WARN] expansion load error {f}: {e}")

    # 2. Enriquecer con observation outcomes
    for f in sorted(glob.glob("outcomes/*_observation.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
            date = d.get("session_date", "")
            if not date:
                continue

            sp = profiles.get(date)
            if sp is None:
                sp = SessionProfile(date=date, source="observation")
                profiles[date] = sp

            # Starvation metrics
            sp.starvation_score  = d.get("signal_starvation_score", 100)
            sp.hb_blocks         = d.get("hb_block_count", 0)
            sp.cont_blocks       = d.get("cont_block_count", 0)
            sp.conf_blocks       = d.get("conf_block_count", 0)
            sp.gtal_rejections   = d.get("gtal_rejection_count", 0)
            sp.over_rejection    = d.get("over_rejection_bars", 0)

            # Regime distribution
            total = max(d.get("total_bars", 1), 1)
            reg   = d.get("regime_distribution", {})
            sp.rotational_pct = reg.get("ROTATIONAL", 0) / total
            sp.et_pct         = reg.get("EFFICIENT_TREND", 0) / total
            sp.trappy_pct     = reg.get("TRAPPY", 0) / total

            if sp.gtal_valid_count == 0 and sp.source == "observation":
                sp.ets_max         = max(sp.ets_max, d.get("max_ets", 0))
                sp.a_setup_count   = max(sp.a_setup_count,
                                         d.get("a_setup_bars", 0))
                sp.hb_count        = max(sp.hb_count, sp.hb_blocks)

        except Exception as e:
            print(f"  [WARN] observation load error {f}: {e}")

    # 3. Derivar métricas calculadas
    for sp in profiles.values():
        sp = _compute_failure_map(sp)
        sp = _compute_edge_stability(sp)

    return sorted(profiles.values(), key=lambda x: -x.ep_score)


def _compute_failure_map(sp: SessionProfile) -> SessionProfile:
    """Clasifica por qué el edge no ejecutó."""
    fm = {}

    if sp.hb_count > 0:
        fm["HB_SPIKE"] = sp.hb_count
    if sp.cont_blocks > 0:
        fm["CONT_WARMUP"] = sp.cont_blocks
    if sp.ets_max >= 65 and sp.ets_active_count <= 1:
        fm["ETS_UNSUSTAINED"] = 1
    if sp.et_pct < 0.01 and sp.ets_max >= 65:
        fm["ROTATIONAL_ENV"] = 1
    if sp.conf_blocks > 0:
        fm["LOW_CONF"] = sp.conf_blocks
    if sp.etil_env_lag == 999 and sp.ets_max >= 65:
        fm["LATE_DETECTION"] = 1
    if sp.ets_max == 0:
        fm["NO_SIGNAL"] = 1

    sp.failure_map = fm

    # Primary failure = el más frecuente
    if fm:
        sp.primary_failure = max(fm, key=lambda k: fm[k])
    else:
        sp.primary_failure = "NONE"

    # Warmup loss
    if "CONT_WARMUP" in fm or "LATE_DETECTION" in fm:
        sp.warmup_loss = True
        sp.warmup_loss_bars = sp.cont_blocks + (
            sp.first_ets65_bar if sp.first_ets65_bar > 0 else 0)

    return sp


def _compute_edge_stability(sp: SessionProfile) -> SessionProfile:
    """Score 0-100 de estabilidad del edge."""
    s = 0.0

    # HB baja → estabilidad alta
    hb_penalty = sp.hb_rate * 50
    s += max(0, 30 - hb_penalty)

    # ETS sostenido
    if sp.ets_active_count >= 3:    s += 20
    elif sp.ets_active_count >= 2:  s += 12
    elif sp.ets_active_count >= 1:  s += 6

    # Warmup intacto
    if not sp.warmup_loss:          s += 15

    # ET lag corto
    if sp.etil_env_lag == 0:        s += 15
    elif sp.etil_env_lag <= 50:     s += 8
    elif sp.etil_env_lag <= 200:    s += 3

    # Régimen favorable
    if sp.et_pct >= 0.02:           s += 10
    elif sp.et_pct > 0:             s += 5

    # Opening drive
    if sp.od_score >= 70:           s += 10
    elif sp.od_score >= 40:         s += 5

    sp.edge_stability_score = round(min(s, 100), 1)
    return sp


# ── TRAINING CAMP REPORT ─────────────────────────────────────────

def generate_report(profiles: List[SessionProfile], verbose: bool = False):
    """Genera el Training Camp Report completo."""

    n = len(profiles)
    if n == 0:
        print("No hay datos. Corre los replays con --save-outcomes primero.")
        return

    elite   = [p for p in profiles if p.ep_score >= 70]
    high    = [p for p in profiles if 50 <= p.ep_score < 70]
    med     = [p for p in profiles if 30 <= p.ep_score < 50]
    low     = [p for p in profiles if p.ep_score < 30]

    # Failure map global
    global_fm = defaultdict(int)
    for p in profiles:
        for k, v in p.failure_map.items():
            global_fm[k] += v

    # Warmup losses
    warmup_losses = [p for p in profiles if p.warmup_loss]

    # Avg metrics
    avg_ep      = sum(p.ep_score for p in profiles) / n
    avg_stab    = sum(p.edge_stability_score for p in profiles) / n
    avg_hb      = sum(p.hb_rate for p in profiles) / n
    avg_ets     = sum(p.ets_max for p in profiles) / n
    avg_et_pct  = sum(p.et_pct for p in profiles) / n * 100

    # Sessions with GTAL valid
    gtal_sessions = [p for p in profiles if p.gtal_valid_count > 0]

    # Shadow expectancy proxy
    # Sesiones con EP>=50, HB<20%, ets_active>=2, no warmup_loss
    shadow_candidates = [
        p for p in profiles
        if p.ep_score >= 50
        and p.hb_rate < 0.20
        and p.ets_active_count >= 2
        and not p.warmup_loss
    ]

    # Production readiness (0-100%)
    # Basado en: GTAL_VALID rate, edge stability, HB contamination
    gtal_rate   = len(gtal_sessions) / n
    stable_rate = len([p for p in profiles if p.edge_stability_score >= 50]) / n
    hb_ok_rate  = len([p for p in profiles if p.hb_rate < 0.15]) / n
    prod_ready  = round((gtal_rate * 40 + stable_rate * 35 + hb_ok_rate * 25), 1)

    # ── IMPRIMIR REPORTE ──────────────────────────────────────────
    print(f"\n{'='*80}")
    print(f"  GIBBZ V3 — TRAINING CAMP REPORT")
    print(f"  Institutional Auditor v1.0")
    print(f"{'='*80}")

    print(f"\n  ── SESSION INVENTORY ────────────────────────────────")
    print(f"  Sessions analyzed:       {n}")
    print(f"  ELITE  (EP>=70):         {len(elite)}")
    print(f"  HIGH   (EP>=50):         {len(high)}")
    print(f"  MED    (EP>=30):         {len(med)}")
    print(f"  LOW    (EP< 30):         {len(low)}")
    print(f"  Sessions with GTAL_VALID:{len(gtal_sessions)}")
    print(f"  Shadow candidates:       {len(shadow_candidates)}")

    print(f"\n  ── EDGE QUALITY METRICS ────────────────────────────")
    print(f"  avg_EP:                  {avg_ep:.1f}/100")
    print(f"  avg_edge_stability:      {avg_stab:.1f}/100")
    print(f"  avg_HB_rate:             {avg_hb:.1%}")
    print(f"  avg_ETS_max:             {avg_ets:.1f}")
    print(f"  avg_EFFICIENT_TREND_pct: {avg_et_pct:.2f}%")
    best_ets = max(p.ets_max for p in profiles)
    best_od  = max(p.od_score for p in profiles)
    print(f"  best_ETS_max:            {best_ets}")
    print(f"  best_OD_score:           {best_od}")

    print(f"\n  ── FAILURE MAP ─────────────────────────────────────")
    print(f"  (causas de invalidación por frecuencia)")
    for failure, count in sorted(global_fm.items(), key=lambda x: -x[1]):
        desc = FAILURE_TYPES.get(failure, "")
        bar  = "█" * min(count, 30)
        print(f"  {failure:<18} {count:>4}  {bar}  {desc[:40]}")

    print(f"\n  ── WARMUP ANALYSIS ─────────────────────────────────")
    print(f"  Sessions lost to warmup: {len(warmup_losses)}")
    if warmup_losses:
        for p in warmup_losses[:5]:
            lag_str = str(p.etil_env_lag) if p.etil_env_lag != 999 else "never"
            print(f"    {p.date}  EP={p.ep_score:3d}  "
                  f"ETS={p.ets_max:3d}  lag={lag_str}  "
                  f"failure={p.primary_failure}")
    warmup_loss_rate = len(warmup_losses) / n * 100
    print(f"  warmup_loss_rate:        {warmup_loss_rate:.1f}%")

    print(f"\n  ── EDGE PERSISTENCE ────────────────────────────────")
    avg_lifespan = sum(p.ets_lifespan for p in profiles) / n
    avg_duration = sum(p.edge_duration for p in profiles) / n
    best_lifespan= max(p.ets_lifespan for p in profiles)
    print(f"  avg_ETS_cluster_lifespan:{avg_lifespan:.1f} bars")
    print(f"  avg_early_edge_duration: {avg_duration:.1f} bars")
    print(f"  max_ETS_cluster:         {best_lifespan} consecutive bars")

    print(f"\n  ── REGIME DEPENDENCY MAP ───────────────────────────")
    by_type = defaultdict(list)
    for p in profiles:
        by_type[p.session_type].append(p)
    for stype in sorted(by_type, key=lambda t: -len(by_type[t])):
        plist = by_type[stype]
        avg_e = sum(x.ep_score for x in plist) / len(plist)
        avg_h = sum(x.hb_rate for x in plist) / len(plist)
        gtal  = sum(x.gtal_valid_count for x in plist)
        print(f"  {stype:<22} n={len(plist):2d}  "
              f"avg_EP={avg_e:4.0f}  HB={avg_h:.0%}  "
              f"GTAL_valid={gtal}")

    print(f"\n  ── SHADOW EXPECTANCY ───────────────────────────────")
    print(f"  Shadow candidates (EP>=50, HB<20%, ETS>=2x, no warmup loss):")
    if shadow_candidates:
        for p in shadow_candidates:
            lag_str = str(p.etil_env_lag) if p.etil_env_lag != 999 else "never"
            print(f"    {p.date}  EP={p.ep_score:3d}  "
                  f"type={p.session_type:<20}  "
                  f"ETS={p.ets_max}  lag={lag_str}  "
                  f"stab={p.edge_stability_score:.0f}")
        shadow_ep = sum(p.ep_score for p in shadow_candidates) / len(shadow_candidates)
        shadow_hb = sum(p.hb_rate for p in shadow_candidates) / len(shadow_candidates)
        print(f"  Shadow avg_EP:           {shadow_ep:.1f}")
        print(f"  Shadow avg_HB:           {shadow_hb:.1%}")
    else:
        print(f"  (ninguna sesión cumple todos los criterios shadow todavía)")

    print(f"\n  ── PRODUCTION READINESS ────────────────────────────")
    if prod_ready >= 60:
        verdict = "APPROACHING — más expansion sessions needed"
    elif prod_ready >= 40:
        verdict = "DEVELOPING — dataset insuficiente"
    else:
        verdict = "NOT READY — necesita más expansion sessions con pre-open"
    print(f"  production_readiness:    {prod_ready:.1f}%")
    print(f"  verdict:                 {verdict}")

    print(f"\n  ── TOP 5 SESSIONS BY EDGE STABILITY ───────────────")
    by_stab = sorted(profiles, key=lambda p: -p.edge_stability_score)
    for i, p in enumerate(by_stab[:5], 1):
        print(f"  #{i}  {p.date}  stab={p.edge_stability_score:4.1f}  "
              f"EP={p.ep_score:3d}  type={p.session_type:<20}  "
              f"HB={p.hb_rate:.0%}  "
              f"failure={p.primary_failure}")

    print(f"\n  ── KEY FINDINGS ─────────────────────────────────────")
    # Most common failure
    if global_fm:
        top_failure = max(global_fm, key=lambda k: global_fm[k])
        print(f"  Most common failure:     {top_failure} "
              f"({global_fm[top_failure]} events)")
        print(f"    → {FAILURE_TYPES.get(top_failure, '')}")
    # Best regime
    best_regime = max(by_type, key=lambda t:
        sum(x.ep_score for x in by_type[t]) / len(by_type[t]))
    print(f"  Most productive regime:  {best_regime}")
    # HB contamination
    hb_dominated = len([p for p in profiles if p.hb_rate >= 0.15])
    print(f"  HB-dominated sessions:   {hb_dominated}/{n} "
          f"({hb_dominated/n:.0%})")
    # ET frequency
    et_present = len([p for p in profiles if p.et_pct > 0.005])
    print(f"  Sessions with ET>0.5%:   {et_present}/{n}")

    print(f"\n{'='*80}")
    print(f"  SUMMARY: {len(elite)} ELITE | {len(high)} HIGH | "
          f"{len(warmup_losses)} warmup losses | "
          f"{len(shadow_candidates)} shadow candidates | "
          f"prod_ready={prod_ready:.0f}%")
    print(f"{'='*80}\n")

    if verbose:
        print(f"\n  ── DETAILED SESSION TABLE ──────────────────────────")
        print(f"  {'DATE':<12} {'EP':>5} {'TYPE':<22} {'STAB':>5} "
              f"{'ETS':>4} {'HB%':>5} {'LAG':>6} {'GTAL':>5} {'FAIL':<16}")
        print(f"  {'─'*95}")
        for p in profiles:
            lag_str = str(p.etil_env_lag) if p.etil_env_lag != 999 else "never"
            print(f"  {p.date:<12} {p.ep_score:>5} "
                  f"{p.session_type:<22} {p.edge_stability_score:>5.1f} "
                  f"{p.ets_max:>4} {p.hb_rate:>4.0%} "
                  f"{lag_str:>6} {p.gtal_valid_count:>5} "
                  f"{p.primary_failure:<16}")


def print_session_detail(profiles: List[SessionProfile], date: str):
    match = [p for p in profiles if p.date == date]
    if not match:
        print(f"Sesión {date} no encontrada.")
        return
    p = match[0]
    print(f"\n{'='*65}")
    print(f"  TRAINING CAMP DETAIL: {p.date}")
    print(f"{'─'*65}")
    print(f"  session_type:         {p.session_type}")
    print(f"  ep_score:             {p.ep_score}/100")
    print(f"  edge_stability_score: {p.edge_stability_score}/100")
    print(f"  ets_max:              {p.ets_max}")
    print(f"  ets_active_count:     {p.ets_active_count}")
    print(f"  ets_lifespan:         {p.ets_lifespan} consecutive bars ETS>=50")
    print(f"  edge_duration:        {p.edge_duration} bars")
    print(f"  gtal_valid_count:     {p.gtal_valid_count}")
    print(f"  hb_rate:              {p.hb_rate:.1%}")
    print(f"  etil_env_lag:         {p.etil_env_lag if p.etil_env_lag!=999 else 'never'}")
    print(f"  opening_drive_score:  {p.od_score}")
    print(f"  warmup_loss:          {p.warmup_loss}")
    print(f"  primary_failure:      {p.primary_failure}")
    print(f"  failure_map:          {p.failure_map}")
    print(f"  starvation_score:     {p.starvation_score}/100")
    print(f"  hb_blocks:            {p.hb_blocks}")
    print(f"  cont_blocks:          {p.cont_blocks}")
    print(f"  gtal_rejections:      {p.gtal_rejections}")
    print(f"  et_pct:               {p.et_pct:.1%}")
    print(f"  rotational_pct:       {p.rotational_pct:.1%}")
    print(f"{'='*65}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GIBBZ V3 — Training Camp Institutional Auditor")
    parser.add_argument("--verbose",  action="store_true",
                        help="Tabla detallada de todas las sesiones")
    parser.add_argument("--session",  type=str, default="",
                        help="Detalle de una sesión específica (YYYY-MM-DD)")
    args = parser.parse_args()

    profiles = load_session_profiles()
    if not profiles:
        print("No hay datos. Corre primero:")
        print("  python expansion_session_miner.py --mine-all")
        print("  python replay_debug_v3.py ... --save-outcomes")
    elif args.session:
        print_session_detail(profiles, args.session)
    else:
        generate_report(profiles, verbose=args.verbose)