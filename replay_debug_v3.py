"""
GIBBZ V3 Replay Debug — Pipeline institucional completo + ACG + Outcome Engine
ETIL → Timing → Edge → Opportunity → GTAL → ESL →
PNL → PortRisk → Adaptive → ACG → OutcomeEngine → Logger

USO:
  python replay_debug_v3.py recordings/archivo.jsonl --date 2026-02-02 --bars 200
  python replay_debug_v3.py recordings/archivo.jsonl --date 2026-02-02 --bars 200 --save-outcomes
"""

import json
import os
import sys
import argparse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
from gibbz_or_timer            import ORTimer
from gibbz_vwap                import VWAPEngine
from gibbz_bounce_detector     import BounceDetector
from gibbz_va_rule80           import VA80Detector
from gibbz_failed_auction      import FADetector
from gibbz_gap_fill            import GapFillDetector
from gibbz_poc_magnet          import POCMagnetDetector
from gibbz_setup_router        import SetupRouter
from learning_engine           import LearningEngine
from historical_context_loader import HistoricalContextLoader
from bar_aggregator            import BarAggregator

from gibbz_etil        import ETILEngine
from gibbz_timing      import TimingEngine
from gibbz_edge_decay  import EdgeDecayEngine
from gibbz_opportunity import OpportunityClassifier
from gibbz_gtal        import GTALEngine
from gibbz_esl         import ESLEngine
from gibbz_logger_v3   import GIBBZLoggerV3

from pnl_attribution_layer        import PNLAttributionLayer
from portfolio_risk_context_layer import PortfolioRiskContextLayer
from adaptive_parameter_layer     import AdaptiveParameterLayer
from adaptive_confidence_gate     import AdaptiveConfidenceGate
from outcome_engine               import OutcomeEngine

G="\033[92m"; R="\033[91m"; Y="\033[93m"; B="\033[94m"
C="\033[96m"; W="\033[97m"; RST="\033[0m"; BOLD="\033[1m"
M="\033[95m"


def run_debug_v3(replay_file: str, replay_date: str,
                 max_bars: int = 200, skip_bars: int = 0,
                 save_outcomes: bool = False):

    loader = HistoricalContextLoader()
    ctx    = loader.load(replay_date)
    VAH, POC, VAL = ctx.vah, ctx.poc, ctx.val
    IBH, IBL      = ctx.ibh, ctx.ibl
    _ibh_eq_vah   = IBH > 0 and abs(IBH - VAH) <= 2.0
    if _ibh_eq_vah:
        _open = ctx.open_price
        ibh_setup = "SETUP_COMPLETO" if (_open > 0 and abs(_open - VAH) <= 30.0) else "OPEN_FAR"
    else:
        ibh_setup = ""

    # ── CORE ENGINES ─────────────────────────────────────────────
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
    levels       = create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0, ibh=IBH, ibl=IBL)
    aggregator   = BarAggregator(mode="TICK", ticks=500)

    # ── V3 LAYERS ─────────────────────────────────────────────────
    etil       = ETILEngine()
    timing     = TimingEngine()
    edge_decay = EdgeDecayEngine()
    opp_clf    = OpportunityClassifier()
    gtal       = GTALEngine()
    esl        = ESLEngine()
    pnl_attr   = PNLAttributionLayer()
    port_risk  = PortfolioRiskContextLayer()
    adaptive   = AdaptiveParameterLayer()
    acg        = AdaptiveConfidenceGate()
    outcome    = OutcomeEngine(session_date=replay_date)
    logger_v3  = GIBBZLoggerV3()
    or_timer    = ORTimer()
    vwap_engine = VWAPEngine()
    bounce_det  = BounceDetector()
    va80_det    = VA80Detector(vah=VAH, val=VAL, open_price=ctx.open_price)
    fa_det      = FADetector(vah=VAH, val=VAL)
    gap_fill    = GapFillDetector(open_price=ctx.open_price,
                                  prev_close=ctx.prev_close)
    poc_magnet  = POCMagnetDetector(poc=POC)
    setup_router = SetupRouter()

    bar_count        = 0
    tick_count       = 0
    or_complete_count = 0

    print(f"\n{BOLD}{B}{'='*120}{RST}")
    print(f"{BOLD}{W}  GIBBZ V3 + ACG + OUTCOME ENGINE — {replay_date} — barras {skip_bars+1} a {skip_bars+max_bars}{RST}")
    print(f"{BOLD}{B}{'='*120}{RST}\n")
    ibh_flag = f" {G}* IBH=VAH ({ibh_setup}){RST}" if ibh_setup else ""
    print(f"  Levels: VAH={VAH} POC={POC} VAL={VAL} | "
          f"IBH={IBH if IBH else '--'} IBL={IBL if IBL else '--'}{ibh_flag}\n")

    with open(replay_file, "r", encoding="utf-8") as f:
        for line in f:
            if bar_count > skip_bars + max_bars:
                break
            line = line.strip()
            if not line:
                continue
            try:
                tick = json.loads(line)
            except Exception:
                continue

            tick_count += 1
            last_ts = tick.get("timestamp", 0)
            bar = aggregator.process(tick)
            if bar is None:
                continue

            bar_count += 1
            if bar_count <= skip_bars:
                continue
            raw = bar
            raw["timestamp"] = last_ts

            # ── CORE PIPELINE ─────────────────────────────────────
            result   = event_eng.process(raw)
            context  = levels.get_context(raw["price"])
            regime_r = sess_regime.update(raw, result)
            env_r    = market_env.analyze_environment(raw, result)

            mp = ConfluenceResult(
                event="NONE", zone=context.zone, confluence="",
                bias="NEUTRAL", score=50, classification="MEDIUM QUALITY",
                action="OBSERVE", reason="", hpz_bonus=False,
                bias_aligned=False, consecutive=0,
            )
            micro_r  = micro.analyze(result, context, mp, raw)
            conf_r   = confirmation.analyze(result, context, None, micro_r, raw)
            raw["env"]  = env_r.environment
            raw["zone"] = context.zone
            cont_r   = continuation.analyze(result, conf_r, regime_r, raw)
            ac_r     = adaptive_cont.analyze_continuation(
                result, conf_r, regime_r, env_r, raw)
            poc_r    = poc_engine.analyze(raw, result, conf_r)
            analysis = conf_eng.evaluate(
                result, context,
                confirmation=conf_r, session_regime=regime_r,
                continuation=cont_r, adaptive_continuation=ac_r,
                market_env=env_r, poc_acceptance=poc_r,
            )
            validation = validator.validate(
                analysis, result, raw,
                confirmation=conf_r, session_regime=regime_r,
                continuation=cont_r, adaptive_continuation=ac_r,
                market_env=env_r, poc_acceptance=poc_r,
            )
            risk = risk_eng.analyze(
                price=raw["price"], confluence=analysis,
                validation=validation, intent=intent_eng.analyze(
                    result, context, analysis, validation),
                level_context=context,
            )

            # ── V3 PIPELINE ───────────────────────────────────────
            etil_r   = etil.analyze(env_r, cont_r, conf_r, raw)
            timing_r = timing.analyze(etil_r, conf_r, validation, bar_count)
            decay_r  = edge_decay.analyze(env_r, etil_r, bar_count)
            opp_r    = opp_clf.classify(etil_r, timing_r, decay_r,
                                        conf_r, validation)
            gtal_r   = gtal.analyze(
                etil_r, timing_r, decay_r, opp_r,
                env_r, conf_r, cont_r, bar_count)
            esl_r    = esl.analyze(
                gtal_r, etil_r, timing_r,
                env_r, cont_r, raw, bar_count)
            pnl_r    = pnl_attr.analyze(
                etil_r, gtal_r, timing_r, esl_r, opp_r,
                conf_r, cont_r, validation, bar_count)
            port_r   = port_risk.analyze(
                env_r, etil_r, gtal_r, opp_r, bar_count)
            adapt_r  = adaptive.analyze(
                etil_r, gtal_r, conf_r, cont_r,
                env_r, pnl_r, bar_count)
            acg_r    = acg.analyze(
                env_r, conf_r, cont_r, etil_r, gtal_r,
                port_r, adapt_r, raw, bar_count)

            if getattr(acg_r, "relaxed_mode_active", False):
                acg.register_relaxed_outcome(
                    gtal_valid=(getattr(gtal_r, "execution_validity", "INVALID") == "VALID"),
                    hb=getattr(gtal_r, "hindsight_bias_flag", False)
                )

            # ── OUTCOME ENGINE ────────────────────────────────────
            outcome.observe(
                bar_count, raw, env_r, etil_r, gtal_r,
                conf_r, cont_r, opp_r, esl_r, pnl_r,
                port_r, adapt_r, acg_r, validation, analysis
            )

            or_r = or_timer.update(raw)
            if or_r.or_complete:
                or_complete_count += 1

            vwap_r = vwap_engine.update(raw)
            if vwap_r.vwap > 0:
                levels.set_vwap(vwap_r.vwap)

            bounce_r = bounce_det.update(context, result, etil_r)
            va80_r   = va80_det.update(raw["price"])
            fa_r     = fa_det.update(raw["price"], raw.get("delta", 0))
            gap_r    = gap_fill.update(raw["price"])
            poc_r    = poc_magnet.update(raw["price"])

            setup_r  = setup_router.route(
                bar_count=bar_count,
                price=raw["price"], vah=VAH, val=VAL, poc=POC,
                open_price=ctx.open_price,
                gtal_r=gtal_r, risk=risk, env_r=env_r,
                or_r=or_r, ibh_setup=ibh_setup,
                fa_r=fa_r, va80_r=va80_r, vwap_r=vwap_r,
                gap_r=gap_r, poc_r=poc_r, bounce_r=bounce_r,
            )

            # ── OUTPUT ────────────────────────────────────────────
            _print_bar(
                bar_count, raw, env_r, conf_r, cont_r,
                etil_r, timing_r, decay_r, opp_r,
                gtal_r, esl_r, pnl_r, port_r, adapt_r, acg_r,
                analysis, validation, risk,
                or_r, or_complete_count, ibh_setup,
                vwap_r, bounce_r, va80_r, fa_r, gap_r, poc_r, setup_r,
                G, R, Y, W, M, C, B, RST, BOLD
            )

            logger_v3.log(bar_count, raw, env_r, conf_r, cont_r,
                          etil_r, timing_r, decay_r, opp_r,
                          analysis, validation)

    # ── SESSION SUMMARIES ─────────────────────────────────────────
    _print_summaries(
        logger_v3, pnl_attr, port_risk, adaptive, acg, outcome,
        tick_count, bar_count, G, Y, R, W, B, C, M, RST, BOLD
    )

    # Guardar JSON si se solicita
    if save_outcomes:
        path = outcome.save(replay_date)
        print(f"\n{G}  ✅ Observation guardada: {path}{RST}\n")


def _print_bar(bar_count, raw, env_r, conf_r, cont_r,
               etil_r, timing_r, decay_r, opp_r,
               gtal_r, esl_r, pnl_r, port_r, adapt_r, acg_r,
               analysis, validation, risk,
               or_r, or_complete_count, ibh_setup,
               vwap_r, bounce_r, va80_r, fa_r, gap_r, poc_r, setup_r,
               G, R, Y, W, M, C, B, RST, BOLD):

    eff       = getattr(env_r,    "directional_efficiency",  0)
    trap      = getattr(env_r,    "trap_density",            0)
    env_name  = getattr(env_r,    "environment",             "?")
    sc        = getattr(analysis, "score",                   0)
    conf_sc   = getattr(conf_r,   "confirmation_score",      0)
    ets       = getattr(etil_r,   "ets_score",               0)
    ets_class = getattr(etil_r,   "classification",          "NOISE")
    opp_grade = getattr(opp_r,    "grade",                   "NONE")
    rt_score  = getattr(gtal_r,   "real_tradeability_score", 0)
    hb_flag   = getattr(gtal_r,   "hindsight_bias_flag",     False)
    ev        = getattr(gtal_r,   "execution_validity",      "INVALID")
    bias_src  = getattr(gtal_r,   "bias_source",             "")
    val_ok    = getattr(validation, "validated",             False)
    val_reason= getattr(validation, "reason",                "")
    risk_ok   = risk.approved

    esl_active = getattr(esl_r, "active",                   False)
    esl_score  = getattr(esl_r, "final_executable_score",   0)
    fill       = getattr(esl_r, "fill_likelihood",          "LOW")
    slip       = getattr(esl_r, "slippage_estimate",        0.0)
    lat        = getattr(esl_r, "latency_risk",             "HIGH")

    edge_eff   = getattr(pnl_r, "edge_efficiency_score",    0)
    false_edge = getattr(pnl_r, "false_edge_detection_flag", False)
    pnl_summ   = getattr(pnl_r, "attribution_summary",      "")

    prs        = getattr(port_r, "prs",                     0)
    overlap    = getattr(port_r, "setup_overlap_warning",   False)
    toxic_acc  = getattr(port_r, "toxic_accumulation",      False)
    risk_note  = getattr(port_r, "risk_note",               "")

    stability   = getattr(adapt_r, "system_stability_score",  0)
    ets_drift   = getattr(adapt_r, "ets_drift",               "LOW")
    conf_drift  = getattr(adapt_r, "conf_drift",              "LOW")
    decay_shift = getattr(adapt_r, "decay_shift",             "NO")
    filt_qual   = getattr(adapt_r, "filtering_quality",       "OK")
    micro_or    = getattr(adapt_r, "micro_over_rejection",    False)
    micro_str   = getattr(adapt_r, "micro_signal_strength",   0)

    acg_relaxed = getattr(acg_r, "relaxed_mode_active",      False)
    acg_thresh  = getattr(acg_r, "effective_conf_threshold",  65)
    acg_locked  = getattr(acg_r, "session_locked", False) or \
                  getattr(acg_r, "safety_lock",    False)
    acg_reject  = getattr(acg_r, "rejection_reason",         "")
    acg_change  = getattr(acg_r, "would_change_outcome",     False)

    opp_c  = G if opp_grade == "A" else \
             Y if opp_grade == "B" else \
             C if opp_grade == "C" else W
    gtal_c = G if ev == "VALID" else M if hb_flag else R
    esl_c  = G if (esl_active and esl_score >= 70) else \
             Y if (esl_active and esl_score >= 50) else R
    prs_c  = G if prs <= 30 else Y if prs <= 60 else R
    hb_str = f"{M}HB=T{RST} " if hb_flag else "HB=F "

    if esl_active:
        esl_str = (f"{esl_c}ESL={esl_score:3d} "
                   f"Fill={fill:<6} Slip={slip:.2f}{RST}")
    else:
        esl_str = f"{W}ESL=SKIP{RST}"

    if acg_relaxed:
        acg_flag = f" {G}[ACG_RELAX c={acg_thresh}]{RST}"
        if acg_change:
            acg_flag += f" {G}[WOULD_CHANGE]{RST}"
    elif acg_locked:
        acg_flag = f" {M}[ACG_LOCK]{RST}"
    else:
        acg_flag = ""

    or_high = getattr(or_r, "or_high",     0.0)
    or_low  = getattr(or_r, "or_low",      0.0)
    or_min  = getattr(or_r, "elapsed_min", -1.0)
    or_in   = getattr(or_r, "in_or",       False)
    or_done = getattr(or_r, "or_complete", False)

    if or_in:
        or_flag = f" {C}[OR+{or_min:.0f}m H={or_high:.2f} L={or_low:.2f}]{RST}"
    elif or_done and or_complete_count <= 3:
        if ibh_setup:
            or_flag = f" {G}[OR_DONE IBH=VAH({ibh_setup})]{RST}"
        else:
            or_flag = f" {W}[OR_DONE]{RST}"
    else:
        or_flag = ""

    vwap_price = getattr(vwap_r, "vwap",         0.0)
    vwap_state = getattr(vwap_r, "state",         "NO_DATA")
    vwap_rcl   = getattr(vwap_r, "reclaim",       False)
    vwap_bars  = getattr(vwap_r, "reclaim_bars",  0)

    flags = ""
    if false_edge:                    flags += f" {Y}[FE]{RST}"
    if overlap:                       flags += f" {Y}[OVL]{RST}"
    if toxic_acc:                     flags += f" {R}[TOX]{RST}"
    if micro_or:                      flags += f" {C}[MICRO_OR str={micro_str}]{RST}"
    if ets_drift   in ("MED","HIGH"): flags += f" {M}[ETS_D={ets_drift}]{RST}"
    if conf_drift  in ("MED","HIGH"): flags += f" {M}[CONF_D={conf_drift}]{RST}"
    if decay_shift == "YES":          flags += f" {M}[DECAY_SHIFT]{RST}"
    if filt_qual   != "OK":           flags += f" {Y}[{filt_qual}]{RST}"
    if vwap_rcl:
        flags += f" {G}[VWAP_RCL={vwap_bars}]{RST}"
    elif vwap_state == "AT_VWAP" and vwap_price > 0:
        flags += f" {C}[AT_VWAP={vwap_price:.2f}]{RST}"

    bounce_sig = getattr(bounce_r, "signal", "NONE")
    if bounce_sig == "BOUNCE_VAL_CONFIRMED":
        flags += f" {G}[BOUNCE_VAL]{RST}"
    elif bounce_sig == "BOUNCE_VAH_CONFIRMED":
        flags += f" {R}[BOUNCE_VAH]{RST}"

    va80_sig    = getattr(va80_r, "signal", "NONE")
    va80_target = getattr(va80_r, "target",  0.0)
    if va80_sig == "VA_RULE80_LONG":
        flags += f" {C}[VA80_L→{va80_target:.0f}]{RST}"
    elif va80_sig == "VA_RULE80_SHORT":
        flags += f" {C}[VA80_S→{va80_target:.0f}]{RST}"

    fa_sig = getattr(fa_r, "signal", "NONE")
    if fa_sig == "FAILED_AUCTION_LONG":
        flags += f" {G}[FA_LONG]{RST}"
    elif fa_sig == "FAILED_AUCTION_SHORT":
        flags += f" {R}[FA_SHORT]{RST}"

    gap_sig = getattr(gap_r, "signal", "NO_GAP")
    if gap_sig == "GAP_UP_ACTIVE":
        flags += f" {Y}[GAP_UP→fill]{RST}"
    elif gap_sig == "GAP_DOWN_ACTIVE":
        flags += f" {Y}[GAP_DN→fill]{RST}"
    elif gap_sig == "GAP_FILL_COMPLETE":
        flags += f" {G}[GAP_FILLED]{RST}"

    poc_sig = getattr(poc_r, "signal", "NO_SIGNAL")
    if poc_sig == "POC_MAGNET_LONG":
        flags += f" {M}[POC_MAG_L]{RST}"
    elif poc_sig == "POC_MAGNET_SHORT":
        flags += f" {M}[POC_MAG_S]{RST}"
    elif poc_sig == "POC_REACHED":
        flags += f" {M}[POC_REACHED]{RST}"

    setup_type = getattr(setup_r, "signal_type", "NO_SETUP")
    if setup_type != "NO_SETUP":
        s_dir  = getattr(setup_r, "direction",  "NEUTRAL")
        s_conf = getattr(setup_r, "confidence", 0)
        s_stop = getattr(setup_r, "stop_pts",   0.0)
        s_tgt  = getattr(setup_r, "target_pts", 0.0)
        s_why  = getattr(setup_r, "reason",     "")
        s_col  = G if s_conf >= 80 else Y if s_conf >= 65 else C
        flags += (f" {BOLD}{s_col}[SETUP:{setup_type} {s_dir}"
                  f" conf={s_conf} stp={s_stop:.1f} tgt={s_tgt:.1f}]{RST}")

    print(
        f"  Bar {bar_count:4d} | "
        f"P={raw['price']:8.2f} | "
        f"env={env_name:<16} eff={eff:3d} trap={trap:3d} | "
        f"ETS={ets:3d}[{ets_class:<11}] conf={conf_sc:3d} | "
        f"{opp_c}OPP={opp_grade}{RST} "
        f"{gtal_c}RT={rt_score:3d} {hb_str}EV={ev}{RST} | "
        f"{esl_str} | "
        f"EdgeEff={edge_eff:3d} "
        f"{prs_c}PRS={prs:3d}{RST} "
        f"Stab={stability:3d}"
        f"{acg_flag}{flags}{or_flag}"
    )

    if setup_type != "NO_SETUP" and s_why:
        print(f"         {s_col}↳ ROUTER: {s_why} | stop={s_stop:.1f}pts tgt={s_tgt:.1f}pts{RST}")
    if vwap_rcl:
        print(f"         {G}-> VWAP_RECLAIM: above VWAP={vwap_price:.2f} x{vwap_bars}bars{RST}")
    if not val_ok and sc > 0:
        print(f"         {Y}-> Val: {val_reason}{RST}")
    if hb_flag and bias_src:
        print(f"         {M}↳ GTAL: {bias_src}{RST}")

    if ets >= 50 or acg_relaxed or acg_locked:
        acg_c2 = G if acg_relaxed else M if acg_locked else W
        print(f"         {acg_c2}↳ {acg_r.acg_line()}{RST}", end="")
        if acg_change:
            print(f" {G}← OUTCOME WOULD CHANGE{RST}", end="")
        elif acg_relaxed and not acg_change:
            print(f" {Y}← RELAXED but conf={conf_sc} < {acg_thresh}{RST}", end="")
        elif not acg_relaxed and acg_reject:
            print(f"  {acg_reject}", end="")
        print()

    if ev == "VALID" and esl_active:
        fill_c = G if fill == "HIGH" else Y if fill == "MEDIUM" else R
        lat_c  = G if lat == "LOW"   else Y if lat == "MEDIUM"  else R
        print(f"         {G}✅ VALID{RST} → "
              f"Exec={getattr(esl_r,'execution_probability',0)} "
              f"Fill={fill_c}{fill}{RST} "
              f"Lat={lat_c}{lat}{RST} | "
              f"PNL: {pnl_summ}")
        print(f"         {B}↳ {port_r.portfolio_line()}{RST}")
        print(f"         {C}↳ {adapt_r.adaptive_line()}{RST}")
    elif risk_note:
        print(f"         {Y}↳ Risk: {risk_note}{RST}")

    if risk_ok:
        print(f"         {G}🚀 TRADE APPROVED{RST}")


def _print_summaries(logger_v3, pnl_attr, port_risk, adaptive, acg, outcome,
                     tick_count, bar_count,
                     G, Y, R, W, B, C, M, RST, BOLD):

    summary   = logger_v3.summary()
    pnl_sum   = pnl_attr.session_summary()
    port_sum  = port_risk.session_summary()
    adapt_rep = adaptive.session_report()
    acg_sum   = acg.session_summary()

    print(f"\n{BOLD}{B}{'='*120}{RST}")
    print(f"{BOLD}  GIBBZ V3 + ACG + OUTCOME ENGINE SUMMARY{RST}")
    print(f"{BOLD}{B}{'─'*120}{RST}")

    print(f"\n  {BOLD}[SIGNAL LAYER]{RST}")
    for k, v in summary.items():
        print(f"    {k:<32} {v}")

    print(f"\n  {BOLD}[PNL ATTRIBUTION]{RST}")
    for k, v in pnl_sum.items():
        print(f"    {k:<32} {v}")

    print(f"\n  {BOLD}[PORTFOLIO RISK v2.0]{RST}")
    for k, v in port_sum.items():
        print(f"    {k:<32} {v}")

    if adapt_rep:
        print(f"\n  {BOLD}[ADAPTIVE PARAMETERS v2.1]{RST}")
        ets_d  = adapt_rep.get("ets_distribution",  {})
        conf_d = adapt_rep.get("conf_distribution", {})
        print(f"    {'bars_analyzed':<32} {adapt_rep.get('bars_analyzed', 0)}")
        print(f"    {'ETS avg/max/pct>65':<32} "
              f"{ets_d.get('avg','?')} / {ets_d.get('max','?')} / "
              f"{ets_d.get('pct_above_65','?')}%")
        print(f"    {'conf avg/max/pct>65':<32} "
              f"{conf_d.get('avg','?')} / {conf_d.get('max','?')} / "
              f"{conf_d.get('pct_above_65','?')}%")
        drift_c = lambda v: G if v=="LOW" else Y if v=="MED" else R
        ets_d2 = adapt_rep.get("ets_drift","LOW")
        conf_d2= adapt_rep.get("conf_drift","LOW")
        decay_s= adapt_rep.get("decay_shift","NO")
        filt_q = adapt_rep.get("filtering_quality","OK")
        micro_or_rep = adapt_rep.get("micro_over_rejection", False)
        micro_str_rep= adapt_rep.get("micro_signal_strength", 0)
        micro_aln_rep= adapt_rep.get("micro_window_alignment","LOW")
        print(f"    {'ets_drift':<32} {drift_c(ets_d2)}{ets_d2}{RST}")
        print(f"    {'conf_drift':<32} {drift_c(conf_d2)}{conf_d2}{RST}")
        print(f"    {'decay_shift':<32} {Y if decay_s=='YES' else G}{decay_s}{RST}")
        print(f"    {'filtering_quality':<32} {Y if filt_q!='OK' else G}{filt_q}{RST}")
        print(f"    {'micro_over_rejection':<32} "
              f"{G if micro_or_rep else W}{micro_or_rep}{RST}"
              + (f" align={micro_aln_rep} str={micro_str_rep}" if micro_or_rep else ""))
        print(f"    {'system_stability':<32} {adapt_rep.get('system_stability','?')}")
        print(f"    {'over_rejection':<32} {adapt_rep.get('over_rejection', False)}")
        print(f"    {'shadow_conf_proposed':<32} "
              f"{adapt_rep.get('shadow_conf_proposed','?')} (shadow only)")

    print(f"\n  {BOLD}[ACG — ADAPTIVE CONFIDENCE GATE]{RST}")
    print(f"    {'total_relaxed_activations':<32} {acg_sum.get('total_relaxed_activations',0)} "
          f"/ {acg_sum.get('max_allowed',3)} max")
    print(f"    {'session_locked':<32} {acg_sum.get('session_locked', False)}")
    print(f"    {'would_change_outcome':<32} {acg_sum.get('would_change_outcome', 0)} barras")
    print(f"    {'micro_or_activations':<32} {acg_sum.get('micro_or_activations', 0)}")
    print(f"    {'struct_or_activations':<32} {acg_sum.get('struct_or_activations', 0)}")

    act_log = acg_sum.get("activation_log", [])
    if act_log:
        print(f"\n  {BOLD}  ACG ACTIVATION LOG:{RST}")
        for entry in act_log:
            change_str = f"{G}← WOULD CHANGE{RST}" if entry.get("would_change") else ""
            print(f"    Bar {entry['bar']:4d} | "
                  f"conf={entry['conf']:3d} ets={entry['ets']:3d} "
                  f"env={entry['env']:<16} ev={entry['ev']:<8} "
                  f"src={entry.get('source','?'):<10} {change_str}")
    else:
        print(f"    {'activation_log':<32} (no activations this session)")

    # ── OUTCOME ENGINE ────────────────────────────────────────────
    print(f"\n{BOLD}{B}{'─'*120}{RST}")
    for line in outcome.observation_summary_lines():
        print(line)

    # Regime calibration
    regime_calib = adapt_rep.get("regime_calibration", {}) if adapt_rep else {}
    if regime_calib:
        print(f"\n  {BOLD}[REGIME CALIBRATION]{RST}")
        for regime, vals in regime_calib.items():
            print(f"    {regime:<20} "
                  f"bars={vals['bars_seen']:3d} "
                  f"avg_ets={vals['avg_ets']:5.1f} "
                  f"max_ets={vals['max_ets']:3d} "
                  f"avg_conf={vals['avg_conf']:5.1f} "
                  f"ets_active={vals['ets_active']}")

    print(f"\n  {BOLD}Ticks: {tick_count} → Bars: {bar_count}{RST}")
    print(f"{BOLD}{B}{'='*120}{RST}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--date",          required=True)
    parser.add_argument("--bars",          type=int,  default=200)
    parser.add_argument("--skip",          type=int,  default=0)
    parser.add_argument("--save-outcomes", action="store_true",
                        help="Guardar JSON en outcomes/")
    args = parser.parse_args()
    run_debug_v3(args.file, args.date, args.bars, args.skip,
                 save_outcomes=args.save_outcomes)