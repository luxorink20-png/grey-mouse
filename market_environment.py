# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — market_environment.py
#  Market Environment Analyzer v1.1
#
#  CAMBIOS v1.1 vs v1.0:
#  ─ Warmup period (< MIN_BARS) retorna ROTATIONAL con tradeable=True
#    en lugar de COMPRESSION con dir_eff=0 que disparaba blocks_trading()
#  ─ blocks_trading() agrega guard: dir_eff=0 solo bloquea si
#    tenemos suficientes barras para confiar en la métrica (MIN_BARS)
#  ─ MIN_BARS reducido de 8 a 6 — menos barras de warmup ciego
#  ─ COMPRESSION en _classify ahora requiere vol_state="LOW" explícito
#    (antes bloqueaba en NORMAL también con dir_eff bajo)
#
#  PROBLEMA RESUELTO:
#  Barras de reinicio de contexto (evt=INIT) recibían COMPRESSION
#  con dir_eff=0, lo que disparaba blocks_trading() por
#  directional_efficiency < 35, bloqueando toda la secuencia
#  antes de que continuation/validator pudieran evaluarla.
#
#  SIN CAMBIOS:
#  ─ Todas las métricas (_calc_*)
#  ─ _classify lógica general
#  ─ _calc_danger
#  ─ Todos los thresholds de TOXIC_ENVS
#  ─ LIQUIDATION / TRAPPY / CHOPPY / EFFICIENT_TREND detection
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class MarketEnvironmentResult:
    environment:            str   = "ROTATIONAL"
    confidence:             int   = 0
    tradeable:              bool  = True
    danger_level:           int   = 0
    rotation_factor:        int   = 0
    trend_efficiency:       int   = 0
    trap_density:           int   = 0
    breakout_failure_rate:  int   = 0
    directional_efficiency: int   = 0
    mean_reversion_strength:int   = 0
    delta_noise:            int   = 0
    volatility_state:       str   = "NORMAL"
    sweep_frequency:        int   = 0
    # v1.1: flag para saber si estamos en warmup
    warming_up:             bool  = False

    def blocks_trading(self) -> bool:
        """
        Retorna True si el entorno debe bloquear trades.
        v1.1: durante warmup (warming_up=True) NO bloquear por
        dir_eff bajo — la métrica no es confiable todavía.
        """
        if self.environment == "LIQUIDATION":
            return True
        if self.trap_density > 70:
            return True
        if self.breakout_failure_rate > 65:
            return True
        # v1.1: solo bloquear por dir_eff si tenemos datos suficientes
        if not self.warming_up and self.directional_efficiency < 35:
            return True
        if self.rotation_factor > 80 and self.volatility_state == "HIGH":
            return True
        if self.danger_level > 80:
            return True
        return False

    def block_reason(self) -> str:
        if self.environment == "LIQUIDATION":
            return "Entorno LIQUIDATION — estructura rota"
        if self.trap_density > 70:
            return f"Trap density excesiva ({self.trap_density})"
        if self.breakout_failure_rate > 65:
            return f"Breakout failure rate alto ({self.breakout_failure_rate}%)"
        if not self.warming_up and self.directional_efficiency < 35:
            return f"Eficiencia direccional baja ({self.directional_efficiency})"
        if self.rotation_factor > 80 and self.volatility_state == "HIGH":
            return "Rotación extrema con vol alta"
        if self.danger_level > 80:
            return f"Danger level crítico ({self.danger_level})"
        return ""

    def __str__(self) -> str:
        return (f"{self.environment} conf={self.confidence}% "
                f"danger={self.danger_level} "
                f"eff={self.directional_efficiency} "
                f"trap={self.trap_density} "
                f"bfr={self.breakout_failure_rate}%")


@dataclass
class EnvBar:
    price:      float
    high:       float
    low:        float
    open:       float
    close:      float
    delta:      float
    volume:     float
    price_move: float
    absorption: bool


class MarketEnvironmentAnalyzer:
    """
    Analiza el entorno de mercado usando ventana deslizante.

    v1.1 — Cambios clave:
    - MIN_BARS reducido de 8 a 6
    - Warmup retorna ROTATIONAL tradeable=True (no COMPRESSION)
    - blocks_trading() respeta warming_up flag
    - COMPRESSION en classify requiere vol_state=LOW explícito
    """

    WINDOW   = 20
    MIN_BARS = 6    # v1.1: reducido de 8 a 6

    def __init__(self, tick: float = 0.25):
        self._tick          = tick
        self._bars: deque   = deque(maxlen=self.WINDOW)
        self._avg_vol: float = 0.0
        self._vol_s:  deque = deque(maxlen=30)
        self._atrs:   deque = deque(maxlen=20)
        self._recent_breakouts: deque = deque(maxlen=10)
        self._last_result: Optional[MarketEnvironmentResult] = None
        self._bars_seen:   int = 0   # v1.1: contador global de barras

    def analyze_environment(self, raw_data: dict,
                             event_result: dict) -> MarketEnvironmentResult:
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

        if volume > 0:
            self._vol_s.append(volume)
        self._avg_vol = (sum(self._vol_s) / len(self._vol_s)
                         if self._vol_s else volume)

        atr = high - low
        self._atrs.append(atr)

        bar = EnvBar(price=price, high=high, low=low,
                     open=open_, close=close, delta=delta,
                     volume=volume, price_move=price_move,
                     absorption=absorption)
        self._bars.append(bar)
        self._bars_seen += 1

        # ── v1.1 WARMUP — retorna ROTATIONAL tradeable, NO COMPRESSION ──
        if len(self._bars) < self.MIN_BARS:
            result = MarketEnvironmentResult(
                environment            = "ROTATIONAL",   # era COMPRESSION
                confidence             = 0,
                tradeable              = True,
                danger_level           = 0,
                directional_efficiency = 50,             # valor neutral, no 0
                warming_up             = True,
            )
            self._last_result = result
            return result
        # ─────────────────────────────────────────────────────────────────

        bars = list(self._bars)

        rotation     = self._calc_rotation(bars)
        trap_density = self._calc_trap_density(bars)
        bfr          = self._calc_breakout_failure_rate(bars)
        dir_eff      = self._calc_directional_efficiency(bars)
        mean_rev     = self._calc_mean_reversion(bars)
        delta_noise  = self._calc_delta_noise(bars)
        sweep_freq   = self._calc_sweep_frequency(bars)
        vol_state    = self._calc_volatility_state()

        env, confidence = self._classify(
            rotation, trap_density, bfr, dir_eff,
            mean_rev, delta_noise, vol_state, sweep_freq
        )

        danger = self._calc_danger(
            env, trap_density, bfr, dir_eff, rotation, vol_state
        )

        # Construir resultado temporal para evaluar blocks_trading
        temp = MarketEnvironmentResult(
            environment            = env,
            confidence             = confidence,
            danger_level           = danger,
            rotation_factor        = rotation,
            trap_density           = trap_density,
            breakout_failure_rate  = bfr,
            directional_efficiency = dir_eff,
            volatility_state       = vol_state,
            warming_up             = False,
        )
        tradeable = not temp.blocks_trading()

        result = MarketEnvironmentResult(
            environment             = env,
            confidence              = confidence,
            tradeable               = tradeable,
            danger_level            = danger,
            rotation_factor         = rotation,
            trend_efficiency        = dir_eff,
            trap_density            = trap_density,
            breakout_failure_rate   = bfr,
            directional_efficiency  = dir_eff,
            mean_reversion_strength = mean_rev,
            delta_noise             = delta_noise,
            volatility_state        = vol_state,
            sweep_frequency         = sweep_freq,
            warming_up              = False,
        )
        self._last_result = result
        return result

    # ──────────────────────────────────────────────────────────────
    #  MÉTRICAS — sin cambios vs v1.0
    # ──────────────────────────────────────────────────────────────

    def _calc_rotation(self, bars: list) -> int:
        if len(bars) < 3:
            return 50
        moves     = [b.price_move for b in bars]
        reversals = sum(
            1 for i in range(1, len(moves))
            if moves[i] * moves[i-1] < 0 and abs(moves[i]) > self._tick
        )
        rate = reversals / max(len(moves)-1, 1)
        if rate >= 0.65:  return 90
        if rate >= 0.50:  return 75
        if rate >= 0.35:  return 55
        if rate >= 0.20:  return 35
        return 15

    def _calc_trap_density(self, bars: list) -> int:
        if len(bars) < 4:
            return 20
        traps = 0
        for b in bars[-10:]:
            candle_range = b.high - b.low
            if candle_range > self._tick * 3:
                body       = abs(b.close - b.open)
                wick_ratio = 1.0 - (body / candle_range)
                if wick_ratio > 0.65:
                    traps += 1
                if ((b.price_move > self._tick*2 and b.delta < -80) or
                        (b.price_move < -self._tick*2 and b.delta > 80)):
                    traps += 1
        total_bars = min(10, len(bars))
        rate       = traps / total_bars if total_bars > 0 else 0
        return min(int(rate * 100), 100)

    def _calc_breakout_failure_rate(self, bars: list) -> int:
        if len(bars) < 5:
            return 30
        failures = 0
        attempts = 0
        for i in range(2, len(bars)-1):
            if abs(bars[i].price_move) >= self._tick * 4:
                attempts += 1
                future_prices = [bars[j].price for j in range(i+1, min(i+3, len(bars)))]
                if future_prices:
                    if (bars[i].price_move > 0 and
                            min(future_prices) < bars[i].open):
                        failures += 1
                    elif (bars[i].price_move < 0 and
                            max(future_prices) > bars[i].open):
                        failures += 1
        if attempts == 0:
            return 25
        return min(int(failures / attempts * 100), 100)

    def _calc_directional_efficiency(self, bars: list) -> int:
        if len(bars) < 3:
            return 50
        prices     = [b.price for b in bars]
        net_move   = abs(prices[-1] - prices[0])
        total_path = sum(abs(b.price_move) for b in bars)
        if total_path == 0:
            return 10
        eff = net_move / total_path
        return min(int(eff * 100), 100)

    def _calc_mean_reversion(self, bars: list) -> int:
        if len(bars) < 5:
            return 40
        prices    = [b.price for b in bars]
        center    = sum(prices) / len(prices)
        crossings = sum(
            1 for i in range(1, len(prices))
            if (prices[i-1] - center) * (prices[i] - center) < 0
        )
        rate = crossings / max(len(prices)-1, 1)
        if rate >= 0.5:   return 85
        if rate >= 0.35:  return 65
        if rate >= 0.20:  return 45
        return 20

    def _calc_delta_noise(self, bars: list) -> int:
        if len(bars) < 3:
            return 40
        incoherent = sum(
            1 for b in bars[-8:]
            if ((b.price_move > self._tick*2 and b.delta < -50) or
                (b.price_move < -self._tick*2 and b.delta > 50))
        )
        total = min(8, len(bars))
        return min(int(incoherent / total * 100), 100)

    def _calc_sweep_frequency(self, bars: list) -> int:
        if len(bars) < 4:
            return 20
        sweeps = 0
        for b in bars[-8:]:
            candle_range = b.high - b.low
            body         = abs(b.close - b.open)
            if candle_range > self._tick * 6 and body < candle_range * 0.3:
                sweeps += 1
        return min(int(sweeps / min(8, len(bars)) * 100), 100)

    def _calc_volatility_state(self) -> str:
        if len(self._atrs) < 5:
            return "NORMAL"
        avg_atr    = sum(self._atrs) / len(self._atrs)
        recent_atr = sum(list(self._atrs)[-5:]) / 5
        ratio      = recent_atr / avg_atr if avg_atr > 0 else 1.0
        if ratio >= 2.0:  return "EXTREME"
        if ratio >= 1.4:  return "HIGH"
        if ratio <= 0.5:  return "LOW"
        return "NORMAL"

    # ──────────────────────────────────────────────────────────────
    #  CLASIFICACIÓN v1.1
    # ──────────────────────────────────────────────────────────────

    def _classify(self, rotation, trap_density, bfr,
                   dir_eff, mean_rev, delta_noise,
                   vol_state, sweep_freq) -> tuple:

        if dir_eff >= 75 and delta_noise <= 20 and rotation <= 25:
            return "EFFICIENT_TREND", min(dir_eff, 88)

        if (trap_density >= 65 or bfr >= 60 or
                (delta_noise >= 65 and rotation >= 60)):
            return "TRAPPY", max(trap_density, bfr)

        if rotation >= 70 and dir_eff <= 35:
            return "CHOPPY", rotation

        if (rotation >= 50 and mean_rev >= 60 and
                dir_eff <= 50 and vol_state in ("NORMAL", "LOW")):
            return "ROTATIONAL", min(rotation + mean_rev // 2, 80)

        if vol_state == "LOW" and dir_eff <= 40 and rotation <= 30:
            return "DEAD_MARKET", 75

        # v1.1: COMPRESSION ahora requiere vol_state="LOW" explícito
        # Antes también clasificaba con vol_state="NORMAL", lo que
        # capturaba setups válidos en compresión normal pre-expansión
        if (delta_noise <= 30 and dir_eff <= 40 and
                rotation <= 30 and vol_state == "LOW"):
            return "COMPRESSION", 65

        # Liquidation: dir_eff alto + sweep alto + vol extremo
        if dir_eff >= 65 and sweep_freq >= 60 and vol_state in ("HIGH", "EXTREME"):
            return "LIQUIDATION", min(dir_eff, 85)

        return "ROTATIONAL", 50

    def _calc_danger(self, env, trap_density, bfr,
                     dir_eff, rotation, vol_state) -> int:
        danger = 0
        if env == "LIQUIDATION":   danger += 50
        elif env == "TRAPPY":       danger += 40
        elif env == "CHOPPY":       danger += 30
        elif env == "DEAD_MARKET":  danger += 25
        if trap_density > 70:      danger += 20
        if bfr > 60:                danger += 18
        if dir_eff < 30:            danger += 12
        if rotation > 75 and vol_state == "HIGH":
            danger += 15
        return min(danger, 100)

    @property
    def last_result(self) -> Optional[MarketEnvironmentResult]:
        return self._last_result
