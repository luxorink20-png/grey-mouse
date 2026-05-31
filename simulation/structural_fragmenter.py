"""
GIBBZ V3 — simulation/structural_fragmenter.py
Structural Session Fragmenter v1.0

Analiza sesiones reales y las divide en fragmentos institucionales:
  PREMARKET, OPENING_DRIVE, EARLY_EXPANSION, MIDDAY_ROTATION,
  VOL_RELEASE, HB_SPIKE_ZONE, EXHAUSTION

NUNCA modifica los ticks originales.
Cada fragmento preserva microestructura completa.
Output: simulation/fragments/YYYY-MM-DD_fragments.json

USO:
  python simulation/structural_fragmenter.py --date 2026-03-11
  python simulation/structural_fragmenter.py --all
  python simulation/structural_fragmenter.py --report
"""

import json
import os
import sys
import glob
import argparse
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple

# Core pipeline
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from event_engine              import EventEngine
from confluence_engine         import ConfluenceEngine, ConfluenceResult
from validator                 import Validator
from intent_engine             import IntentEngine
from risk_engine               import RiskEngine
from confirmation_engine       import ConfirmationEngine
from continuation_engine       import ContinuationEngine
from session_regime_engine     import SessionRegimeEngine
from adaptive_continuation     import AdaptiveContinuationEngine
from market_environment        import MarketEnvironmentAnalyzer
from poc_acceptance            import PocAcceptanceEngine
from microstructure_engine     import MicrostructureEngine
from levels                    import create_levels
from historical_context_loader import HistoricalContextLoader
from bar_aggregator            import BarAggregator
from gibbz_etil                import ETILEngine
from gibbz_timing              import TimingEngine
from gibbz_edge_decay          import EdgeDecayEngine
from gibbz_opportunity         import OpportunityClassifier
from gibbz_gtal                import GTALEngine
from gibbz_esl                 import ESLEngine
from pnl_attribution_layer     import PNLAttributionLayer
from portfolio_risk_context_layer import PortfolioRiskContextLayer
from adaptive_parameter_layer  import AdaptiveParameterLayer

FRAGMENTS_DIR = os.path.join("simulation", "fragments")

# ── FRAGMENT TYPES ────────────────────────────────────────────────
FRAGMENT_TYPES = [
    "PREMARKET",
    "OPENING_DRIVE",
    "EARLY_EXPANSION",
    "MIDDAY_ROTATION",
    "VOL_RELEASE",
    "HB_SPIKE_ZONE",
    "POWER_HOUR",
    "EXHAUSTION",
]


@dataclass
class BarSnapshot:
    """Snapshot mínimo de una barra para el fragmenter."""
    bar:        int   = 0
    price:      float = 0.0
    high:       float = 0.0
    low:        float = 0.0
    env:        str   = "ROTATIONAL"
    eff:        int   = 0
    trap:       int   = 0
    ets:        int   = 0
    conf:       int   = 0
    hb:         bool  = False
    ev:         str   = "INVALID"
    rt:         int   = 0
    opp:        str   = "NONE"
    cont:       int   = 0


@dataclass
class Fragment:
    """Un fragmento institucional de una sesión real."""
    fragment_id:        str   = ""
    session_date:       str   = ""
    fragment_type:      str   = "MIDDAY_ROTATION"
    recording_file:     str   = ""

    # Posición en la sesión
    start_bar:          int   = 0
    end_bar:            int   = 0
    bar_count:          int   = 0

    # Microestructura preserved
    entry_price:        float = 0.0
    exit_price:         float = 0.0
    price_delta:        float = 0.0    # exit - entry (normalizable)
    price_range:        float = 0.0    # high - low del fragmento

    # Edge metrics del fragmento
    ets_max:            int   = 0
    ets_avg:            float = 0.0
    eff_max:            int   = 0
    eff_avg:            float = 0.0
    hb_rate:            float = 0.0
    hb_count:           int   = 0
    gtal_valid_count:   int   = 0
    opp_a_count:        int   = 0
    et_bar_count:       int   = 0
    rt_max:             int   = 0

    # Regime profile del fragmento
    regime_distribution: Dict = field(default_factory=dict)
    dominant_regime:    str   = "ROTATIONAL"

    # Continuidad (para compatibilidad en morpher)
    entry_eff:          int   = 0    # eff en primer bar
    exit_eff:           int   = 0    # eff en último bar
    entry_env:          str   = "ROTATIONAL"
    exit_env:           str   = "ROTATIONAL"
    entry_hb:           bool  = False
    exit_hb:            bool  = False

    # Fingerprints del fragmento
    fingerprints:       List  = field(default_factory=list)

    # Compatibilidad
    compatible_after:   List  = field(default_factory=list)
    compatible_before:  List  = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ── FRAGMENTER ────────────────────────────────────────────────────

class StructuralFragmenter:
    """
    Analiza una sesión real barra por barra y la divide
    en fragmentos institucionales preservando microestructura.
    """

    # Thresholds de clasificación (solo lectura — NO afectan core)
    ETS_HIGH       = 50
    ETS_ACTIVE     = 65
    EFF_HIGH       = 60
    EFF_MODERATE   = 40
    HB_SPIKE_RUN   = 3    # barras consecutivas HB=True para zona

    def fragment(self,
                 replay_file: str,
                 replay_date: str,
                 max_bars:    int  = 400,
                 silent:      bool = False) -> List[Fragment]:

        bars = self._run_pipeline(replay_file, replay_date, max_bars, silent)
        if not bars:
            return []

        fragments = self._detect_fragments(bars, replay_date, replay_file)
        fragments = self._compute_fragment_metrics(fragments, bars)
        fragments = self._compute_fingerprints(fragments)
        fragments = self._compute_compatibility(fragments)

        return fragments

    def _run_pipeline(self, replay_file, replay_date,
                      max_bars, silent) -> List[BarSnapshot]:
        """Corre el pipeline completo y captura snapshots por barra."""
        loader = HistoricalContextLoader()
        ctx    = loader.load(replay_date)
        VAH, POC, VAL = ctx.vah, ctx.poc, ctx.val

        event_eng    = EventEngine(window=10)
        conf_eng     = ConfluenceEngine(history_size=10)
        validator    = Validator(tick=0.25, min_liq_ticks=4)
        intent_eng   = IntentEngine(buffer_size=15, tick=0.25)
        risk_eng     = RiskEngine(tick=0.25)
        confirmation = ConfirmationEngine(window=20, tick=0.25)
        continuation = ContinuationEngine(window=12, tick=0.25)
        sess_regime  = SessionRegimeEngine(tick=0.25)
        adaptive_cont= AdaptiveContinuationEngine(tick=0.25)
        market_env   = MarketEnvironmentAnalyzer(tick=0.25)
        poc_engine   = PocAcceptanceEngine(vah=VAH, poc=POC, val=VAL, tick=0.25)
        micro        = MicrostructureEngine(window=25)
        levels       = create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0)
        aggregator   = BarAggregator(mode="TICK", ticks=500)
        etil         = ETILEngine()
        timing       = TimingEngine()
        decay        = EdgeDecayEngine()
        opp_clf      = OpportunityClassifier()
        gtal         = GTALEngine()
        esl          = ESLEngine()
        pnl          = PNLAttributionLayer()
        port         = PortfolioRiskContextLayer()
        adaptive     = AdaptiveParameterLayer()

        snapshots = []
        bar_count = 0

        with open(replay_file, encoding="utf-8") as f:
            for line in f:
                if bar_count >= max_bars:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    tick = json.loads(line)
                except Exception:
                    continue

                bar = aggregator.process(tick)
                if bar is None:
                    continue
                bar_count += 1
                raw = bar

                # Core
                evt    = event_eng.process(raw)
                ctx_l  = levels.get_context(raw["price"])
                reg_r  = sess_regime.update(raw, evt)
                env_r  = market_env.analyze_environment(raw, evt)
                mp     = ConfluenceResult(
                    event="NONE", zone=ctx_l.zone, confluence="",
                    bias="NEUTRAL", score=50,
                    classification="MEDIUM QUALITY",
                    action="OBSERVE", reason="",
                    hpz_bonus=False, bias_aligned=False, consecutive=0)
                mic_r  = micro.analyze(evt, ctx_l, mp, raw)
                conf_r = confirmation.analyze(evt, ctx_l, None, mic_r, raw)
                raw["env"]  = env_r.environment
                raw["zone"] = ctx_l.zone
                cont_r = continuation.analyze(evt, conf_r, reg_r, raw)
                ac_r   = adaptive_cont.analyze_continuation(
                    evt, conf_r, reg_r, env_r, raw)
                poc_r  = poc_engine.analyze(raw, evt, conf_r)
                analysis = conf_eng.evaluate(
                    evt, ctx_l,
                    confirmation=conf_r, session_regime=reg_r,
                    continuation=cont_r, adaptive_continuation=ac_r,
                    market_env=env_r, poc_acceptance=poc_r)
                val    = validator.validate(
                    analysis, evt, raw,
                    confirmation=conf_r, session_regime=reg_r,
                    continuation=cont_r, adaptive_continuation=ac_r,
                    market_env=env_r, poc_acceptance=poc_r)

                # V3
                etil_r  = etil.analyze(env_r, cont_r, conf_r, raw)
                timing_r= timing.analyze(etil_r, conf_r, val, bar_count)
                decay_r = decay.analyze(env_r, etil_r, bar_count)
                opp_r   = opp_clf.classify(etil_r, timing_r, decay_r,
                                             conf_r, val)
                gtal_r  = gtal.analyze(
                    etil_r, timing_r, decay_r, opp_r,
                    env_r, conf_r, cont_r, bar_count)

                snap = BarSnapshot(
                    bar   = bar_count,
                    price = raw.get("price", 0),
                    high  = raw.get("high",  raw.get("price", 0)),
                    low   = raw.get("low",   raw.get("price", 0)),
                    env   = getattr(env_r,  "environment",             "ROTATIONAL"),
                    eff   = getattr(env_r,  "directional_efficiency",  0),
                    trap  = getattr(env_r,  "trap_density",            0),
                    ets   = getattr(etil_r, "ets_score",               0),
                    conf  = getattr(conf_r, "confirmation_score",      0),
                    hb    = getattr(gtal_r, "hindsight_bias_flag",     False),
                    ev    = getattr(gtal_r, "execution_validity",      "INVALID"),
                    rt    = getattr(gtal_r, "real_tradeability_score", 0),
                    opp   = getattr(opp_r,  "grade",                   "NONE"),
                    cont  = getattr(cont_r, "continuation_probability", 0),
                )
                snapshots.append(snap)

                if not silent and bar_count % 100 == 0:
                    print(f"  ... bar {bar_count}  env={snap.env:<16}  "
                          f"ETS={snap.ets:3d}  eff={snap.eff:3d}", flush=True)

        return snapshots

    def _detect_fragments(self,
                          bars: List[BarSnapshot],
                          date: str,
                          recording: str) -> List[Fragment]:
        """Detecta dónde empieza y termina cada fragmento."""
        fragments     = []
        frag_id       = 0
        current_type  = "PREMARKET"
        current_start = 1
        hb_run        = 0
        in_hb_zone    = False

        def close_fragment(start, end, ftype):
            nonlocal frag_id
            if end < start:
                return
            frag_id += 1
            frag = Fragment(
                fragment_id    = f"{date}_{frag_id:03d}",
                session_date   = date,
                fragment_type  = ftype,
                recording_file = os.path.basename(recording),
                start_bar      = start,
                end_bar        = end,
                bar_count      = end - start + 1,
            )
            fragments.append(frag)

        prev_type = "PREMARKET"

        for b in bars:
            i = b.bar

            # Detección de HB_SPIKE_ZONE
            if b.hb:
                hb_run += 1
                if hb_run >= self.HB_SPIKE_RUN and not in_hb_zone:
                    # Cerrar fragmento actual
                    close_fragment(current_start, i - 1, current_type)
                    current_start = i
                    current_type  = "HB_SPIKE_ZONE"
                    in_hb_zone    = True
            else:
                if in_hb_zone and hb_run >= self.HB_SPIKE_RUN:
                    # Salir de zona HB
                    close_fragment(current_start, i - 1, "HB_SPIKE_ZONE")
                    current_start = i
                    current_type  = prev_type
                    in_hb_zone    = False
                hb_run = 0

            if in_hb_zone:
                continue

            # Clasificación estructural
            if i <= 5:
                new_type = "PREMARKET"

            elif (i <= 30 and
                  b.ets >= self.ETS_HIGH and
                  b.eff >= self.EFF_HIGH):
                new_type = "OPENING_DRIVE"

            elif (b.env == "EFFICIENT_TREND" and
                  b.ets >= self.ETS_ACTIVE and
                  b.rt >= 50):
                new_type = "EARLY_EXPANSION"

            elif (b.ets >= self.ETS_HIGH and
                  b.eff >= self.EFF_MODERATE and
                  b.env != "EFFICIENT_TREND"):
                new_type = "VOL_RELEASE"

            elif (b.eff < self.EFF_MODERATE and
                  b.ets < self.ETS_HIGH and
                  b.env in ("ROTATIONAL", "CHOPPY")):
                new_type = "MIDDAY_ROTATION"

            elif (i > int(len(bars) * 0.75) and
                  b.eff < self.EFF_MODERATE):
                new_type = "EXHAUSTION"

            else:
                new_type = current_type  # mantener

            # Cambio de tipo → cerrar fragmento
            if new_type != current_type:
                if i - current_start >= 3:  # mínimo 3 barras por fragmento
                    close_fragment(current_start, i - 1, current_type)
                    current_start = i
                prev_type    = current_type
                current_type = new_type

        # Cerrar último fragmento
        if bars:
            close_fragment(current_start, bars[-1].bar, current_type)

        return fragments

    def _compute_fragment_metrics(self,
                                   fragments: List[Fragment],
                                   bars: List[BarSnapshot]) -> List[Fragment]:
        """Calcula métricas de cada fragmento desde los snapshots."""
        bar_map = {b.bar: b for b in bars}

        for frag in fragments:
            frag_bars = [bar_map[i] for i in
                         range(frag.start_bar, frag.end_bar + 1)
                         if i in bar_map]
            if not frag_bars:
                continue

            prices = [b.price for b in frag_bars]
            highs  = [b.high  for b in frag_bars]
            lows   = [b.low   for b in frag_bars]

            frag.entry_price  = frag_bars[0].price
            frag.exit_price   = frag_bars[-1].price
            frag.price_delta  = round(frag.exit_price - frag.entry_price, 2)
            frag.price_range  = round(max(highs) - min(lows), 2)

            frag.ets_max  = max(b.ets  for b in frag_bars)
            frag.ets_avg  = round(sum(b.ets  for b in frag_bars) / len(frag_bars), 1)
            frag.eff_max  = max(b.eff  for b in frag_bars)
            frag.eff_avg  = round(sum(b.eff  for b in frag_bars) / len(frag_bars), 1)
            frag.rt_max   = max(b.rt   for b in frag_bars)

            hb_count = sum(1 for b in frag_bars if b.hb)
            frag.hb_count  = hb_count
            frag.hb_rate   = round(hb_count / len(frag_bars), 3)
            frag.gtal_valid_count = sum(1 for b in frag_bars if b.ev == "VALID")
            frag.opp_a_count = sum(1 for b in frag_bars if b.opp == "A")
            frag.et_bar_count = sum(1 for b in frag_bars
                                    if b.env == "EFFICIENT_TREND")

            # Regime distribution
            reg_dist = {}
            for b in frag_bars:
                reg_dist[b.env] = reg_dist.get(b.env, 0) + 1
            frag.regime_distribution = reg_dist
            frag.dominant_regime = max(reg_dist, key=lambda k: reg_dist[k])

            # Continuity metrics
            frag.entry_eff = frag_bars[0].eff
            frag.exit_eff  = frag_bars[-1].eff
            frag.entry_env = frag_bars[0].env
            frag.exit_env  = frag_bars[-1].env
            frag.entry_hb  = frag_bars[0].hb
            frag.exit_hb   = frag_bars[-1].hb

        return fragments

    def _compute_fingerprints(self,
                               fragments: List[Fragment]) -> List[Fragment]:
        """Asigna fingerprints institucionales a cada fragmento."""
        for frag in fragments:
            fp = []
            if frag.fragment_type == "OPENING_DRIVE" and frag.eff_max >= 80:
                fp.append("HIGH_EFF_OPEN")
            if frag.et_bar_count >= 1:
                fp.append("ET_CONFIRMED")
            if frag.hb_rate <= 0.07:
                fp.append("LOW_HB_CLEAN")
            elif frag.hb_rate >= 0.30:
                fp.append("HIGH_HB_CONTAMINATED")
            if frag.ets_max >= 65:
                fp.append("ETS_ACTIVE")
            if frag.ets_max >= 80:
                fp.append("ETS_STRONG")
            if frag.eff_avg >= 50:
                fp.append("SUSTAINED_EFF")
            if frag.gtal_valid_count > 0:
                fp.append("GTAL_VALID")
            if frag.rt_max >= 60:
                fp.append("RT_HIGH")
            if frag.price_range >= 10:
                fp.append("WIDE_RANGE")
            if abs(frag.price_delta) >= 8:
                fp.append("STRONG_MOVE")
            frag.fingerprints = fp
        return fragments

    def _compute_compatibility(self,
                                fragments: List[Fragment]) -> List[Fragment]:
        """
        Determina qué tipos de fragmentos pueden seguir a cada fragmento.
        Regla: compatibilidad basada en eff_exit y regime.
        """
        compat_matrix = {
            "PREMARKET":       ["OPENING_DRIVE", "VOL_RELEASE",
                                "MIDDAY_ROTATION"],
            "OPENING_DRIVE":   ["EARLY_EXPANSION", "VOL_RELEASE",
                                "MIDDAY_ROTATION"],
            "EARLY_EXPANSION": ["MIDDAY_ROTATION", "VOL_RELEASE",
                                "EXHAUSTION"],
            "MIDDAY_ROTATION": ["VOL_RELEASE", "MIDDAY_ROTATION",
                                "POWER_HOUR", "EXHAUSTION"],
            "VOL_RELEASE":     ["MIDDAY_ROTATION", "EARLY_EXPANSION",
                                "EXHAUSTION"],
            "HB_SPIKE_ZONE":   ["MIDDAY_ROTATION", "VOL_RELEASE"],
            "POWER_HOUR":      ["EXHAUSTION", "VOL_RELEASE"],
            "EXHAUSTION":      [],
        }
        for frag in fragments:
            frag.compatible_after  = compat_matrix.get(frag.fragment_type, [])
            frag.compatible_before = [
                t for t, compat in compat_matrix.items()
                if frag.fragment_type in compat
            ]
        return fragments

    def save(self, fragments: List[Fragment],
             date: str) -> str:
        os.makedirs(FRAGMENTS_DIR, exist_ok=True)
        path = os.path.join(FRAGMENTS_DIR, f"{date}_fragments.json")
        data = {
            "session_date":   date,
            "total_fragments": len(fragments),
            "fragment_types":  {
                ft: len([f for f in fragments if f.fragment_type == ft])
                for ft in FRAGMENT_TYPES
            },
            "fragments": [f.to_dict() for f in fragments],
        }
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, ensure_ascii=False)
        return path

    def print_report(self, fragments: List[Fragment], date: str):
        print(f"\n{'='*70}")
        print(f"  FRAGMENT ANALYSIS: {date}  ({len(fragments)} fragments)")
        print(f"{'─'*70}")
        for frag in fragments:
            fp_str = " | ".join(frag.fingerprints[:4]) or "(none)"
            print(f"  [{frag.fragment_id}] {frag.fragment_type:<18} "
                  f"bars {frag.start_bar:3d}-{frag.end_bar:3d} "
                  f"({frag.bar_count:3d}b)  "
                  f"ETS={frag.ets_max:3d}  eff={frag.eff_max:3d}  "
                  f"HB={frag.hb_rate:.0%}  "
                  f"Δ={frag.price_delta:+.2f}")
            if frag.fingerprints:
                print(f"           {fp_str}")
        print(f"{'─'*70}")
        type_summary = {}
        for f in fragments:
            type_summary[f.fragment_type] = type_summary.get(
                f.fragment_type, 0) + 1
        for ftype, cnt in sorted(type_summary.items(),
                                  key=lambda x: -x[1]):
            print(f"  {ftype:<20} {cnt:2d} fragments")
        print(f"{'='*70}\n")


def generate_catalog_report():
    """Resumen de todos los fragmentos en simulation/fragments/."""
    files = sorted(glob.glob(os.path.join(FRAGMENTS_DIR,
                                          "*_fragments.json")))
    if not files:
        print("No hay fragmentos. Corre --all primero.")
        return

    total_frags = 0
    type_totals = {t: 0 for t in FRAGMENT_TYPES}
    fp_totals   = {}
    sessions    = []

    for f in files:
        d = json.load(open(f, encoding="utf-8"))
        date = d["session_date"]
        n    = d["total_fragments"]
        total_frags += n
        for ft, cnt in d["fragment_types"].items():
            type_totals[ft] = type_totals.get(ft, 0) + cnt
        for frag in d["fragments"]:
            for fp in frag.get("fingerprints", []):
                fp_totals[fp] = fp_totals.get(fp, 0) + 1
        sessions.append((date, n))

    print(f"\n{'='*70}")
    print(f"  FRAGMENT CATALOG — {len(files)} sessions  "
          f"{total_frags} total fragments")
    print(f"{'─'*70}")
    for date, n in sessions:
        print(f"  {date}  {n:2d} fragments")
    print(f"\n  FRAGMENT TYPE DISTRIBUTION:")
    for ftype, cnt in sorted(type_totals.items(), key=lambda x: -x[1]):
        if cnt > 0:
            bar = "█" * int(cnt / max(type_totals.values()) * 20)
            print(f"  {ftype:<22} {cnt:4d}  {bar}")
    print(f"\n  FINGERPRINT FREQUENCY:")
    for fp, cnt in sorted(fp_totals.items(), key=lambda x: -x[1]):
        print(f"  {fp:<28} {cnt:4d}")
    print(f"{'='*70}\n")


# ── KNOWN_DATES (mismo que expansion_session_miner) ──────────────
KNOWN_DATES = {
    "2026-05-08_1912": "2026-04-09",
    "2026-05-08_1927": "2026-03-11",
    "2026-05-08_1937": "2026-03-18",
    "2026-05-08_2013": "2026-01-13",
    "2026-05-08_2022": "2026-02-02",
    "2026-05-08_2031": "2026-03-24",
    "2026-05-08_2057": "2026-01-06",
    "2026-05-08_2106": "2026-01-22",
    "2026-05-08_2113": "2026-01-16",
    "2026-05-08_2142": "2026-01-16",
    "2026-05-08_2153": "2026-02-02",
    "2026-05-08_2200": "2026-01-22",
    "2026-05-09_1334": "2025-02-13",
    "2026-05-09_1339": "2025-03-19",
    "2026-05-09_1346": "2025-04-04",
    "2026-05-09_1349": "2025-04-10",
    "2026-05-09_1413": "2025-05-02",
    "2026-05-09_1356": "2025-05-30",
    "2026-05-09_1143": "2026-01-29",
    "2026-05-09_1331": "2026-02-13",
    "2026-05-08_1926": "2026-03-12",
    "2026-05-08_1711": "2026-04-09",
    "2026-05-09_1408": "2026-04-30",
    "2026-05-08_1630": "2026-05-04",
    "2026-05-08_1608": "2026-05-04",
    "2026-05-08_1619": "2026-05-04",
    "2026-05-08_1141": "2026-05-05",
    "2026-05-08_1639": "2026-05-05",
    "2026-05-08_1206": "2026-05-05",
    "2026-05-08_1559": "2026-05-05",
    "2026-05-08_1621": "2026-05-05",
    "2026-05-08_1650": "2026-05-06",
    "2026-05-08_1257": "2026-05-06",
    "2026-05-08_1604": "2026-05-06",
    "2026-05-08_1623": "2026-05-06",
    "2026-05-08_1224": "2026-05-06",
    "2026-05-08_1021": "2026-05-08",
    "2026-05-09_1608": "2026-01-28",
    "2026-05-09_1613": "2026-02-05",
    "2026-05-09_1618": "2026-02-06",
    "2026-05-09_1622": "2026-03-19",
    "2026-05-09_1629": "2026-03-20",
    "2026-05-09_1633": "2025-09-17",
    "2026-05-09_1638": "2025-09-19",
    "2026-05-09_1641": "2025-10-29",
    "2026-05-09_1650": "2025-07-30",
    "2026-05-09_1655": "2026-04-29",
}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GIBBZ V3 — Structural Session Fragmenter")
    parser.add_argument("--date",    type=str, default="",
                        help="Fragmentar sesión específica (YYYY-MM-DD)")
    parser.add_argument("--all",     action="store_true",
                        help="Fragmentar todos los recordings conocidos")
    parser.add_argument("--report",  action="store_true",
                        help="Reporte del catálogo de fragmentos")
    parser.add_argument("--bars",    type=int, default=400)
    args = parser.parse_args()

    os.makedirs(FRAGMENTS_DIR, exist_ok=True)

    fragmenter = StructuralFragmenter()

    if args.report:
        generate_catalog_report()

    elif args.all:
        files = sorted(glob.glob("recordings/*.jsonl"))
        processed = 0
        for f in files:
            base = os.path.basename(f).replace(".jsonl", "")
            date = KNOWN_DATES.get(base)
            if not date:
                continue
            # Evitar duplicados (misma fecha ya procesada)
            out = os.path.join(FRAGMENTS_DIR, f"{date}_fragments.json")
            if os.path.exists(out):
                print(f"  [SKIP] {date} — ya fragmentado")
                continue
            print(f"\n  Fragmenting {base} → {date} ...", flush=True)
            try:
                frags = fragmenter.fragment(f, date,
                                            max_bars=args.bars, silent=True)
                path  = fragmenter.save(frags, date)
                fragmenter.print_report(frags, date)
                print(f"  → {len(frags)} fragments saved: {path}")
                processed += 1
            except Exception as e:
                print(f"  [ERROR] {base}: {e}")

        print(f"\n  Total processed: {processed} sessions")
        generate_catalog_report()

    elif args.date:
        # Buscar recording para esa fecha
        matches = [f for f, d in KNOWN_DATES.items() if d == args.date]
        if not matches:
            print(f"No recording encontrado para {args.date}")
        else:
            recording = f"recordings/{matches[0]}.jsonl"
            print(f"\n  Fragmenting {recording} → {args.date} ...",
                  flush=True)
            frags = fragmenter.fragment(recording, args.date,
                                        max_bars=args.bars)
            fragmenter.print_report(frags, args.date)
            path = fragmenter.save(frags, args.date)
            print(f"  → Saved: {path}")

    else:
        parser.print_help()