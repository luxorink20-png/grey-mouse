"""
Diagnóstico rápido de módulos en replay_feed
Muestra qué valores retornan confirmation, session_regime, etc.
"""
import json, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from event_engine          import EventEngine
from confirmation_engine   import ConfirmationEngine
from continuation_engine   import ContinuationEngine
from session_regime_engine import SessionRegimeEngine
from adaptive_continuation import AdaptiveContinuationEngine
from market_environment    import MarketEnvironmentAnalyzer
from poc_acceptance        import PocAcceptanceEngine
from microstructure_engine import MicrostructureEngine
from confluence_engine     import ConfluenceEngine, ConfluenceResult
from levels                import create_levels
from bar_aggregator        import BarAggregator
from historical_context_loader import HistoricalContextLoader

FILE   = "recordings/2026-05-08_1257.jsonl"
DATE   = "2026-05-06"
N_BARS = 100   # cuántas barras analizar después del warmup

loader = HistoricalContextLoader()
ctx    = loader.load(DATE)
VAH, POC, VAL = ctx.vah, ctx.poc, ctx.val

event_eng    = EventEngine(window=10)
confirmation = ConfirmationEngine(window=20, tick=0.25)
continuation = ContinuationEngine(window=12, tick=0.25)
sess_regime  = SessionRegimeEngine(tick=0.25)
adaptive_cont= AdaptiveContinuationEngine(tick=0.25)
market_env   = MarketEnvironmentAnalyzer(tick=0.25)
poc_engine   = PocAcceptanceEngine(vah=VAH, poc=POC, val=VAL, tick=0.25)
micro        = MicrostructureEngine(window=25)
conf_eng     = ConfluenceEngine(history_size=10)
levels       = create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0)
aggregator   = BarAggregator(mode="TICK", ticks=500)

bar_count = 0
analyzed  = 0

with open(FILE) as f:
    for line in f:
        if analyzed >= N_BARS:
            break
        tick = json.loads(line.strip())
        bar  = aggregator.process(tick)
        if bar is None:
            continue
        bar_count += 1
        if bar_count < 10:   # warmup
            continue

        raw     = bar
        result  = event_eng.process(raw)
        context = levels.get_context(raw["price"])
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
        cont_r   = continuation.analyze(result, conf_r, regime_r, raw)
        ac_r     = adaptive_cont.analyze_continuation(result, conf_r, regime_r, env_r, raw)
        poc_r    = poc_engine.analyze(raw, result, conf_r)

        analysis = conf_eng.evaluate(
            result, context,
            confirmation=conf_r, session_regime=regime_r,
            continuation=cont_r, adaptive_continuation=ac_r,
            market_env=env_r, poc_acceptance=poc_r,
        )

        if env_r.blocks_trading(): continue
        analyzed += 1
        print(f"\n{'='*60}")
        print(f"Bar {bar_count} | P={raw['price']} D={raw['delta']:+.0f} R={raw['high']-raw['low']:.2f}")
        print(f"  event     : {result.get('event')} conf={result.get('confidence')}")
        print(f"  zone      : {context.zone}")
        print(f"  BQ        : {getattr(conf_r,'breakout_type','?')}")
        print(f"  BQ score  : {getattr(conf_r,'confirmation_score',0)}")
        print(f"  acc_type  : {getattr(conf_r,'acceptance_type','?')}")
        print(f"  delta_per : {getattr(conf_r,'delta_persistence',0)}")
        print(f"  exp_eff   : {getattr(conf_r,'expansion_efficiency',0):.2f}")
        print(f"  regime    : {getattr(regime_r,'session_regime','?')}")
        print(f"  reg_conf  : {getattr(regime_r,'regime_confidence',0)}")
        print(f"  ac_qual   : {getattr(ac_r,'continuation_quality','?')}")
        print(f"  ac_prob   : {getattr(ac_r,'continuation_probability',0)}")
        print(f"  env       : {env_r.environment} eff={env_r.directional_efficiency} trap={env_r.trap_density}")
        print(f"  SCORE     : {analysis.score} [{analysis.classification}]")
        print(f"  breakdown : {analysis.score_breakdown.get('adjustments',{})}")
