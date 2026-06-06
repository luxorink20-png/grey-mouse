"""
run_replay.py — VISUALIZATION / MONITORING ONLY. NOT FOR SHADOW VALIDATION.

════════════════════════════════════════════════════════════════════════
  ⚠  DEPRECATION NOTICE — DO NOT USE FOR SHADOW VALIDATION
════════════════════════════════════════════════════════════════════════

This script MUST NOT be used to generate shadow trade statistics or
validate setup performance. It replicates engine.py's live-monitoring
pipeline, which is intentionally lightweight and is MISSING the
institutional filter stack used in production:

  Missing filters vs the validated pipeline (full_backtest.py):
    • ConfirmationEngine          — blocks early-bar false signals
    • ContinuationEngine          — requires follow-through confirmation
    • SessionRegimeEngine         — regime gating (EXPANSION / COMPRESSION)
    • AdaptiveContinuationEngine  — adapts thresholds to regime
    • PocAcceptanceEngine         — POC re-test validation
    • env_r.blocks_trading()      — hard block in rotational environments
    • ETIL / EdgeDecay / Timing   — edge quality and decay filters
    • ACG / ESL / Opportunity     — late-session and continuation gates
    • Extended ConfluenceEngine   — 8-arg call vs 2-arg used here

Without these layers RiskEngine approves 7–8× more signals than
production. Running this script and feeding results to shadow_stats.py
produces catastrophically inflated trade counts and negative WR/PnL
that do NOT represent real edge (validated on 2026-05-12: 149 trades
vs 19 expected, WR 10.7% vs 47% expected).

OFFICIAL SHADOW VALIDATION PATH:
    python -X utf8 full_backtest.py
    (reads expansion_outcomes/*.json → runs 15+ component pipeline)

THIS SCRIPT IS KEPT FOR:
    • Visualizing bar-by-bar event flow during replay
    • Debugging EventEngine / ConfluenceEngine / SetupRouter in isolation
    • Verifying that GibbzBridge data lands correctly (packet format check)

It intentionally does NOT write gibbz_trades_*.csv or gibbz_session_*.csv
to prevent accidental pollution of shadow_stats.py datasets.

Usage (visualization only):
    python -X utf8 run_replay.py 2024-08-22 recordings/2026-05-11_1716.jsonl
    python -X utf8 run_replay.py --all
════════════════════════════════════════════════════════════════════════
"""

import json
import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from historical_context_loader import HistoricalContextLoader
from event_engine              import EventEngine
from confluence_engine         import ConfluenceEngine, ConfluenceResult
from session_filter            import SessionFilter
from logger                    import GibbzLogger
from validator                 import Validator, ValidationResult
from intent_engine             import IntentEngine
from risk_engine               import RiskEngine
from feedback_engine           import FeedbackEngine
from microstructure_engine     import MicrostructureEngine
from adaptive_layer            import AdaptiveLayer
from learning_engine           import LearningEngine
from bar_aggregator            import BarAggregator
from levels                    import create_levels
from market_environment        import MarketEnvironmentAnalyzer
from gibbz_vwap                import VWAPEngine
from gibbz_failed_auction      import FADetector
from gibbz_va_rule80           import VA80Detector
from gibbz_setup_router        import SetupRouter

# ── Sessions to run with --all ─────────────────────────────────────────────
SHADOW_SESSIONS = [
    ("2024-08-22", "recordings/2026-05-11_1716.jsonl"),
    ("2024-12-18", "recordings/2026-05-11_1735.jsonl"),
    ("2025-07-30", "recordings/2026-05-11_1729.jsonl"),
    ("2026-02-05", "recordings/2026-05-09_1613.jsonl"),
    ("2026-05-06", "recordings/2026-05-08_1650.jsonl"),
]


def run_session(session_date: str, recording_path: str) -> dict:
    """
    Run one session through the full engine.py pipeline.
    Returns a summary dict with trades, WR, PnL.
    """
    ctx        = HistoricalContextLoader().load(session_date)
    VAH        = ctx.vah
    POC        = ctx.poc
    VAL        = ctx.val
    IBH        = ctx.ibh
    OPEN_PRICE = ctx.open_price

    _ibh_eq_vah       = IBH > 0 and abs(IBH - VAH) <= 2.0
    _ROUTER_IBH_SETUP = (
        "SETUP_COMPLETO" if _ibh_eq_vah and OPEN_PRICE > 0
                            and abs(OPEN_PRICE - VAH) <= 30.0
        else ("OPEN_FAR" if _ibh_eq_vah else "")
    )

    # ── Componentes — mismos que engine.py ─────────────────────────
    event_eng  = EventEngine(window=10)
    conf_eng   = ConfluenceEngine(history_size=10)
    sess_filt  = SessionFilter(override_always_active=True)
    logger     = GibbzLogger(log_dir="logs", enabled=False)  # disabled — visualization only
    validator  = Validator(tick=0.25, min_liq_ticks=8)
    intent     = IntentEngine(buffer_size=15, tick=0.25)
    risk       = RiskEngine(tick=0.25)
    feedback   = FeedbackEngine(log_dir="logs", enabled=False, tick=0.25)  # disabled — visualization only
    micro      = MicrostructureEngine(window=20)
    adaptive   = AdaptiveLayer()
    learning   = LearningEngine(log_dir="logs")
    levels_obj = create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0)
    aggregator = BarAggregator(mode="TICK", ticks=500)

    # ── Router — mismos que engine.py ──────────────────────────────
    _env_rtr  = MarketEnvironmentAnalyzer(tick=0.25)
    _vwap_rtr = VWAPEngine()
    _fa_rtr   = FADetector(vah=VAH, val=VAL)
    _va80_rtr = VA80Detector(vah=VAH, val=VAL, open_price=OPEN_PRICE)
    router    = SetupRouter()

    # logger._init_file() — CSV writing intentionally disabled (see module docstring)

    print(f"\n  [{session_date}]  "
          f"VAH={VAH}  POC={POC}  VAL={VAL}  Open={OPEN_PRICE}",
          flush=True)

    _router_bar = 0
    bar_count   = 0
    closed_pnl  = 0.0

    with open(recording_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                tick = json.loads(line)
            except Exception:
                continue

            raw = aggregator.process(tick)
            if raw is None:
                continue

            bar_count     += 1
            result         = event_eng.process(raw)
            context        = levels_obj.get_context(raw["price"])
            session_active = sess_filt.is_active_session()
            session_name   = sess_filt.get_session_name()

            if session_active:
                analysis = conf_eng.evaluate(result, context)
            else:
                analysis = ConfluenceResult(
                    event="NONE", zone=context.zone,
                    confluence="DEAD ZONE - " + session_name,
                    bias="NEUTRAL", score=0,
                    classification="NO TRADE ZONE", action="IGNORE",
                    reason="Outside session", hpz_bonus=False,
                    bias_aligned=False, consecutive=0,
                )

            micro_result = micro.analyze(
                event_result=result, level_context=context,
                confluence=analysis, raw_data=raw,
            )

            if session_active:
                validation = validator.validate(analysis, result, raw)
            else:
                validation = ValidationResult(
                    validated=False, adjusted_score=0, original_score=0,
                    filters_passed=[], filters_failed=["SESSION"],
                    reason="Dead zone",
                )

            adaptive.adjust(
                confluence=analysis, validation=validation,
                microstructure=micro_result, level_context=context,
            )

            narrative = intent.analyze(result, context, analysis, validation)
            event_key = result.get("event", "NONE")
            zone_key  = getattr(context,   "zone",      "UNKNOWN")
            narr_key  = getattr(narrative, "narrative", "UNCLEAR")

            risk_result = risk.analyze(
                price=raw["price"], confluence=analysis,
                validation=validation, intent=narrative,
                level_context=context,
            )

            # ── SetupRouter (shadow — no afecta riesgo) ─────────────
            _router_bar += 1
            _env_r  = _env_rtr.analyze_environment(raw, result)
            _vwap_r = _vwap_rtr.update(raw)
            _fa_r   = _fa_rtr.update(raw["price"], raw.get("delta", 0))
            _va80_r = _va80_rtr.update(raw["price"])
            setup_r = router.route(
                bar_count=_router_bar, price=raw["price"],
                vah=VAH, val=VAL, poc=POC, open_price=OPEN_PRICE,
                gtal_r=None, risk=risk_result, env_r=_env_r,
                or_r=None, ibh_setup=_ROUTER_IBH_SETUP,
                fa_r=_fa_r, va80_r=_va80_r, vwap_r=_vwap_r,
                gap_r=None, poc_r=None, bounce_r=None,
            )

            if risk_result.approved:
                feedback.open_trade(
                    risk_result=risk_result, analysis=analysis,
                    narrative=narrative, session_name=session_name,
                    signal_price=raw["price"],
                )

            closed = feedback.update(raw["price"])

            if closed is not None:
                closed_pnl += getattr(closed, "pnl_pts", 0.0)
                direction   = getattr(closed, "direction", "NONE")
                tr_result   = getattr(closed, "result",    "UNKNOWN")
                tr_score    = getattr(closed, "score",     0)
                tr_zone     = getattr(closed, "zone",      zone_key)
                tr_narr     = getattr(closed, "narrative", narr_key)
                adaptive.register_trade(
                    direction=direction, result=tr_result,
                    zone=tr_zone, score=tr_score,
                )
                learning.register(
                    event=event_key, zone=tr_zone, narrative=tr_narr,
                    result=tr_result, score=tr_score, direction=direction,
                )

            logger.log(
                price=raw["price"], event_result=result,
                level_context=context, analysis=analysis,
                session_name=session_name,
                setup_type=setup_r.signal_type,
                setup_confidence=setup_r.confidence,
                setup_env=getattr(_env_r, "environment", "ROTATIONAL"),
            )

            if bar_count % 500 == 0:
                fb = feedback.get_summary()
                print(f"    bar {bar_count:5d} | "
                      f"trades={fb.total_trades}  "
                      f"WR={fb.win_rate}%",
                      flush=True)

    fb     = feedback.get_summary()
    wins   = fb.wins
    losses = fb.losses
    total  = fb.total_trades
    wr     = fb.win_rate

    print(f"  → DONE  trades={total}  WR={wr}%  "
          f"W={wins} L={losses}  PnL={round(closed_pnl,2):+.2f}pts  "
          f"bars={bar_count}",
          flush=True)

    return {
        "session": session_date,
        "trades":  total,
        "wins":    wins,
        "losses":  losses,
        "wr":      wr,
        "pnl":     round(closed_pnl, 2),
        "bars":    bar_count,
    }


def main():
    parser = argparse.ArgumentParser(description="GIBBZ Shadow Replay")
    parser.add_argument("session_date", nargs="?", help="YYYY-MM-DD")
    parser.add_argument("recording",    nargs="?",
                        help="recordings/YYYY-MM-DD_HHMM.jsonl")
    parser.add_argument("--all", action="store_true",
                        help="Run all 5 shadow sessions sequentially")
    args = parser.parse_args()

    if args.all:
        sessions = SHADOW_SESSIONS
    elif args.session_date and args.recording:
        sessions = [(args.session_date, args.recording)]
    else:
        parser.print_help()
        sys.exit(1)

    print(f"\n{'='*60}")
    print(f"  GIBBZ Shadow Replay — {len(sessions)} session(s)")
    print(f"{'='*60}")

    results = []
    for date, rec in sessions:
        rec_path = Path(rec)
        if not rec_path.exists():
            print(f"\n  [SKIP] {date}: {rec} no encontrado")
            continue
        try:
            r = run_session(date, str(rec_path))
            results.append(r)
        except Exception as e:
            print(f"\n  [ERROR] {date}: {e}", flush=True)
            import traceback
            traceback.print_exc()

    if not results:
        print("\n  Sin resultados.")
        return

    # ── Resumen final ───────────────────────────────────────────────
    total_t  = sum(r["trades"] for r in results)
    total_w  = sum(r["wins"]   for r in results)
    total_pnl= round(sum(r["pnl"]   for r in results), 2)
    global_wr = round(100 * total_w / total_t, 1) if total_t else 0

    print(f"\n{'='*60}")
    print(f"  RESUMEN FINAL — {len(results)} sesiones")
    print(f"{'='*60}")
    print(f"  {'Session':<12}  {'Trades':>6}  {'WR':>6}  {'PnL':>9}")
    print(f"  {'-'*40}")
    for r in results:
        print(f"  {r['session']:<12}  {r['trades']:>6}  "
              f"{r['wr']:>5.1f}%  {r['pnl']:>+9.2f}")
    print(f"  {'-'*40}")
    print(f"  {'TOTAL':<12}  {total_t:>6}  "
          f"{global_wr:>5.1f}%  {total_pnl:>+9.2f}")
    print(f"\n  Trades escritos en: logs/gibbz_trades_*.csv")
    print(f"  Corre: python -X utf8 shadow_stats.py\n")


if __name__ == "__main__":
    main()
