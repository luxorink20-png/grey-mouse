# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — session_regime_engine.py
#  Session Regime Detection Engine v1.0
#
#  Detecta el contexto macro de la sesión en tiempo real.
#  Se actualiza cada tick y alimenta confluence + validator.
#
#  REGÍMENES:
#  TREND_DAY      — dirección clara, expansión persistente
#  BALANCED_DAY   — rotación entre niveles, sin dirección
#  ROTATIONAL_DAY — múltiples reversals, range definido
#  EXPANSION_DAY  — ATR muy alto, movimientos grandes
#  LOW_VOL_DAY    — ATR bajo, poco interés institucional
#  HIGH_VOL_DAY   — ATR alto con dirección menos clara
#  TRAPPED_DAY    — precio atrapado entre muros, sin salida
#  SHORT_COVERING — rally fuerte sin delta comprador previo
#  LIQUIDATION    — caída fuerte con delta vendedor dominante
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class SessionRegimeResult:
    session_regime:           str   = "BALANCED_DAY"
    regime_confidence:        int   = 0    # 0-100
    trend_strength:           int   = 0    # 0-100
    rotation_strength:        int   = 0    # 0-100
    volatility_state:         str   = "NORMAL"   # LOW / NORMAL / HIGH / EXTREME
    continuation_probability: int   = 50   # 0-100
    mean_reversion_probability: int = 50   # 0-100
    session_range:            float = 0.0
    range_efficiency:         float = 0.0  # desplazamiento neto / rango total
    directional_bias:         str   = "NEUTRAL"  # BULLISH / BEARISH / NEUTRAL
    bars_analyzed:            int   = 0

    def allows_continuation(self) -> bool:
        return self.session_regime in (
            "TREND_DAY", "EXPANSION_DAY", "SHORT_COVERING", "LIQUIDATION"
        )

    def is_range_regime(self) -> bool:
        return self.session_regime in (
            "BALANCED_DAY", "ROTATIONAL_DAY", "LOW_VOL_DAY", "TRAPPED_DAY"
        )

    def allows_weak_breakout(self) -> bool:
        """Solo TREND/EXPANSION permiten breakouts débiles."""
        return self.session_regime in ("TREND_DAY", "EXPANSION_DAY")

    def __str__(self) -> str:
        return (f"{self.session_regime} conf={self.regime_confidence}% "
                f"trend={self.trend_strength} rot={self.rotation_strength} "
                f"bias={self.directional_bias}")


class SessionRegimeEngine:
    """
    Detecta el régimen institucional de la sesión en tiempo real.

    Usa ventana deslizante de N barras para calcular:
    - ATR y expansión vs histórico
    - Eficiencia direccional (desplazamiento neto / camino total)
    - Persistencia de delta
    - Profundidad de pullbacks
    - Número de reversals
    """

    WINDOW_SHORT  = 10   # barras recientes
    WINDOW_LONG   = 30   # contexto de sesión
    MIN_BARS      = 8    # mínimo para análisis

    def __init__(self, tick: float = 0.25):
        self._tick           = tick
        self._prices:  deque = deque(maxlen=self.WINDOW_LONG)
        self._highs:   deque = deque(maxlen=self.WINDOW_LONG)
        self._lows:    deque = deque(maxlen=self.WINDOW_LONG)
        self._deltas:  deque = deque(maxlen=self.WINDOW_LONG)
        self._volumes: deque = deque(maxlen=self.WINDOW_LONG)
        self._moves:   deque = deque(maxlen=self.WINDOW_LONG)
        self._atrs:    deque = deque(maxlen=50)
        self._session_open:  float = 0.0
        self._session_high:  float = 0.0
        self._session_low:   float = 9999.0
        self._bar_count:     int   = 0
        self._last_result:   Optional[SessionRegimeResult] = None

    def update(self, raw_data: dict,
               event_result: dict) -> SessionRegimeResult:
        """
        Actualiza el régimen con el tick más reciente.
        Llamar en cada tick antes de confluence_engine.
        """
        price      = float(raw_data.get("price", 0))
        high       = float(raw_data.get("high",  price))
        low        = float(raw_data.get("low",   price))
        volume     = float(raw_data.get("volume", 0))
        ctx        = event_result.get("context", {})
        delta      = ctx.get("delta",      0)
        price_move = ctx.get("price_move", 0)

        self._bar_count += 1

        if self._session_open == 0.0:
            self._session_open = price
        self._session_high = max(self._session_high, high)
        self._session_low  = min(self._session_low,  low)

        atr = high - low
        self._atrs.append(atr)
        self._prices.append(price)
        self._highs.append(high)
        self._lows.append(low)
        self._deltas.append(delta)
        self._volumes.append(volume)
        self._moves.append(price_move)

        if self._bar_count < self.MIN_BARS:
            result = SessionRegimeResult(
                session_regime     = "BALANCED_DAY",
                regime_confidence  = 0,
                bars_analyzed      = self._bar_count,
            )
            self._last_result = result
            return result

        result = self._classify()
        self._last_result = result
        return result

    def _classify(self) -> SessionRegimeResult:
        prices  = list(self._prices)
        highs   = list(self._highs)
        lows    = list(self._lows)
        deltas  = list(self._deltas)
        moves   = list(self._moves)
        atrs    = list(self._atrs)

        # ── ATR análisis ──────────────────────────────────────────
        avg_atr     = sum(atrs) / len(atrs) if atrs else 1.0
        recent_atr  = sum(atrs[-5:]) / min(5, len(atrs))
        atr_ratio   = recent_atr / avg_atr if avg_atr > 0 else 1.0

        if atr_ratio >= 2.0:    vol_state = "EXTREME"
        elif atr_ratio >= 1.4:  vol_state = "HIGH"
        elif atr_ratio <= 0.5:  vol_state = "LOW"
        else:                   vol_state = "NORMAL"

        # ── Eficiencia direccional ────────────────────────────────
        net_move  = prices[-1] - prices[0] if len(prices) >= 2 else 0.0
        total_path= sum(abs(m) for m in moves) if moves else 1.0
        range_eff = abs(net_move) / total_path if total_path > 0 else 0.0

        # ── Tendencia ─────────────────────────────────────────────
        trend_strength = self._calc_trend_strength(prices, deltas, range_eff)

        # ── Rotación (reversals) ──────────────────────────────────
        rotation_strength = self._calc_rotation(moves)

        # ── Delta persistencia ────────────────────────────────────
        bull_delta = sum(1 for d in deltas[-10:] if d > 80)
        bear_delta = sum(1 for d in deltas[-10:] if d < -80)
        delta_persistent = max(bull_delta, bear_delta) >= 5

        # ── Sesión range ──────────────────────────────────────────
        session_range = self._session_high - self._session_low
        session_net   = abs(prices[-1] - self._session_open)
        session_range_eff = session_net / session_range if session_range > 0 else 0.0

        # ── Dirección dominante ───────────────────────────────────
        if net_move > self._tick * 4 and bull_delta > bear_delta:
            directional_bias = "BULLISH"
        elif net_move < -self._tick * 4 and bear_delta > bull_delta:
            directional_bias = "BEARISH"
        else:
            directional_bias = "NEUTRAL"

        # ── Pullback depth ────────────────────────────────────────
        avg_pullback = self._calc_avg_pullback(prices)

        # ── CLASIFICACIÓN ─────────────────────────────────────────
        regime, confidence = self._determine_regime(
            trend_strength, rotation_strength, vol_state,
            range_eff, delta_persistent, session_range_eff,
            atr_ratio, avg_pullback, directional_bias, deltas
        )

        # ── Probabilidades ────────────────────────────────────────
        cont_prob = self._continuation_prob(
            regime, trend_strength, range_eff, delta_persistent
        )
        mean_rev_prob = 100 - cont_prob

        return SessionRegimeResult(
            session_regime            = regime,
            regime_confidence         = confidence,
            trend_strength            = trend_strength,
            rotation_strength         = rotation_strength,
            volatility_state          = vol_state,
            continuation_probability  = cont_prob,
            mean_reversion_probability= mean_rev_prob,
            session_range             = round(session_range, 2),
            range_efficiency          = round(range_eff, 3),
            directional_bias          = directional_bias,
            bars_analyzed             = self._bar_count,
        )

    def _calc_trend_strength(self, prices: list,
                              deltas: list,
                              range_eff: float) -> int:
        if len(prices) < 5:
            return 0
        score = 0
        # Precio en un extremo del rango reciente
        recent_high = max(prices[-10:])
        recent_low  = min(prices[-10:])
        curr        = prices[-1]
        if recent_high > recent_low:
            pos = (curr - recent_low) / (recent_high - recent_low)
            if pos >= 0.8 or pos <= 0.2:
                score += 25
        # Eficiencia alta = tendencia
        if range_eff >= 0.65:  score += 30
        elif range_eff >= 0.45: score += 15
        # Precios en secuencia (HH o LL)
        ups   = sum(1 for i in range(1, len(prices)) if prices[i] > prices[i-1])
        downs = len(prices) - 1 - ups
        if ups >= len(prices) * 0.70:   score += 25
        elif downs >= len(prices) * 0.70: score += 25
        # Delta persistente
        bull_d = sum(1 for d in deltas[-8:] if d > 60)
        bear_d = sum(1 for d in deltas[-8:] if d < -60)
        if max(bull_d, bear_d) >= 5:
            score += 20
        return min(score, 100)

    def _calc_rotation(self, moves: list) -> int:
        if len(moves) < 4:
            return 0
        reversals = 0
        for i in range(1, len(moves)):
            if moves[i] * moves[i-1] < 0 and abs(moves[i]) > self._tick:
                reversals += 1
        reversal_rate = reversals / max(len(moves)-1, 1)
        if reversal_rate >= 0.6:   return 90
        elif reversal_rate >= 0.45: return 70
        elif reversal_rate >= 0.30: return 50
        elif reversal_rate >= 0.15: return 30
        return 10

    def _calc_avg_pullback(self, prices: list) -> float:
        if len(prices) < 4:
            return 0.0
        pullbacks = []
        for i in range(2, len(prices)):
            if prices[i-1] > prices[i-2] and prices[i] < prices[i-1]:
                pullbacks.append(prices[i-1] - prices[i])
            elif prices[i-1] < prices[i-2] and prices[i] > prices[i-1]:
                pullbacks.append(prices[i] - prices[i-1])
        return sum(pullbacks) / len(pullbacks) if pullbacks else 0.0

    def _determine_regime(self, trend_str, rotation_str, vol_state,
                           range_eff, delta_persistent, session_eff,
                           atr_ratio, avg_pullback, direction, deltas) -> tuple:

        # LIQUIDATION: caída fuerte sostenida con delta vendedor
        bear_delta = sum(1 for d in deltas[-8:] if d < -100)
        bull_delta = sum(1 for d in deltas[-8:] if d > 100)
        if (bear_delta >= 6 and trend_str >= 65 and
                direction == "BEARISH" and range_eff >= 0.60):
            return "LIQUIDATION", min(trend_str, 90)

        # SHORT_COVERING: rally fuerte sin delta comprador previo
        if (bull_delta >= 5 and trend_str >= 60 and
                direction == "BULLISH" and atr_ratio >= 1.3):
            return "SHORT_COVERING", min(trend_str + 10, 90)

        # EXPANSION_DAY: ATR muy alto con alguna dirección
        if atr_ratio >= 1.8 and session_eff >= 0.40:
            return "EXPANSION_DAY", 75

        # TREND_DAY: eficiencia alta, tendencia clara
        if (trend_str >= 70 and range_eff >= 0.55 and
                rotation_str <= 40 and delta_persistent):
            conf = min(trend_str, 85)
            return "TREND_DAY", conf

        # TREND_DAY moderado
        if trend_str >= 55 and range_eff >= 0.45 and rotation_str <= 50:
            return "TREND_DAY", min(trend_str, 70)

        # LOW_VOL_DAY: ATR bajo, poco movimiento
        if vol_state == "LOW" and trend_str <= 30:
            return "LOW_VOL_DAY", 70

        # HIGH_VOL_DAY: ATR alto pero sin dirección clara
        if vol_state in ("HIGH", "EXTREME") and trend_str < 55:
            return "HIGH_VOL_DAY", 65

        # TRAPPED_DAY: precio oscilando en rango con mucha rotación
        if rotation_str >= 70 and range_eff <= 0.30 and trend_str <= 35:
            return "TRAPPED_DAY", rotation_str

        # ROTATIONAL_DAY: rotación moderada-alta
        if rotation_str >= 55 and trend_str <= 50:
            return "ROTATIONAL_DAY", rotation_str

        # BALANCED_DAY: sin señal clara
        conf = max(30, 100 - trend_str - rotation_str // 2)
        return "BALANCED_DAY", min(conf, 80)

    def _continuation_prob(self, regime, trend_str,
                            range_eff, delta_persistent) -> int:
        base = {
            "TREND_DAY":     75,
            "EXPANSION_DAY": 70,
            "SHORT_COVERING":68,
            "LIQUIDATION":   72,
            "BALANCED_DAY":  40,
            "ROTATIONAL_DAY":30,
            "TRAPPED_DAY":   25,
            "LOW_VOL_DAY":   35,
            "HIGH_VOL_DAY":  50,
        }.get(regime, 45)

        if trend_str >= 70:  base += 10
        if range_eff >= 0.6: base += 8
        if delta_persistent: base += 7
        return min(base, 95)

    @property
    def last_result(self) -> Optional[SessionRegimeResult]:
        return self._last_result

    def reset_session(self) -> None:
        """Llamar al inicio de cada sesión."""
        self._session_open = 0.0
        self._session_high = 0.0
        self._session_low  = 9999.0
        self._bar_count    = 0
        self._prices.clear()
        self._highs.clear()
        self._lows.clear()
        self._deltas.clear()
        self._volumes.clear()
        self._moves.clear()