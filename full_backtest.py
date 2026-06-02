"""
full_backtest.py — Bar-a-bar backtest across all 43 pool sessions using the full GIBBZ pipeline.

Runs every session inline (no subprocess), collects SetupResult per bar, simulates trades.

Usage:
    python full_backtest.py [--max-bars 4000] [--target-cap 20] [--verbose]
"""

import json
import os
import sys
import argparse
from dataclasses import dataclass, field
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Reconfigure stdout to UTF-8 so print() calls inside HistoricalContextLoader
# (box-drawing chars) don't fail on Windows cp1252 terminals.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

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

from gibbz_etil        import ETILEngine
from gibbz_timing      import TimingEngine
from gibbz_edge_decay  import EdgeDecayEngine
from gibbz_opportunity import OpportunityClassifier
from gibbz_gtal        import GTALEngine
from gibbz_esl         import ESLEngine

from pnl_attribution_layer        import PNLAttributionLayer
from portfolio_risk_context_layer import PortfolioRiskContextLayer
from adaptive_parameter_layer     import AdaptiveParameterLayer
from adaptive_confidence_gate     import AdaptiveConfidenceGate
from outcome_engine               import OutcomeEngine
from gibbz_logger_v3              import GIBBZLoggerV3

CORE_DIR     = Path(__file__).parent
OUTCOMES_DIR = CORE_DIR / "expansion_outcomes"
RECORDINGS   = CORE_DIR / "recordings"

SKIP_TYPES   = {"NO_SETUP", "INSTITUTIONAL_GRADE"}


@dataclass
class BarData:
    bar:   int
    price: float
    stype: str   = "NO_SETUP"
    sdir:  str   = "NEUTRAL"
    sconf: int   = 0
    sstp:  float = 0.0
    stgt:  float = 0.0


@dataclass
class Trade:
    session:     str
    entry_bar:   int
    entry_price: float
    stype:       str
    direction:   str
    stop_level:  float
    tgt_level:   float
    raw_tgt:     float
    exit_bar:    int   = 0
    exit_price:  float = 0.0
    result:      str   = "OPEN"
    pnl:         float = 0.0


def run_session(session_date: str, recording_file: str,
                max_bars: int, target_cap: float,
                context_filter=None,
                session_type: str = "") -> list[BarData]:
    """Run the full pipeline on one session and return bars with setup signals.

    Optional args:
        context_filter: ContextFilter instance. When provided, sessions of
            filtered type (e.g. VOL_RELEASE) return [] immediately.
        session_type: session classification string from expansion metadata.
    """
    if context_filter is not None and session_type:
        if context_filter.is_session_filtered(session_type):
            print(f"  [CONTEXT FILTER] {session_date}: {session_type} → SKIP")
            return []

    replay_path = RECORDINGS / recording_file
    if not replay_path.exists():
        print(f"  [SKIP] {session_date}: recording not found ({recording_file})")
        return []

    try:
        loader = HistoricalContextLoader()
        ctx    = loader.load(session_date)
    except Exception as e:
        print(f"  [SKIP] {session_date}: context load failed — {e}")
        return []

    VAH, POC, VAL = ctx.vah, ctx.poc, ctx.val
    IBH, IBL      = ctx.ibh, ctx.ibl
    _ibh_eq_vah   = IBH > 0 and abs(IBH - VAH) <= 2.0
    if _ibh_eq_vah:
        _open = ctx.open_price
        ibh_setup = "SETUP_COMPLETO" if (_open > 0 and abs(_open - VAH) <= 30.0) else "OPEN_FAR"
    else:
        ibh_setup = ""

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
    vwap_engine = VWAPEngine(rth_required=False)  # backtest bars lack timestamp
    bounce_det  = BounceDetector()
    va80_det    = VA80Detector(vah=VAH, val=VAL, open_price=ctx.open_price)
    fa_det      = FADetector(vah=VAH, val=VAL)
    gap_fill    = GapFillDetector(open_price=ctx.open_price, prev_close=ctx.prev_close)
    poc_magnet  = POCMagnetDetector(poc=POC)
    setup_router = SetupRouter()

    bars: list[BarData] = []
    bar_count = 0

    with open(replay_path, "r", encoding="utf-8") as f:
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
            validation = validator.validate(
                analysis, result, raw,
                confirmation=conf_r, session_regime=regime_r,
                continuation=cont_r, adaptive_continuation=ac_r,
                market_env=env_r, poc_acceptance=poc_r_core,
            )
            risk = risk_eng.analyze(
                price=raw["price"], confluence=analysis,
                validation=validation, intent=intent_eng.analyze(
                    result, context, analysis, validation),
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

            or_r = or_timer.update(raw)
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

            logger_v3.log(bar_count, raw, env_r, conf_r, cont_r,
                          etil_r, timing_r, decay_r, opp_r, analysis, validation)

            bd = BarData(bar=bar_count, price=raw["price"])
            bd.stype = setup_r.signal_type
            bd.sdir  = setup_r.direction
            bd.sconf = setup_r.confidence
            bd.sstp  = setup_r.stop_pts
            bd.stgt  = setup_r.target_pts
            bars.append(bd)

    return bars


def run_backtest(bars: list[BarData], session: str, target_cap: float) -> list[Trade]:
    trades: list[Trade] = []
    active: Trade | None = None
    prev_type = "NO_SETUP"

    for b in bars:
        if active is not None:
            if active.direction == "LONG":
                if b.price <= active.stop_level:
                    active.exit_bar   = b.bar
                    active.exit_price = active.stop_level
                    active.pnl        = round(active.stop_level - active.entry_price, 2)
                    active.result     = "LOSS"
                    active = None
                elif b.price >= active.tgt_level:
                    active.exit_bar   = b.bar
                    active.exit_price = active.tgt_level
                    active.pnl        = round(active.tgt_level - active.entry_price, 2)
                    active.result     = "WIN"
                    active = None
            else:
                if b.price >= active.stop_level:
                    active.exit_bar   = b.bar
                    active.exit_price = active.stop_level
                    active.pnl        = round(active.entry_price - active.stop_level, 2)
                    active.result     = "LOSS"
                    active = None
                elif b.price <= active.tgt_level:
                    active.exit_bar   = b.bar
                    active.exit_price = active.tgt_level
                    active.pnl        = round(active.entry_price - active.tgt_level, 2)
                    active.result     = "WIN"
                    active = None

        if (active is None
                and b.stype not in SKIP_TYPES
                and b.stype != "NO_SETUP"
                and b.stype != prev_type
                and b.sdir in ("LONG", "SHORT")
                and b.sstp > 0):

            capped_tgt = min(b.stgt, target_cap)
            if capped_tgt <= 0:
                prev_type = b.stype
                continue

            if b.sdir == "LONG":
                stop_lvl = round(b.price - b.sstp, 2)
                tgt_lvl  = round(b.price + capped_tgt, 2)
            else:
                stop_lvl = round(b.price + b.sstp, 2)
                tgt_lvl  = round(b.price - capped_tgt, 2)

            t = Trade(
                session=session, entry_bar=b.bar, entry_price=b.price,
                stype=b.stype, direction=b.sdir,
                stop_level=stop_lvl, tgt_level=tgt_lvl, raw_tgt=b.stgt,
            )
            trades.append(t)
            active = t

        prev_type = b.stype

    if active is not None:
        last_price = bars[-1].price if bars else active.entry_price
        active.exit_bar   = bars[-1].bar if bars else active.entry_bar
        active.exit_price = last_price
        if active.direction == "LONG":
            active.pnl = round(last_price - active.entry_price, 2)
        else:
            active.pnl = round(active.entry_price - last_price, 2)
        active.result = "WIN" if active.pnl > 0 else "LOSS"

    return trades


def report_all(all_trades: list[Trade], sessions_run: int):
    print(f"\n{'='*80}")
    print(f"  FULL BACKTEST — {sessions_run} sesiones — {len(all_trades)} trades total")
    print(f"{'='*80}")

    if not all_trades:
        print("  Sin trades.\n")
        return

    wins   = sum(1 for t in all_trades if t.result == "WIN")
    losses = len(all_trades) - wins
    wr     = 100 * wins / len(all_trades)
    aw     = sum(t.pnl for t in all_trades if t.result == "WIN") / max(wins, 1)
    al     = sum(t.pnl for t in all_trades if t.result == "LOSS") / max(losses, 1)
    ex     = round(wr/100 * aw + (1 - wr/100) * al, 2)
    total  = round(sum(t.pnl for t in all_trades), 2)

    print(f"\n  GLOBAL:  trades={len(all_trades)}  WR={wr:.1f}%  "
          f"Expectancy={ex:+.2f}pts  PnL={total:+.2f}pts")
    print(f"           avg_win={aw:+.2f}  avg_loss={al:+.2f}\n")

    priority = ["ORB_SETUP", "FA_SETUP", "VA80_SETUP", "VWAP_SETUP",
                "GAP_SETUP", "POC_SETUP", "BOUNCE_SETUP"]

    by_type: dict[str, list[Trade]] = defaultdict(list)
    for t in all_trades:
        by_type[t.stype].append(t)

    print(f"  {'SETUP TYPE':<22} {'N':>3}  {'WR':>6}  {'Exp':>7}  {'PnL':>9}  {'AvgW':>7}  {'AvgL':>7}")
    print(f"  {'-'*72}")
    for p in priority:
        ts = by_type.get(p, [])
        if not ts:
            print(f"  {p:<22} {0:>3}  {'--':>6}  {'--':>7}  {'--':>9}")
            continue
        w   = sum(1 for t in ts if t.result == "WIN")
        l   = len(ts) - w
        wrt = 100 * w / len(ts)
        aw_ = sum(t.pnl for t in ts if t.result == "WIN") / max(w, 1)
        al_ = sum(t.pnl for t in ts if t.result == "LOSS") / max(l, 1)
        ex_ = round(wrt/100 * aw_ + (1 - wrt/100) * al_, 2)
        pnl = round(sum(t.pnl for t in ts), 2)
        print(f"  {p:<22} {len(ts):>3}  {wrt:>5.1f}%  {ex_:>+7.2f}  {pnl:>+9.2f}  {aw_:>+7.2f}  {al_:>+7.2f}")

    # Per-session PnL table
    by_sess: dict[str, list[Trade]] = defaultdict(list)
    for t in all_trades:
        by_sess[t.session].append(t)

    print(f"\n  PER-SESSION SUMMARY:")
    print(f"  {'Date':<12} {'N':>3}  {'WR':>6}  {'PnL':>9}  Best setup")
    print(f"  {'-'*55}")
    for sess in sorted(by_sess.keys()):
        ts  = by_sess[sess]
        w   = sum(1 for t in ts if t.result == "WIN")
        wrt = 100 * w / len(ts)
        pnl = round(sum(t.pnl for t in ts), 2)
        types = sorted(set(t.stype for t in ts))
        print(f"  {sess:<12} {len(ts):>3}  {wrt:>5.1f}%  {pnl:>+9.2f}  {', '.join(types)}")

    print(f"\n  {'='*80}\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--max-bars",   type=int,   default=4000)
    parser.add_argument("--target-cap", type=float, default=20.0)
    parser.add_argument("--verbose",    action="store_true")
    parser.add_argument("--sessions",   type=str,   default="",
                        help="Comma-separated YYYY-MM-DD dates to run (default: all)")
    args = parser.parse_args()

    session_filter = set(d.strip() for d in args.sessions.split(",") if d.strip())

    expansion_files = sorted(OUTCOMES_DIR.glob("*_expansion.json"))
    if session_filter:
        expansion_files = [ef for ef in expansion_files
                           if ef.stem.replace("_expansion", "") in session_filter]
        print(f"\nSession filter active: {len(session_filter)} dates requested, "
              f"{len(expansion_files)} matched.")
    else:
        print(f"\nFound {len(expansion_files)} sessions in pool.")
    print(f"max_bars={args.max_bars}  target_cap={args.target_cap}pts\n")

    all_trades: list[Trade] = []
    sessions_run = 0

    for ef in expansion_files:
        with open(ef, encoding="utf-8") as f:
            exp = json.load(f)
        session_date   = exp.get("session_date", ef.stem.replace("_expansion", ""))
        recording_file = exp.get("recording_file", "")
        if not recording_file:
            print(f"  [SKIP] {session_date}: no recording_file in expansion JSON")
            continue

        print(f"  Running {session_date} ({recording_file}) ...", end=" ", flush=True)
        bars = run_session(session_date, recording_file, args.max_bars, args.target_cap)
        if not bars:
            print("0 bars")
            continue

        trades = run_backtest(bars, session_date, args.target_cap)
        sessions_run += 1
        all_trades.extend(trades)

        w = sum(1 for t in trades if t.result == "WIN")
        pnl = round(sum(t.pnl for t in trades), 2)
        print(f"{len(bars)} bars  {len(trades)} trades  WR={100*w/max(len(trades),1):.0f}%  PnL={pnl:+.1f}")

    report_all(all_trades, sessions_run)


if __name__ == "__main__":
    main()
