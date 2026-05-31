# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP -- backtest_engine.py
#  Institutional Backtest Engine v4.3
#
#  v4.0: session_regime + continuation + debug extremo
#  v4.3: PATCH 1 -- Outlier Control System
#        PATCH 4 -- Predictive Stability Engine
#        PATCH 5 -- Edge Distribution Balancer
# ╚══════════════════════════════════════════════════════════════════╝

import random
import time
import os
import sys
from dataclasses import dataclass, field
from collections import defaultdict
from typing import Optional, List

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from event_engine           import EventEngine
from confluence_engine      import ConfluenceEngine, ConfluenceResult
from validator              import Validator, ValidationResult
from intent_engine          import IntentEngine
from risk_engine            import RiskEngine
from confirmation_engine    import ConfirmationEngine
from continuation_engine    import ContinuationEngine
from session_regime_engine  import SessionRegimeEngine
from poc_acceptance         import PocAcceptanceEngine
from microstructure_engine  import MicrostructureEngine
from levels                 import create_levels

G="\033[92m"; R="\033[91m"; Y="\033[93m"; B="\033[94m"
C="\033[96m"; W="\033[97m"; RST="\033[0m"; BOLD="\033[1m"; DIM="\033[2m"

VAH=7326.0; POC=7135.0; VAL=6826.0
TICK=0.25; TARGET_TRADES=100; BARS_PER_SIM=4000; CAPITAL=10000.0


# ══════════════════════════════════════════════════════════════════
#  SIMULADOR v4.0
# ══════════════════════════════════════════════════════════════════

class MarketRegime:
    TREND_UP="TREND_UP"; TREND_DOWN="TREND_DOWN"; RANGE="RANGE"
    MICRO="MICRO_RANGE"; BREAKOUT="BREAKOUT"; REVERSAL="REVERSAL"
    STOP_HUNT="STOP_HUNT"; NEWS="NEWS_SPIKE"


@dataclass
class Bar:
    price:float; open:float; high:float; low:float; close:float
    volume:float; delta:float; ask_volume:float; bid_volume:float
    trades:int; timestamp:float; regime:str=""; slippage:float=0.0


class InstitutionalMarketSimulator:

    def __init__(self, start_price=7150.0, seed=42):
        random.seed(seed)
        self.price        = start_price
        self.regime       = MarketRegime.RANGE
        self.regime_bars  = 0
        self.regime_max   = random.randint(15, 40)
        self.momentum     = 0.0
        self.trend_dir    = 1
        self.range_high   = start_price + 8.0
        self.range_low    = start_price - 8.0
        self.bar_index    = 0
        self._profile     = self._build_profile()

    def _build_profile(self):
        p = []
        for i in range(BARS_PER_SIM):
            pct = i / BARS_PER_SIM
            if pct < 0.05:    vm,vl = 2.5,2.0
            elif pct < 0.20:  vm,vl = 1.8,1.5
            elif pct < 0.40:  vm,vl = 1.3,1.2
            elif pct < 0.55:  vm,vl = 0.6,0.5
            elif pct < 0.75:  vm,vl = 1.2,1.1
            else:              vm,vl = 1.0,1.0
            p.append((vm,vl))
        return p

    def _transition(self):
        self.regime_bars += 1
        if self.regime_bars < self.regime_max:
            return
        self.regime_bars = 0
        roll = random.random()
        if random.random() < 0.04:
            self.regime = MarketRegime.STOP_HUNT
            self.regime_max = random.randint(2, 4)
            return
        if random.random() < 0.02:
            self.regime = MarketRegime.NEWS
            self.regime_max = random.randint(2, 5)
            self.trend_dir = 1 if random.random() > 0.5 else -1
            return
        if self.regime == MarketRegime.RANGE:
            if roll < 0.30:   self.regime=MarketRegime.TREND_UP;   self.trend_dir=1
            elif roll < 0.60: self.regime=MarketRegime.TREND_DOWN; self.trend_dir=-1
            elif roll < 0.78: self.regime=MarketRegime.MICRO
            else:             self.regime_max=random.randint(15,40)
        elif self.regime in (MarketRegime.TREND_UP, MarketRegime.TREND_DOWN):
            if roll < 0.38:   self.regime=MarketRegime.REVERSAL; self.trend_dir*=-1
            elif roll < 0.68: self.regime=MarketRegime.RANGE
            else:             self.regime_max=random.randint(10,25)
        elif self.regime == MarketRegime.MICRO:
            if random.random() < 0.55:
                self.regime=MarketRegime.BREAKOUT
                self.trend_dir=1 if random.random()>0.45 else -1
            else:
                self.regime=MarketRegime.REVERSAL
                self.trend_dir=1 if random.random()>0.5 else -1
        elif self.regime == MarketRegime.BREAKOUT:
            self.regime=(MarketRegime.TREND_UP if self.trend_dir>0
                         else MarketRegime.TREND_DOWN) if roll<0.50 else MarketRegime.RANGE
        elif self.regime in (MarketRegime.REVERSAL, MarketRegime.STOP_HUNT,
                              MarketRegime.NEWS):
            self.regime=MarketRegime.RANGE
        self.regime_max=random.randint(8,35)

    def next_bar(self) -> Bar:
        self.bar_index += 1
        idx = min(self.bar_index, len(self._profile)-1)
        vm, vl = self._profile[idx]
        self._transition()
        if self.regime == MarketRegime.TREND_UP:
            self.momentum=min(self.momentum+0.3,3.0)
            move=random.gauss(0.8,1.2)*vl+self.momentum*0.3
        elif self.regime == MarketRegime.TREND_DOWN:
            self.momentum=max(self.momentum-0.3,-3.0)
            move=random.gauss(-0.8,1.2)*vl+self.momentum*0.3
        elif self.regime == MarketRegime.RANGE:
            center=(self.range_high+self.range_low)/2
            move=random.gauss((center-self.price)*0.15,1.5)*vl
            self.momentum*=0.7
        elif self.regime == MarketRegime.MICRO:
            move=random.gauss(0,0.35)*vl; self.momentum*=0.5
        elif self.regime == MarketRegime.BREAKOUT:
            move=random.gauss(2.5*self.trend_dir,0.8)*vl
            self.momentum=self.trend_dir*2.5
        elif self.regime == MarketRegime.REVERSAL:
            move=random.gauss(-self.momentum*1.5,1.0)*vl; self.momentum*=-0.5
        elif self.regime == MarketRegime.STOP_HUNT:
            move=random.gauss(self.trend_dir*3.0,0.5)*vl
        elif self.regime == MarketRegime.NEWS:
            move=random.gauss(self.trend_dir*5.0,1.5)*vl
            self.momentum=self.trend_dir*3.0
        else:
            move=random.gauss(0,1.0)
        move=round(move/TICK)*TICK
        open_=self.price; close=round(self.price+move,2)
        high=round(max(open_,close)+abs(random.gauss(0,0.8))*vl,2)
        low =round(min(open_,close)-abs(random.gauss(0,0.8))*vl,2)
        if self.regime == MarketRegime.STOP_HUNT:
            if self.trend_dir > 0:
                high=round(close+random.uniform(2.0,4.0),2)
                close=round(open_-random.uniform(0.5,1.5),2)
            else:
                low=round(close-random.uniform(2.0,4.0),2)
                close=round(open_+random.uniform(0.5,1.5),2)
        base_vol=max(100,random.gauss(800,200)*vm)
        if self.regime==MarketRegime.BREAKOUT:   base_vol*=random.uniform(1.8,3.0)
        elif self.regime==MarketRegime.NEWS:      base_vol*=random.uniform(3.0,5.0)
        elif self.regime==MarketRegime.STOP_HUNT: base_vol*=random.uniform(1.5,2.5)
        elif self.regime==MarketRegime.MICRO:     base_vol*=0.7
        if self.regime==MarketRegime.BREAKOUT:
            delta=random.gauss(move*180*self.trend_dir,100)
        elif self.regime in (MarketRegime.STOP_HUNT, MarketRegime.REVERSAL):
            delta=random.gauss(-move*150,80)
        else:
            delta=random.gauss(move*100,150)
        if random.random()<0.07: base_vol*=1.8; delta=random.gauss(0,50)
        volume=round(base_vol); delta=round(delta)
        ask_vol=max(0,round((volume+delta)/2))
        bid_vol=max(0,volume-ask_vol)
        slippage=0.0
        if abs(move)>2.0:    slippage=round(random.uniform(0.25,0.75),2)
        elif abs(move)>1.0:  slippage=round(random.uniform(0.0,0.25),2)
        self.price=close
        return Bar(price=close,open=open_,high=high,low=low,close=close,
                   volume=volume,delta=delta,ask_volume=ask_vol,bid_volume=bid_vol,
                   trades=random.randint(30,200),
                   timestamp=time.time()+self.bar_index*60,
                   regime=self.regime,slippage=slippage)


# ══════════════════════════════════════════════════════════════════
#  TRADE RECORD
# ══════════════════════════════════════════════════════════════════

@dataclass
class TradeRecord:
    trade_id:int; direction:str; entry:float; stop:float
    target1:float; target2:float
    exit_price:float=0.0; result:str="OPEN"; pnl_pts:float=0.0; pnl_pct:float=0.0
    zone:str=""; event:str=""; score:int=0; narrative:str=""
    reason:str=""; bar_open:int=0; bar_close:int=0
    sim_regime:str=""; slippage:float=0.0
    # Confirmation
    confirmation_score:int=0; breakout_quality:int=0
    acceptance_score:int=0; delta_persistence:int=0
    expansion_eff:float=0.0; breakout_type:str=""
    acceptance_type:str=""; structure_bias:str=""
    compression_strength:int=0
    # Session regime
    session_regime:str=""; regime_confidence:int=0
    trend_strength:int=0; volatility_state:str=""
    # Continuation
    continuation_probability:int=0; runner_probability:int=0
    continuation_quality:str=""; follow_through:int=0
    # v4.3: outlier flag
    outlier_type:str=""   # "POSITIVE" | "NEGATIVE" | ""


# ══════════════════════════════════════════════════════════════════
#  PATCH 1 -- OUTLIER CONTROL SYSTEM
# ══════════════════════════════════════════════════════════════════

def _calc_outlier_adjusted_expectancy(trades: list,
                                       avg_win: float,
                                       avg_loss: float) -> tuple:
    """
    Recalcula expectancy excluyendo impacto de outliers extremos.
    Outlier positivo : pnl > avg_win  * 3  -> peso * 0.5
    Outlier negativo : pnl < avg_loss * 2.5 -> peso * 0.7
    Retorna (adj_exp, outlier_msgs)
    """
    if not trades or avg_win == 0:
        return 0.0, []

    outlier_msgs = []
    adj_pnls     = []

    for t in trades:
        pnl = t.pnl_pts
        if avg_win > 0 and pnl > avg_win * 3:
            adj = round(pnl * 0.5, 2)
            outlier_msgs.append(
                f"OUTLIER+ #{t.trade_id}: {pnl:+.2f} -> {adj:+.2f}pts"
            )
            adj_pnls.append(adj)
        elif avg_loss < 0 and pnl < avg_loss * 2.5:
            adj = round(pnl * 0.7, 2)
            outlier_msgs.append(
                f"OUTLIER- #{t.trade_id}: {pnl:+.2f} -> {adj:+.2f}pts"
            )
            adj_pnls.append(adj)
        else:
            adj_pnls.append(pnl)

    total   = len(adj_pnls)
    wins_p  = [p for p in adj_pnls if p > 0]
    loss_p  = [p for p in adj_pnls if p <= 0]
    adj_wr  = len(wins_p) / total if total > 0 else 0
    adj_aw  = sum(wins_p) / len(wins_p) if wins_p else 0.0
    adj_al  = sum(loss_p) / len(loss_p) if loss_p else 0.0
    adj_exp = round(adj_wr * adj_aw + (1 - adj_wr) * adj_al, 2)
    return adj_exp, outlier_msgs


# ══════════════════════════════════════════════════════════════════
#  PATCH 4 -- PREDICTIVE STABILITY ENGINE
# ══════════════════════════════════════════════════════════════════

def _calc_predictive_stability(trades: list,
                                wins: list,
                                losses: list) -> tuple:
    """
    stability_score = consistency(0.4) + regime_align(0.3) + edge_balance(0.3)

    consistency   : score diff normalizado (diff/15 -> 0-1)
    regime_align  : % trades en regímenes con WR >= 50%
    edge_balance  : penaliza si WEAK > 55% del PnL total
    """
    if not trades:
        return 0.0, {}

    total = len(trades)

    # Consistency
    avg_sw = sum(t.score for t in wins)   / len(wins)   if wins   else 0
    avg_sl = sum(t.score for t in losses) / len(losses) if losses else 0
    diff   = avg_sw - avg_sl
    consistency = min(max(diff / 15.0, 0.0), 1.0)

    # Regime alignment
    regime_data = defaultdict(lambda: {"w": 0, "t": 0})
    for t in trades:
        rn = t.session_regime or "UNK"
        regime_data[rn]["t"] += 1
        if t.result == "WIN":
            regime_data[rn]["w"] += 1
    good_trades = sum(
        d["t"] for d in regime_data.values()
        if d["t"] >= 3 and d["w"] / d["t"] >= 0.50
    )
    regime_alignment = good_trades / total if total > 0 else 0.5

    # Edge balance (WEAK PnL ratio)
    weak_pnl  = sum(t.pnl_pts for t in trades if t.breakout_type == "WEAK")
    total_pnl = sum(t.pnl_pts for t in trades)
    weak_ratio = abs(weak_pnl / total_pnl) if total_pnl != 0 else 0.5
    edge_balance = max(0.0, 1.0 - max(0.0, weak_ratio - 0.55))

    stability = round(
        consistency   * 0.4 +
        regime_alignment * 0.3 +
        edge_balance  * 0.3,
        3
    )

    stats = {
        "consistency":   round(consistency, 3),
        "regime_align":  round(regime_alignment, 3),
        "edge_balance":  round(edge_balance, 3),
        "weak_pnl_pct":  round(weak_ratio * 100, 1),
        "score_diff":    round(diff, 1),
    }
    return stability, stats


# ══════════════════════════════════════════════════════════════════
#  PATCH 2 -- SHADOW READINESS FLAG SYSTEM
# ══════════════════════════════════════════════════════════════════

def _calc_shadow_readiness(stability_score: float,
                            trades: list,
                            avg_win: float,
                            total_pnl: float) -> tuple:
    """
    PATCH 3: Shadow readiness recalibrada v4.5
    Pesos: execution_friction(35%) + regime_purity(30%)
           + outlier_stability(20%) + consistency(15%)
    TARGET: >= 0.80 para live shadow
    """
    if not trades:
        return 0.0, {}, False

    total = len(trades)

    # Execution friction: ratio de slippage sobre PnL bruto esperado
    slip_total = sum(t.slippage for t in trades)
    brut_pnl   = sum(abs(t.pnl_pts) for t in trades)
    friction_ratio = slip_total / brut_pnl if brut_pnl > 0 else 1.0
    friction_score = max(0.0, 1.0 - friction_ratio * 2.0)  # 0.5->0, 0->1

    # Regime purity: % trades en regímenes "limpios" (no TREND_DAY noise)
    from collections import defaultdict
    pure_regimes = {"SHORT_COVERING", "EXPANSION_DAY", "BALANCED_DAY"}
    pure_trades  = sum(1 for t in trades if t.session_regime in pure_regimes)
    trend_noise  = sum(
        1 for t in trades
        if t.session_regime == "TREND_DAY" and
           t.continuation_quality not in ("STRONG",)
    )
    regime_purity = max(0.0, (pure_trades - trend_noise) / total)

    # Outlier stability: inverso de dependencia de outliers
    outlier_pnl = sum(t.pnl_pts for t in trades if t.outlier_type == "POSITIVE")
    outlier_dep = abs(outlier_pnl / total_pnl) if total_pnl != 0 else 0.0
    outlier_stability = max(0.0, 1.0 - min(outlier_dep, 1.0))

    # Consistency: score diff normalizado (v4.3 calculation)
    consistency = min(stability_score, 1.0)

    # Score compuesto v4.5
    score = round(
        friction_score    * 0.35 +
        regime_purity     * 0.30 +
        outlier_stability * 0.20 +
        consistency       * 0.15,
        3
    )

    stats = {
        "stability":           round(stability_score,        3),
        "friction_score":      round(friction_score,         3),
        "friction_ratio_pct":  round(friction_ratio * 100,   1),
        "regime_purity":       round(regime_purity,          3),
        "outlier_stability":   round(outlier_stability,      3),
        "outlier_dep_pct":     round(outlier_dep * 100,      1),
        "friction_impact_pct": round(friction_ratio * 100,   1),
        "regime_consistency":  round(regime_purity,          3),
    }

    ready = score >= 0.80   # PATCH 3: umbral subido de 0.75 a 0.80
    return score, stats, ready


# ══════════════════════════════════════════════════════════════════
#  PATCH 3 -- EDGE REALISM NORMALIZATION
# ══════════════════════════════════════════════════════════════════

def _calc_edge_realism(trades: list) -> dict:
    """
    v4.6: Edge Attribution Matrix -- cruza breakout_type × session_regime.
    Calcula net_edge, WR, volatility y attribution_score por celda.
    Permite identificar exactamente dónde vive el edge real.
    """
    from collections import defaultdict
    import math

    # Primero por breakout_type solo (para compatibilidad con reporte)
    classes = defaultdict(lambda: {"pnl": 0.0, "slip": 0.0, "n": 0,
                                    "wins": 0, "pnls": []})
    for t in trades:
        k = t.breakout_type or "UNKNOWN"
        classes[k]["pnl"]  += t.pnl_pts
        classes[k]["slip"] += t.slippage
        classes[k]["n"]    += 1
        classes[k]["wins"] += 1 if t.result == "WIN" else 0
        classes[k]["pnls"].append(t.pnl_pts)

    result = {}
    for k, d in classes.items():
        if d["n"] == 0:
            continue
        avg_pnl  = round(d["pnl"]  / d["n"], 2)
        avg_slip = round(d["slip"] / d["n"], 3)
        net_edge = round(avg_pnl - avg_slip, 2)
        wr       = round(d["wins"] / d["n"] * 100, 1)
        # Volatility del PnL (std dev)
        mean_p   = d["pnl"] / d["n"]
        variance = sum((p - mean_p)**2 for p in d["pnls"]) / d["n"]
        vol      = round(math.sqrt(variance), 2)
        # Attribution score: net_edge / vol (Sharpe-like por categoria)
        attr_score = round(net_edge / vol, 3) if vol > 0 else 0.0
        result[k] = {
            "avg_pnl":     avg_pnl,
            "avg_slip":    avg_slip,
            "net_edge":    net_edge,
            "wr":          wr,
            "vol":         vol,
            "attr_score":  attr_score,
            "n":           d["n"],
        }
    return result


def _calc_edge_attribution_matrix(trades: list) -> list:
    """
    v4.6 FIXED: Matriz breakout_type x session_regime con attr_score.

    attr_score = net_edge / pnl_vol  (Sharpe-like por celda)
    Permite identificar:
    - Top edge sources: net_edge alto + attr_score alto
    - Edge destroyers: net_edge negativo
    - Unstable sources: net_edge positivo pero attr_score bajo (vol alta)

    Solo celdas con n >= 3.
    """
    from collections import defaultdict
    import math

    matrix = defaultdict(lambda: {
        "pnl": 0.0, "n": 0, "wins": 0,
        "slip": 0.0, "pnls": []
    })
    for t in trades:
        bq  = t.breakout_type   or "UNK"
        reg = t.session_regime  or "UNK"
        key = (bq, reg)
        matrix[key]["pnl"]   += t.pnl_pts
        matrix[key]["slip"]  += t.slippage
        matrix[key]["n"]     += 1
        matrix[key]["wins"]  += 1 if t.result == "WIN" else 0
        matrix[key]["pnls"].append(t.pnl_pts)

    rows = []
    for (bq, reg), d in matrix.items():
        if d["n"] < 3:
            continue
        avg_pnl  = round(d["pnl"]  / d["n"], 2)
        avg_slip = round(d["slip"] / d["n"], 3)
        net_edge = round(avg_pnl - avg_slip, 2)
        wr       = round(d["wins"] / d["n"] * 100, 1)
        mean_p   = d["pnl"] / d["n"]
        variance = sum((p - mean_p) ** 2 for p in d["pnls"]) / d["n"]
        vol      = round(math.sqrt(variance), 2)
        # attr_score: Sharpe-like — penaliza volatilidad alta
        attr_score = round(net_edge / vol, 3) if vol > 0 else 0.0
        rows.append({
            "bq":         bq,
            "regime":     reg,
            "net_edge":   net_edge,
            "wr":         wr,
            "avg_pnl":    avg_pnl,
            "avg_slip":   avg_slip,
            "vol":        vol,
            "attr_score": attr_score,
            "n":          d["n"],
        })

    return sorted(rows, key=lambda x: x["net_edge"], reverse=True)


# ══════════════════════════════════════════════════════════════════
#  BACKTEST ENGINE v4.4
# ══════════════════════════════════════════════════════════════════

class BacktestEngine:

    MAX_BARS_IN_TRADE = 20

    def __init__(self):
        self.event_eng   = EventEngine(window=10)
        self.conf_eng    = ConfluenceEngine(history_size=10)
        self.validator   = Validator(tick=TICK, min_liq_ticks=4)
        self.intent_eng  = IntentEngine(buffer_size=15, tick=TICK)
        self.risk_eng    = RiskEngine(tick=TICK)
        self.confirmation= ConfirmationEngine(window=20, tick=TICK)
        self.continuation= ContinuationEngine(window=12, tick=TICK)
        self.sess_regime = SessionRegimeEngine(tick=TICK)
        self.micro       = MicrostructureEngine(window=25)
        self.poc_engine  = PocAcceptanceEngine(vah=VAH, poc=POC, val=VAL, tick=TICK)
        self.levels      = create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0)
        self.simulator   = InstitutionalMarketSimulator(start_price=7150.0)
        self.trades: List[TradeRecord] = []
        self.trade_id  = 0
        self.pending   = None
        self.bar_index = 0
        self.equity    = CAPITAL
        self.equity_curve = [CAPITAL]
        self.blocked   = defaultdict(int)
        self.gate_blocked_count = 0   # PATCH 1: execution gate counter

    def run(self):
        print(f"\n{BOLD}{B}{'═'*64}{RST}")
        print(f"{BOLD}{B}  GIBBZ SMC COP -- BACKTEST v4.6{RST}")
        print(f"{BOLD}{B}  VAH={VAH}  POC={POC}  VAL={VAL}{RST}")
        print(f"{BOLD}{B}  Target:{TARGET_TRADES} | Barras:{BARS_PER_SIM}{RST}")
        print(f"{BOLD}{B}{'═'*64}{RST}\n")
        bar_count = 0
        print(f"{DIM}Procesando con session regime + continuation...{RST}")
        while bar_count < BARS_PER_SIM:
            bar = self.simulator.next_bar()
            self.bar_index += 1
            bar_count      += 1
            raw = self._bar_to_raw(bar)
            if self.pending:
                self._update_pending(bar)
            if not self.pending:
                self._process_bar(bar, raw)
            if bar_count % 500 == 0:
                print(f"  Bar {bar_count:4d} | Trades:{len(self.trades):3d} | "
                      f"P:{bar.price:.2f} | {bar.regime}")
            if len(self.trades) >= TARGET_TRADES:
                print(f"\n{G}  Target {TARGET_TRADES} trades en barra {bar_count}.{RST}")
                break
        if self.pending:
            self._close_trade(self.pending, self.simulator.price, "TIMEOUT")
        self._print_report()

    def _bar_to_raw(self, bar):
        return {
            "price": bar.close, "open": bar.open, "high": bar.high,
            "low": bar.low, "close": bar.close, "volume": bar.volume,
            "delta": bar.delta, "ask_volume": bar.ask_volume,
            "bid_volume": bar.bid_volume, "trades": bar.trades,
            "timestamp": bar.timestamp, "symbol": "MESM6",
        }

    def _process_bar(self, bar, raw):
        result  = self.event_eng.process(raw)
        context = self.levels.get_context(bar.price)
        mp = ConfluenceResult(
            event="NONE", zone=context.zone, confluence="",
            bias="NEUTRAL", score=50, classification="MEDIUM QUALITY",
            action="OBSERVE", reason="", hpz_bonus=False,
            bias_aligned=False, consecutive=0,
        )
        micro_r  = self.micro.analyze(result, context, mp, raw)
        regime_r = self.sess_regime.update(raw, result)
        conf_r   = self.confirmation.analyze(result, context, None, micro_r, raw)
        cont_r   = self.continuation.analyze(result, conf_r, regime_r, raw)

        # POC Acceptance — refinement layer institucional
        poc_r    = self.poc_engine.analyze(raw, result, conf_r)

        analysis = self.conf_eng.evaluate(
            result, context,
            confirmation   = conf_r,
            session_regime = regime_r,
            continuation   = cont_r,
            poc_acceptance = poc_r,
        )
        validation = self.validator.validate(
            analysis, result, raw,
            confirmation   = conf_r,
            session_regime = regime_r,
            poc_acceptance = poc_r,
        )
        narrative = self.intent_eng.analyze(result, context, analysis, validation)
        risk      = self.risk_eng.analyze(
            price         = bar.price,
            confluence    = analysis,
            validation    = validation,
            intent        = narrative,
            level_context = context,
        )
        if not risk.approved:
            self.blocked[risk.reason[:55]] += 1
            return

        # ── PATCH 1: EXECUTION GATE CONTROL ──────────────────────
        bq_type     = getattr(conf_r,   "breakout_type",          "")
        sess_regime = getattr(regime_r, "session_regime",          "")
        regime_conf = getattr(regime_r, "regime_confidence",       0)
        score_val   = getattr(analysis, "score",                    0)
        conf_sc     = getattr(conf_r,   "confirmation_score",      0)
        struct      = getattr(conf_r,   "structure_bias",          "NEUTRAL")
        zone        = getattr(context,  "zone",                     "")
        cont_qual   = getattr(cont_r,   "continuation_quality",    "")
        dp_score    = getattr(conf_r,   "delta_persistence",       0)
        exp_eff     = getattr(conf_r,   "expansion_efficiency",    0.5)
        follow_t    = getattr(cont_r,   "follow_through_strength", 0)

        gate_blocked, gate_reason = self._execution_gate(
            bq_type, sess_regime, score_val, conf_sc,
            struct, zone, cont_qual, regime_conf,
            dp_score, exp_eff, follow_t
        )
        if gate_blocked:
            self.blocked[f"EXEC_GATE: {gate_reason}"] += 1
            return

        self._open_trade(bar, risk, analysis, narrative, context,
                         conf_r, regime_r, cont_r)

    def _execution_gate(self, bq_type: str, sess_regime: str,
                         score: int, conf_sc: int,
                         struct: str, zone: str,
                         cont_qual: str,
                         regime_conf: int = 50,
                         dp_score: int = 50,
                         exp_eff: float = 0.5,
                         follow_t: int = 50) -> tuple:
        """
        v4.6 FIXED + quality gate para EXPLOSIVE.

        EXPLOSIVE ahora requiere:
        - delta_persistence >= 50 (Trade #27 tenia dp=30 -- fake explosive)
        - expansion_efficiency >= 0.40 (Trade #27 tenia eff=0.35 -- absorbido)
        - follow_through >= 50

        Sin estas condiciones, EXPLOSIVE es classified como FAKE_EXPLOSIVE.
        """
        # AT_POC: solo EXPLOSIVE
        if zone == "AT_POC" and bq_type != "EXPLOSIVE":
            return True, "AT_POC non-EXPLOSIVE"

        # TREND_DAY -- quality tiers
        if sess_regime == "TREND_DAY":
            if bq_type == "EXPLOSIVE":
                # NUEVO: EXPLOSIVE requiere quality real
                if dp_score < 50:
                    return True, f"EXPLOSIVE fake: dp={dp_score} < 50"
                if exp_eff < 0.40:
                    return True, f"EXPLOSIVE absorbed: eff={round(exp_eff,2)} < 0.40"
                if follow_t < 50:
                    return True, f"EXPLOSIVE no followthru: ft={follow_t} < 50"
                # pass
            elif bq_type == "REAL":
                if cont_qual != "STRONG":
                    return True, "TREND_DAY REAL without STRONG cont"
                if conf_sc < 75:
                    return True, "TREND_DAY REAL conf < 75"
                if struct == "NEUTRAL":
                    return True, "TREND_DAY REAL struct NEUTRAL"
                if score < 72:
                    return True, "TREND_DAY REAL score < 72"
                # FIX 1: dp bajo en TREND_DAY = momentum inconsistente
                # Trade #42 pattern: dp=30 con REAL en tendencia = continuacion fragil
                # Umbral de 55 porque TREND_DAY requiere persistencia minima
                if dp_score < 55:
                    return True, f"TREND_DAY REAL low dp={dp_score} < 55"
            else:
                return True, f"TREND_DAY noise tier ({bq_type})"

        # BALANCED_DAY -- solo REAL con regimen confirmado
        elif sess_regime == "BALANCED_DAY":
            if bq_type == "EXPLOSIVE":
                return True, "BALANCED_DAY EXPLOSIVE blocked (edge destroyer)"
            if bq_type != "REAL":
                return True, "BALANCED_DAY non-REAL"
            if regime_conf < 50:
                return True, f"BALANCED_DAY low regime_conf ({regime_conf}%)"
            if conf_sc < 75 or struct == "NEUTRAL":
                return True, "BALANCED_DAY REAL low-quality"

        # SHORT_COVERING -- permisivo
        elif sess_regime == "SHORT_COVERING":
            pass

        # EXPLOSIVE global: quality check (fuera de TREND_DAY)
        if bq_type == "EXPLOSIVE":
            if sess_regime != "SHORT_COVERING":
                if score < 85:
                    return True, "EXPLOSIVE score < 85"
                if dp_score < 50:
                    return True, f"EXPLOSIVE fake dp={dp_score}"
                if exp_eff < 0.35:
                    return True, f"EXPLOSIVE absorbed eff={round(exp_eff,2)}"

        # REAL score y estructura (fuera de TREND_DAY)
        if bq_type == "REAL" and sess_regime != "TREND_DAY":
            if score < 75:
                return True, "REAL score < 75"
            if struct == "NEUTRAL":
                return True, "REAL structure NEUTRAL"

        # MODERATE: solo SHORT_COVERING
        if bq_type == "MODERATE" and sess_regime not in ("SHORT_COVERING",):
            return True, "MODERATE non-SC regime"

        return False, ""

    def _open_trade(self, bar, risk, analysis, narrative, context,
                    conf, regime, cont):

        # ── PATCH 1: EXECUTION FRICTION MODEL ────────────────────
        bq_type     = getattr(conf,   "breakout_type",  "")
        sess_regime = getattr(regime, "session_regime", "")
        sim_reg     = bar.regime
        cont_qual   = getattr(cont,   "continuation_quality", "")

        slip = bar.slippage

        # Friction aumentada en regímenes volátiles
        if sess_regime in ("EXPANSION_DAY", "HIGH_VOL_DAY", "TRAPPED_DAY"):
            slip = round(slip * 1.8, 2)

        # PATCH 2: AT_POC -- slippage +40%
        zone_val = getattr(getattr(conf, "zone", None), "__str__", lambda: "")()
        # extraer zone del context pasado a _open_trade via analysis
        zone_str = getattr(analysis, "zone", "") if analysis else ""
        if zone_str == "AT_POC":
            slip = round(slip * 1.4, 2)

        # EXPLOSIVE: varianza de slippage aumentada
        if bq_type == "EXPLOSIVE":
            slip = round(slip + random.uniform(0.3, 1.2), 2)

        # PATCH 2: TREND_DAY split -- TREND_DAY sin STRONG continuation degrada
        trend_day_noise = (
            sess_regime == "TREND_DAY" and
            cont_qual not in ("STRONG",)
        )
        if trend_day_noise:
            slip = round(slip * 1.35, 2)  # más friction en TREND_DAY ruidoso

        # FILL LATENCY: missed entry probability
        miss_rate = 0.0
        if sim_reg == MarketRegime.REVERSAL:
            miss_rate = 0.12
        elif sim_reg == MarketRegime.RANGE:
            miss_rate = 0.06

        if miss_rate > 0 and random.random() < miss_rate:
            # Trade perdido por latencia de ejecución -- registrar en blocked
            self.blocked[f"FILL_LATENCY: {sim_reg}"] += 1
            return

        self.trade_id += 1
        entry = bar.price + (slip if risk.direction == "LONG" else -slip)

        # ── RR CAP INSTITUCIONAL ──────────────────────────────────
        # R:R irreal (>4) indica target del risk engine inflado por score alto.
        # En live no se ejecutarían esos targets — cappear para realismo.
        raw_stop   = risk.stop
        raw_target = risk.target_1
        risk_pts   = abs(entry - raw_stop)

        if risk_pts > 0:
            raw_rr = abs(raw_target - entry) / risk_pts
            # Cap por tipo: EXPLOSIVE max 3.5, REAL max 4.0, otros max 3.0
            if bq_type == "EXPLOSIVE":
                max_rr = 3.5
            elif bq_type == "REAL":
                max_rr = 4.0
            else:
                max_rr = 3.0

            if raw_rr > max_rr:
                # Recalcular target con RR cappado
                if risk.direction == "LONG":
                    capped_target = round(entry + risk_pts * max_rr, 2)
                else:
                    capped_target = round(entry - risk_pts * max_rr, 2)
            else:
                capped_target = raw_target
        else:
            capped_target = raw_target
        t = TradeRecord(
            trade_id   = self.trade_id,
            direction  = risk.direction,
            entry      = round(entry, 2),
            stop       = risk.stop,
            target1    = capped_target,
            target2    = risk.target_2,
            zone       = getattr(context,   "zone",      ""),
            event      = getattr(analysis,  "event",     ""),
            score      = getattr(analysis,  "score",     0),
            narrative  = getattr(narrative, "narrative", ""),
            reason     = risk.reason[:80],
            bar_open   = self.bar_index,
            sim_regime = bar.regime,
            slippage   = slip,
            confirmation_score   = getattr(conf,   "confirmation_score",      0),
            breakout_quality     = getattr(conf,   "breakout_quality",         0),
            acceptance_score     = getattr(conf,   "acceptance_score",         0),
            delta_persistence    = getattr(conf,   "delta_persistence",        0),
            expansion_eff        = getattr(conf,   "expansion_efficiency",     0.0),
            breakout_type        = getattr(conf,   "breakout_type",           ""),
            acceptance_type      = getattr(conf,   "acceptance_type",         ""),
            structure_bias       = getattr(conf,   "structure_bias",    "NEUTRAL"),
            compression_strength = getattr(conf,   "compression_strength",     0),
            session_regime       = getattr(regime, "session_regime",           ""),
            regime_confidence    = getattr(regime, "regime_confidence",         0),
            trend_strength       = getattr(regime, "trend_strength",            0),
            volatility_state     = getattr(regime, "volatility_state",    "NORMAL"),
            continuation_probability = getattr(cont, "continuation_probability", 50),
            runner_probability       = getattr(cont, "runner_probability",       30),
            continuation_quality     = getattr(cont, "continuation_quality",     ""),
            follow_through           = getattr(cont, "follow_through_strength",   0),
        )
        self.pending = t

    def _update_pending(self, bar):
        t = self.pending
        bars_open = self.bar_index - t.bar_open
        if t.direction == "LONG":
            if bar.low  <= t.stop:    self._close_trade(t, t.stop,   "LOSS"); return
            if bar.high >= t.target1: self._close_trade(t, t.target1, "WIN"); return
        elif t.direction == "SHORT":
            if bar.high >= t.stop:    self._close_trade(t, t.stop,   "LOSS"); return
            if bar.low  <= t.target1: self._close_trade(t, t.target1, "WIN"); return
        if bars_open >= self.MAX_BARS_IN_TRADE:
            self._close_trade(t, bar.price, "TIMEOUT")

    def _close_trade(self, t, exit_price, result):
        slip2 = round(random.uniform(0, 0.25), 2)
        if result == "LOSS":
            exit_price += (slip2 if t.direction == "LONG" else -slip2)
        t.exit_price = round(exit_price, 2)
        t.result     = result
        t.bar_close  = self.bar_index
        raw_pnl = round(
            (exit_price - t.entry) if t.direction == "LONG"
            else (t.entry - exit_price), 2
        )

        # ── PATCH 1: OUTLIER REALISM CORRECTION ──────────────────
        # Ganancias extremas raras en ejecución real -- aplicar cap decay
        effective_pnl = raw_pnl
        if raw_pnl > 100:
            effective_pnl = round(raw_pnl * 0.45, 2)
            t.outlier_type = "POSITIVE"
        elif raw_pnl > 50:
            effective_pnl = round(raw_pnl * 0.60, 2)
            t.outlier_type = "POSITIVE"

        # PATCH 4: AT_POC -- reducir PnL esperado 25% (probability weight)
        if t.zone == "AT_POC":
            effective_pnl = round(effective_pnl * 0.75, 2)

        # PATCH 5: MODERATE -- cap de contribución al expectancy
        if t.breakout_type == "MODERATE":
            # Score contribution reducido: PnL cappado al 80%
            effective_pnl = round(effective_pnl * 0.80, 2)

        # PATCH 1: STRONG continuation en TREND_DAY -- reducir win inflation 10%
        if (result == "WIN" and
                t.continuation_quality == "STRONG" and
                t.session_regime == "TREND_DAY"):
            effective_pnl = round(effective_pnl * 0.90, 2)

        t.pnl_pts = effective_pnl
        pnl_usd   = t.pnl_pts * 5
        t.pnl_pct = round(pnl_usd / self.equity * 100, 3)
        self.equity += pnl_usd
        self.equity_curve.append(self.equity)
        self.trades.append(t)
        self.pending = None

    # ──────────────────────────────────────────────────────────────
    #  REPORTE v4.3
    # ──────────────────────────────────────────────────────────────

    def _print_report(self):
        trades = self.trades
        if not trades:
            print(f"\n{R}Sin trades.{RST}")
            self._print_blocked()
            return

        wins    = [t for t in trades if t.result == "WIN"]
        losses  = [t for t in trades if t.result == "LOSS"]
        tos     = [t for t in trades if t.result == "TIMEOUT"]
        total   = len(trades)
        wr      = round(len(wins) / total * 100, 1)
        avg_win = round(sum(t.pnl_pts for t in wins)   / len(wins),   2) if wins   else 0
        avg_loss= round(sum(t.pnl_pts for t in losses) / len(losses), 2) if losses else 0
        exp     = round((wr/100 * avg_win) + ((1 - wr/100) * avg_loss), 2)

        peak = CAPITAL; max_dd = 0.0
        for eq in self.equity_curve:
            if eq > peak: peak = eq
            dd = (peak - eq) / peak * 100
            if dd > max_dd: max_dd = dd

        ms = cs = 0
        for t in trades:
            if t.result in ("LOSS", "TIMEOUT"): cs += 1; ms = max(ms, cs)
            else: cs = 0

        total_pnl = round(sum(t.pnl_pts for t in trades), 2)
        avg_slip  = round(sum(t.slippage for t in trades) / total, 3)

        # ── PATCH 1: OUTLIER CONTROL ──────────────────────────────
        adj_exp, outlier_msgs = _calc_outlier_adjusted_expectancy(
            trades, avg_win, avg_loss
        )

        print(f"\n{BOLD}{B}{'═'*64}{RST}")
        print(f"{BOLD}{W}  GIBBZ SMC COP -- BACKTEST REPORT v4.6{RST}")
        print(f"{BOLD}{B}{'═'*64}{RST}")

        print(f"\n{BOLD}{C}  PERFORMANCE{RST}")
        print(f"  {'─'*60}")
        print(f"  Total trades      : {W}{total}{RST}")
        print(f"  W/L/TO            : {G}{len(wins)}{RST}/{R}{len(losses)}{RST}/{Y}{len(tos)}{RST}")
        wc = G if wr >= 45 else Y if wr >= 35 else R
        print(f"  Win rate          : {wc}{wr}%{RST}  {self._bar(wr, 100)}")
        print(f"  Avg win           : {G}+{avg_win}pts{RST}")
        print(f"  Avg loss          : {R}{avg_loss}pts{RST}")
        ec = G if exp > 0 else R
        print(f"  Expectancy        : {ec}{exp}pts/trade{RST}")
        # PATCH 1: outlier-adjusted expectancy
        ec2 = G if adj_exp > 0 else R
        print(f"  Expectancy (adj)  : {ec2}{adj_exp}pts/trade{RST}  "
              f"{DIM}(outlier-adjusted){RST}")
        if outlier_msgs:
            print(f"  Outliers          : {Y}{len(outlier_msgs)} detectados{RST}")
            for om in outlier_msgs[:5]:
                print(f"    {DIM}{om}{RST}")
        print(f"  Max drawdown      : {R}{round(max_dd,1)}%{RST}")
        print(f"  Max losing streak : {R}{ms}{RST}")
        print(f"  Total PnL         : {G if total_pnl>0 else R}"
              f"{total_pnl}pts (${round(total_pnl*5,0)}){RST}")
        print(f"  Avg slippage      : {avg_slip}pts")

        edge = self._edge_score(wr, exp, max_dd)
        ec3  = G if edge >= 60 else Y if edge >= 40 else R
        print(f"\n  Edge Score        : {ec3}{BOLD}{edge}/100{RST}  "
              f"{self._edge_label(edge)}")

        # ── SCORE PREDICTIVO ──────────────────────────────────────
        sw    = round(sum(t.score for t in wins)   / len(wins),   1) if wins   else 0
        sl    = round(sum(t.score for t in losses) / len(losses), 1) if losses else 0
        cw    = round(sum(t.confirmation_score for t in wins)   / len(wins),   1) if wins   else 0
        cl    = round(sum(t.confirmation_score for t in losses) / len(losses), 1) if losses else 0
        diff  = round(sw - sl, 1)
        cdiff = round(cw - cl, 1)
        dc    = G if diff  > 8 else Y if diff  > 3 else R
        cdc   = G if cdiff > 8 else Y if cdiff > 3 else R

        print(f"\n{BOLD}{C}  SCORE PREDICTIVO{RST}")
        print(f"  {'─'*60}")
        print(f"  Score principal  wins:{G}{sw}{RST} losses:{R}{sl}{RST} "
              f"diff:{dc}{'+' if diff>=0 else ''}{diff}{RST} "
              f"{'OK META ALCANZADA' if diff>=8 else '! MEJORAR' if diff>0 else 'X SIN EDGE'}")
        print(f"  Conf score       wins:{G}{cw}{RST} losses:{R}{cl}{RST} "
              f"diff:{cdc}{'+' if cdiff>=0 else ''}{cdiff}{RST}")

        # ── PATCH 2+4: SHADOW READINESS + SHADOW SIMULATION VIEW ─
        stab, stab_stats = _calc_predictive_stability(trades, wins, losses)
        shadow_score, shadow_stats, shadow_ready = _calc_shadow_readiness(
            stab, trades, avg_win, total_pnl
        )
        edge_real = _calc_edge_realism(trades)

        # Theoretical vs execution-adjusted PnL
        theoretical_pnl = round(sum(
            t.pnl_pts / 0.60 if t.outlier_type == "POSITIVE" else t.pnl_pts
            for t in trades
        ), 2)
        slip_total = round(sum(t.slippage for t in trades), 2)
        slip_impact_pct = round(abs(slip_total / theoretical_pnl * 100), 1) if theoretical_pnl != 0 else 0
        missed = sum(1 for v in self.blocked.values()
                     if "FILL_LATENCY" in str(v) or
                     any("FILL_LATENCY" in k for k in self.blocked.keys()
                         if self.blocked[k] == v))
        # Contar missed entries directamente
        missed_entries = sum(v for k, v in self.blocked.items()
                             if "FILL_LATENCY" in k)

        print(f"\n{BOLD}{C}  SHADOW SIMULATION VIEW (v4.5){RST}")
        print(f"  {'─'*60}")

        # PATCH 6: Execution Gate Status
        gate_blocked = sum(v for k, v in self.blocked.items()
                           if k.startswith("EXEC_GATE:"))
        gate_total   = total + gate_blocked
        gate_pass_pct= round(total / gate_total * 100, 1) if gate_total > 0 else 100.0
        gate_status  = f"{G}OPEN{RST}" if shadow_ready else f"{R}CLOSED{RST}"
        print(f"  Execution Gate    : {gate_status}  "
              f"(pass={gate_pass_pct}% | blocked={gate_blocked} signals)")

        # PATCH 6: Live eligible set -- solo EXPLOSIVE + REAL en SHORT_COVERING
        live_eligible = [
            t for t in trades
            if t.breakout_type in ("EXPLOSIVE", "REAL") and
               t.session_regime == "SHORT_COVERING"
        ]
        live_wr = (round(sum(1 for t in live_eligible if t.result == "WIN") /
                         len(live_eligible) * 100, 1)
                   if live_eligible else 0.0)
        print(f"  Live eligible set : {W}{len(live_eligible)}{RST} trades  "
              f"WR={G if live_wr>=50 else R}{live_wr}%{RST}  "
              f"{DIM}(EXPLOSIVE/REAL x SHORT_COVERING){RST}")

        print(f"  Theoretical PnL   : {G if theoretical_pnl>0 else R}"
              f"{theoretical_pnl:+.2f}pts{RST}")
        print(f"  Execution adj PnL : {G if total_pnl>0 else R}"
              f"{total_pnl:+.2f}pts{RST}  "
              f"{DIM}(friction applied){RST}")
        print(f"  Slippage impact   : {Y}{slip_impact_pct}%{RST}  "
              f"({slip_total:+.2f}pts total)")
        print(f"  Missed entries    : {Y}{missed_entries}{RST}  "
              f"{DIM}(fill latency){RST}")
        sc = G if shadow_score >= 0.80 else Y if shadow_score >= 0.65 else R
        print(f"  Shadow readiness  : {sc}{BOLD}{shadow_score:.3f}{RST}  "
              f"{'OK SHADOW READY' if shadow_ready else 'X NOT READY FOR LIVE'}")
        print(f"    friction_score={shadow_stats['friction_score']:.3f}  "
              f"regime_purity={shadow_stats['regime_purity']:.3f}  "
              f"outlier_stab={shadow_stats['outlier_stability']:.3f}  "
              f"consistency={shadow_stats['stability']:.3f}")

        # PATCH 3: Edge Realism -- jerarquía bajo friction con vol y attr_score
        print(f"\n{BOLD}{C}  EDGE REALISM NORMALIZATION (v4.6){RST}")
        print(f"  {'─'*60}")
        for k, d in sorted(edge_real.items(),
                             key=lambda x: x[1]["net_edge"], reverse=True):
            nc = G if d["net_edge"] > 0 else R
            ac = G if d["attr_score"] > 0.1 else Y if d["attr_score"] > 0 else R
            print(f"  {k:<14} net={nc}{d['net_edge']:+.2f}pts{RST}  "
                  f"wr={d['wr']:4.1f}%  "
                  f"vol={d['vol']:.2f}  "
                  f"attr={ac}{d['attr_score']:+.3f}{RST}  "
                  f"n={d['n']:3d}")

        # v4.6: EDGE ATTRIBUTION MATRIX (FIXED)
        matrix_rows = _calc_edge_attribution_matrix(trades)
        if matrix_rows:
            print(f"\n{BOLD}{C}  EDGE ATTRIBUTION MATRIX -- breakout x regime (v4.6){RST}")
            print(f"  {'─'*60}")
            print(f"  {'BQ':<12} {'REGIME':<22} {'net':>7}  {'WR':>6}  "
                  f"{'avg':>7}  {'vol':>5}  {'attr':>6}  n")
            for row in matrix_rows[:12]:
                nc  = G if row["net_edge"] > 0 else R
                wrc = G if row["wr"] >= 50 else Y if row["wr"] >= 35 else R
                ac  = (G if row.get("attr_score", 0) > 0.20 else
                       Y if row.get("attr_score", 0) > 0 else R)
                print(f"  {row['bq']:<12} {row['regime']:<22} "
                      f"{nc}{row['net_edge']:+6.2f}pts{RST}  "
                      f"{wrc}{row['wr']:5.1f}%{RST}  "
                      f"{row['avg_pnl']:+6.2f}pts  "
                      f"{row.get('vol', 0):5.2f}  "
                      f"{ac}{row.get('attr_score', 0):+6.3f}{RST}  "
                      f"{row['n']:3d}")
            top = [r for r in matrix_rows
                   if r["net_edge"] > 0 and r.get("attr_score", 0) > 0.15][:3]
            if top:
                print(f"\n  {G}Top edge sources (net>0 + attr>0.15):{RST}")
                for r in top:
                    print(f"    {G}{r['bq']} x {r['regime']}: "
                          f"net={r['net_edge']:+.2f}  WR={r['wr']}%  "
                          f"attr={r.get('attr_score', 0):+.3f}  n={r['n']}{RST}")
            unstable = [r for r in matrix_rows
                        if r["net_edge"] > 0 and r.get("attr_score", 0) <= 0.15
                        and r["n"] >= 5][:3]
            if unstable:
                print(f"\n  {Y}Unstable sources (high vol, low attr):{RST}")
                for r in unstable:
                    print(f"    {Y}{r['bq']} x {r['regime']}: "
                          f"net={r['net_edge']:+.2f}  vol={r.get('vol', 0):.2f}  "
                          f"attr={r.get('attr_score', 0):+.3f}  n={r['n']}{RST}")
            bottom = [r for r in reversed(matrix_rows) if r["net_edge"] < 0][:3]
            if bottom:
                print(f"\n  {R}Edge destroyers (net<0):{RST}")
                for r in bottom:
                    print(f"    {R}{r['bq']} x {r['regime']}: "
                          f"net={r['net_edge']:+.2f}  WR={r['wr']}%  n={r['n']}{RST}")

        if stab >= 0.85:
            stab_label = f"{G}{BOLD}INSTITUTIONAL MODE ACTIVE{RST}"
        elif stab >= 0.70:
            stab_label = f"{Y}STABLE{RST}"
        else:
            stab_label = f"{R}INSTABILITY -- review WEAK weight{RST}"

        print(f"\n{BOLD}{C}  PREDICTIVE STABILITY ENGINE (v4.3){RST}")
        print(f"  {'─'*60}")
        print(f"  Stability score   : {stab:.3f}  {stab_label}")
        print(f"  Consistency       : {stab_stats['consistency']:.3f}  "
              f"(score diff={stab_stats['score_diff']:+.1f}pts)")
        print(f"  Regime alignment  : {stab_stats['regime_align']:.3f}")
        wk_c = R if stab_stats['weak_pnl_pct'] > 55 else G
        print(f"  Edge balance      : {stab_stats['edge_balance']:.3f}  "
              f"(WEAK PnL={wk_c}{stab_stats['weak_pnl_pct']}%{RST})")
        if stab_stats['weak_pnl_pct'] > 55:
            print(f"  {Y}! WEAK contribuye {stab_stats['weak_pnl_pct']}% del PnL "
                  f"-- posible dependencia excesiva{RST}")

        # ── PATCH 5: EDGE DISTRIBUTION ────────────────────────────
        print(f"\n{BOLD}{C}  EDGE DISTRIBUTION (v4.3){RST}")
        print(f"  {'─'*60}")
        bq_pnl = defaultdict(float)
        bq_cnt = defaultdict(int)
        for t in trades:
            k = t.breakout_type or "UNKNOWN"
            bq_pnl[k] += t.pnl_pts
            bq_cnt[k] += 1
        total_pnl_abs = sum(abs(v) for v in bq_pnl.values())
        for k in sorted(bq_pnl.keys()):
            pct  = round(bq_pnl[k] / total_pnl * 100, 1) if total_pnl != 0 else 0.0
            pcts = round(abs(bq_pnl[k]) / total_pnl_abs * 100, 1) if total_pnl_abs != 0 else 0.0
            pc   = G if pct > 0 else R
            warn = f"  {Y}<- alto{RST}" if k == "WEAK" and pcts > 55 else ""
            print(f"  {k:<14} PnL={pc}{bq_pnl[k]:+7.2f}pts{RST} "
                  f"({pct:+5.1f}%)  n={bq_cnt[k]:3d}  weight={pcts:.1f}%{warn}")

        # ── POR SESSION REGIME ────────────────────────────────────
        print(f"\n{BOLD}{C}  WR POR SESSION REGIME{RST}")
        print(f"  {'─'*60}")
        reg_stats = self._group_stats(trades, "session_regime")
        for reg, s in sorted(reg_stats.items(),
                               key=lambda x: x[1]["wr"], reverse=True):
            wc2 = G if s["wr"] >= 45 else Y if s["wr"] >= 35 else R
            print(f"  {reg:<22} WR={wc2}{s['wr']:5.1f}%{RST}  "
                  f"n={s['n']:3d}  avg={s['avg_pnl']:+.2f}pts  "
                  f"{self._bar(s['wr'], 100, 12)}")

        # ── POR BREAKOUT TYPE ─────────────────────────────────────
        print(f"\n{BOLD}{C}  WR POR BREAKOUT TYPE{RST}")
        print(f"  {'─'*60}")
        bq_stats = self._group_stats(trades, "breakout_type")
        for bt, s in sorted(bq_stats.items(),
                              key=lambda x: x[1]["wr"], reverse=True):
            wc2 = G if s["wr"] >= 45 else Y if s["wr"] >= 35 else R
            print(f"  {bt:<14} WR={wc2}{s['wr']:5.1f}%{RST}  "
                  f"n={s['n']:3d}  avg={s['avg_pnl']:+.2f}pts  "
                  f"{self._bar(s['wr'], 100, 12)}")

        # ── POR CONTINUATION QUALITY ──────────────────────────────
        print(f"\n{BOLD}{C}  WR POR CONTINUATION QUALITY{RST}")
        print(f"  {'─'*60}")
        cq_stats = self._group_stats(trades, "continuation_quality")
        for cq, s in sorted(cq_stats.items(),
                              key=lambda x: x[1]["wr"], reverse=True):
            wc2 = G if s["wr"] >= 45 else R
            print(f"  {cq:<14} WR={wc2}{s['wr']:5.1f}%{RST}  "
                  f"n={s['n']:3d}  avg={s['avg_pnl']:+.2f}pts")

        # ── POR ZONA ──────────────────────────────────────────────
        print(f"\n{BOLD}{C}  WR POR ZONA{RST}")
        print(f"  {'─'*60}")
        for zone, s in sorted(self._group_stats(trades, "zone").items(),
                               key=lambda x: x[1]["wr"], reverse=True):
            wc2 = G if s["wr"] >= 45 else R
            print(f"  {zone:<22} WR={wc2}{s['wr']:5.1f}%{RST}  "
                  f"n={s['n']:3d}  avg={s['avg_pnl']:+.2f}pts  "
                  f"{self._bar(s['wr'], 100, 12)}")

        # ── POR EVENTO ────────────────────────────────────────────
        print(f"\n{BOLD}{C}  WR POR EVENTO{RST}")
        print(f"  {'─'*60}")
        for ev, s in sorted(self._group_stats(trades, "event").items(),
                              key=lambda x: x[1]["wr"], reverse=True):
            wc2 = G if s["wr"] >= 45 else R
            print(f"  {ev:<22} WR={wc2}{s['wr']:5.1f}%{RST}  "
                  f"n={s['n']:3d}  avg={s['avg_pnl']:+.2f}pts")

        # ── BREAKOUT x RÉGIMEN ────────────────────────────────────
        print(f"\n{BOLD}{C}  BREAKOUT TYPE x SESSION REGIME{RST}")
        print(f"  {'─'*60}")
        cross = defaultdict(lambda: {"t": 0, "w": 0, "pnl": 0.0})
        for t in trades:
            k = f"{t.breakout_type or 'NONE'}_{t.session_regime or 'UNK'}"
            cross[k]["t"] += 1
            if t.result == "WIN": cross[k]["w"] += 1
            cross[k]["pnl"] += t.pnl_pts
        for k, d in sorted(cross.items(),
                             key=lambda x: x[1]["t"], reverse=True)[:15]:
            bwr  = round(d["w"] / d["t"] * 100, 1) if d["t"] else 0
            bavg = round(d["pnl"] / d["t"], 2)      if d["t"] else 0
            wc2  = G if bwr >= 50 else Y if bwr >= 35 else R
            print(f"  {k:<35} WR={wc2}{bwr:5.1f}%{RST}  "
                  f"n={d['t']:3d}  avg={bavg:+.2f}")

        # ── ÚLTIMOS 5 TRADES DEBUG ────────────────────────────────
        print(f"\n{BOLD}{C}  ÚLTIMOS 5 TRADES -- DEBUG EXTREMO{RST}")
        print(f"  {'─'*60}")
        for t in trades[-5:]:
            rc = G if t.result == "WIN" else R if t.result == "LOSS" else Y
            ol = f"  {Y}[OUTLIER]{RST}" if t.outlier_type else ""
            print(f"\n  {BOLD}#{t.trade_id} {rc}{t.result}{RST} {t.direction} "
                  f"{t.zone} score={t.score}{ol}")
            print(f"    Evento    : {t.event} | Narrativa: {t.narrative}")
            print(f"    Razón     : {t.reason}")
            print(f"    SESSION   : regime={t.session_regime} "
                  f"conf={t.regime_confidence}% trend={t.trend_strength} "
                  f"vol={t.volatility_state}")
            print(f"    CONFIRM   : score={t.confirmation_score} "
                  f"bq={t.breakout_quality} dp={t.delta_persistence} "
                  f"acc={t.acceptance_score} eff={round(t.expansion_eff,2)}")
            print(f"    BQ_TYPE   : {t.breakout_type} | "
                  f"ACC: {t.acceptance_type} | "
                  f"STRUCT: {t.structure_bias}")
            print(f"    CONT      : prob={t.continuation_probability}% "
                  f"runner={t.runner_probability}% "
                  f"quality={t.continuation_quality} "
                  f"ft={t.follow_through}")
            print(f"    PnL       : {rc}{'+' if t.pnl_pts>0 else ''}"
                  f"{t.pnl_pts}pts{RST} | slip={t.slippage}")

        self._print_blocked()
        self._print_recommendations(trades, wr, diff, cdiff)
        print(f"\n{BOLD}{B}{'═'*64}{RST}\n")

    def _print_blocked(self):
        if not self.blocked:
            return
        print(f"\n{BOLD}{C}  TOP RAZONES DE BLOQUEO{RST}")
        print(f"  {'─'*60}")
        for reason, count in sorted(self.blocked.items(),
                                     key=lambda x: x[1], reverse=True)[:12]:
            print(f"  {count:5d}x  {reason}")

    def _print_recommendations(self, trades, wr, score_diff, conf_diff):
        print(f"\n{BOLD}{C}  RECOMENDACIONES{RST}")
        print(f"  {'─'*60}")
        if wr < 35:
            print(f"  {R}X WR<35% -- subir MIN_CONFIRMATION_SCORE{RST}")
        if score_diff >= 8:
            print(f"  {G}OK META ALCANZADA: score predice resultados (+{score_diff}pts){RST}")
        elif score_diff > 0:
            print(f"  {Y}! Score parcialmente predictivo (+{score_diff}) -- meta: +8pts{RST}")
        else:
            print(f"  {R}X Score no predice -- revisar pesos en confluence_engine.py{RST}")

        reg_s     = self._group_stats(trades, "session_regime")
        best_reg  = max(reg_s.items(), key=lambda x: x[1]["wr"],
                        default=(None, {"wr": 0}))
        worst_reg = min(reg_s.items(), key=lambda x: x[1]["wr"],
                        default=(None, {"wr": 0}))
        if best_reg[0] and best_reg[1]["n"] >= 5:
            print(f"  {G}OK Mejor régimen: {best_reg[0]} WR={best_reg[1]['wr']}%{RST}")
        if worst_reg[0] and worst_reg[1]["n"] >= 5 and worst_reg[1]["wr"] < 35:
            print(f"  {R}X Evitar régimen: {worst_reg[0]} WR={worst_reg[1]['wr']}%{RST}")

        bq_s = self._group_stats(trades, "breakout_type")
        for bt, s in bq_s.items():
            if s["n"] >= 5 and s["wr"] < 30:
                print(f"  {R}X {bt} WR={s['wr']}% -- filtrar más{RST}")
            if s["n"] >= 5 and s["wr"] >= 65:
                print(f"  {G}OK {bt} WR={s['wr']}% -- priorizar{RST}")

    def _group_stats(self, trades, field_name):
        g = defaultdict(list)
        for t in trades:
            val = getattr(t, field_name, "") or "UNKNOWN"
            g[val].append(t)
        return {
            k: {
                "n":       len(v),
                "wr":      round(sum(1 for t in v if t.result == "WIN") / len(v) * 100, 1),
                "avg_pnl": round(sum(t.pnl_pts for t in v) / len(v), 2),
            }
            for k, v in g.items()
        }

    def _edge_score(self, wr, exp, dd):
        s = min(30, int(wr * 0.5))
        if exp > 0: s += min(30, int(exp * 10))
        if dd < 10:  s += 20
        elif dd < 20: s += 10
        elif dd < 30: s += 5
        return min(s, 100)

    def _edge_label(self, score):
        if score >= 70: return f"{G}{BOLD}EDGE CONFIRMADO{RST}"
        if score >= 50: return f"{Y}EDGE EN DESARROLLO{RST}"
        if score >= 30: return f"{Y}EDGE DÉBIL{RST}"
        return f"{R}SIN EDGE{RST}"

    @staticmethod
    def _bar(v, mx, w=20):
        f = int((v / mx) * w)
        return f"{DIM}[{'█'*f}{'░'*(w-f)}]{RST}"


if __name__ == "__main__":
    bt = BacktestEngine()
    bt.run()