"""
GIBBZ V3 — expansion_session_miner.py
Expansion Session Discovery Tool v1.0

Analiza replay sessions buscando:
- Opening drive signatures
- Volatility expansion
- ETIL clusters tempranos
- Delta persistence
- Early regime transition
- GTAL_VALID rarity events
- Low HB contamination

USO:
  # Analizar una sesión
  python expansion_session_miner.py recordings/archivo.jsonl --date 2026-03-11

  # Analizar todas las sesiones disponibles y rankear
  python expansion_session_miner.py --mine-all

  # Solo el score de una sesión (para batch)
  python expansion_session_miner.py recordings/archivo.jsonl --date 2026-03-11 --score-only

NO modifica nada del core. Solo observación.
"""

import json
import os
import sys
import glob
import argparse
from dataclasses import dataclass, field, asdict
from collections import deque
from typing import List, Dict, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── IMPORTS PIPELINE ─────────────────────────────────────────────
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
from adaptive_confidence_gate  import AdaptiveConfidenceGate

EXPANSION_DIR = "expansion_outcomes"


# ── EXPANSION RESULT ─────────────────────────────────────────────

@dataclass
class ExpansionResult:
    session_date:           str   = ""
    recording_file:         str   = ""
    total_bars:             int   = 0

    # ── OPENING DRIVE METRICS (bars 1-30) ────────────────────────
    opening_drive_score:    int   = 0    # 0-100
    od_eff_peak:            int   = 0    # max directional_efficiency in bars 1-30
    od_ets_peak:            int   = 0    # max ETS in bars 1-30
    od_conf_peak:           int   = 0    # max conf in bars 1-30
    od_et_bars:             int   = 0    # EFFICIENT_TREND bars in first 30
    od_first_et_bar:        int   = 0    # first EFFICIENT_TREND bar

    # ── ETIL TIMING METRICS ───────────────────────────────────────
    first_ets65_bar:        int   = 0    # first bar with ETS >= 65
    first_et_bar:           int   = 0    # first bar with env=EFFICIENT_TREND
    etil_to_env_delay:      int   = 999  # bars between ETS65 and ET (999=never)
    ets65_count:            int   = 0    # total bars ETS >= 65
    ets_max:                int   = 0    # max ETS in session
    ets_cluster_max:        int   = 0    # longest consecutive ETS>=50 run

    # ── VOLATILITY EXPANSION ─────────────────────────────────────
    volatility_expansion_score: int = 0  # 0-100
    max_range_bar:          int   = 0    # bar with highest range
    range_expansion_bars:   int   = 0    # bars where range > session avg * 1.5

    # ── DELTA PERSISTENCE ─────────────────────────────────────────
    delta_persistence_score: int  = 0    # 0-100
    consecutive_eff_high:   int   = 0    # longest run of eff >= 70
    eff_max:                int   = 0    # max directional_efficiency
    eff_sustained:          int   = 0    # bars with eff >= 50

    # ── GTAL ALIGNMENT ────────────────────────────────────────────
    gtal_valid_count:       int   = 0
    gtal_alignment_window:  int   = 0    # bars between first ETS65 and first VALID
    hb_rate:                float = 0.0
    hb_count:               int   = 0
    hb_on_ets_bars:         int   = 0    # HB on bars where ETS >= 65

    # ── EARLY EDGE WINDOW ─────────────────────────────────────────
    early_edge_duration:    int   = 0    # bars where ETS>=65 + eff>=50
    early_edge_quality:     str   = "NONE"  # NONE/WEAK/MODERATE/STRONG/ELITE

    # ── MICRO EXPANSION CLUSTERS ──────────────────────────────────
    micro_expansion_clusters: int = 0    # count of ETS65+ bursts separated by gaps
    best_cluster_start:     int   = 0
    best_cluster_ets:       int   = 0

    # ── INSTITUTIONAL ALIGNMENT ───────────────────────────────────
    institutional_alignment_score: int = 0   # 0-100
    a_setup_count:          int   = 0
    gtal_valid_density:     float = 0.0

    # ── COMPOSITE SCORES ──────────────────────────────────────────
    ep_score:               int   = 0    # Edge Potential 0-100
    expansion_probability:  int   = 0    # 0-100
    session_type:           str   = "ROTATIONAL"
    recommendation:         str   = ""

    def to_dict(self) -> dict:
        return asdict(self)

    def summary_line(self) -> str:
        stars = {70:"★★★★",50:"★★★☆",30:"★★☆☆",0:"★☆☆☆"}
        s = next(v for k,v in sorted(stars.items(),reverse=True) if self.ep_score>=k)
        return (f"{self.session_date:<12} EP={self.ep_score:3d}/100 {s}  "
                f"type={self.session_type:<20}  "
                f"OD={self.opening_drive_score:3d}  "
                f"ETS_max={self.ets_max:3d}  "
                f"lag={self.etil_to_env_delay if self.etil_to_env_delay!=999 else 'never':>5}  "
                f"HB={self.hb_rate:.0%}")


# ── MINER ─────────────────────────────────────────────────────────

class ExpansionSessionMiner:
    """
    Analiza un recording completo y extrae métricas de expansión.
    Corre el pipeline completo de GIBBZ V3 en modo observación.
    """

    OPENING_DRIVE_WINDOW = 30    # primeras N barras = "opening drive"
    ETS_ACTIVE           = 65
    ETS_CLUSTER          = 50
    EFF_HIGH             = 70
    EFF_MODERATE         = 50

    def mine(self,
             replay_file:  str,
             replay_date:  str,
             max_bars:     int = 400,
             silent:       bool = False) -> ExpansionResult:

        result = ExpansionResult(
            session_date   = replay_date,
            recording_file = os.path.basename(replay_file),
        )

        # ── SETUP PIPELINE ────────────────────────────────────────
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
        etil_eng     = ETILEngine()
        timing_eng   = TimingEngine()
        decay_eng    = EdgeDecayEngine()
        opp_clf      = OpportunityClassifier()
        gtal_eng     = GTALEngine()
        esl_eng      = ESLEngine()
        pnl_attr     = PNLAttributionLayer()
        port_risk    = PortfolioRiskContextLayer()
        adaptive     = AdaptiveParameterLayer()

        # ── TRACKING ──────────────────────────────────────────────
        bar_count         = 0
        prices            = []
        ets_history       = deque(maxlen=20)
        eff_history       = deque(maxlen=20)
        eff_run           = 0           # consecutive eff >= EFF_HIGH
        eff_run_max       = 0
        ets_run           = 0           # consecutive ETS >= ETS_CLUSTER
        ets_run_max       = 0
        cluster_starts    = []          # (bar, ets) for each ETS65+ burst
        in_cluster        = False
        cluster_bar       = 0
        cluster_ets       = 0
        hb_total          = 0
        hb_on_ets         = 0
        early_edge_bars   = 0
        range_values      = []
        expansion_bars    = 0
        first_gtal_bar    = 0

        with open(replay_file, "r", encoding="utf-8") as f:
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

                # ── CORE ──────────────────────────────────────────
                evt      = event_eng.process(raw)
                ctx_l    = levels.get_context(raw["price"])
                reg_r    = sess_regime.update(raw, evt)
                env_r    = market_env.analyze_environment(raw, evt)
                mp       = ConfluenceResult(
                    event="NONE", zone=ctx_l.zone, confluence="",
                    bias="NEUTRAL", score=50,
                    classification="MEDIUM QUALITY",
                    action="OBSERVE", reason="",
                    hpz_bonus=False, bias_aligned=False, consecutive=0)
                mic_r    = micro.analyze(evt, ctx_l, mp, raw)
                conf_r   = confirmation.analyze(evt, ctx_l, None, mic_r, raw)
                raw["env"]  = env_r.environment
                raw["zone"] = ctx_l.zone
                cont_r   = continuation.analyze(evt, conf_r, reg_r, raw)
                ac_r     = adaptive_cont.analyze_continuation(
                    evt, conf_r, reg_r, env_r, raw)
                poc_r    = poc_engine.analyze(raw, evt, conf_r)
                analysis = conf_eng.evaluate(
                    evt, ctx_l,
                    confirmation=conf_r, session_regime=reg_r,
                    continuation=cont_r, adaptive_continuation=ac_r,
                    market_env=env_r, poc_acceptance=poc_r)
                val      = validator.validate(
                    analysis, evt, raw,
                    confirmation=conf_r, session_regime=reg_r,
                    continuation=cont_r, adaptive_continuation=ac_r,
                    market_env=env_r, poc_acceptance=poc_r)
                risk     = risk_eng.analyze(
                    price=raw["price"], confluence=analysis,
                    validation=val,
                    intent=intent_eng.analyze(evt, ctx_l, analysis, val),
                    level_context=ctx_l)

                # ── V3 ────────────────────────────────────────────
                etil_r   = etil_eng.analyze(env_r, cont_r, conf_r, raw)
                timing_r = timing_eng.analyze(etil_r, conf_r, val, bar_count)
                decay_r  = decay_eng.analyze(env_r, etil_r, bar_count)
                opp_r    = opp_clf.classify(etil_r, timing_r, decay_r,
                                             conf_r, val)
                gtal_r   = gtal_eng.analyze(
                    etil_r, timing_r, decay_r, opp_r,
                    env_r, conf_r, cont_r, bar_count)
                esl_r    = esl_eng.analyze(
                    gtal_r, etil_r, timing_r,
                    env_r, cont_r, raw, bar_count)
                pnl_r    = pnl_attr.analyze(
                    etil_r, gtal_r, timing_r, esl_r, opp_r,
                    conf_r, cont_r, val, bar_count)
                port_r   = port_risk.analyze(
                    env_r, etil_r, gtal_r, opp_r, bar_count)
                adapt_r  = adaptive.analyze(
                    etil_r, gtal_r, conf_r, cont_r,
                    env_r, pnl_r, bar_count)

                # ── EXTRACT VALUES ────────────────────────────────
                env      = getattr(env_r,   "environment",             "ROTATIONAL")
                eff      = getattr(env_r,   "directional_efficiency",  0)
                ets      = getattr(etil_r,  "ets_score",               0)
                conf_sc  = getattr(conf_r,  "confirmation_score",      0)
                hb       = getattr(gtal_r,  "hindsight_bias_flag",     False)
                ev       = getattr(gtal_r,  "execution_validity",      "INVALID")
                opp      = getattr(opp_r,   "grade",                   "NONE")
                price    = raw.get("price", 0)
                high_    = raw.get("high",  price)
                low_     = raw.get("low",   price)
                bar_range= high_ - low_

                prices.append(price)
                range_values.append(bar_range)
                ets_history.append(ets)
                eff_history.append(eff)

                # ── OPENING DRIVE (primeras N barras) ─────────────
                if bar_count <= self.OPENING_DRIVE_WINDOW:
                    result.od_eff_peak  = max(result.od_eff_peak,  eff)
                    result.od_ets_peak  = max(result.od_ets_peak,  ets)
                    result.od_conf_peak = max(result.od_conf_peak, conf_sc)
                    if env == "EFFICIENT_TREND":
                        result.od_et_bars += 1
                        if result.od_first_et_bar == 0:
                            result.od_first_et_bar = bar_count

                # ── ETIL TIMING ───────────────────────────────────
                if ets >= self.ETS_ACTIVE:
                    result.ets65_count += 1
                    if result.first_ets65_bar == 0:
                        result.first_ets65_bar = bar_count
                if env == "EFFICIENT_TREND":
                    if result.first_et_bar == 0:
                        result.first_et_bar = bar_count

                result.ets_max = max(result.ets_max, ets)

                # ── ETS CLUSTER TRACKING ──────────────────────────
                if ets >= self.ETS_CLUSTER:
                    ets_run += 1
                    ets_run_max = max(ets_run_max, ets_run)
                else:
                    ets_run = 0

                # ── ETS65 CLUSTER DETECTION ───────────────────────
                if ets >= self.ETS_ACTIVE:
                    if not in_cluster:
                        in_cluster   = True
                        cluster_bar  = bar_count
                        cluster_ets  = ets
                    else:
                        cluster_ets = max(cluster_ets, ets)
                else:
                    if in_cluster:
                        cluster_starts.append((cluster_bar, cluster_ets))
                        in_cluster = False

                # ── EFF TRACKING ──────────────────────────────────
                result.eff_max = max(result.eff_max, eff)
                if eff >= self.EFF_HIGH:
                    eff_run += 1
                    eff_run_max = max(eff_run_max, eff_run)
                else:
                    eff_run = 0
                if eff >= self.EFF_MODERATE:
                    result.eff_sustained += 1

                # ── HB TRACKING ───────────────────────────────────
                if hb:
                    hb_total += 1
                    if ets >= self.ETS_ACTIVE:
                        hb_on_ets += 1

                # ── GTAL VALID ────────────────────────────────────
                if ev == "VALID":
                    result.gtal_valid_count += 1
                    if first_gtal_bar == 0:
                        first_gtal_bar = bar_count

                # ── A-SETUP ───────────────────────────────────────
                if opp == "A":
                    result.a_setup_count += 1

                # ── EARLY EDGE ────────────────────────────────────
                if ets >= self.ETS_ACTIVE and eff >= self.EFF_MODERATE:
                    early_edge_bars += 1

                # ── RANGE EXPANSION ───────────────────────────────
                if bar_count > 20 and range_values:
                    avg_range = sum(range_values[:-1]) / max(len(range_values)-1, 1)
                    if bar_range > avg_range * 1.5:
                        expansion_bars += 1

                if not silent and bar_count % 50 == 0:
                    print(f"  ... bar {bar_count} | env={env:<16} ETS={ets:3d} eff={eff:3d} "
                          f"conf={conf_sc:3d} ev={ev}", flush=True)

        # Close last cluster
        if in_cluster:
            cluster_starts.append((cluster_bar, cluster_ets))

        result.total_bars = bar_count

        # ── COMPUTE DERIVED METRICS ───────────────────────────────

        # ETIL to env delay
        if result.first_ets65_bar > 0 and result.first_et_bar > 0:
            result.etil_to_env_delay = abs(
                result.first_et_bar - result.first_ets65_bar)
        elif result.first_ets65_bar > 0 and result.first_et_bar == 0:
            result.etil_to_env_delay = 999  # never confirmed

        result.ets_cluster_max  = ets_run_max
        result.consecutive_eff_high = eff_run_max
        result.hb_count         = hb_total
        result.hb_on_ets_bars   = hb_on_ets
        result.hb_rate          = round(hb_total / max(bar_count, 1), 3)
        result.early_edge_duration = early_edge_bars
        result.micro_expansion_clusters = len(cluster_starts)
        result.range_expansion_bars = expansion_bars

        if cluster_starts:
            best = max(cluster_starts, key=lambda x: x[1])
            result.best_cluster_start = best[0]
            result.best_cluster_ets   = best[1]

        if first_gtal_bar > 0 and result.first_ets65_bar > 0:
            result.gtal_alignment_window = abs(
                first_gtal_bar - result.first_ets65_bar)

        result.gtal_valid_density = round(
            result.gtal_valid_count / max(bar_count, 1), 4)

        # ── SCORE COMPUTATION ─────────────────────────────────────
        result = self._compute_scores(result)

        return result

    def _compute_scores(self, r: ExpansionResult) -> ExpansionResult:
        """Calcula todos los scores compuestos."""
        n = max(r.total_bars, 1)

        # ── OPENING DRIVE SCORE (0-100) ───────────────────────────
        od = 0
        if r.od_eff_peak >= 80:      od += 30
        elif r.od_eff_peak >= 60:    od += 20
        elif r.od_eff_peak >= 40:    od += 10
        if r.od_ets_peak >= 65:      od += 25
        elif r.od_ets_peak >= 50:    od += 15
        elif r.od_ets_peak >= 35:    od += 5
        if r.od_et_bars >= 3:        od += 25
        elif r.od_et_bars >= 1:      od += 15
        if r.od_conf_peak >= 65:     od += 20
        elif r.od_conf_peak >= 50:   od += 10
        elif r.od_conf_peak >= 35:   od += 5
        r.opening_drive_score = min(od, 100)

        # ── VOLATILITY EXPANSION SCORE (0-100) ────────────────────
        ve = 0
        ve += min(r.range_expansion_bars * 3, 40)
        if r.ets_cluster_max >= 5:   ve += 30
        elif r.ets_cluster_max >= 3: ve += 20
        elif r.ets_cluster_max >= 2: ve += 10
        if r.ets_max >= 80:          ve += 30
        elif r.ets_max >= 65:        ve += 20
        elif r.ets_max >= 50:        ve += 10
        r.volatility_expansion_score = min(ve, 100)

        # ── DELTA PERSISTENCE SCORE (0-100) ───────────────────────
        dp = 0
        if r.eff_max >= 90:           dp += 30
        elif r.eff_max >= 70:         dp += 20
        elif r.eff_max >= 50:         dp += 10
        dp += min(r.consecutive_eff_high * 5, 30)
        dp += min(r.eff_sustained / max(n,1) * 200, 25)
        if r.ets65_count >= 5:        dp += 15
        elif r.ets65_count >= 2:      dp += 8
        r.delta_persistence_score = min(dp, 100)

        # ── INSTITUTIONAL ALIGNMENT SCORE (0-100) ─────────────────
        ia = 0
        ia += min(r.a_setup_count * 25, 40)
        ia += min(r.gtal_valid_count * 20, 40)
        hb_ok_rate = 1 - r.hb_rate
        ia += int(hb_ok_rate * 20)
        r.institutional_alignment_score = min(ia, 100)

        # ── EARLY EDGE QUALITY ────────────────────────────────────
        if r.early_edge_duration >= 5:     r.early_edge_quality = "ELITE"
        elif r.early_edge_duration >= 3:   r.early_edge_quality = "STRONG"
        elif r.early_edge_duration >= 2:   r.early_edge_quality = "MODERATE"
        elif r.early_edge_duration >= 1:   r.early_edge_quality = "WEAK"
        else:                              r.early_edge_quality = "NONE"

        # ── EXPANSION PROBABILITY (0-100) ────────────────────────
        ep = 0
        if r.od_et_bars > 0:              ep += 20
        if r.od_first_et_bar > 0 and r.od_first_et_bar <= 10: ep += 15
        if r.ets_max >= 65:               ep += 20
        if r.ets65_count >= 2:            ep += 10
        if r.micro_expansion_clusters >= 2: ep += 10
        if r.etil_to_env_delay != 999:    ep += 10
        if r.etil_to_env_delay != 999 and r.etil_to_env_delay <= 5: ep += 5
        if r.gtal_valid_count > 0:        ep += 10
        r.expansion_probability = min(ep, 100)

        # ── EP SCORE — EDGE POTENTIAL (0-100) ────────────────────
        ep_score = int(
            r.opening_drive_score      * 0.30 +
            r.volatility_expansion_score * 0.20 +
            r.delta_persistence_score  * 0.20 +
            r.institutional_alignment_score * 0.15 +
            r.expansion_probability    * 0.15
        )
        r.ep_score = max(0, min(ep_score, 100))

        # ── SESSION TYPE ──────────────────────────────────────────
        if r.od_et_bars >= 3 and r.ets_max >= 65:
            r.session_type = "OPENING_DRIVE"
        elif r.od_et_bars >= 1 and r.od_first_et_bar <= 10:
            r.session_type = "EARLY_EXPANSION"
        elif r.ets_max >= 80 and r.micro_expansion_clusters >= 2:
            r.session_type = "EXPANSION"
        elif r.ets_max >= 65 and r.gtal_valid_count > 0:
            r.session_type = "INSTITUTIONAL_SIGNAL"
        elif r.ets_max >= 65:
            r.session_type = "VOL_RELEASE"
        elif r.ets_max >= 50:
            r.session_type = "WATCH"
        else:
            r.session_type = "ROTATIONAL"

        # ── RECOMMENDATION ────────────────────────────────────────
        if r.ep_score >= 70:
            r.recommendation = "ELITE — análisis profundo requerido"
        elif r.ep_score >= 50:
            r.recommendation = "HIGH — candidata para Outcome Engine"
        elif r.ep_score >= 30:
            r.recommendation = "MED — monitorear en vivo"
        else:
            r.recommendation = "LOW — sesión rotacional normal"

        return r

    def save(self, result: ExpansionResult) -> str:
        os.makedirs(EXPANSION_DIR, exist_ok=True)
        path = os.path.join(
            EXPANSION_DIR,
            f"{result.session_date}_expansion.json"
        )
        with open(path, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, indent=2, ensure_ascii=False)
        return path

    def print_report(self, r: ExpansionResult):
        print(f"\n{'='*80}")
        print(f"  EXPANSION ANALYSIS: {r.session_date}  ({r.recording_file})")
        print(f"{'─'*80}")
        print(f"  session_type:          {r.session_type}")
        print(f"  recommendation:        {r.recommendation}")
        print(f"  ep_score:              {r.ep_score}/100")
        print(f"  expansion_probability: {r.expansion_probability}/100")
        print()
        print(f"  ── OPENING DRIVE (bars 1-{self.OPENING_DRIVE_WINDOW}) ──────")
        print(f"  opening_drive_score:   {r.opening_drive_score}/100")
        print(f"  od_eff_peak:           {r.od_eff_peak}")
        print(f"  od_ets_peak:           {r.od_ets_peak}")
        print(f"  od_conf_peak:          {r.od_conf_peak}")
        print(f"  od_et_bars:            {r.od_et_bars}")
        print(f"  od_first_et_bar:       {r.od_first_et_bar or '(none)'}")
        print()
        print(f"  ── ETIL TIMING ──────────────────────────────────")
        print(f"  first_ets65_bar:       {r.first_ets65_bar or '(never)'}")
        print(f"  first_et_bar:          {r.first_et_bar or '(never)'}")
        delay_str = str(r.etil_to_env_delay) if r.etil_to_env_delay != 999 else "NEVER confirmed"
        print(f"  etil_to_env_delay:     {delay_str}")
        print(f"  ets65_count:           {r.ets65_count}")
        print(f"  ets_max:               {r.ets_max}")
        print(f"  ets_cluster_max:       {r.ets_cluster_max} consecutive bars ETS>=50")
        print(f"  micro_expansion_clusters: {r.micro_expansion_clusters}")
        if r.best_cluster_start > 0:
            print(f"  best_cluster:          bar {r.best_cluster_start} (ETS={r.best_cluster_ets})")
        print()
        print(f"  ── DELTA PERSISTENCE ────────────────────────────")
        print(f"  delta_persistence_score: {r.delta_persistence_score}/100")
        print(f"  eff_max:               {r.eff_max}")
        print(f"  consecutive_eff_high:  {r.consecutive_eff_high} bars eff>={self.EFF_HIGH}")
        print(f"  eff_sustained:         {r.eff_sustained} bars eff>={self.EFF_MODERATE}")
        print()
        print(f"  ── VOLATILITY EXPANSION ─────────────────────────")
        print(f"  volatility_expansion_score: {r.volatility_expansion_score}/100")
        print(f"  range_expansion_bars:  {r.range_expansion_bars}")
        print()
        print(f"  ── GTAL ALIGNMENT ───────────────────────────────")
        print(f"  gtal_valid_count:      {r.gtal_valid_count}")
        print(f"  hb_rate:               {r.hb_rate:.1%}")
        print(f"  hb_on_ets_bars:        {r.hb_on_ets_bars}")
        print(f"  early_edge_duration:   {r.early_edge_duration} bars")
        print(f"  early_edge_quality:    {r.early_edge_quality}")
        print(f"{'='*80}\n")


# ── MINE ALL ──────────────────────────────────────────────────────

def mine_all_recordings(recordings_dir: str = "recordings",
                         max_bars: int = 400) -> List[ExpansionResult]:
    """
    Analiza todos los recordings disponibles y los rankea.
    Usa los contextos históricos existentes cuando sea posible.
    """

    # Mapeo recording → date (basado en los archivos conocidos)
    KNOWN_DATES = {
        # 2026 originales
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
        # 2025 sessions
        "2026-05-09_1334": "2025-02-13",
        "2026-05-09_1339": "2025-03-19",
        "2026-05-09_1346": "2025-04-04",
        "2026-05-09_1349": "2025-04-10",
        "2026-05-09_1413": "2025-05-02",
        "2026-05-09_1356": "2025-05-30",
        # 2026 nuevos
        "2026-05-09_1143": "2026-01-29",
        "2026-05-09_1331": "2026-02-13",
        "2026-05-08_1926": "2026-03-12",
        "2026-05-08_1711": "2026-04-09",
        "2026-05-09_1408": "2026-04-30",
        # Mayo 2026
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
        # Elite expansion targets
        "2026-05-09_1608": "2026-01-28",
        "2026-05-09_1613": "2026-02-05",
        "2026-05-09_1618": "2026-02-06",
        "2026-05-09_1622": "2026-03-19",
        "2026-05-09_1629": "2026-03-20",
        "2026-05-09_1633": "2025-09-17",
        "2026-05-09_1638": "2025-09-19",
        "2026-05-09_1641": "2025-10-29",
        "2026-05-09_1650": "2025-07-29",  # corrected: first tick is 2025-07-29 08:05 ET
        "2026-05-09_1655": "2026-04-29",
        # 2026-05-10 batch 1 (mañana temprano)
        "2026-05-10_0609": "2026-03-23",
        "2026-05-10_0614": "2025-05-30",
        "2026-05-10_0618": "2026-01-28",
        "2026-05-10_0631": "2026-03-23",
        "2026-05-10_0637": "2025-05-30",
        "2026-05-10_0641": "2026-01-28",
        # 2026-05-10 batch 2 (históricos 8 sesiones)
        "2026-05-10_1032": "2026-03-19",
        "2026-05-10_1037": "2025-02-13",
        "2026-05-10_1039": "2026-01-28",
        "2026-05-10_1045": "2026-04-30",
        "2026-05-10_1047": "2025-05-30",
        "2026-05-10_1051": "2024-12-18",
        "2026-05-10_1055": "2025-03-19",
        "2026-05-10_1100": "2024-04-10",
        # 2026-05-10 batch 3 (sesiones nuevas)
        "2026-05-10_1147": "2025-02-06",
        "2026-05-10_1159": "2026-06-17",
        "2026-05-10_1207": "2025-06-11",
        "2026-05-10_1212": "2024-09-18",
        "2026-05-10_1216": "2026-03-18",
        "2026-05-10_1221": "2025-03-19",
        "2026-05-10_1225": "2024-12-18",
        # 2026-05-11 nuevas sesiones RTH-catalyst
        "2026-05-11_2214": "2024-11-06",
        "2026-05-11_2221": "2025-04-03",
        "2026-05-11_2227": "2025-04-09",
        # 2026-05-11 sesiones nuevas identificadas
        "2026-05-11_1516": "2026-02-13",
        "2026-05-11_1729": "2025-07-30",
        # 2026-05-11 re-grabaciones RTH completas
        "2026-05-11_2322": "2024-08-22",
        "2026-05-11_2325": "2024-09-18",
        "2026-05-11_2329": "2025-07-30",
        # 2026-05-11 batch adicional (identificados por timestamp)
        "2026-05-11_1548": "2025-09-18",
        "2026-05-11_1556": "2025-01-17",
        "2026-05-11_1606": "2026-01-23",
        "2026-05-11_1609": "2024-10-04",
        "2026-05-11_1613": "2024-10-04",
        "2026-05-11_1616": "2026-05-06",
        "2026-05-11_1630": "2026-05-06",
        "2026-05-11_1651": "2026-01-22",
        "2026-05-11_1654": "2026-01-16",
        "2026-05-11_1709": "2024-11-07",
        "2026-05-11_1716": "2024-08-22",
        "2026-05-11_1723": "2025-01-29",
        "2026-05-11_1735": "2024-12-18",
    }

    files = sorted(glob.glob(os.path.join(recordings_dir, "*.jsonl")))
    if not files:
        print(f"No se encontraron .jsonl en {recordings_dir}/")
        return []

    miner   = ExpansionSessionMiner()
    results = []

    for f in files:
        base = os.path.basename(f).replace(".jsonl", "")
        date = KNOWN_DATES.get(base)
        if not date:
            print(f"  [SKIP] {base} — fecha desconocida (agregar a KNOWN_DATES)")
            continue

        print(f"\n  Mining {base} → {date} ...", flush=True)
        try:
            r = miner.mine(f, date, max_bars=max_bars, silent=True)
            miner.save(r)
            results.append(r)
            print(f"  → EP={r.ep_score:3d}  type={r.session_type}  "
                  f"ETS_max={r.ets_max}  OD={r.opening_drive_score}")
        except Exception as e:
            print(f"  [ERROR] {base}: {e}")

    return results


def print_ranking(results: List[ExpansionResult]):
    """Ranking consolidado de todas las sesiones."""
    ranked = sorted(results, key=lambda r: -r.ep_score)

    print(f"\n{'='*100}")
    print(f"  EXPANSION SESSION RANKING — {len(results)} sesiones")
    print(f"{'─'*100}")
    print(f"  {'DATE':<12} {'EP':>6} {'TYPE':<22} {'OD':>4} "
          f"{'VE':>4} {'DP':>4} {'IA':>4} "
          f"{'ETS_max':>8} {'lag':>6} {'clusters':>9} {'HB%':>5}")
    print(f"  {'─'*98}")

    for r in ranked:
        stars = "★★★★" if r.ep_score>=70 else "★★★☆" if r.ep_score>=50 else "★★☆☆" if r.ep_score>=30 else "★☆☆☆"
        lag = str(r.etil_to_env_delay) if r.etil_to_env_delay != 999 else "never"
        print(f"  {r.session_date:<12} {r.ep_score:>4}/100 {stars} "
              f"{r.session_type:<22} "
              f"{r.opening_drive_score:>4} "
              f"{r.volatility_expansion_score:>4} "
              f"{r.delta_persistence_score:>4} "
              f"{r.institutional_alignment_score:>4} "
              f"{r.ets_max:>8} "
              f"{lag:>6} "
              f"{r.micro_expansion_clusters:>9} "
              f"{r.hb_rate:>4.0%}")

    print(f"\n  {'─'*98}")
    elite = [r for r in ranked if r.ep_score >= 70]
    high  = [r for r in ranked if 50 <= r.ep_score < 70]
    print(f"  ELITE (EP>=70): {len(elite)}  |  HIGH (EP>=50): {len(high)}  |  "
          f"Total sesiones: {len(results)}")

    print(f"\n  TOP 5 CANDIDATAS PARA OUTCOME ENGINE:")
    for i, r in enumerate(ranked[:5], 1):
        print(f"  #{i}  {r.session_date}  EP={r.ep_score}  {r.session_type}  "
              f"→  {r.recommendation}")
    print(f"{'='*100}\n")


# ── MAIN ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GIBBZ V3 — Expansion Session Miner")
    parser.add_argument("file",     nargs="?",
                        help="Recording .jsonl a analizar")
    parser.add_argument("--date",   type=str, default="",
                        help="Fecha del replay (YYYY-MM-DD)")
    parser.add_argument("--bars",   type=int, default=400,
                        help="Máx barras a analizar")
    parser.add_argument("--mine-all", action="store_true",
                        help="Analizar todos los recordings disponibles")
    parser.add_argument("--score-only", action="store_true",
                        help="Solo mostrar el score (sin detalle)")
    parser.add_argument("--save",   action="store_true",
                        help="Guardar JSON en expansion_outcomes/")
    args = parser.parse_args()

    miner = ExpansionSessionMiner()

    if args.mine_all:
        print("\nMining todos los recordings...")
        results = mine_all_recordings(max_bars=args.bars)
        if results:
            print_ranking(results)
    elif args.file and args.date:
        silent = args.score_only
        r = miner.mine(args.file, args.date,
                       max_bars=args.bars, silent=silent)
        if args.score_only:
            print(r.summary_line())
        else:
            miner.print_report(r)
        if args.save:
            path = miner.save(r)
            print(f"  Guardado: {path}")
    else:
        parser.print_help()