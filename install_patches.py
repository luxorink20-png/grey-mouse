"""
GIBBZ — Installer de patches v2.1
Escribe los 4 archivos corregidos directamente en la carpeta core.
Correr desde: C:\Users\valer\Desktop\GIBBZ\core
Comando: python install_patches.py
"""

import os

BASE = os.path.dirname(os.path.abspath(__file__))

FILES = {}

# ══════════════════════════════════════════════════════════════════
FILES["confirmation_engine.py"] = r'''# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — confirmation_engine.py
#  Institutional Confirmation Engine v2.1
#
#  CAMBIOS v2.1 vs v2.0:
#  — Swing detector con lookback real (3 bars cada lado, antes era 1)
#  — Magnitude filter: mínimo MIN_SWING_TICKS para swing válido
#  — Structure hysteresis: bias no puede flippear instantáneamente
#  — Flip protection: requiere MIN_BIAS_BARS antes de permitir flip
#  — Flip confirmation: requiere FLIP_CONFIRM_BARS consecutivos
#  — Threshold con tick buffer en comparaciones HH/HL/LH/LL
# ╚══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass, field
from collections import deque
from typing import Optional

TICK = 0.25

MIN_BREAKOUT_TICKS  = 3
MIN_CONFIRMATION_SCORE = 50

MIN_SWING_TICKS    = 6
MIN_BIAS_BARS      = 4
FLIP_CONFIRM_BARS  = 3


@dataclass
class ConfirmationResult:
    confirmed:               bool  = False
    confirmation_score:      int   = 0
    breakout_quality:        int   = 0
    acceptance_score:        int   = 0
    delta_persistence:       int   = 0
    displacement_efficiency: float = 0.0
    expansion_efficiency:    float = 0.0
    microstructure_quality:  int   = 0
    follow_through:          int   = 0
    compression_strength:    int   = 0
    breakout_type:           str   = "NONE"
    acceptance_type:         str   = "NONE"
    structure_bias:          str   = "NEUTRAL"
    reason:                  str   = ""
    detail:                  dict  = field(default_factory=dict)

    def __str__(self):
        s = "CONFIRMED" if self.confirmed else "NOT_CONFIRMED"
        return (f"{s} score={self.confirmation_score} "
                f"type={self.breakout_type} "
                f"acc={self.acceptance_type} "
                f"eff={round(self.expansion_efficiency,2)}")


@dataclass
class ConfBar:
    price: float; high: float; low: float
    open: float; close: float
    delta: float; volume: float
    price_move: float; absorption: bool; event: str


class ConfirmationEngine:

    def __init__(self, window: int = 20, tick: float = TICK):
        self._bars:        deque = deque(maxlen=window)
        self._tick:        float = tick
        self._avg_volume:  float = 0.0
        self._vol_samples: deque = deque(maxlen=30)
        self._swing_highs: deque = deque(maxlen=5)
        self._swing_lows:  deque = deque(maxlen=5)
        self._last_swing_high: float = 0.0
        self._last_swing_low:  float = 0.0
        self._prev_swing_high: float = 0.0
        self._prev_swing_low:  float = 0.0
        self._range_high:   float = 0.0
        self._range_low:    float = 0.0
        self._range_bars:   int   = 0
        self._bars_outside: int   = 0
        self._structure_bias_current: str = "NEUTRAL"
        self._structure_bias_bars:    int = 0
        self._structure_flip_pending: str = ""
        self._structure_flip_count:   int = 0

    def analyze(self, event_result: dict, level_context,
                confluence, micro_result, raw_data: dict) -> ConfirmationResult:

        ctx        = event_result.get("context", {})
        event      = event_result.get("event",   "NONE")
        price      = float(raw_data.get("price",  0))
        high       = float(raw_data.get("high",   price))
        low        = float(raw_data.get("low",    price))
        open_      = float(raw_data.get("open",   price))
        close      = float(raw_data.get("close",  price))
        volume     = float(raw_data.get("volume", ctx.get("volume", 0)))
        delta      = ctx.get("delta",      0)
        price_move = ctx.get("price_move", 0)
        absorption = ctx.get("absorption", False)

        if volume > 0:
            self._vol_samples.append(volume)
        self._avg_volume = (sum(self._vol_samples) / len(self._vol_samples)
                            if self._vol_samples else volume)

        bar = ConfBar(price=price, high=high, low=low, open=open_,
                      close=close, delta=delta, volume=volume,
                      price_move=price_move, absorption=absorption, event=event)
        self._bars.append(bar)
        self._update_range(price, high, low)
        self._update_structure(high, low)

        if len(self._bars) < 3:
            return ConfirmationResult(reason="Calentando buffer")

        bars = list(self._bars)

        bq_score, bq_type, bq_detail     = self._breakout_quality(bars)
        acc_score, acc_type, acc_detail   = self._range_acceptance(bars, bq_type)
        dp_score, dp_detail               = self._delta_persistence(bars)
        exp_eff, exp_detail               = self._expansion_efficiency(bars)
        ms_score, ms_detail, comp_str     = self._microstructure_quality(bars, micro_result)
        ft_score, ft_detail               = self._follow_through(bars, event)
        struct_bias, struct_detail        = self._structure_bias()

        raw_score = int(
            bq_score  * 0.32 +
            dp_score  * 0.24 +
            acc_score * 0.20 +
            ft_score  * 0.14 +
            ms_score  * 0.10
        )

        if bq_type == "WEAK":
            raw_score = min(raw_score, 42)
        if bq_type == "FAKE":
            raw_score = min(raw_score, 25)
            raw_score = max(0, raw_score - 20)
        if exp_eff < 0.25:
            raw_score = max(0, raw_score - 15)
        elif exp_eff > 0.60:
            raw_score = min(100, raw_score + 8)
        if acc_type == "RECLAIM":
            raw_score = max(0, raw_score - 18)
        if bq_type == "REAL" and acc_type == "ACCEPTED":
            raw_score = min(100, raw_score + 12)
        if bq_type == "EXPLOSIVE":
            raw_score = min(100, raw_score + 18)
        if struct_bias != "NEUTRAL":
            raw_score = min(100, raw_score + 5)
        if comp_str >= 70:
            raw_score = min(100, raw_score + 6)

        conf_score = max(0, min(raw_score, 100))
        confirmed  = conf_score >= MIN_CONFIRMATION_SCORE

        reason_parts = [f"type={bq_type}", f"acc={acc_type}",
                        f"eff={round(exp_eff,2)}", f"struct={struct_bias}"]
        reason = " | ".join(reason_parts)

        return ConfirmationResult(
            confirmed               = confirmed,
            confirmation_score      = conf_score,
            breakout_quality        = bq_score,
            acceptance_score        = acc_score,
            delta_persistence       = dp_score,
            displacement_efficiency = exp_eff,
            expansion_efficiency    = exp_eff,
            microstructure_quality  = ms_score,
            follow_through          = ft_score,
            compression_strength    = comp_str,
            breakout_type           = bq_type,
            acceptance_type         = acc_type,
            structure_bias          = struct_bias,
            reason                  = reason,
            detail = {
                "breakout":   bq_detail, "acceptance": acc_detail,
                "delta":      dp_detail, "efficiency": exp_detail,
                "micro":      ms_detail, "followthru": ft_detail,
                "structure":  struct_detail,
            },
        )

    def _breakout_quality(self, bars):
        if len(bars) < 3:
            return 0, "NONE", {}
        curr   = bars[-1]
        detail = {}
        total_move  = abs(curr.price - bars[-3].price)
        min_break   = MIN_BREAKOUT_TICKS * self._tick
        vol_ratio   = curr.volume / self._avg_volume if self._avg_volume > 0 else 1.0
        detail["total_move"] = round(total_move, 2)
        detail["vol_ratio"]  = round(vol_ratio, 2)
        candle_range = curr.high - curr.low
        if candle_range > 0:
            body       = abs(curr.close - curr.open)
            wick_ratio = 1.0 - (body / candle_range)
            detail["wick_ratio"] = round(wick_ratio, 2)
        else:
            wick_ratio = 0.0
        delta_aligned = (
            (curr.price_move > 0 and curr.delta > 50) or
            (curr.price_move < 0 and curr.delta < -50)
        )
        is_fake = (
            (curr.price_move > 0 and curr.close < curr.open and wick_ratio > 0.65) or
            (curr.price_move < 0 and curr.close > curr.open and wick_ratio > 0.65) or
            (curr.price_move > self._tick * 3 and curr.delta < -150) or
            (curr.price_move < -self._tick * 3 and curr.delta > 150)
        )
        if is_fake:
            return 20, "FAKE", detail
        if total_move < min_break:
            return 15, "NONE", detail
        if (total_move >= min_break * 5 and vol_ratio >= 2.0 and
                delta_aligned and wick_ratio < 0.35):
            score = 95; bq_type = "EXPLOSIVE"
        elif (total_move >= min_break * 3 and vol_ratio >= 1.4 and
              delta_aligned and wick_ratio < 0.50):
            score = 78; bq_type = "REAL"
        elif total_move >= min_break * 1.5 and vol_ratio >= 1.1:
            score = 52; bq_type = "MODERATE"
        elif total_move >= min_break:
            score = 30; bq_type = "WEAK"
        else:
            score = 10; bq_type = "WEAK"
        if bq_type == "WEAK" and wick_ratio > 0.55:
            score = max(0, score - 10)
        detail["type"] = bq_type
        return score, bq_type, detail

    def _range_acceptance(self, bars, bq_type):
        if len(bars) < 2 or bq_type == "NONE":
            return 50, "NONE", {}
        detail = {}
        curr = bars[-1]; prev = bars[-2]
        if curr.price_move > 0:
            holds   = curr.close > prev.close - self._tick
            close_hi= curr.close >= curr.high - self._tick * 2
            reclaim = curr.close < prev.open - self._tick
            detail.update({"holds": holds, "close_hi": close_hi, "reclaim": reclaim})
            if reclaim: return 10, "RECLAIM", detail
            if holds and close_hi:
                vf = sum(1 for b in bars[-3:] if b.close > prev.close)
                detail["velas_fuera"] = vf
                return 70 + min(25, vf * 8), "ACCEPTED", detail
            if holds: return 55, "ACCEPTED", detail
            return 30, "WEAK_ACC", detail
        if curr.price_move < 0:
            holds   = curr.close < prev.close + self._tick
            close_lo= curr.close <= curr.low + self._tick * 2
            reclaim = curr.close > prev.open + self._tick
            detail.update({"holds": holds, "close_lo": close_lo, "reclaim": reclaim})
            if reclaim: return 10, "RECLAIM", detail
            if holds and close_lo:
                vf = sum(1 for b in bars[-3:] if b.close < prev.close)
                detail["velas_fuera"] = vf
                return 70 + min(25, vf * 8), "ACCEPTED", detail
            if holds: return 55, "ACCEPTED", detail
            return 30, "WEAK_ACC", detail
        return 45, "NONE", detail

    def _delta_persistence(self, bars):
        if len(bars) < 2: return 0, {}
        recent = bars[-min(3, len(bars)):]
        detail = {}
        bull_bars     = sum(1 for b in recent if b.delta > 80)
        bear_bars     = sum(1 for b in recent if b.delta < -80)
        delta_growing = (len(recent) >= 2 and
                         abs(recent[-1].delta) > abs(recent[-2].delta) * 0.85)
        detail.update({"bull_bars": bull_bars, "bear_bars": bear_bars,
                        "delta_growing": delta_growing})
        max_consec = max(bull_bars, bear_bars)
        if max_consec >= 3 and delta_growing:   score = 92
        elif max_consec >= 3:                   score = 76
        elif max_consec >= 2 and delta_growing: score = 70
        elif max_consec >= 2:                   score = 56
        elif max_consec == 1:                   score = 30
        else:                                   score = 10
        if bull_bars >= 1 and bear_bars >= 1:
            score = max(0, score - 22); detail["conflicting"] = True
        return score, detail

    def _expansion_efficiency(self, bars):
        if len(bars) < 2: return 0.0, {}
        recent     = bars[-min(4, len(bars)):]
        detail     = {}
        net_move   = abs(recent[-1].price - recent[0].price)
        total_path = sum(abs(b.price_move) for b in recent)
        detail["net_move"] = round(net_move, 2)
        detail["total_path"] = round(total_path, 2)
        if total_path == 0: return 0.0, detail
        prices    = [b.price for b in recent]
        direction = 1 if recent[-1].price > recent[0].price else -1
        max_retrace = 0.0
        if direction > 0:
            peak = prices[0]
            for p in prices[1:]:
                if p > peak: peak = p
                max_retrace = max(max_retrace, peak - p)
        else:
            trough = prices[0]
            for p in prices[1:]:
                if p < trough: trough = p
                max_retrace = max(max_retrace, p - trough)
        detail["max_retrace"] = round(max_retrace, 2)
        path_eff = net_move / total_path
        if max_retrace > 0 and net_move > 0:
            eff = path_eff * (1.0 - min(max_retrace / net_move * 0.5, 0.8))
        else:
            eff = path_eff
        eff = round(max(0.0, min(eff, 1.0)), 3)
        detail["efficiency"] = eff
        return eff, detail

    def _microstructure_quality(self, bars, micro_result):
        detail = {}; score = 40; comp_strength = 0
        if micro_result is not None:
            micro_active   = getattr(micro_result, "active",             False)
            micro_conf     = getattr(micro_result, "confidence",         0)
            micro_compress = getattr(micro_result, "compression_active", False)
            micro_breakout = getattr(micro_result, "breakout",           None)
            micro_bars     = getattr(micro_result, "bars_in_range",      0)
            if micro_active and micro_compress and micro_bars >= 6:
                score += 20; comp_strength = min(100, micro_conf + 10)
            if micro_breakout is not None and micro_conf >= 70:
                score += 25; comp_strength = max(comp_strength, micro_conf)
            if micro_bars >= 10:
                score += 10; comp_strength = min(100, comp_strength + 10)
        if len(bars) >= 5:
            recent      = bars[-5:]
            price_range = max(b.high for b in recent) - min(b.low for b in recent)
            avg_move    = sum(abs(b.price_move) for b in recent) / len(recent)
            detail["price_range"] = round(price_range, 2)
            detail["avg_move"]    = round(avg_move, 2)
            if price_range < self._tick * 8:
                avg_vol = sum(b.volume for b in recent) / len(recent)
                if avg_vol > self._avg_volume * 0.65:
                    score += 15; comp_strength = max(comp_strength, 60)
            absorb_count = sum(1 for b in recent if b.absorption)
            if absorb_count >= 2:
                score += 15; comp_strength = min(100, comp_strength + 15)
                detail["absorption_count"] = absorb_count
            failed = sum(
                1 for i in range(1, len(recent))
                if ((recent[i].high > recent[i-1].high and recent[i].close < recent[i-1].close) or
                    (recent[i].low < recent[i-1].low and recent[i].close > recent[i-1].close))
            )
            if failed >= 2:
                score += 10; comp_strength = min(100, comp_strength + 10)
                detail["failed_attempts"] = failed
        if len(bars) >= 3:
            avg_delta = sum(abs(b.delta) for b in bars[-3:]) / 3
            if avg_delta < 40:
                score = max(0, score - 20)
        score = max(0, min(score, 100)); comp_strength = max(0, min(comp_strength, 100))
        detail["score"] = score; detail["comp_strength"] = comp_strength
        return score, detail, comp_strength

    def _follow_through(self, bars, event):
        if len(bars) < 3: return 40, {}
        detail = {}
        moves    = [b.price_move for b in bars[-3:]]
        pos_bars = sum(1 for m in moves if m > self._tick)
        neg_bars = sum(1 for m in moves if m < -self._tick)
        detail.update({"pos_bars": pos_bars, "neg_bars": neg_bars})
        if pos_bars >= 2 and neg_bars == 0:   score = 82; detail["type"] = "BULL_CONT"
        elif neg_bars >= 2 and pos_bars == 0: score = 82; detail["type"] = "BEAR_CONT"
        elif pos_bars == 2 and neg_bars == 1: score = 55; detail["type"] = "BULL_WEAK"
        elif neg_bars == 2 and pos_bars == 1: score = 55; detail["type"] = "BEAR_WEAK"
        else:                                 score = 20; detail["type"] = "CHOPPY"
        if event == "AGOTAMIENTO" and (pos_bars >= 2 or neg_bars >= 2):
            score = min(100, score + 15)
        if pos_bars >= 1 and neg_bars >= 1 and abs(pos_bars - neg_bars) == 0:
            score = max(0, score - 20)
        return score, detail

    def _structure_bias(self):
        detail = {}
        if (self._last_swing_high == 0 or self._last_swing_low == 0 or
                self._prev_swing_high == 0 or self._prev_swing_low == 0):
            return "NEUTRAL", detail
        buf = self._tick * 2
        hh = self._last_swing_high > self._prev_swing_high + buf
        hl = self._last_swing_low  > self._prev_swing_low  + buf
        lh = self._last_swing_high < self._prev_swing_high - buf
        ll = self._last_swing_low  < self._prev_swing_low  - buf
        detail.update({"hh": hh, "hl": hl, "lh": lh, "ll": ll})
        if hh and hl:   raw_bias = "BULLISH"; detail["pattern"] = "HH+HL"
        elif lh and ll: raw_bias = "BEARISH"; detail["pattern"] = "LH+LL"
        elif hh:        raw_bias = "BULLISH"; detail["pattern"] = "BOS_UP"
        elif ll:        raw_bias = "BEARISH"; detail["pattern"] = "BOS_DOWN"
        else:           raw_bias = "NEUTRAL"
        if raw_bias == "NEUTRAL":
            self._structure_bias_bars += 1
            detail["bias_held"] = self._structure_bias_current
            detail["bias_bars"] = self._structure_bias_bars
            return self._structure_bias_current, detail
        if raw_bias == self._structure_bias_current:
            self._structure_bias_bars   += 1
            self._structure_flip_pending = ""
            self._structure_flip_count   = 0
        else:
            if self._structure_bias_bars < MIN_BIAS_BARS:
                detail["flip_blocked"] = True
                raw_bias = self._structure_bias_current
            else:
                if self._structure_flip_pending == raw_bias:
                    self._structure_flip_count += 1
                else:
                    self._structure_flip_pending = raw_bias
                    self._structure_flip_count   = 1
                if self._structure_flip_count >= FLIP_CONFIRM_BARS:
                    self._structure_bias_current = raw_bias
                    self._structure_bias_bars    = 0
                    self._structure_flip_pending = ""
                    self._structure_flip_count   = 0
                    detail["flip_confirmed"] = True
                else:
                    detail["flip_pending"] = f"{self._structure_flip_count}/{FLIP_CONFIRM_BARS}"
                    raw_bias = self._structure_bias_current
        if raw_bias != "NEUTRAL":
            self._structure_bias_current = raw_bias
        self._structure_bias_bars = max(self._structure_bias_bars, 0)
        detail["bias_bars"]    = self._structure_bias_bars
        detail["bias_current"] = self._structure_bias_current
        return raw_bias, detail

    def _update_structure(self, high, low):
        bars = list(self._bars)
        if len(bars) < 9: return
        pivot = bars[-4]
        left  = bars[-7:-4]
        right = bars[-4:-1]
        is_swing_high = (all(pivot.high >= b.high for b in left) and
                         all(pivot.high >= b.high for b in right))
        if is_swing_high:
            magnitude = pivot.high - self._last_swing_high
            if (self._last_swing_high == 0 or
                    magnitude > self._tick * MIN_SWING_TICKS or
                    pivot.high > self._last_swing_high + self._tick * 2):
                self._prev_swing_high = self._last_swing_high
                self._last_swing_high = pivot.high
                self._swing_highs.append(pivot.high)
        is_swing_low = (all(pivot.low <= b.low for b in left) and
                        all(pivot.low <= b.low for b in right))
        if is_swing_low:
            magnitude = self._last_swing_low - pivot.low
            if (self._last_swing_low == 0 or
                    magnitude > self._tick * MIN_SWING_TICKS or
                    pivot.low < self._last_swing_low - self._tick * 2):
                self._prev_swing_low = self._last_swing_low
                self._last_swing_low = pivot.low
                self._swing_lows.append(pivot.low)

    def _update_range(self, price, high, low):
        if self._range_high == 0:
            self._range_high = high; self._range_low = low; return
        if low >= self._range_low * 0.998 and high <= self._range_high * 1.002:
            self._range_high = max(self._range_high, high)
            self._range_low  = min(self._range_low,  low)
            self._range_bars += 1; self._bars_outside = 0
        else:
            self._bars_outside += 1
            if self._bars_outside > 3:
                self._range_high = high; self._range_low = low
                self._range_bars = 1;    self._bars_outside = 0

    @property
    def range_high(self):    return self._range_high
    @property
    def range_low(self):     return self._range_low
    @property
    def bars_in_range(self): return self._range_bars
'''

# ══════════════════════════════════════════════════════════════════
FILES["continuation_engine.py"] = r'''# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — continuation_engine.py
#  Institutional Continuation Probability Engine v1.1
#
#  CAMBIOS v1.1:
#  - WEAK penalty dinámica por régimen
#  - Delta override: delta fuerte sostenido cancela penalización WEAK
#  - Imbalance lookback: 3 barras (antes 5)
#  - EFFICIENT_TREND recibe boost igual a TREND_DAY
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ContinuationResult:
    continuation_probability:  int   = 50
    runner_probability:        int   = 30
    continuation_quality:      str   = "UNKNOWN"
    follow_through_strength:   int   = 0
    continuation_bias:         str   = "NEUTRAL"
    pullback_depth:            float = 0.0
    pullback_healthy:          bool  = False
    absorption_after_break:    bool  = False
    imbalance_persistence:     int   = 0
    speed_score:               int   = 0

    def __str__(self):
        return (f"cont={self.continuation_probability}% "
                f"runner={self.runner_probability}% "
                f"quality={self.continuation_quality} "
                f"bias={self.continuation_bias}")


@dataclass
class ContBar:
    price: float; high: float; low: float; open: float; close: float
    delta: float; volume: float; price_move: float; absorption: bool


_TREND_REGIMES      = {"TREND_DAY","STRONG_TREND","MOMENTUM","EXPANSION_DAY","EFFICIENT_TREND"}
_ROTATIONAL_REGIMES = {"ROTATIONAL","BALANCED_DAY","COMPRESSION","LOW_VOL","RANGE_DAY"}
_WEAK_PENALTY = {"TREND": -8, "ROTATIONAL": -20, "DEFAULT": -15}

def _get_regime_class(regime):
    if regime in _TREND_REGIMES:      return "TREND"
    if regime in _ROTATIONAL_REGIMES: return "ROTATIONAL"
    return "DEFAULT"


class ContinuationEngine:

    def __init__(self, window=12, tick=0.25):
        self._bars        = deque(maxlen=window)
        self._tick        = tick
        self._avg_volume  = 0.0
        self._vol_samples = deque(maxlen=30)

    def analyze(self, event_result, confirmation, session_regime, raw_data):
        ctx        = event_result.get("context", {})
        price      = float(raw_data.get("price",  0))
        high       = float(raw_data.get("high",   price))
        low        = float(raw_data.get("low",    price))
        open_      = float(raw_data.get("open",   price))
        close      = float(raw_data.get("close",  price))
        volume     = float(raw_data.get("volume", ctx.get("volume", 0)))
        delta      = ctx.get("delta",      0)
        price_move = ctx.get("price_move", 0)
        absorption = ctx.get("absorption", False)
        if volume > 0: self._vol_samples.append(volume)
        self._avg_volume = sum(self._vol_samples)/len(self._vol_samples) if self._vol_samples else volume
        bar = ContBar(price=price,high=high,low=low,open=open_,close=close,
                      delta=delta,volume=volume,price_move=price_move,absorption=absorption)
        self._bars.append(bar)
        if len(self._bars) < 3:
            return ContinuationResult(continuation_probability=50,runner_probability=25,continuation_quality="UNKNOWN")
        bars = list(self._bars)
        bq_type   = getattr(confirmation,"breakout_type","NONE")
        acc_type  = getattr(confirmation,"acceptance_type","NONE")
        exp_eff   = getattr(confirmation,"expansion_efficiency",0.5)
        regime    = getattr(session_regime,"session_regime","BALANCED_DAY")
        trend_str = getattr(session_regime,"trend_strength",40)
        cont_base = getattr(session_regime,"continuation_probability",50)
        reg_class = _get_regime_class(regime)
        ft_score, ft_bias = self._follow_through(bars)
        pb_depth, pb_ok   = self._pullback_analysis(bars)
        absorb_post       = self._absorption_after_break(bars)
        imbalance         = self._imbalance_persistence(bars)
        speed             = self._expansion_speed(bars)
        recent3        = bars[-3:]
        bull_delta_str = sum(1 for b in recent3 if b.delta > 200)
        bear_delta_str = sum(1 for b in recent3 if b.delta < -200)
        delta_override = bull_delta_str >= 2 or bear_delta_str >= 2
        cont_prob = cont_base
        if bq_type == "EXPLOSIVE":  cont_prob += 18
        elif bq_type == "REAL":     cont_prob += 10
        elif bq_type == "MODERATE": cont_prob += 0
        elif bq_type == "WEAK":
            cont_prob += 0 if delta_override else _WEAK_PENALTY.get(reg_class, _WEAK_PENALTY["DEFAULT"])
        elif bq_type == "FAKE":     cont_prob -= 35
        if ft_score >= 75:   cont_prob += 12
        elif ft_score >= 50: cont_prob += 5
        elif ft_score < 30:  cont_prob -= 12
        if pb_ok:                          cont_prob += 8
        elif pb_depth > self._tick * 6:    cont_prob -= 10
        if absorb_post:                    cont_prob -= 15
        if acc_type == "ACCEPTED":         cont_prob += 8
        elif acc_type == "RECLAIM":        cont_prob -= 20
        if imbalance >= 70:   cont_prob += 10
        elif imbalance < 30:  cont_prob -= 8
        if speed >= 70:       cont_prob += 6
        cont_prob = max(5, min(cont_prob, 95))
        runner_prob = cont_prob - 20
        if regime in ("TREND_DAY","EXPANSION_DAY","EFFICIENT_TREND"): runner_prob += 15
        elif regime in ("BALANCED_DAY","ROTATIONAL_DAY","TRAPPED_DAY"): runner_prob -= 15
        if bq_type == "EXPLOSIVE" and acc_type == "ACCEPTED": runner_prob += 15
        if trend_str >= 70: runner_prob += 10
        if exp_eff >= 0.65: runner_prob += 8
        if delta_override and reg_class == "TREND": runner_prob += 8
        runner_prob = max(5, min(runner_prob, 90))
        if cont_prob >= 75:   quality = "STRONG"
        elif cont_prob >= 55: quality = "MODERATE"
        elif cont_prob >= 35: quality = "WEAK"
        else:                 quality = "NONE"
        if absorb_post and acc_type == "RECLAIM":
            quality = "NONE"; cont_prob = max(5, cont_prob - 20)
        return ContinuationResult(
            continuation_probability=cont_prob, runner_probability=runner_prob,
            continuation_quality=quality, follow_through_strength=ft_score,
            continuation_bias=ft_bias, pullback_depth=round(pb_depth,2),
            pullback_healthy=pb_ok, absorption_after_break=absorb_post,
            imbalance_persistence=imbalance, speed_score=speed)

    def _follow_through(self, bars):
        if len(bars) < 3: return 40, "NEUTRAL"
        recent = bars[-3:]
        pos = sum(1 for b in recent if b.price_move > self._tick)
        neg = sum(1 for b in recent if b.price_move < -self._tick)
        if pos >= 2 and neg == 0: return 82, "BULLISH"
        if neg >= 2 and pos == 0: return 82, "BEARISH"
        if pos == 2 and neg == 1: return 55, "BULLISH"
        if neg == 2 and pos == 1: return 55, "BEARISH"
        return 20, "NEUTRAL"

    def _pullback_analysis(self, bars):
        if len(bars) < 4: return 0.0, False
        prices = [b.price for b in bars]
        net = prices[-1] - prices[0]
        if abs(net) < self._tick: return 0.0, False
        if net > 0:
            peak = max(prices); retrace = peak - prices[-1]
        else:
            trough = min(prices); retrace = prices[-1] - trough
        retrace = max(0.0, retrace)
        total = abs(net)
        return retrace, (retrace/total if total > 0 else 1.0) <= 0.38

    def _absorption_after_break(self, bars):
        if len(bars) < 2: return False
        curr = bars[-1]
        if (curr.absorption and self._avg_volume > 0 and
                curr.volume > self._avg_volume * 1.5 and abs(curr.price_move) < self._tick * 2):
            return True
        if abs(curr.delta) > 300 and abs(curr.price_move) < self._tick: return True
        return False

    def _imbalance_persistence(self, bars):
        if len(bars) < 2: return 50
        recent = bars[-min(3, len(bars)):]
        bull  = sum(1 for b in recent if b.delta > 80)
        bear  = sum(1 for b in recent if b.delta < -80)
        mixed = sum(1 for b in recent if -80 <= b.delta <= 80)
        total = len(recent)
        if max(bull,bear) >= total*0.70: return 85
        if max(bull,bear) >= total*0.55: return 65
        if mixed >= total*0.60:          return 25
        return 45

    def _expansion_speed(self, bars):
        if len(bars) < 2: return 40
        recent = bars[-3:]
        moves  = [abs(b.price_move) for b in recent]
        avg    = sum(moves)/len(moves) if moves else 0
        if avg >= self._tick*6: return 90
        if avg >= self._tick*4: return 70
        if avg >= self._tick*2: return 50
        if avg >= self._tick:   return 30
        return 10
'''

# ══════════════════════════════════════════════════════════════════
FILES["market_environment.py"] = r'''# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — market_environment.py
#  Market Environment Analyzer v1.1
#
#  CAMBIOS v1.1:
#  - Warmup retorna ROTATIONAL tradeable=True (no COMPRESSION)
#  - blocks_trading() respeta warming_up flag
#  - MIN_BARS reducido de 8 a 6
#  - COMPRESSION requiere vol_state=LOW explícito
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MarketEnvironmentResult:
    environment:            str  = "ROTATIONAL"
    confidence:             int  = 0
    tradeable:              bool = True
    danger_level:           int  = 0
    rotation_factor:        int  = 0
    trend_efficiency:       int  = 0
    trap_density:           int  = 0
    breakout_failure_rate:  int  = 0
    directional_efficiency: int  = 0
    mean_reversion_strength:int  = 0
    delta_noise:            int  = 0
    volatility_state:       str  = "NORMAL"
    sweep_frequency:        int  = 0
    warming_up:             bool = False

    def blocks_trading(self):
        if self.environment == "LIQUIDATION":          return True
        if self.trap_density > 70:                     return True
        if self.breakout_failure_rate > 65:            return True
        if not self.warming_up and self.directional_efficiency < 35: return True
        if self.rotation_factor > 80 and self.volatility_state == "HIGH": return True
        if self.danger_level > 80:                     return True
        return False

    def block_reason(self):
        if self.environment == "LIQUIDATION":          return "Entorno LIQUIDATION"
        if self.trap_density > 70:                     return f"Trap density ({self.trap_density})"
        if self.breakout_failure_rate > 65:            return f"BFR alto ({self.breakout_failure_rate}%)"
        if not self.warming_up and self.directional_efficiency < 35: return f"Dir eff baja ({self.directional_efficiency})"
        if self.rotation_factor > 80 and self.volatility_state == "HIGH": return "Rotación extrema"
        if self.danger_level > 80:                     return f"Danger ({self.danger_level})"
        return ""

    def __str__(self):
        return (f"{self.environment} conf={self.confidence}% "
                f"danger={self.danger_level} eff={self.directional_efficiency} "
                f"trap={self.trap_density} bfr={self.breakout_failure_rate}%")


@dataclass
class EnvBar:
    price: float; high: float; low: float; open: float; close: float
    delta: float; volume: float; price_move: float; absorption: bool


class MarketEnvironmentAnalyzer:
    WINDOW   = 20
    MIN_BARS = 6

    def __init__(self, tick=0.25):
        self._tick          = tick
        self._bars          = deque(maxlen=self.WINDOW)
        self._avg_vol       = 0.0
        self._vol_s         = deque(maxlen=30)
        self._atrs          = deque(maxlen=20)
        self._recent_breakouts = deque(maxlen=10)
        self._last_result   = None
        self._bars_seen     = 0

    def analyze_environment(self, raw_data, event_result):
        price      = float(raw_data.get("price", 0))
        high       = float(raw_data.get("high",  price))
        low        = float(raw_data.get("low",   price))
        open_      = float(raw_data.get("open",  price))
        close      = float(raw_data.get("close", price))
        volume     = float(raw_data.get("volume", 0))
        ctx        = event_result.get("context", {})
        delta      = ctx.get("delta",      0)
        price_move = ctx.get("price_move", 0)
        absorption = ctx.get("absorption", False)
        if volume > 0: self._vol_s.append(volume)
        self._avg_vol = sum(self._vol_s)/len(self._vol_s) if self._vol_s else volume
        self._atrs.append(high - low)
        bar = EnvBar(price=price,high=high,low=low,open=open_,close=close,
                     delta=delta,volume=volume,price_move=price_move,absorption=absorption)
        self._bars.append(bar)
        self._bars_seen += 1
        if len(self._bars) < self.MIN_BARS:
            result = MarketEnvironmentResult(
                environment="ROTATIONAL", confidence=0, tradeable=True,
                danger_level=0, directional_efficiency=50, warming_up=True)
            self._last_result = result
            return result
        bars     = list(self._bars)
        rotation = self._calc_rotation(bars)
        trap_d   = self._calc_trap_density(bars)
        bfr      = self._calc_breakout_failure_rate(bars)
        dir_eff  = self._calc_directional_efficiency(bars)
        mean_rev = self._calc_mean_reversion(bars)
        delta_n  = self._calc_delta_noise(bars)
        sweep_f  = self._calc_sweep_frequency(bars)
        vol_st   = self._calc_volatility_state()
        env, conf= self._classify(rotation,trap_d,bfr,dir_eff,mean_rev,delta_n,vol_st,sweep_f)
        danger   = self._calc_danger(env,trap_d,bfr,dir_eff,rotation,vol_st)
        temp     = MarketEnvironmentResult(environment=env,confidence=conf,danger_level=danger,
                     rotation_factor=rotation,trap_density=trap_d,breakout_failure_rate=bfr,
                     directional_efficiency=dir_eff,volatility_state=vol_st,warming_up=False)
        tradeable = not temp.blocks_trading()
        result = MarketEnvironmentResult(
            environment=env, confidence=conf, tradeable=tradeable,
            danger_level=danger, rotation_factor=rotation, trend_efficiency=dir_eff,
            trap_density=trap_d, breakout_failure_rate=bfr, directional_efficiency=dir_eff,
            mean_reversion_strength=mean_rev, delta_noise=delta_n,
            volatility_state=vol_st, sweep_frequency=sweep_f, warming_up=False)
        self._last_result = result
        return result

    def _calc_rotation(self, bars):
        if len(bars) < 3: return 50
        moves = [b.price_move for b in bars]
        reversals = sum(1 for i in range(1,len(moves))
                        if moves[i]*moves[i-1] < 0 and abs(moves[i]) > self._tick)
        rate = reversals / max(len(moves)-1, 1)
        if rate >= 0.65: return 90
        if rate >= 0.50: return 75
        if rate >= 0.35: return 55
        if rate >= 0.20: return 35
        return 15

    def _calc_trap_density(self, bars):
        if len(bars) < 4: return 20
        traps = 0
        for b in bars[-10:]:
            cr = b.high - b.low
            if cr > self._tick * 3:
                body = abs(b.close - b.open)
                if 1.0 - (body/cr) > 0.65: traps += 1
                if ((b.price_move > self._tick*2 and b.delta < -80) or
                        (b.price_move < -self._tick*2 and b.delta > 80)): traps += 1
        total = min(10, len(bars))
        return min(int(traps/total*100), 100)

    def _calc_breakout_failure_rate(self, bars):
        if len(bars) < 5: return 30
        failures = 0; attempts = 0
        for i in range(2, len(bars)-1):
            if abs(bars[i].price_move) >= self._tick * 4:
                attempts += 1
                fp = [bars[j].price for j in range(i+1, min(i+3, len(bars)))]
                if fp:
                    if bars[i].price_move > 0 and min(fp) < bars[i].open: failures += 1
                    elif bars[i].price_move < 0 and max(fp) > bars[i].open: failures += 1
        return 25 if attempts == 0 else min(int(failures/attempts*100), 100)

    def _calc_directional_efficiency(self, bars):
        if len(bars) < 3: return 50
        prices = [b.price for b in bars]
        net_move   = abs(prices[-1] - prices[0])
        total_path = sum(abs(b.price_move) for b in bars)
        if total_path == 0: return 10
        return min(int(net_move/total_path*100), 100)

    def _calc_mean_reversion(self, bars):
        if len(bars) < 5: return 40
        prices  = [b.price for b in bars]
        center  = sum(prices)/len(prices)
        crossings = sum(1 for i in range(1,len(prices))
                        if (prices[i-1]-center)*(prices[i]-center) < 0)
        rate = crossings/max(len(prices)-1, 1)
        if rate >= 0.5:  return 85
        if rate >= 0.35: return 65
        if rate >= 0.20: return 45
        return 20

    def _calc_delta_noise(self, bars):
        if len(bars) < 3: return 40
        incoherent = sum(1 for b in bars[-8:]
                         if ((b.price_move > self._tick*2 and b.delta < -50) or
                             (b.price_move < -self._tick*2 and b.delta > 50)))
        return min(int(incoherent/min(8,len(bars))*100), 100)

    def _calc_sweep_frequency(self, bars):
        if len(bars) < 4: return 20
        sweeps = sum(1 for b in bars[-8:]
                     if b.high-b.low > self._tick*6 and abs(b.close-b.open) < (b.high-b.low)*0.3)
        return min(int(sweeps/min(8,len(bars))*100), 100)

    def _calc_volatility_state(self):
        if len(self._atrs) < 5: return "NORMAL"
        avg_atr    = sum(self._atrs)/len(self._atrs)
        recent_atr = sum(list(self._atrs)[-5:])/5
        ratio      = recent_atr/avg_atr if avg_atr > 0 else 1.0
        if ratio >= 2.0: return "EXTREME"
        if ratio >= 1.4: return "HIGH"
        if ratio <= 0.5: return "LOW"
        return "NORMAL"

    def _classify(self, rotation, trap_d, bfr, dir_eff, mean_rev, delta_n, vol_st, sweep_f):
        if dir_eff >= 75 and delta_n <= 20 and rotation <= 25:
            return "EFFICIENT_TREND", min(dir_eff, 88)
        if trap_d >= 65 or bfr >= 60 or (delta_n >= 65 and rotation >= 60):
            return "TRAPPY", max(trap_d, bfr)
        if rotation >= 70 and dir_eff <= 35:
            return "CHOPPY", rotation
        if rotation >= 50 and mean_rev >= 60 and dir_eff <= 50 and vol_st in ("NORMAL","LOW"):
            return "ROTATIONAL", min(rotation + mean_rev//2, 80)
        if vol_st == "LOW" and dir_eff <= 40 and rotation <= 30:
            return "DEAD_MARKET", 75
        if delta_n <= 30 and dir_eff <= 40 and rotation <= 30 and vol_st == "LOW":
            return "COMPRESSION", 65
        if dir_eff >= 65 and sweep_f >= 60 and vol_st in ("HIGH","EXTREME"):
            return "LIQUIDATION", min(dir_eff, 85)
        return "ROTATIONAL", 50

    def _calc_danger(self, env, trap_d, bfr, dir_eff, rotation, vol_st):
        danger = 0
        if env == "LIQUIDATION":  danger += 50
        elif env == "TRAPPY":     danger += 40
        elif env == "CHOPPY":     danger += 30
        elif env == "DEAD_MARKET":danger += 25
        if trap_d > 70:           danger += 20
        if bfr > 60:              danger += 18
        if dir_eff < 30:          danger += 12
        if rotation > 75 and vol_st == "HIGH": danger += 15
        return min(danger, 100)

    @property
    def last_result(self): return self._last_result
'''

# ══════════════════════════════════════════════════════════════════
def install():
    ok = []; fail = []
    for fname, content in FILES.items():
        path = os.path.join(BASE, fname)
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            size = os.path.getsize(path)
            ok.append(f"  ✅ {fname} ({size} bytes)")
        except Exception as e:
            fail.append(f"  ❌ {fname}: {e}")

    print("\n╔══ GIBBZ PATCH INSTALLER ════════════════════╗")
    for line in ok:   print(line)
    for line in fail: print(line)
    if not fail:
        print("  Todos los patches instalados correctamente.")
    else:
        print(f"  {len(fail)} archivo(s) fallaron.")
    print("╚═════════════════════════════════════════════╝\n")

if __name__ == "__main__":
    install()

