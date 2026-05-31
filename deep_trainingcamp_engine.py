"""
GIBBZ V3 — deep_trainingcamp_engine.py
Deep Training Camp — Institutional Deep Auditor v1.0

100% observacional. NO modifica nada del core.
Lee expansion_outcomes/ y genera análisis profundo de:
- Micro expansion clusters ocultos
- Warmup recovery analysis
- Hidden synchronization detection
- Near-shadow candidate promotion
- Edge survivability
- Expansion fingerprints
- Dataset Intelligence Report

USO:
  python deep_trainingcamp_engine.py
  python deep_trainingcamp_engine.py --session 2026-03-11
  python deep_trainingcamp_engine.py --near-shadow
"""

import json
import os
import glob
import argparse
from dataclasses import dataclass, field
from collections import defaultdict
from typing import List, Dict, Optional


# ── NEAR-SHADOW CRITERIA ──────────────────────────────────────────
NEAR_SHADOW_EP_MIN      = 45
NEAR_SHADOW_HB_MAX      = 0.15
NEAR_SHADOW_LAG_MAX     = 50   # 999 = never → excluded
NEAR_SHADOW_STAB_MIN    = 45
NEAR_SHADOW_CLUSTERS_MIN= 1


@dataclass
class DeepProfile:
    date:               str   = ""
    session_type:       str   = ""
    ep_score:           int   = 0

    # Raw metrics
    ets_max:            int   = 0
    ets65_count:        int   = 0
    ets_cluster_max:    int   = 0
    hb_rate:            float = 0.0
    etil_env_lag:       int   = 999
    od_score:           int   = 0
    od_eff_peak:        int   = 0
    eff_max:            int   = 0
    eff_sustained:      int   = 0
    consecutive_eff:    int   = 0
    range_expansion:    int   = 0
    micro_clusters:     int   = 0
    best_cluster_ets:   int   = 0
    best_cluster_bar:   int   = 0
    gtal_valid:         int   = 0
    first_et_bar:       int   = 0
    first_ets65_bar:    int   = 0
    hb_count:           int   = 0
    early_edge_duration:int   = 0
    od_et_bars:         int   = 0

    # Derived
    edge_stability:     float = 0.0
    warmup_loss:        bool  = False
    near_shadow:        bool  = False
    near_shadow_score:  int   = 0
    missed_elite:       bool  = False
    hidden_sync:        bool  = False
    survivability_2:    str   = "UNKNOWN"
    survivability_5:    str   = "UNKNOWN"
    survivability_10:   str   = "UNKNOWN"
    fingerprints:       List  = field(default_factory=list)


def load_expansion_profiles() -> List[DeepProfile]:
    profiles = []
    for f in sorted(glob.glob("expansion_outcomes/*_expansion.json")):
        try:
            d = json.load(open(f, encoding="utf-8"))
            dp = DeepProfile(
                date          = d.get("session_date", ""),
                session_type  = d.get("session_type", "ROTATIONAL"),
                ep_score      = d.get("ep_score", 0),
                ets_max       = d.get("ets_max", 0),
                ets65_count   = d.get("ets65_count", 0),
                ets_cluster_max = d.get("ets_cluster_max", 0),
                hb_rate       = d.get("hb_rate", 0.0),
                etil_env_lag  = d.get("etil_to_env_delay", 999),
                od_score      = d.get("opening_drive_score", 0),
                od_eff_peak   = d.get("od_eff_peak", 0),
                eff_max       = d.get("eff_max", 0),
                eff_sustained = d.get("eff_sustained", 0),
                consecutive_eff = d.get("consecutive_eff_high", 0),
                range_expansion = d.get("range_expansion_bars", 0),
                micro_clusters  = d.get("micro_expansion_clusters", 0),
                best_cluster_ets= d.get("best_cluster_ets", 0),
                best_cluster_bar= d.get("best_cluster_start", 0),
                gtal_valid      = d.get("gtal_valid_count", 0),
                first_et_bar    = d.get("first_et_bar", 0),
                first_ets65_bar = d.get("first_ets65_bar", 0),
                hb_count        = d.get("hb_count", 0),
                early_edge_duration = d.get("early_edge_duration", 0),
                od_et_bars      = d.get("od_et_bars", 0),
            )
            dp = _compute_deep_metrics(dp)
            profiles.append(dp)
        except Exception as e:
            print(f"  [WARN] {f}: {e}")
    return sorted(profiles, key=lambda p: -p.ep_score)


def _compute_deep_metrics(dp: DeepProfile) -> DeepProfile:
    """Calcula todas las métricas derivadas profundas."""

    # ── EDGE STABILITY ────────────────────────────────────────────
    s = 0.0
    s += max(0, 30 - dp.hb_rate * 50)
    if dp.ets65_count >= 3:    s += 20
    elif dp.ets65_count >= 2:  s += 12
    elif dp.ets65_count >= 1:  s += 6
    if dp.etil_env_lag == 0:   s += 15
    elif dp.etil_env_lag <= 50: s += 8
    elif dp.etil_env_lag <= 200: s += 3
    if dp.first_et_bar > 0 and dp.first_et_bar <= 20: s += 10
    if dp.od_score >= 70:      s += 10
    elif dp.od_score >= 40:    s += 5
    if dp.eff_max >= 90:       s += 10
    elif dp.eff_max >= 70:     s += 5
    dp.edge_stability = round(min(s, 100), 1)

    # ── WARMUP LOSS ───────────────────────────────────────────────
    dp.warmup_loss = (
        dp.etil_env_lag == 999 and dp.ets_max >= 65
        or dp.first_ets65_bar > 0 and dp.first_et_bar == 0
    )

    # ── NEAR-SHADOW CANDIDATE ─────────────────────────────────────
    lag_ok = dp.etil_env_lag != 999 and dp.etil_env_lag <= NEAR_SHADOW_LAG_MAX
    ns_score = 0
    if dp.ep_score >= NEAR_SHADOW_EP_MIN:    ns_score += 30
    if dp.hb_rate <= NEAR_SHADOW_HB_MAX:     ns_score += 25
    if lag_ok:                                ns_score += 20
    if dp.edge_stability >= NEAR_SHADOW_STAB_MIN: ns_score += 15
    if dp.micro_clusters >= NEAR_SHADOW_CLUSTERS_MIN: ns_score += 10
    dp.near_shadow_score = ns_score
    dp.near_shadow = (
        dp.ep_score >= NEAR_SHADOW_EP_MIN
        and dp.hb_rate <= NEAR_SHADOW_HB_MAX
        and dp.edge_stability >= NEAR_SHADOW_STAB_MIN
        and dp.micro_clusters >= NEAR_SHADOW_CLUSTERS_MIN
    )

    # ── MISSED ELITE ──────────────────────────────────────────────
    # Sesiones que habrían sido ELITE con pre-open correcto
    dp.missed_elite = (
        dp.ets_max >= 65
        and dp.hb_rate <= 0.10
        and dp.ep_score >= 50
        and (dp.warmup_loss or dp.etil_env_lag > 100)
    )

    # ── HIDDEN SYNCHRONIZATION ────────────────────────────────────
    # ETIL detectó antes que ET pero con eff alto y HB bajo
    dp.hidden_sync = (
        dp.ets_max >= 60
        and dp.eff_max >= 70
        and dp.hb_rate <= 0.12
        and dp.ets65_count >= 1
        and dp.micro_clusters >= 1
        and dp.etil_env_lag != 0   # ETIL adelantó a ET
    )

    # ── SURVIVABILITY ANALYSIS ────────────────────────────────────
    # ¿Cuánto tiempo habrían sobrevivido setups con ETS>=65?
    lifespan = dp.ets_cluster_max  # bars consecutivos ETS>=50
    for bars, attr in [(2, "survivability_2"), (5, "survivability_5"),
                       (10, "survivability_10")]:
        if dp.ets_max < 65:
            val = "NO_SIGNAL"
        elif lifespan >= bars:
            val = f"SURVIVED_{bars}B"
        elif lifespan >= bars // 2:
            val = f"PARTIAL_{bars}B"
        else:
            val = f"EXPIRED_{bars}B"
        setattr(dp, attr, val)

    # ── EXPANSION FINGERPRINTS ────────────────────────────────────
    fp = []
    if dp.od_eff_peak >= 80:         fp.append("HIGH_EFF_OPEN")
    if dp.od_et_bars >= 1:           fp.append("ET_IN_FIRST_30")
    if dp.range_expansion >= 30:     fp.append("RANGE_EXPANSION")
    if dp.ets_cluster_max >= 3:      fp.append("ETS_CLUSTER_3+")
    if dp.consecutive_eff >= 5:      fp.append("DELTA_PERSISTENCE")
    if dp.hb_rate <= 0.07:           fp.append("LOW_HB_CONTAMINATION")
    if dp.etil_env_lag == 0:         fp.append("PERFECT_ALIGNMENT")
    if 0 < dp.etil_env_lag <= 30:    fp.append("NEAR_ALIGNMENT")
    if dp.micro_clusters >= 3:       fp.append("MULTI_CLUSTER")
    if dp.eff_sustained >= 50:       fp.append("SUSTAINED_EFF")
    dp.fingerprints = fp

    return dp


def generate_deep_report(profiles: List[DeepProfile]):
    n = len(profiles)
    if n == 0:
        print("No hay datos en expansion_outcomes/")
        return

    near_shadow   = [p for p in profiles if p.near_shadow]
    missed_elite  = [p for p in profiles if p.missed_elite]
    hidden_sync   = [p for p in profiles if p.hidden_sync]
    warmup_losses = [p for p in profiles if p.warmup_loss]
    shadow_full   = [p for p in profiles if
                     p.ep_score >= 50 and p.hb_rate < 0.20
                     and p.ets65_count >= 2 and not p.warmup_loss]

    # Fingerprint frequency
    fp_count = defaultdict(int)
    for p in profiles:
        for fp in p.fingerprints:
            fp_count[fp] += 1

    # Survivability stats
    surv2  = [p for p in profiles if "SURVIVED" in p.survivability_2]
    surv5  = [p for p in profiles if "SURVIVED" in p.survivability_5]
    surv10 = [p for p in profiles if "SURVIVED" in p.survivability_10]

    print(f"\n{'='*80}")
    print(f"  GIBBZ V3 — DEEP TRAINING CAMP REPORT")
    print(f"  Institutional Deep Auditor v1.0")
    print(f"{'='*80}")

    print(f"\n  ── DATASET INTELLIGENCE SUMMARY ─────────────────────")
    print(f"  Sessions in dataset:         {n}")
    print(f"  Full shadow candidates:      {len(shadow_full)}")
    print(f"  Near-shadow candidates:      {len(near_shadow)}")
    print(f"  Missed ELITE (warmup):       {len(missed_elite)}")
    print(f"  Hidden sync detected:        {len(hidden_sync)}")
    print(f"  Warmup losses:               {len(warmup_losses)}")

    print(f"\n  ── MICRO EXPANSION MINING ───────────────────────────")
    micro_rich = sorted(
        [p for p in profiles if p.micro_clusters >= 2 and p.ets_max >= 60],
        key=lambda p: -(p.micro_clusters * 10 + p.ets_max)
    )
    if micro_rich:
        print(f"  Sessions with hidden micro expansion (clusters>=2, ETS>=60):")
        for p in micro_rich:
            print(f"    {p.date}  EP={p.ep_score:3d}  "
                  f"clusters={p.micro_clusters}  "
                  f"best_ETS={p.best_cluster_ets}@bar{p.best_cluster_bar}  "
                  f"HB={p.hb_rate:.0%}  lag={p.etil_env_lag if p.etil_env_lag!=999 else 'never'}")
    else:
        print(f"  (ninguna sesión tiene micro clusters >= 2 con ETS >= 60)")

    print(f"\n  ── WARMUP RECOVERY ANALYSIS ────────────────────────")
    print(f"  Sessions that would improve with pre-open warmup:")
    warmup_sorted = sorted(warmup_losses, key=lambda p: -p.ets_max)
    for p in warmup_sorted[:8]:
        est_ep = min(p.ep_score + 15, 95)
        print(f"    {p.date}  current_EP={p.ep_score:3d}  "
              f"→  est_EP_with_warmup={est_ep:3d}  "
              f"ETS={p.ets_max:3d}  HB={p.hb_rate:.0%}  "
              f"type={p.session_type}")
    if warmup_sorted:
        avg_gain = 15
        print(f"  Estimated avg EP gain with pre-open:  +{avg_gain} pts")
        would_be_high = len([p for p in warmup_losses
                             if p.ep_score + avg_gain >= 50])
        print(f"  Would reach HIGH (EP>=50) with warmup: {would_be_high} sessions")

    print(f"\n  ── HIDDEN SYNCHRONIZATION DETECTION ────────────────")
    if hidden_sync:
        print(f"  Sessions where ETIL detected BEFORE market_env confirmed:")
        for p in sorted(hidden_sync, key=lambda p: p.etil_env_lag):
            lag_str = str(p.etil_env_lag) if p.etil_env_lag != 999 else "never"
            print(f"    {p.date}  EP={p.ep_score:3d}  "
                  f"lag={lag_str:>6} bars  "
                  f"eff_max={p.eff_max:3d}  "
                  f"ETS={p.ets_max:3d}  "
                  f"HB={p.hb_rate:.0%}  "
                  f"clusters={p.micro_clusters}")
        avg_lag = sum(p.etil_env_lag for p in hidden_sync
                      if p.etil_env_lag != 999) / max(
            len([p for p in hidden_sync if p.etil_env_lag != 999]), 1)
        print(f"  avg_ETIL_to_ET_lag:          {avg_lag:.0f} bars")
        print(f"  → Edge window BEFORE confirmation: {avg_lag:.0f} bars early")
    else:
        print(f"  (ninguna sesión con hidden sync detectado)")

    print(f"\n  ── NEAR-SHADOW CANDIDATES ───────────────────────────")
    print(f"  Criteria: EP>=45, HB<=15%, stab>=45, clusters>=1")
    if near_shadow:
        for p in sorted(near_shadow, key=lambda p: -p.near_shadow_score):
            lag_str = str(p.etil_env_lag) if p.etil_env_lag != 999 else "never"
            print(f"    {p.date}  EP={p.ep_score:3d}  "
                  f"ns_score={p.near_shadow_score:3d}  "
                  f"stab={p.edge_stability:4.1f}  "
                  f"HB={p.hb_rate:.0%}  lag={lag_str}  "
                  f"type={p.session_type}")
            if p.fingerprints:
                print(f"           fingerprints: {' | '.join(p.fingerprints)}")
        print(f"\n  Total near-shadow promoted: {len(near_shadow)}")
        print(f"  Full shadow + near-shadow:  {len(shadow_full) + len(near_shadow)}")
    else:
        print(f"  (ninguna sesión promovida a near-shadow)")

    print(f"\n  ── MISSED ELITE ANALYSIS ────────────────────────────")
    if missed_elite:
        print(f"  Sessions that would likely be ELITE with pre-open:")
        for p in sorted(missed_elite, key=lambda p: -p.ets_max):
            est = min(p.ep_score + 20, 95)
            print(f"    {p.date}  ETS={p.ets_max:3d}  HB={p.hb_rate:.0%}  "
                  f"current_EP={p.ep_score:3d}  est_ELITE_EP={est:3d}")
    else:
        print(f"  (ninguna sesión missed ELITE identificada)")

    print(f"\n  ── EDGE SURVIVABILITY MAP ───────────────────────────")
    print(f"  (cuánto dura el edge cuando aparece)")
    print(f"  Survived 2 bars:   {len(surv2):2d}/{n}  "
          f"({len(surv2)/n:.0%})")
    print(f"  Survived 5 bars:   {len(surv5):2d}/{n}  "
          f"({len(surv5)/n:.0%})")
    print(f"  Survived 10 bars:  {len(surv10):2d}/{n}  "
          f"({len(surv10)/n:.0%})")
    if surv5:
        print(f"  Sessions surviving 5+ bars:")
        for p in surv5:
            print(f"    {p.date}  EP={p.ep_score}  ETS_cluster={p.ets_cluster_max}  "
                  f"type={p.session_type}")

    print(f"\n  ── EXPANSION FINGERPRINTS ───────────────────────────")
    print(f"  (características que aparecen antes de HIGH sessions)")
    for fp, cnt in sorted(fp_count.items(), key=lambda x: -x[1]):
        pct = cnt / n * 100
        bar = "█" * int(pct / 3)
        print(f"  {fp:<28} {cnt:2d}/{n}  ({pct:4.1f}%)  {bar}")
    if fp_count:
        top_fp = max(fp_count, key=lambda k: fp_count[k])
        print(f"\n  Most common fingerprint: {top_fp} ({fp_count[top_fp]} sessions)")
        print(f"  → Presente en {fp_count[top_fp]/n:.0%} del dataset")

    print(f"\n  ── NEAR-SHADOW PROGRESSION TRACKER ─────────────────")
    full_n  = len(shadow_full)
    near_n  = len(near_shadow)
    total_c = full_n + near_n
    target  = 10
    print(f"  Full shadow candidates:      {full_n:2d}")
    print(f"  Near-shadow candidates:      {near_n:2d}")
    print(f"  Total candidate pool:        {total_c:2d}")
    print(f"  Target:                      {target:2d}")
    progress = min(total_c / target * 100, 100)
    bar = "█" * int(progress / 5)
    print(f"  Progress to target:          {progress:.0f}%  {bar}")
    needed = max(0, target - total_c)
    print(f"  Still needed:                {needed} candidates")
    if needed > 0:
        print(f"  → Graba {needed} sesiones HIGH con pre-open 06:30-07:00 CR")

    print(f"\n  ── DATASET INTELLIGENCE REPORT ──────────────────────")
    best_stab = max(profiles, key=lambda p: p.edge_stability)
    best_ets  = max(profiles, key=lambda p: p.ets_max)
    best_od   = max(profiles, key=lambda p: p.od_score)
    best_lag  = min([p for p in profiles if p.etil_env_lag != 999],
                    key=lambda p: p.etil_env_lag, default=None)
    print(f"  Most stable session:         {best_stab.date} "
          f"(stab={best_stab.edge_stability})")
    print(f"  Strongest ETS:               {best_ets.date} "
          f"(ETS={best_ets.ets_max})")
    print(f"  Best opening drive:          {best_od.date} "
          f"(OD={best_od.od_score})")
    if best_lag:
        print(f"  Fastest ETIL→ET alignment:   {best_lag.date} "
              f"(lag={best_lag.etil_env_lag})")
    print(f"  Sessions with ET in open:    "
          f"{len([p for p in profiles if p.od_et_bars >= 1])}")
    print(f"  Sessions HB<=7%:             "
          f"{len([p for p in profiles if p.hb_rate <= 0.07])}")
    print(f"  Sessions multi-cluster:      "
          f"{len([p for p in profiles if p.micro_clusters >= 3])}")

    # Readiness update
    readiness = round(
        (total_c / target * 40) +
        (len([p for p in profiles if p.edge_stability >= 50]) / n * 35) +
        (len([p for p in profiles if p.hb_rate <= 0.15]) / n * 25), 1)
    print(f"\n  Updated production readiness: {readiness:.1f}%")

    print(f"\n{'='*80}")
    print(f"  DEEP SUMMARY: {full_n} shadow | {near_n} near-shadow | "
          f"{len(missed_elite)} missed ELITE | "
          f"{len(hidden_sync)} hidden sync | "
          f"readiness={readiness:.0f}%")
    print(f"{'='*80}\n")


def print_session_deep(profiles: List[DeepProfile], date: str):
    match = [p for p in profiles if p.date == date]
    if not match:
        print(f"Sesión {date} no encontrada.")
        return
    p = match[0]
    print(f"\n{'='*65}")
    print(f"  DEEP ANALYSIS: {p.date}  [{p.session_type}  EP={p.ep_score}]")
    print(f"{'─'*65}")
    print(f"  edge_stability:       {p.edge_stability}/100")
    print(f"  near_shadow:          {p.near_shadow}  (score={p.near_shadow_score})")
    print(f"  missed_elite:         {p.missed_elite}")
    print(f"  hidden_sync:          {p.hidden_sync}")
    print(f"  warmup_loss:          {p.warmup_loss}")
    print(f"  survivability_2bar:   {p.survivability_2}")
    print(f"  survivability_5bar:   {p.survivability_5}")
    print(f"  survivability_10bar:  {p.survivability_10}")
    print(f"  fingerprints:         {' | '.join(p.fingerprints) or '(none)'}")
    print(f"  micro_clusters:       {p.micro_clusters}")
    print(f"  best_cluster:         bar {p.best_cluster_bar} ETS={p.best_cluster_ets}")
    print(f"  eff_max:              {p.eff_max}")
    print(f"  consecutive_eff_high: {p.consecutive_eff}")
    print(f"  hb_rate:              {p.hb_rate:.1%}")
    print(f"  etil_env_lag:         {p.etil_env_lag if p.etil_env_lag!=999 else 'never'}")
    print(f"{'='*65}\n")


def print_near_shadow_table(profiles: List[DeepProfile]):
    near = [p for p in profiles if p.near_shadow]
    full = [p for p in profiles if
            p.ep_score >= 50 and p.hb_rate < 0.20
            and p.ets65_count >= 2 and not p.warmup_loss]
    print(f"\n{'='*80}")
    print(f"  NEAR-SHADOW + FULL SHADOW CANDIDATE TABLE")
    print(f"{'─'*80}")
    print(f"  {'DATE':<12} {'TYPE':<8} {'EP':>4} {'STAB':>5} "
          f"{'HB%':>5} {'LAG':>6} {'CLUST':>6} {'NS_SCORE':>9} {'FINGERPRINTS'}")
    print(f"  {'─'*78}")
    all_c = sorted(set([p.date for p in full + near]))
    for date in all_c:
        matches = [p for p in profiles if p.date == date]
        if not matches:
            continue
        p = matches[0]
        kind = "FULL  " if p in full else "NEAR  "
        lag  = str(p.etil_env_lag) if p.etil_env_lag != 999 else "never"
        fp   = " ".join(p.fingerprints[:3])
        print(f"  {p.date:<12} {kind:<8} {p.ep_score:>4} "
              f"{p.edge_stability:>5.1f} {p.hb_rate:>4.0%} "
              f"{lag:>6} {p.micro_clusters:>6} {p.near_shadow_score:>9}  {fp}")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GIBBZ V3 — Deep Training Camp Auditor")
    parser.add_argument("--session",    type=str, default="",
                        help="Detalle profundo de una sesión")
    parser.add_argument("--near-shadow",action="store_true",
                        help="Tabla de near-shadow + full shadow candidates")
    args = parser.parse_args()

    profiles = load_expansion_profiles()
    if not profiles:
        print("No hay datos en expansion_outcomes/")
        print("Corre: python expansion_session_miner.py --mine-all")
    elif args.session:
        print_session_deep(profiles, args.session)
    elif args.near_shadow:
        print_near_shadow_table(profiles)
    else:
        generate_deep_report(profiles)