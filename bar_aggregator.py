"""
GIBBZ Bar Aggregator
Acumula trades individuales del bridge y genera barras
con volumen, delta, high/low reales.

Modos:
  TIME_BASED  — barra cada N segundos (default: 5s)
  VOLUME_BASED — barra cada N contratos
  TICK_BASED  — barra cada N ticks
"""

import time
from dataclasses import dataclass, field
from typing import Optional


# ── CONFIGURACIÓN ──────────────────────────────────────────────────
BAR_MODE    = "TIME"   # "TIME" | "VOLUME" | "TICK"
BAR_SECONDS = 5        # segundos por barra (TIME mode)
BAR_VOLUME  = 100      # contratos por barra (VOLUME mode)
BAR_TICKS   = 50       # ticks por barra (TICK mode)
# ──────────────────────────────────────────────────────────────────


@dataclass
class Bar:
    open:       float = 0.0
    high:       float = 0.0
    low:        float = float("inf")
    close:      float = 0.0
    volume:     float = 0.0
    ask_volume: float = 0.0
    bid_volume: float = 0.0
    delta:      float = 0.0
    trades:     int   = 0
    tick_count: int   = 0
    open_time:  float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "price":      self.close,
            "open":       self.open,
            "high":       self.high,
            "low":        self.low,
            "close":      self.close,
            "volume":     self.volume,
            "ask_volume": self.ask_volume,
            "bid_volume": self.bid_volume,
            "delta":      self.delta,
            "trades":     self.trades,
        }


class BarAggregator:

    def __init__(self,
                 mode:    str = BAR_MODE,
                 seconds: int = BAR_SECONDS,
                 volume:  int = BAR_VOLUME,
                 ticks:   int = BAR_TICKS):

        self.mode    = mode
        self.seconds = seconds
        self.volume  = volume
        self.ticks   = ticks

        self._bar:      Optional[Bar] = None
        self._bar_start: float        = 0.0
        self._completed_bar: Optional[dict] = None

    # ── API PÚBLICA ────────────────────────────────────────────────

    def process(self, tick: dict) -> Optional[dict]:
        """
        Recibe un tick individual del bridge.
        Retorna una barra completa cuando se cumple la condición,
        o None si la barra sigue acumulando.
        """
        self._completed_bar = None

        if self._bar is None:
            self._open_bar(tick)

        self._update_bar(tick)

        if self._should_close():
            self._completed_bar = self._bar.to_dict()
            self._bar = None
            return self._completed_bar

        return None

    def get_partial(self) -> Optional[dict]:
        """Retorna la barra parcial actual (para display en tiempo real)."""
        if self._bar is None:
            return None
        return self._bar.to_dict()

    def force_close(self) -> Optional[dict]:
        """Fuerza el cierre de la barra actual (útil al detener engine)."""
        if self._bar is not None and self._bar.trades > 0:
            result = self._bar.to_dict()
            self._bar = None
            return result
        return None

    # ── INTERNOS ──────────────────────────────────────────────────

    def _open_bar(self, tick: dict):
        price      = float(tick.get("price", 0))
        self._bar  = Bar(
            open      = price,
            high      = price,
            low       = price,
            close     = price,
            open_time = time.time(),
        )
        self._bar_start = time.time()

    def _update_bar(self, tick: dict):
        b = self._bar
        price      = float(tick.get("price",      0))
        ask_vol    = float(tick.get("ask_volume",  tick.get("volume", 0)))
        bid_vol    = float(tick.get("bid_volume",  0))
        vol        = float(tick.get("volume",      ask_vol + bid_vol))

        b.close      = price
        b.high       = max(b.high, price)
        b.low        = min(b.low,  price)
        b.volume    += vol
        b.ask_volume += ask_vol
        b.bid_volume += bid_vol
        b.delta      = b.ask_volume - b.bid_volume
        b.trades    += int(tick.get("trades", 1))
        b.tick_count += 1

    def _should_close(self) -> bool:
        if self._bar is None:
            return False
        if self.mode == "TIME":
            return (time.time() - self._bar_start) >= self.seconds
        if self.mode == "VOLUME":
            return self._bar.volume >= self.volume
        if self.mode == "TICK":
            return self._bar.tick_count >= self.ticks
        return False