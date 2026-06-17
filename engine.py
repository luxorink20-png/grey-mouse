import time
import random
import json
import os
import sys
import shutil
import datetime
from state                import GibbzState
from event_engine         import EventEngine
from engine_view          import EngineView
from levels               import create_levels
from confluence_engine    import ConfluenceEngine, ConfluenceResult
from session_filter       import SessionFilter
from logger               import GibbzLogger
from validator            import Validator, ValidationResult
from intent_engine        import IntentEngine
from risk_engine          import RiskEngine
from feedback_engine      import FeedbackEngine
from stats_engine         import StatsEngine
from market_feed          import MarketFeed
from voice_engine         import VoiceEngine
from microstructure_engine import MicrostructureEngine
from adaptive_layer       import AdaptiveLayer
from learning_engine      import LearningEngine
from bar_aggregator       import BarAggregator
from market_environment   import MarketEnvironmentAnalyzer
from confirmation_engine  import ConfirmationEngine
from continuation_engine  import ContinuationEngine
from session_regime_engine import SessionRegimeEngine
from adaptive_continuation import AdaptiveContinuationEngine
from poc_acceptance        import PocAcceptanceEngine
from gibbz_vwap           import VWAPEngine
from gibbz_failed_auction import FADetector
from gibbz_va_rule80      import VA80Detector
from gibbz_setup_router   import SetupRouter
from config import (
    ENABLE_LOGGING, OVERRIDE_SESSION,
    USE_REAL_FEED, ENABLE_VOICE,
    UDP_HOST, UDP_PORT,
)
from context_filter import ContextFilter
from quality_engine import QualityEngine
from confidence_engine import ConfidenceEngine
from concentration_monitor import ConcentrationMonitor
from auto_levels import VolumeProfileBuilder
from log_config import get_logger as _get_logger
_cf_log = _get_logger("context_filter.engine")

# ── NIVELES DESDE ARCHIVO ──────────────────────────────────────────
_levels_path = os.path.join(os.path.dirname(__file__), "levels.json")
try:
    with open(_levels_path, encoding="utf-8") as _f:
        _lvl = json.load(_f)
except FileNotFoundError:
    print(
        f"[FATAL] levels.json not found: {_levels_path}\n"
        f"  Run: python scripts/update_context.py\n"
        f"  Or:  python scripts/set_session.py {__import__('datetime').date.today()}"
    )
    sys.exit(1)
except json.JSONDecodeError as _json_err:
    print(
        f"[FATAL] levels.json is corrupt: {_json_err}\n"
        f"  Restore from backup or run: python scripts/update_context.py"
    )
    sys.exit(1)

VAH = float(_lvl["volume_profile"]["VAH"])
POC = float(_lvl["volume_profile"]["POC"])
VAL = float(_lvl["volume_profile"]["VAL"])

CALL_WALL          = float(_lvl["spotgamma"]["call_wall"])
PUT_WALL           = float(_lvl["spotgamma"]["put_wall"])
ZERO_GAMMA         = float(_lvl["spotgamma"]["zero_gamma"])
VOLATILITY_TRIGGER = float(_lvl["spotgamma"]["volatility_trigger"])
HPZ                = float(_lvl["spotgamma"]["hpz"])

PREV_HIGH  = float(_lvl["session"]["prev_high"])
PREV_LOW   = float(_lvl["session"]["prev_low"])
PREV_CLOSE = float(_lvl["session"]["prev_close"])
OPEN_PRICE = float(_lvl["session"]["open_price"])
ONH        = float(_lvl["session"]["onh"])
ONL        = float(_lvl["session"]["onl"])
IBH        = float(_lvl["session"]["ibh"])
IBL        = float(_lvl["session"]["ibl"])
# ──────────────────────────────────────────────────────────────────

_ibh_eq_vah       = IBH > 0 and abs(IBH - VAH) <= 2.0
_ROUTER_IBH_SETUP = (
    "SETUP_COMPLETO" if _ibh_eq_vah and OPEN_PRICE > 0
                        and abs(OPEN_PRICE - VAH) <= 30.0
    else ("OPEN_FAR" if _ibh_eq_vah else "")
)

state         = GibbzState()
engine        = EventEngine(window=10)
view          = EngineView(history_size=5)
confluence    = ConfluenceEngine(history_size=10)
session       = SessionFilter(override_always_active=OVERRIDE_SESSION)
logger        = GibbzLogger(log_dir="logs", enabled=ENABLE_LOGGING)
validator     = Validator(tick=0.25, min_liq_ticks=8)
intent        = IntentEngine(buffer_size=15, tick=0.25)
risk          = RiskEngine(tick=0.25)
feedback      = FeedbackEngine(log_dir="logs", enabled=ENABLE_LOGGING, tick=0.25)
stats         = StatsEngine(log_dir="logs")
voice         = VoiceEngine(enabled=ENABLE_VOICE)
levels        = create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0)
microstructure= MicrostructureEngine(window=20)
adaptive      = AdaptiveLayer()
learning      = LearningEngine(log_dir="logs")
aggregator    = BarAggregator(mode="TIME", seconds=5)

_market_env_rtr = MarketEnvironmentAnalyzer(tick=0.25)
_confirmation   = ConfirmationEngine(window=20, tick=0.25)
_continuation   = ContinuationEngine(window=12, tick=0.25)
_sess_regime    = SessionRegimeEngine(tick=0.25)
_adaptive_cont  = AdaptiveContinuationEngine(tick=0.25)
_poc_engine     = PocAcceptanceEngine(vah=VAH, poc=POC, val=VAL, tick=0.25)
_vwap_eng_rtr   = VWAPEngine()
_fa_det_rtr     = FADetector(vah=VAH, val=VAL)
_va80_det_rtr   = VA80Detector(vah=VAH, val=VAL, open_price=OPEN_PRICE)
setup_router    = SetupRouter()
_context_filter  = ContextFilter()
_quality_engine  = QualityEngine(threshold=62)
_confidence_eng  = ConfidenceEngine()
_conc_monitor    = ConcentrationMonitor(min_trades=5, pf_floor=1.0, window=20, cooldown_trades=10)

feed = MarketFeed(host=UDP_HOST, port=UDP_PORT) if USE_REAL_FEED else None

_sim_price = POC

# ── AUTO LEVELS — Volume Profile from feed ────────────────────────
_vp_builder        = VolumeProfileBuilder(tick=0.25, min_ticks=100, min_levels=5)
_auto_levels_done  = False   # becomes True once levels are applied


def _apply_auto_levels(new_vah: float, new_poc: float, new_val: float) -> bool:
    """
    Update levels.json with VP-computed VAH/POC/VAL and reinit all
    level-dependent engines.  Only the volume_profile section is
    overwritten — spotgamma and session keys are preserved.

    Enforces VAL < POC < VAH before writing.  Returns True on success.
    """
    global levels, _fa_det_rtr, _va80_det_rtr, _poc_engine

    if not (new_val < new_poc < new_vah):
        _cf_log.error(
            "AUTO_LEVELS: invariant VAL<POC<VAH violated — skipping "
            "(vah=%.2f poc=%.2f val=%.2f)", new_vah, new_poc, new_val,
        )
        return False

    # Write volume_profile section atomically
    try:
        with open(_levels_path, encoding="utf-8") as _f:
            _lvl_data = json.load(_f)
        _lvl_data["volume_profile"]["VAH"] = new_vah
        _lvl_data["volume_profile"]["POC"] = new_poc
        _lvl_data["volume_profile"]["VAL"] = new_val
        _lvl_data["_date"] = str(datetime.date.today())
        _tmp = _levels_path + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as _f:
            json.dump(_lvl_data, _f, indent=2)
        shutil.move(_tmp, _levels_path)
    except Exception as _e:
        _cf_log.error("AUTO_LEVELS: levels.json write failed: %s", _e)
        return False

    # Reinit level engines with the new levels
    try:
        levels        = create_levels(vah=new_vah, poc=new_poc, val=new_val, proximity=2.0)
        _fa_det_rtr   = FADetector(vah=new_vah, val=new_val)
        _va80_det_rtr = VA80Detector(vah=new_vah, val=new_val, open_price=OPEN_PRICE)
        _poc_engine   = PocAcceptanceEngine(vah=new_vah, poc=new_poc, val=new_val, tick=0.25)
    except Exception as _e:
        _cf_log.error("AUTO_LEVELS: level engine reinit failed: %s", _e)
        return False

    _cf_log.info(
        "AUTO_LEVELS: applied from feed | VAL=%.2f POC=%.2f VAH=%.2f",
        new_val, new_poc, new_vah,
    )
    print(
        f"[AUTO_LEVELS] Levels auto-updated from feed: "
        f"VAL={new_val} POC={new_poc} VAH={new_vah}"
    )
    return True
# ─────────────────────────────────────────────────────────────────


def simulate_price():
    """SIMULATION ONLY — not for live trading. Set GIBBZ_USE_REAL_FEED=1 in production."""
    if not getattr(simulate_price, "_warned", False):
        simulate_price._warned = True
        _cf_log.warning(
            "SIMULATION MODE: random price data active — "
            "set GIBBZ_USE_REAL_FEED=1 for live trading"
        )
    global _sim_price
    move       = random.uniform(-4.0, 4.0)
    _sim_price = round(_sim_price + move, 2)
    base       = random.randint(300, 1500)
    if move > 1.5:
        ask = int(base * random.uniform(0.60, 0.85))
        bid = base - ask
    elif move < -1.5:
        bid = int(base * random.uniform(0.60, 0.85))
        ask = base - bid
    else:
        ask = int(base * random.uniform(0.45, 0.55))
        bid = base - ask
    return {
        "price":      _sim_price,
        "high":       round(_sim_price + random.uniform(0, 1.5), 2),
        "low":        round(_sim_price - random.uniform(0, 1.5), 2),
        "bid_volume": bid,
        "ask_volume": ask,
        "trades":     random.randint(20, 120),
    }


def get_price_data():
    """
    Recibe ticks individuales del bridge y los agrega en barras.
    Retorna None si la barra aún no se completó.
    Retorna dict con barra completa cuando se cumple el intervalo.
    """
    if USE_REAL_FEED and feed is not None:
        tick = feed.get_latest_blocking(timeout=5.0)
        if tick is not None:
            get_price_data._no_data_count = 0
            bar = aggregator.process(tick)
            return bar   # None si barra incompleta, dict si completó
        # timeout — no UDP data received
        count = getattr(get_price_data, "_no_data_count", 0) + 1
        get_price_data._no_data_count = count
        # Warn at ~50s and every ~5 min after — port mismatch diagnosis
        if count == 10 or (count > 10 and count % 60 == 0):
            _cf_log.warning(
                "NO UDP DATA for ~%ds — engine listening on %s:%d. "
                "Check GibbzBridge UdpPort in ATAS matches this port.",
                count * 5, UDP_HOST, UDP_PORT,
            )
        partial = aggregator.get_partial()
        if partial and partial["volume"] > 0:
            get_price_data._no_data_count = 0
            return partial
        return None      # nada que procesar
    return simulate_price()


def dead_zone_analysis(event_result, level_context, session_name):
    return ConfluenceResult(
        event="NONE", zone=level_context.zone,
        confluence="DEAD ZONE - " + session_name,
        bias="NEUTRAL", score=0,
        classification="NO TRADE ZONE", action="IGNORE",
        reason="Outside tradeable session window",
        hpz_bonus=False, bias_aligned=False, consecutive=0,
    )


def run_engine():
    state.start()

    voice.start()

    if USE_REAL_FEED and feed is not None:
        feed.start()
        print("Waiting for ATAS data...")
        time.sleep(2)

    if ENABLE_LOGGING:
        logger._init_file()
        mode = "REAL FEED" if USE_REAL_FEED else "SIMULATION"
        print("Mode          : " + mode)
        print("Voice         : " + ("ON" if ENABLE_VOICE else "OFF"))
        print("Logging       : " + logger.filepath)
        print("Bar mode      : " + aggregator.mode + " / " + str(aggregator.seconds) + "s")
        print("─── NIVELES ───────────────────────────────")
        print("  VAH=" + str(VAH) + "  POC=" + str(POC) + "  VAL=" + str(VAL))
        print("  Call Wall=" + str(CALL_WALL) + "  Put Wall=" + str(PUT_WALL))
        print("  Zero Gamma=" + str(ZERO_GAMMA) + "  Vol Trigger=" + str(VOLATILITY_TRIGGER))
        print("  HPZ=" + str(HPZ) + "  ONH=" + str(ONH) + "  ONL=" + str(ONL))
        print("  PDH=" + str(PREV_HIGH) + "  PDL=" + str(PREV_LOW))
        print("───────────────────────────────────────────")
        print("Microstructure: ON")
        print("Adaptive      : ON")
        print("Learning      : ON")
        time.sleep(1)

    last_session = ""
    _router_bar  = 0
    global _auto_levels_done

    try:
        while state.is_running:

            raw = get_price_data()

            # Si no hay barra completa aún, esperar
            if raw is None:
                time.sleep(0.01)
                continue

            # ── AUTO LEVELS — collect from every bar until triggered ──
            if USE_REAL_FEED and not _auto_levels_done:
                _vp_builder.add_tick(raw["price"], raw.get("volume", 0.0))
                if _vp_builder.is_ready():
                    _vp = _vp_builder.calculate()
                    if _vp and _apply_auto_levels(_vp["vah"], _vp["poc"], _vp["val"]):
                        _auto_levels_done = True
            # ─────────────────────────────────────────────────────────

            _context_filter.update_bar(raw)

            result         = engine.process(raw)
            context        = levels.get_context(raw["price"])
            session_active = session.is_active_session()
            session_name   = session.get_session_name()

            if session_name != last_session:
                if session_active:
                    voice.on_session_start(session_name)
                    _context_filter.reset_session()
                else:
                    voice.on_session_end()
                last_session = session_name

            # ── Context engines — must run before confluence + validator ──
            _regime_r = _sess_regime.update(raw, result)
            _env_r    = _market_env_rtr.analyze_environment(raw, result)

            # Microstructure uses a neutral placeholder (analysis not yet computed)
            _mp = ConfluenceResult(
                event="NONE", zone=context.zone, confluence="",
                bias="NEUTRAL", score=50, classification="MEDIUM QUALITY",
                action="OBSERVE", reason="", hpz_bonus=False,
                bias_aligned=False, consecutive=0,
            )
            micro_result = microstructure.analyze(
                event_result  = result,
                level_context = context,
                confluence    = _mp,
                raw_data      = raw,
            )

            _conf_r = _confirmation.analyze(result, context, None, micro_result, raw)
            # Inject env + zone into raw so continuation_engine v1.2 override works
            raw["env"]  = getattr(_env_r, "environment", "ROTATIONAL")
            raw["zone"] = context.zone
            _cont_r = _continuation.analyze(result, _conf_r, _regime_r, raw)
            _ac_r   = _adaptive_cont.analyze_continuation(
                result, _conf_r, _regime_r, _env_r, raw
            )
            _poc_r  = _poc_engine.analyze(raw, result, _conf_r)

            if session_active:
                analysis = confluence.evaluate(
                    result, context,
                    confirmation=_conf_r, session_regime=_regime_r,
                    continuation=_cont_r, adaptive_continuation=_ac_r,
                    market_env=_env_r, poc_acceptance=_poc_r,
                )
            else:
                analysis = dead_zone_analysis(result, context, session_name)

            if session_active:
                validation = validator.validate(
                    analysis, result, raw,
                    confirmation=_conf_r, session_regime=_regime_r,
                    continuation=_cont_r, adaptive_continuation=_ac_r,
                    market_env=_env_r, poc_acceptance=_poc_r,
                )
            else:
                validation = ValidationResult(
                    validated=False, adjusted_score=0, original_score=0,
                    filters_passed=[], filters_failed=["SESSION"],
                    reason="Dead zone",
                )

            adaptive_result = adaptive.adjust(
                confluence    = analysis,
                validation    = validation,
                microstructure= micro_result,
                level_context = context,
            )

            event_key = result.get("event", "NONE")
            zone_key  = getattr(context, "zone", "UNKNOWN")

            narrative = intent.analyze(result, context, analysis, validation)

            narr_key  = getattr(narrative, "narrative", "UNCLEAR")

            risk_result = risk.analyze(
                price         = raw["price"],
                confluence    = analysis,
                validation    = validation,
                intent        = narrative,
                level_context = context,
            )

            _router_bar += 1
            _vwap_r = _vwap_eng_rtr.update(raw)
            _fa_r   = _fa_det_rtr.update(raw["price"], raw.get("delta", 0))
            _va80_r = _va80_det_rtr.update(raw["price"])
            setup_r = setup_router.route(
                bar_count=_router_bar, price=raw["price"],
                vah=VAH, val=VAL, poc=POC, open_price=OPEN_PRICE,
                gtal_r=None, risk=risk_result, env_r=_env_r,
                or_r=None, ibh_setup=_ROUTER_IBH_SETUP,
                fa_r=_fa_r, va80_r=_va80_r, vwap_r=_vwap_r,
                gap_r=None, poc_r=None, bounce_r=None,
            )

            if risk_result.approved:
                # ── WAVE 1: QUALITY GATE ──────────────────────────────
                quality_r = _quality_engine.score(
                    confluence    = analysis,
                    validation    = validation,
                    level_context = context,
                    intent        = narrative,
                    risk_result   = risk_result,
                )
                if not quality_r.passes:
                    _cf_log.info(
                        "QUALITY SKIP | score=%d<%d | %s | price=%.2f",
                        quality_r.score, quality_r.threshold,
                        quality_r.reason, raw["price"],
                    )
                else:
                    # ── WAVE 1: CONFIDENCE SIZING ─────────────────────
                    conf_r = _confidence_eng.score(quality_r.score)
                    # Apply multiplier: size shrinks on low-confidence bars,
                    # never exceeds the base size approved by risk engine.
                    risk_result.position_size = round(
                        risk_result.position_size * conf_r.multiplier, 3
                    )

                    _cf_skip, _cf_reason = _context_filter.should_skip(raw)
                    if _cf_skip:
                        _cf_log.info(
                            "CONTEXT SKIP | %s | setup=%s price=%.2f",
                            _cf_reason, setup_r.signal_type, raw["price"],
                        )
                    else:
                        _trade_setup = setup_r.signal_type \
                            if setup_r.signal_type != "NO_SETUP" \
                            else "CONFLUENCE"
                        _conc_monitor.set_pending(_trade_setup)
                        feedback.open_trade(
                            risk_result  = risk_result,
                            analysis     = analysis,
                            narrative    = narrative,
                            session_name = session_name,
                            signal_price = raw["price"],
                            setup_type   = _trade_setup,
                        )

            closed_trade = feedback.update(raw["price"])
            fb_summary   = feedback.get_summary()

            if closed_trade is not None:
                direction = getattr(closed_trade, "direction", "NONE")
                tr_result = getattr(closed_trade, "result",    "UNKNOWN")
                tr_pnl    = getattr(closed_trade, "pnl_pts",   0.0)
                _context_filter.register_trade(
                    pnl=tr_pnl,
                    win=tr_result == "WIN",
                )
                # Wave 1: keep confidence engine rolling window current
                _confidence_eng.register_outcome(
                    win     = tr_result == "WIN",
                    pnl_pts = tr_pnl,
                )
                # Epic 5: concentration degradation check
                try:
                    _degrad = _conc_monitor.register_close(
                        pnl_pts = tr_pnl,
                        win     = tr_result == "WIN",
                    )
                    if _degrad is not None:
                        voice.say(_degrad.message, priority=8)
                except Exception as _e:
                    _cf_log.error("concentration_monitor.register_close failed: %s", _e)
                tr_score  = getattr(closed_trade, "score",     0)
                tr_zone   = getattr(closed_trade, "zone",      zone_key)
                tr_narr   = getattr(closed_trade, "narrative", narr_key)

                adaptive.register_trade(
                    direction = direction,
                    result    = tr_result,
                    zone      = tr_zone,
                    score     = tr_score,
                )
                learning.register(
                    event     = event_key,
                    zone      = tr_zone,
                    narrative = tr_narr,
                    result    = tr_result,
                    score     = tr_score,
                    direction = direction,
                )

            voice.on_tick(
                price          = raw["price"],
                result         = result,
                context        = context,
                analysis       = analysis,
                validation     = validation,
                narrative      = narrative,
                risk_result    = risk_result,
                micro_result   = micro_result,
            )

            if closed_trade is not None:
                voice.on_trade_closed(closed_trade)

            if setup_r.signal_type != "NO_SETUP":
                voice.on_setup_signal(
                    setup_r,
                    getattr(_env_r, "environment", "ROTATIONAL"),
                )

            state.update_price(raw["price"])
            state.last_event    = result["event"]
            state.level_context = context

            logger.log(
                price=raw["price"], event_result=result,
                level_context=context, analysis=analysis,
                session_name=session_name,
                setup_type=setup_r.signal_type,
                setup_confidence=setup_r.confidence,
                setup_env=getattr(_env_r, "environment", "ROTATIONAL"),
            )

            view.update(
                state.price, result, context, analysis,
                session_name   = session_name,
                session_active = session_active,
                validation     = validation,
                narrative      = narrative,
                risk_result    = risk_result,
                feedback       = fb_summary,
                closed_trade   = closed_trade,
                pending_trade  = feedback.pending,
            )

    except KeyboardInterrupt:
        print("\nGIBBZ detenido.")
        voice.stop()

        if USE_REAL_FEED and feed is not None:
            feed.stop()

        print("\nAnalizando aprendizaje de sesión...")
        learning.force_analyze()

        print("\nGenerando reporte de sesion...\n")
        report = stats.analyze_today()
        stats.print_report(report)

        fb = feedback.get_summary()
        if fb.total_trades > 0:
            print("-- FEEDBACK ---------------------------")
            print("  Win rate      : " + str(fb.win_rate) + "%")
            print("  Trades        : " + str(fb.total_trades))
            print("  Traps         : " + str(fb.traps_detected))
            print("  Follow-thru   : " + str(fb.follow_through_rate) + "%")
            print("  Best zone     : " + str(fb.best_zone))
            print("--------------------------------------\n")

        adap = adaptive.get_summary()
        print("-- ADAPTIVE LAYER ---------------------")
        print("  Min score final : " + str(adap["min_score_dynamic"]))
        print("  Long WR sesión  : " + str(round(adap["session_long_wr"]*100,1)) + "%")
        print("  Short WR sesión : " + str(round(adap["session_short_wr"]*100,1)) + "%")
        print("  Losses seguidos : " + str(adap["consecutive_losses"]))
        print("--------------------------------------\n")

        val_stats = validator.stats
        print("-- VALIDATOR --------------------------")
        print("  Pass rate : " + str(val_stats["pass_rate"]) + "%")
        print("--------------------------------------\n")


if __name__ == "__main__":
    run_engine()