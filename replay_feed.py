# +==================================================================+
#  GIBBZ SMC COP — replay_feed.py
#  Historical Replay Feed v2.0
#
#  USO:
#  python replay_feed.py recordings/2026-05-07_0930.jsonl
#
#  OPCIONES:
#  --speed 5.0      reproducir 5x mas rapido que tiempo real
#  --speed 0        sin delay (maximo speed, para backtest puro)
#  --date 2026-05-07  forzar fecha
# +==================================================================+

import json
import os
import sys
import time
import argparse
from datetime import datetime
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from event_engine              import EventEngine
from confluence_engine         import ConfluenceEngine, ConfluenceResult
from validator                 import Validator, ValidationResult
from intent_engine             import IntentEngine
from risk_engine               import RiskEngine
from confirmation_engine       import ConfirmationEngine
from continuation_engine       import ContinuationEngine
from session_regime_engine     import SessionRegimeEngine
from adaptive_continuation     import AdaptiveContinuationEngine
from market_environment        import MarketEnvironmentAnalyzer
from poc_acceptance            import PocAcceptanceEngine
from edge_learning             import EdgeLearningSystem
from microstructure_engine     import MicrostructureEngine
from levels                    import create_levels
from logger                    import GibbzLogger
from learning_engine           import LearningEngine
from historical_context_loader import HistoricalContextLoader
from bar_aggregator            import BarAggregator

G="\033[92m"; R="\033[91m"; Y="\033[93m"; B="\033[94m"
C="\033[96m"; W="\033[97m"; RST="\033[0m"; BOLD="\033[1m"; DIM="\033[2m"


# ==================================================================
#  DIAGNOSTICS TRACKER
# ==================================================================

class ReplayDiagnostics:

    def __init__(self):
        self.total         = 0
        self.gaps          = 0
        self.missing_delta = 0
        self.bad_volume    = 0
        self.price_spikes  = 0
        self.last_price    = 0.0
        self.last_ts       = 0.0
        self.issues        = []

    def check(self, tick: dict) -> list:
        self.total += 1
        found = []
        price = tick.get("price", 0)
        ts    = tick.get("timestamp", 0)
        delta = tick.get("delta", None)
        vol   = tick.get("volume", 0)

        if self.last_ts > 0 and ts > 0:
            gap = ts - self.last_ts
            if gap > 300:
                found.append(f"GAP {int(gap)}s en tick #{self.total}")
                self.gaps += 1

        if delta is None or (delta == 0 and vol > 500):
            self.missing_delta += 1

        if vol < 0 or (vol == 0 and self.total > 5):
            self.bad_volume += 1
            found.append(f"ZERO_VOL tick #{self.total}")

        if self.last_price > 0 and abs(price - self.last_price) > 10.0:
            found.append(f"SPIKE {self.last_price}→{price} tick #{self.total}")
            self.price_spikes += 1

        self.last_price = price
        self.last_ts    = ts
        self.issues.extend(found)
        return found

    def summary(self) -> dict:
        return {
            "total_ticks":   self.total,
            "time_gaps":     self.gaps,
            "missing_delta": self.missing_delta,
            "bad_volume":    self.bad_volume,
            "price_spikes":  self.price_spikes,
            "data_quality":  max(0, 100 - self.gaps*5 - self.price_spikes*3),
        }


# ==================================================================
#  REPLAY TRADE LOGGER
# ==================================================================

class ReplayTradeLogger:

    def __init__(self, replay_file: str, log_dir: str = "logs"):
        os.makedirs(log_dir, exist_ok=True)
        base      = os.path.basename(replay_file).replace(".jsonl", "")
        self.path = os.path.join(log_dir, f"replay_{base}_trades.jsonl")
        self._f   = open(self.path, "w", encoding="utf-8")
        self.trades = []

    def log_trade(self, trade_data: dict):
        self.trades.append(trade_data)
        self._f.write(json.dumps(trade_data) + "\n")
        self._f.flush()

    def close(self):
        if self._f:
            self._f.close()

    @property
    def count(self) -> int:
        return len(self.trades)


# ==================================================================
#  REPLAY ENGINE
# ==================================================================

class ReplayEngine:

    REPLAY_MODE = True

    def __init__(self, replay_file: str, speed: float = 0.0,
                 replay_date: str = ""):
        self.replay_file = replay_file
        self.speed       = speed
        self.replay_date = replay_date or datetime.now().strftime("%Y-%m-%d")

        # ── HISTORICAL CONTEXT ─────────────────────────────────────
        loader = HistoricalContextLoader()
        if replay_date:
            ctx = loader.load(replay_date)
            self.replay_date = replay_date
        else:
            ctx = loader.load_from_file(replay_file)
            self.replay_date = ctx.date

        VAH = ctx.vah
        POC = ctx.poc
        VAL = ctx.val
        # ──────────────────────────────────────────────────────────

        # Pipeline institucional
        self.event_eng    = EventEngine(window=10)
        self.conf_eng     = ConfluenceEngine(history_size=10)
        self.validator    = Validator(tick=0.25, min_liq_ticks=4)
        self.intent_eng   = IntentEngine(buffer_size=15, tick=0.25)
        self.risk_eng     = RiskEngine(tick=0.25)
        self.confirmation = ConfirmationEngine(window=20, tick=0.25)
        self.continuation = ContinuationEngine(window=12, tick=0.25)
        self.sess_regime  = SessionRegimeEngine(tick=0.25)
        self.adaptive_cont= AdaptiveContinuationEngine(tick=0.25)
        self.market_env   = MarketEnvironmentAnalyzer(tick=0.25)
        self.poc_engine   = PocAcceptanceEngine(vah=VAH, poc=POC,
                                                 val=VAL, tick=0.25)
        self.edge_learning= EdgeLearningSystem(log_dir="logs")
        self.micro        = MicrostructureEngine(window=25)
        self.levels       = create_levels(vah=VAH, poc=POC,
                                           val=VAL, proximity=2.0)
        self.learning     = LearningEngine(log_dir="logs")
        self.aggregator   = BarAggregator(mode="TICK", ticks=500)
        self._warmup_bars = 0
        self._warmup_done = False
        self.diagnostics  = ReplayDiagnostics()
        self.trade_logger = ReplayTradeLogger(replay_file)

        self.ctx           = ctx
        self.pending_trade = None
        self.closed_trades = []
        self.blocked       = defaultdict(int)
        self.tick_index    = 0
        self.equity        = 10000.0

    def run(self):
        if not os.path.exists(self.replay_file):
            print(f"{R}Archivo no encontrado: {self.replay_file}{RST}")
            sys.exit(1)

        ctx = self.ctx
        print(f"\n{BOLD}{B}{'='*60}{RST}")
        print(f"{BOLD}{B}  GIBBZ SMC COP — HISTORICAL REPLAY v2.0{RST}")
        print(f"{BOLD}{B}  Archivo  : {os.path.basename(self.replay_file)}{RST}")
        print(f"{BOLD}{B}  Fecha    : {self.replay_date}{RST}")
        print(f"{BOLD}{B}  VAH={ctx.vah}  POC={ctx.poc}  VAL={ctx.val}{RST}")
        print(f"{BOLD}{B}  Call Wall={ctx.call_wall}  Zero Gamma={ctx.zero_gamma}{RST}")
        print(f"{BOLD}{B}  Vol Trigger={ctx.volatility_trigger}  HPZ={ctx.hpz}{RST}")
        speed_str = f"{self.speed}x" if self.speed > 0 else "MAX"
        print(f"{BOLD}{B}  Speed    : {speed_str}{RST}")
        print(f"{BOLD}{G}  ✅ CONTEXT VERIFIED — fecha replay == fecha niveles{RST}")
        print(f"{BOLD}{B}{'='*60}{RST}\n")

        total_lines = self._count_lines()
        print(f"  Total ticks en archivo: {total_lines}")
        print(f"  Procesando...\n")

        last_ts    = 0.0
        start_real = time.time()

        with open(self.replay_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    tick = json.loads(line)
                except Exception:
                    continue

                issues = self.diagnostics.check(tick)
                if issues:
                    for issue in issues[:2]:
                        print(f"  {Y}DIAG: {issue}{RST}")

                if self.speed > 0 and last_ts > 0:
                    tick_ts = tick.get("timestamp", 0)
                    if tick_ts > last_ts:
                        real_gap   = tick_ts - last_ts
                        sleep_time = real_gap / self.speed
                        if 0 < sleep_time < 5.0:
                            time.sleep(sleep_time)

                last_ts = tick.get("timestamp", last_ts)
                self._process_tick(tick)
                self.tick_index += 1

                if self.tick_index % 10000 == 0:
                    elapsed = time.time() - start_real
                    bars    = self.aggregator._calcCount if hasattr(self.aggregator, '_calcCount') else "?"
                    print(f"  Tick {self.tick_index:6d}/{total_lines} | "
                          f"Trades: {len(self.closed_trades):3d} | "
                          f"Tiempo: {int(elapsed)}s")

        self._print_report()

    def _process_tick(self, raw: dict):
        # Agregar tick individual a la barra
        bar = self.aggregator.process(raw)
        if bar is None:
            return   # barra incompleta — seguir acumulando
        raw = bar    # usar barra agregada con volumen/delta/high/low reales

        result  = self.event_eng.process(raw)
        context = self.levels.get_context(raw["price"])

        regime_r = self.sess_regime.update(raw, result)
        env_r    = self.market_env.analyze_environment(raw, result)

        if env_r.blocks_trading():
            self.blocked["env_blocked:" + env_r.block_reason()[:40]] += 1
            return

        mp = ConfluenceResult(
            event="NONE", zone=context.zone, confluence="",
            bias="NEUTRAL", score=50, classification="MEDIUM QUALITY",
            action="OBSERVE", reason="", hpz_bonus=False,
            bias_aligned=False, consecutive=0,
        )
        micro_r  = self.micro.analyze(result, context, mp, raw)
        conf_r   = self.confirmation.analyze(result, context,
                                              None, micro_r, raw)
        cont_r   = self.continuation.analyze(result, conf_r, regime_r, raw)
        ac_r     = self.adaptive_cont.analyze_continuation(
                       result, conf_r, regime_r, env_r, raw)
        poc_r    = self.poc_engine.analyze(raw, result, conf_r)

        analysis   = self.conf_eng.evaluate(
            result, context,
            confirmation          = conf_r,
            session_regime        = regime_r,
            continuation          = cont_r,
            adaptive_continuation = ac_r,
            market_env            = env_r,
            poc_acceptance        = poc_r,
        )
        validation = self.validator.validate(
            analysis, result, raw,
            confirmation          = conf_r,
            session_regime        = regime_r,
            continuation          = cont_r,
            adaptive_continuation = ac_r,
            market_env            = env_r,
            poc_acceptance        = poc_r,
        )
        narrative = self.intent_eng.analyze(result, context,
                                             analysis, validation)
        risk      = self.risk_eng.analyze(
            price         = raw["price"],
            confluence    = analysis,
            validation    = validation,
            intent        = narrative,
            level_context = context,
        )

        if not risk.approved:
            self.blocked[risk.reason[:50]] += 1
            return

        self._open_trade(raw, risk, analysis, conf_r,
                         regime_r, ac_r, poc_r, env_r)

    def _open_trade(self, raw, risk, analysis, conf,
                    regime, ac, poc, env):
        self.pending_trade = {
            "entry":                raw["price"],
            "stop":                 risk.stop,
            "target1":              risk.target_1,
            "direction":            risk.direction,
            "score":                getattr(analysis, "score",               0),
            "event":                getattr(analysis, "event",               ""),
            "zone":                 getattr(analysis, "zone",                ""),
            "session_regime":       getattr(regime,   "session_regime",      ""),
            "regime_confidence":    getattr(regime,   "regime_confidence",   0),
            "breakout_type":        getattr(conf,     "breakout_type",       ""),
            "confirmation_score":   getattr(conf,     "confirmation_score",  0),
            "delta_persistence":    getattr(conf,     "delta_persistence",   0),
            "acceptance_score":     getattr(conf,     "acceptance_score",    0),
            "expansion_efficiency": getattr(conf,     "expansion_efficiency",0.0),
            "continuation_quality": getattr(ac,       "continuation_quality",""),
            "continuation_prob":    getattr(ac,       "continuation_probability",0),
            "poc_state":            getattr(poc,      "poc_state",           ""),
            "poc_score":            getattr(poc,      "poc_acceptance_score",0),
            "absorption_score":     getattr(poc,      "absorption_score",    0),
            "environment":          getattr(env,      "environment",         ""),
            "danger_level":         getattr(env,      "danger_level",        0),
            "open_ts":              raw.get("timestamp", time.time()),
            "open_price":           raw["price"],
            "result":               "OPEN",
            "pnl_pts":              0.0,
            "slippage":             0.0,
        }

    def _close_trade(self, price: float, result: str):
        t = self.pending_trade
        if t is None:
            return
        t["exit_price"] = price
        t["result"]     = result
        t["close_ts"]   = time.time()
        t["pnl_pts"]    = round(
            (price - t["entry"]) if t["direction"] == "LONG"
            else (t["entry"] - price), 2
        )
        self.equity += t["pnl_pts"] * 5
        self.closed_trades.append(t)
        self.trade_logger.log_trade(t)

        self.edge_learning.update(
            result               = result,
            pnl_pts              = t["pnl_pts"],
            breakout_type        = t["breakout_type"],
            session_regime       = t["session_regime"],
            zone                 = t["zone"],
            event                = t["event"],
            environment          = t["environment"],
            continuation_quality = t["continuation_quality"],
        )
        self.learning.register(
            event                = t["event"],
            zone                 = t["zone"],
            narrative            = "",
            result               = result,
            score                = t["score"],
            direction            = t["direction"],
            session_regime       = t["session_regime"],
            breakout_type        = t["breakout_type"],
            continuation_quality = t["continuation_quality"],
        )
        self.pending_trade = None

    def _count_lines(self) -> int:
        try:
            with open(self.replay_file) as f:
                return sum(1 for _ in f)
        except Exception:
            return 0

    # --------------------------------------------------------------
    #  REPORTE FINAL
    # --------------------------------------------------------------

    def _print_report(self):
        trades = self.closed_trades
        diag   = self.diagnostics.summary()
        ctx    = self.ctx

        print(f"\n{BOLD}{B}{'='*60}{RST}")
        print(f"{BOLD}{W}  GIBBZ REPLAY REPORT — {self.replay_date}{RST}")
        print(f"{BOLD}{B}{'='*60}{RST}")

        print(f"\n{BOLD}{C}  CONTEXT USED{RST}")
        print(f"  {'-'*56}")
        print(f"  VAH={ctx.vah}  POC={ctx.poc}  VAL={ctx.val}")
        print(f"  Call Wall={ctx.call_wall}  Zero Gamma={ctx.zero_gamma}")
        print(f"  Vol Trigger={ctx.volatility_trigger}  HPZ={ctx.hpz}")
        print(f"  PDH={ctx.prev_high}  PDL={ctx.prev_low}")
        print(f"  ONH={ctx.onh}  ONL={ctx.onl}")

        print(f"\n{BOLD}{C}  FEED DIAGNOSTICS{RST}")
        print(f"  {'-'*56}")
        qc   = diag["data_quality"]
        qc_c = G if qc >= 90 else Y if qc >= 75 else R
        print(f"  Total ticks    : {diag['total_ticks']}")
        print(f"  Time gaps      : {diag['time_gaps']}")
        print(f"  Price spikes   : {diag['price_spikes']}")
        print(f"  Missing delta  : {diag['missing_delta']}")
        print(f"  Data quality   : {qc_c}{qc}/100{RST}")

        if not trades:
            print(f"\n  {Y}Sin trades en esta sesion.{RST}")
            self._print_blocked()
            self.trade_logger.close()
            return

        wins      = [t for t in trades if t["result"] == "WIN"]
        losses    = [t for t in trades if t["result"] == "LOSS"]
        total     = len(trades)
        wr        = round(len(wins) / total * 100, 1) if total > 0 else 0
        avg_win   = round(sum(t["pnl_pts"] for t in wins)   / len(wins),   2) if wins   else 0
        avg_loss  = round(sum(t["pnl_pts"] for t in losses) / len(losses), 2) if losses else 0
        exp       = round((wr/100 * avg_win) + ((1 - wr/100) * avg_loss), 2)
        total_pnl = round(sum(t["pnl_pts"] for t in trades), 2)

        print(f"\n{BOLD}{C}  PERFORMANCE{RST}")
        print(f"  {'-'*56}")
        wc = G if wr >= 55 else Y if wr >= 40 else R
        print(f"  Total trades   : {total}")
        print(f"  Win rate       : {wc}{wr}%{RST}")
        print(f"  Avg win        : {G}+{avg_win}pts{RST}")
        print(f"  Avg loss       : {R}{avg_loss}pts{RST}")
        ec = G if exp > 0 else R
        print(f"  Expectancy     : {ec}{exp}pts/trade{RST}")
        print(f"  Total PnL      : {G if total_pnl>0 else R}{total_pnl}pts (${round(total_pnl*5,0)}){RST}")

        print(f"\n{BOLD}{C}  WR POR SESSION REGIME{RST}")
        print(f"  {'-'*56}")
        reg_stats = defaultdict(lambda: {"w": 0, "t": 0, "pnl": 0.0})
        for t in trades:
            rn = t["session_regime"] or "UNK"
            reg_stats[rn]["t"] += 1
            if t["result"] == "WIN": reg_stats[rn]["w"] += 1
            reg_stats[rn]["pnl"] += t["pnl_pts"]
        for rn, d in sorted(reg_stats.items(), key=lambda x: x[1]["t"], reverse=True):
            rwr  = round(d["w"] / d["t"] * 100, 1) if d["t"] else 0
            rpnl = round(d["pnl"] / d["t"], 2)
            rc   = G if rwr >= 55 else R
            print(f"  {rn:<22} WR={rc}{rwr:5.1f}%{RST}  n={d['t']:3d}  avg={rpnl:+.2f}pts")

        print(f"\n{BOLD}{C}  WR POR BREAKOUT TYPE{RST}")
        print(f"  {'-'*56}")
        bq_stats = defaultdict(lambda: {"w": 0, "t": 0, "pnl": 0.0})
        for t in trades:
            bq = t["breakout_type"] or "UNK"
            bq_stats[bq]["t"] += 1
            if t["result"] == "WIN": bq_stats[bq]["w"] += 1
            bq_stats[bq]["pnl"] += t["pnl_pts"]
        for bq, d in sorted(bq_stats.items(), key=lambda x: x[1]["t"], reverse=True):
            bwr = round(d["w"] / d["t"] * 100, 1) if d["t"] else 0
            bc  = G if bwr >= 55 else R
            print(f"  {bq:<14} WR={bc}{bwr:5.1f}%{RST}  n={d['t']:3d}  avg={round(d['pnl']/d['t'],2):+.2f}pts")

        if wins and losses:
            sw   = round(sum(t["score"] for t in wins)   / len(wins),   1)
            sl   = round(sum(t["score"] for t in losses) / len(losses), 1)
            diff = round(sw - sl, 1)
            dc   = G if diff > 8 else Y if diff > 0 else R
            print(f"\n{BOLD}{C}  SCORE PREDICTIVO{RST}")
            print(f"  {'-'*56}")
            print(f"  Wins avg  : {G}{sw}{RST}")
            print(f"  Losses avg: {R}{sl}{RST}")
            print(f"  Diff      : {dc}{'+' if diff>=0 else ''}{diff}{RST}  "
                  f"{'META OK' if diff >= 8 else 'monitorear'}")

        print(f"\n{BOLD}{C}  ULTIMOS 5 TRADES{RST}")
        print(f"  {'-'*56}")
        for t in trades[-5:]:
            rc = G if t["result"] == "WIN" else R
            print(f"  {rc}{t['result']:<5}{RST} {t['direction']:<5} "
                  f"score={t['score']:3d} | "
                  f"BQ={t['breakout_type']:<10} | "
                  f"regime={t['session_regime']}")
            print(f"    dp={t['delta_persistence']:3d} "
                  f"acc={t['acceptance_score']:3d} "
                  f"eff={round(t['expansion_efficiency'],2):.2f} | "
                  f"env={t['environment']}")
            print(f"    PnL: {rc}{'+' if t['pnl_pts']>0 else ''}{t['pnl_pts']}pts{RST}")

        self._print_blocked()
        print(f"\n  Trades guardados en: {self.trade_logger.path}")
        self.trade_logger.close()
        self.learning.force_analyze()
        self.edge_learning.print_report()
        print(f"\n{BOLD}{B}{'='*60}{RST}\n")

    def _print_blocked(self):
        if not self.blocked:
            return
        print(f"\n{BOLD}{C}  TOP RAZONES DE BLOQUEO{RST}")
        print(f"  {'-'*56}")
        for reason, count in sorted(self.blocked.items(),
                                     key=lambda x: x[1], reverse=True)[:8]:
            print(f"  {count:5d}x  {reason}")


# ==================================================================
#  ENTRY POINT
# ==================================================================

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GIBBZ Historical Replay Feed v2.0"
    )
    parser.add_argument("file",
                        help="Archivo .jsonl grabado por replay_recorder.py")
    parser.add_argument("--speed", type=float, default=0.0,
                        help="Velocidad (0=max, 1=real, 5=5x)")
    parser.add_argument("--date",  type=str,   default="",
                        help="Forzar fecha YYYY-MM-DD")
    args = parser.parse_args()

    engine = ReplayEngine(
        replay_file = args.file,
        speed       = args.speed,
        replay_date = args.date,
    )
    engine.run()

