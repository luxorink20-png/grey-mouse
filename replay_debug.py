"""
GIBBZ Replay Debug
Muestra el estado interno de cada barra procesada.
Corre las primeras N barras y muestra todo.

USO:
  python replay_debug.py recordings/2026-05-08_1141.jsonl --date 2026-05-05 --bars 200
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
from learning_engine           import LearningEngine
from historical_context_loader import HistoricalContextLoader
from bar_aggregator            import BarAggregator

G="\033[92m"; R="\033[91m"; Y="\033[93m"; B="\033[94m"
C="\033[96m"; W="\033[97m"; RST="\033[0m"; BOLD="\033[1m"


def run_debug(replay_file: str, replay_date: str, max_bars: int = 200, skip_bars: int = 0):

    # ── CONTEXT ───────────────────────────────────────────────────
    loader = HistoricalContextLoader()
    ctx    = loader.load(replay_date)
    VAH, POC, VAL = ctx.vah, ctx.poc, ctx.val

    # ── ENGINES ───────────────────────────────────────────────────
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

    bar_count  = 0
    tick_count = 0

    print(f"\n{BOLD}{B}{'='*70}{RST}")
    print(f"{BOLD}{W}  GIBBZ REPLAY DEBUG — {replay_date} — barras {skip_bars+1} a {skip_bars+max_bars}{RST}")
    print(f"{BOLD}{B}{'='*70}{RST}\n")

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
            bar = aggregator.process(tick)
            if bar is None:
                continue

            bar_count += 1
            if bar_count <= skip_bars:
                continue
            raw = bar

            # ── PIPELINE ──────────────────────────────────────────
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

            # ── v1.2 patch — inyectar env en raw para continuation engine ──
            raw["env"] = env_r.environment
            # ──────────────────────────────────────────────────────────────

            cont_r   = continuation.analyze(result, conf_r, regime_r, raw)
            ac_r     = adaptive_cont.analyze_continuation(result, conf_r, regime_r, env_r, raw)
            poc_r    = poc_engine.analyze(raw, result, conf_r)

            analysis   = conf_eng.evaluate(
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

            # ── OUTPUT POR BARRA ──────────────────────────────────
            env_blocked = env_r.blocks_trading()
            score       = getattr(analysis, "score", 0)
            event       = getattr(analysis, "event", result.get("event","?"))
            zone        = getattr(context,  "zone",  "?")
            dir_eff     = env_r.directional_efficiency
            trap        = env_r.trap_density
            bfr         = env_r.breakout_failure_rate
            env_name    = env_r.environment
            val_ok      = getattr(validation, "validated", False)
            val_reason  = getattr(validation, "reason", "")
            risk_ok     = risk.approved

            # Color por estado
            if risk_ok:
                state_c = G
                state   = "✅ TRADE"
            elif env_blocked:
                state_c = R
                state   = "🚫 ENV"
            elif not val_ok:
                state_c = Y
                state   = "⚠️  VAL"
            else:
                state_c = W
                state   = "   --"

            vol   = raw.get("volume", 0)
            delta = raw.get("delta",  0)
            hi    = raw.get("high",   raw["price"])
            lo    = raw.get("low",    raw["price"])

            print(
                f"  Bar {bar_count:4d} | "
                f"P={raw['price']:8.2f} "
                f"V={vol:6.0f} "
                f"D={delta:+6.0f} "
                f"R={hi-lo:.2f} | "
                f"evt={event:<14} "
                f"zone={zone:<12} "
                f"sc={score:3d} | "
                f"env={env_name:<16} "
                f"eff={dir_eff:3d} "
                f"trap={trap:3d} "
                f"bfr={bfr:3d} | "
                f"{state_c}{state}{RST}"
            )

            if not val_ok and score > 0:
                print(f"         {Y}↳ Val fail: {val_reason}{RST}")

    print(f"\n{BOLD}  Ticks procesados: {tick_count} → Barras: {bar_count}{RST}")
    print(f"{BOLD}{B}{'='*70}{RST}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("file")
    parser.add_argument("--date",  type=str, required=True)
    parser.add_argument("--bars", type=int, default=200, help="Cantidad de barras a mostrar")
    parser.add_argument("--skip", type=int, default=0,   help="Saltear N barras al inicio")
    args = parser.parse_args()

    run_debug(args.file, args.date, args.bars, args.skip)