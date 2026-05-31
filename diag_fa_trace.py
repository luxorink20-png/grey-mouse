"""
diag_fa_trace.py — Bar-level FA_SETUP diagnostic for noisy sessions.

Runs the full pipeline on a session and prints every bar where:
  - FA signal is active (CONFIRMED_LONG or CONFIRMED_SHORT)
  - OR a VA80 signal is active

Shows: bar, price, fa_state, env, dir_eff, trap_density, bfr,
       rotation_factor, blocks_trading, etil_score, vwap_state, delta

Usage:
    python -X utf8 diag_fa_trace.py 2025-10-29
    python -X utf8 diag_fa_trace.py 2026-03-24
"""

import json, sys, os
from pathlib import Path
from dataclasses import dataclass, field
from collections import defaultdict

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
from historical_context_loader import HistoricalContextLoader
from bar_aggregator            import BarAggregator
from gibbz_etil                import ETILEngine
from gibbz_timing              import TimingEngine
from gibbz_edge_decay          import EdgeDecayEngine
from gibbz_opportunity         import OpportunityClassifier
from gibbz_gtal                import GTALEngine
from gibbz_esl                 import ESLEngine
from pnl_attribution_layer        import PNLAttributionLayer
from portfolio_risk_context_layer import PortfolioRiskContextLayer
from adaptive_parameter_layer     import AdaptiveParameterLayer
from adaptive_confidence_gate     import AdaptiveConfidenceGate
from outcome_engine               import OutcomeEngine
from gibbz_logger_v3              import GIBBZLoggerV3

CORE_DIR     = Path(__file__).parent
OUTCOMES_DIR = CORE_DIR / "expansion_outcomes"
RECORDINGS   = CORE_DIR / "recordings"


def trace_session(session_date: str):
    ef = OUTCOMES_DIR / f"{session_date}_expansion.json"
    if not ef.exists():
        print(f"No expansion JSON for {session_date}"); return

    with open(ef, encoding="utf-8") as f:
        exp = json.load(f)
    recording_file = exp["recording_file"]

    ctx = HistoricalContextLoader().load(session_date)
    VAH, POC, VAL = ctx.vah, ctx.poc, ctx.val
    IBH, IBL      = ctx.ibh, ctx.ibl
    _ibh_eq_vah   = IBH > 0 and abs(IBH - VAH) <= 2.0
    _open         = ctx.open_price
    ibh_setup     = ("SETUP_COMPLETO" if (_ibh_eq_vah and _open > 0
                     and abs(_open - VAH) <= 30.0)
                     else ("OPEN_FAR" if _ibh_eq_vah else ""))

    VA_RANGE = round(VAH - VAL, 2)
    print(f"\n{'='*100}")
    print(f"  DIAGNOSTIC: {session_date}  |  VAH={VAH}  POC={POC}  VAL={VAL}"
          f"  VA_RANGE={VA_RANGE}pts  Open={_open}")
    print(f"{'='*100}")
    print(f"  {'BAR':>5}  {'PRICE':>8}  {'FA_STATE':<18}  {'ENV':<18}"
          f"  {'DIR_EFF':>7}  {'TRAP':>5}  {'BFR':>5}  {'ROT':>5}"
          f"  {'BLOCK':>6}  {'ETS':>5}  {'VWAP':<12}  {'DELTA':>8}"
          f"  {'STYPE':<20}")
    print(f"  {'-'*100}")

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
    outcome    = OutcomeEngine(session_date=session_date)
    logger_v3  = GIBBZLoggerV3()

    or_timer    = ORTimer()
    vwap_engine = VWAPEngine()
    bounce_det  = BounceDetector()
    va80_det    = VA80Detector(vah=VAH, val=VAL, open_price=_open)
    fa_det      = FADetector(vah=VAH, val=VAL)
    gap_fill    = GapFillDetector(open_price=_open, prev_close=ctx.prev_close)
    poc_magnet  = POCMagnetDetector(poc=POC)
    setup_router = SetupRouter()

    replay_path = RECORDINGS / recording_file
    bar_count   = 0
    fa_fires    = 0
    fa_consecutive = 0

    # Summary buckets
    env_when_fa: dict[str, int] = defaultdict(int)
    blocks_when_fa = 0
    ets_sum_fa  = 0
    dir_eff_vals = []
    va80_fires  = 0

    with open(replay_path, "r", encoding="utf-8") as f:
        for line in f:
            if bar_count >= 4000:
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
            ac_r     = adaptive_cont.analyze_continuation(result, conf_r, regime_r, env_r, raw)
            poc_r_core = poc_engine.analyze(raw, result, conf_r)
            analysis = conf_eng.evaluate(
                result, context,
                confirmation=conf_r, session_regime=regime_r,
                continuation=cont_r, adaptive_continuation=ac_r,
                market_env=env_r, poc_acceptance=poc_r_core,
            )
            validation = validator.validate(analysis, result, raw)
            risk = risk_eng.analyze(
                price=raw["price"], confluence=analysis,
                validation=validation,
                intent=intent_eng.analyze(result, context, analysis, validation),
                level_context=context,
            )

            etil_r   = etil.analyze(env_r, cont_r, conf_r, raw)
            timing_r = timing.analyze(etil_r, conf_r, validation, bar_count)
            decay_r  = edge_decay.analyze(env_r, etil_r, bar_count)
            opp_r    = opp_clf.classify(etil_r, timing_r, decay_r, conf_r, validation)
            gtal_r   = gtal.analyze(etil_r, timing_r, decay_r, opp_r,
                                    env_r, conf_r, cont_r, bar_count)
            esl_r    = esl.analyze(gtal_r, etil_r, timing_r, env_r, cont_r, raw, bar_count)
            pnl_r    = pnl_attr.analyze(etil_r, gtal_r, timing_r, esl_r, opp_r,
                                        conf_r, cont_r, validation, bar_count)
            port_r   = port_risk.analyze(env_r, etil_r, gtal_r, opp_r, bar_count)
            adapt_r  = adaptive.analyze(etil_r, gtal_r, conf_r, cont_r, env_r, pnl_r, bar_count)
            acg_r    = acg.analyze(env_r, conf_r, cont_r, etil_r, gtal_r,
                                   port_r, adapt_r, raw, bar_count)
            if getattr(acg_r, "relaxed_mode_active", False):
                acg.register_relaxed_outcome(
                    gtal_valid=(getattr(gtal_r, "execution_validity", "INVALID") == "VALID"),
                    hb=getattr(gtal_r, "hindsight_bias_flag", False)
                )
            outcome.observe(bar_count, raw, env_r, etil_r, gtal_r,
                            conf_r, cont_r, opp_r, esl_r, pnl_r,
                            port_r, adapt_r, acg_r, validation, analysis)

            or_r     = or_timer.update(raw)
            vwap_r   = vwap_engine.update(raw)
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
                open_price=_open,
                gtal_r=gtal_r, risk=risk, env_r=env_r,
                or_r=or_r, ibh_setup=ibh_setup,
                fa_r=fa_r, va80_r=va80_r, vwap_r=vwap_r,
                gap_r=gap_r, poc_r=poc_r, bounce_r=bounce_r,
            )

            fa_sig   = getattr(fa_r,   "signal",      "NONE")
            fa_state = getattr(fa_r,   "state",        "WATCHING")
            va80_sig = getattr(va80_r, "signal",       "NONE")
            env_name = getattr(env_r,  "environment",  "ROTATIONAL")
            dir_eff  = getattr(env_r,  "directional_efficiency", 0)
            trap     = getattr(env_r,  "trap_density",  0)
            bfr      = getattr(env_r,  "breakout_failure_rate", 0)
            rot      = getattr(env_r,  "rotation_factor", 0)
            blocked  = env_r.blocks_trading()
            ets      = getattr(etil_r, "ets_score",    0)
            vwap_st  = getattr(vwap_r, "state",        "NO_DATA")
            delta    = raw.get("delta", 0)
            price    = raw["price"]

            dir_eff_vals.append(dir_eff)

            interesting = (fa_sig != "NONE" or va80_sig != "NONE"
                           or setup_r.signal_type not in ("NO_SETUP",))

            if interesting:
                if fa_sig != "NONE":
                    fa_fires += 1
                    env_when_fa[env_name] += 1
                    if blocked:
                        blocks_when_fa += 1
                    ets_sum_fa += ets
                if va80_sig != "NONE":
                    va80_fires += 1

                # Only print if FA or VA80 active
                print(f"  {bar_count:>5}  {price:>8.2f}  {fa_state:<18}  {env_name:<18}"
                      f"  {dir_eff:>7}  {trap:>5}  {bfr:>5}  {rot:>5}"
                      f"  {str(blocked):>6}  {ets:>5}  {vwap_st:<12}  {delta:>8.0f}"
                      f"  {setup_r.signal_type:<20}")

    # Summary
    avg_dir_eff = round(sum(dir_eff_vals)/max(len(dir_eff_vals),1), 1)
    avg_ets_fa  = round(ets_sum_fa / max(fa_fires, 1), 1)

    print(f"\n  {'─'*80}")
    print(f"  SUMMARY  {session_date}  |  VA_RANGE={VA_RANGE}pts")
    print(f"  Total bars processed : {bar_count}")
    print(f"  FA signal fires      : {fa_fires}  (1 per {bar_count//max(fa_fires,1)} bars)")
    print(f"  VA80 signal fires    : {va80_fires}")
    print(f"  Avg dir_eff (all)    : {avg_dir_eff}")
    print(f"  Avg ETS when FA fires: {avg_ets_fa}")
    print(f"  blocks_trading when FA fires: {blocks_when_fa}/{fa_fires}")
    print(f"  Env distribution when FA fires:")
    for env, cnt in sorted(env_when_fa.items(), key=lambda x: -x[1]):
        print(f"    {env:<22} : {cnt} bars")
    print()


if __name__ == "__main__":
    dates = sys.argv[1:] if len(sys.argv) > 1 else ["2025-10-29", "2026-03-24"]
    for d in dates:
        trace_session(d)
