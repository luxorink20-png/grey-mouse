# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ OCIE — ocie_stacked_imbalance.py
#  Stacked Imbalance Calculator v1.0
#
#  Replica el cálculo de Stacked Imbalance de ATAS en Python puro.
#  El chart deja de calcular esto — solo recibe el resultado.
#
#  LÓGICA INSTITUCIONAL:
#  Un imbalance existe cuando el ask de una vela supera el bid de la
#  siguiente (buy imbalance) o el bid supera el ask (sell imbalance).
#  "Stacked" = 3+ imbalances consecutivos en el mismo nivel de precio.
#
#  OUTPUT: lista de ImbalanceZone para enviar al signal bus.
# ╚══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass, field
from collections import deque
from typing import List, Optional


# ══════════════════════════════════════════════════════════════════
#  CONFIGURACION (equivalente a los params de ATAS)
# ══════════════════════════════════════════════════════════════════

MIN_IMBALANCE_RATIO  = 3.0    # ask / bid mínimo para que sea imbalance
MIN_STACK_COUNT      = 3      # mínimo de imbalances consecutivos
ZONE_MERGE_TICKS     = 4      # ticks de tolerancia para fusionar zonas
ZONE_EXPIRY_BARS     = 50     # barras antes de expirar una zona no testeada
TICK                 = 0.25


# ══════════════════════════════════════════════════════════════════
#  ESTRUCTURAS
# ══════════════════════════════════════════════════════════════════

@dataclass
class ImbalanceZone:
    price_top:    float
    price_bottom: float
    direction:    str        # "BUY" | "SELL"
    strength:     int        # cantidad de imbalances apilados
    bar_created:  int
    tested:       bool  = False
    active:       bool  = True
    absorbed:     bool  = False  # True si entró precio con delta opuesto fuerte

    @property
    def midpoint(self) -> float:
        return round((self.price_top + self.price_bottom) / 2, 2)

    @property
    def size(self) -> float:
        return round(self.price_top - self.price_bottom, 2)

    def to_signal(self) -> dict:
        return {
            "type":         "STACKED_IMBALANCE",
            "direction":    self.direction,
            "price_top":    self.price_top,
            "price_bottom": self.price_bottom,
            "midpoint":     self.midpoint,
            "strength":     self.strength,
            "tested":       self.tested,
            "active":       self.active,
            "absorbed":     self.absorbed,
        }


@dataclass
class BarSnapshot:
    """Una barra cerrada con campos necesarios para imbalance calc."""
    bar_index:  int
    open:       float
    high:       float
    low:        float
    close:      float
    volume:     float
    ask_volume: float
    bid_volume: float
    delta:      float

    @property
    def body_top(self) -> float:
        return max(self.open, self.close)

    @property
    def body_bottom(self) -> float:
        return min(self.open, self.close)


# ══════════════════════════════════════════════════════════════════
#  ENGINE
# ══════════════════════════════════════════════════════════════════

class StackedImbalanceEngine:
    """
    Calcula Stacked Imbalance en Python replicando la lógica de ATAS.

    Compatible con el pipeline GIBBZ — recibe raw_data tick a tick,
    agrega barras internamente, y emite ImbalanceZone cuando detecta
    un stack confirmado.
    """

    WINDOW = 50   # barras a mantener en buffer

    def __init__(self,
                 min_ratio:       float = MIN_IMBALANCE_RATIO,
                 min_stack:       int   = MIN_STACK_COUNT,
                 merge_ticks:     int   = ZONE_MERGE_TICKS,
                 expiry_bars:     int   = ZONE_EXPIRY_BARS,
                 tick:            float = TICK):
        self._min_ratio   = min_ratio
        self._min_stack   = min_stack
        self._merge_ticks = merge_ticks
        self._expiry_bars = expiry_bars
        self._tick        = tick

        self._bars:  deque         = deque(maxlen=self.WINDOW)
        self._zones: List[ImbalanceZone] = []
        self._bar_index = 0

        # Estado de la barra actual en construcción
        self._current: Optional[dict] = None

    def update(self, raw: dict) -> List[ImbalanceZone]:
        """
        Procesa un tick/barra del feed.
        Retorna lista de zonas nuevas o actualizadas en este tick.
        """
        # Acumular barra actual
        self._accumulate(raw)

        # Si el tick indica barra cerrada (nueva barra = barra anterior cerró)
        is_new_bar = raw.get("bar_closed", False) or raw.get("new_bar", False)

        if is_new_bar and self._current is not None:
            bar = self._close_bar()
            self._bars.append(bar)
            self._bar_index += 1

            # Calcular imbalances con las últimas barras
            new_zones = self._detect_stacks()

            # Actualizar estado de zonas existentes
            self._update_zones(bar)

            return new_zones

        return []

    def update_bar(self, bar: BarSnapshot) -> List[ImbalanceZone]:
        """
        Versión directa para cuando ya tenés barras cerradas
        (útil en replay_feed donde procesás barra a barra).
        """
        self._bars.append(bar)
        self._bar_index += 1
        new_zones = self._detect_stacks()
        self._update_zones(bar)
        return new_zones

    def get_active_zones(self) -> List[ImbalanceZone]:
        """Retorna solo las zonas activas (no expiradas, no absorbidas)."""
        return [z for z in self._zones if z.active and not z.absorbed]

    def get_nearest_zone(self, price: float,
                          direction: str = "") -> Optional[ImbalanceZone]:
        """Retorna la zona activa más cercana al precio actual."""
        active = self.get_active_zones()
        if direction:
            active = [z for z in active if z.direction == direction]
        if not active:
            return None
        return min(active, key=lambda z: abs(z.midpoint - price))

    def is_price_in_zone(self, price: float) -> Optional[ImbalanceZone]:
        """True si el precio está dentro de una zona activa."""
        for z in self.get_active_zones():
            if z.price_bottom <= price <= z.price_top:
                return z
        return None

    # ──────────────────────────────────────────────────────────────
    #  INTERNAL: DETECCIÓN DE IMBALANCE Y STACKS
    # ──────────────────────────────────────────────────────────────

    def _detect_stacks(self) -> List[ImbalanceZone]:
        """
        Escanea el buffer de barras y detecta stacks nuevos.

        Lógica:
        BUY imbalance:  ask_vol(i) / bid_vol(i+1) >= min_ratio
        SELL imbalance: bid_vol(i) / ask_vol(i+1) >= min_ratio

        Stack: min_stack imbalances consecutivos del mismo tipo.
        """
        bars = list(self._bars)
        if len(bars) < self._min_stack + 1:
            return []

        new_zones = []

        # Escanear ventana de barras
        for i in range(len(bars) - self._min_stack):
            # Intentar BUY stack
            buy_count  = 0
            sell_count = 0
            prices_buy  = []
            prices_sell = []

            for j in range(i, min(i + 10, len(bars) - 1)):
                cur  = bars[j]
                nxt  = bars[j + 1]

                # BUY imbalance: agresores compradores vs bid siguiente
                if (nxt.bid_volume > 0 and
                        cur.ask_volume / max(nxt.bid_volume, 1) >= self._min_ratio):
                    buy_count += 1
                    prices_buy.append((cur.low, nxt.high))
                else:
                    if buy_count >= self._min_stack:
                        break
                    buy_count = 0
                    prices_buy = []

                # SELL imbalance: agresores vendedores vs ask siguiente
                if (nxt.ask_volume > 0 and
                        cur.bid_volume / max(nxt.ask_volume, 1) >= self._min_ratio):
                    sell_count += 1
                    prices_sell.append((nxt.low, cur.high))
                else:
                    if sell_count >= self._min_stack:
                        break
                    sell_count = 0
                    prices_sell = []

            # Crear zona BUY si stack confirmado
            if buy_count >= self._min_stack and prices_buy:
                zone = self._create_zone(prices_buy, "BUY", buy_count)
                if zone and not self._zone_exists(zone):
                    self._zones.append(zone)
                    new_zones.append(zone)

            # Crear zona SELL si stack confirmado
            if sell_count >= self._min_stack and prices_sell:
                zone = self._create_zone(prices_sell, "SELL", sell_count)
                if zone and not self._zone_exists(zone):
                    self._zones.append(zone)
                    new_zones.append(zone)

        return new_zones

    def _create_zone(self, price_pairs: list,
                      direction: str,
                      strength: int) -> Optional[ImbalanceZone]:
        """Crea una ImbalanceZone a partir de pares de precios."""
        if not price_pairs:
            return None
        lows   = [p[0] for p in price_pairs]
        highs  = [p[1] for p in price_pairs]
        bottom = round(min(lows),  2)
        top    = round(max(highs), 2)
        if top - bottom < self._tick:
            return None
        return ImbalanceZone(
            price_top    = top,
            price_bottom = bottom,
            direction    = direction,
            strength     = strength,
            bar_created  = self._bar_index,
        )

    def _zone_exists(self, new_zone: ImbalanceZone) -> bool:
        """Evita duplicar zonas en el mismo rango de precios."""
        merge_dist = self._merge_ticks * self._tick
        for z in self._zones:
            if z.direction != new_zone.direction:
                continue
            if (abs(z.price_top    - new_zone.price_top)    < merge_dist and
                    abs(z.price_bottom - new_zone.price_bottom) < merge_dist):
                return True
        return False

    def _update_zones(self, bar: BarSnapshot):
        """
        Actualiza estado de zonas existentes con la barra nueva.
        - Marca como tested si el precio entró en la zona
        - Marca como absorbed si delta fue fuerte y opuesto
        - Expira zonas viejas no testeadas
        """
        for zone in self._zones:
            if not zone.active:
                continue

            # Expirar
            age = self._bar_index - zone.bar_created
            if age > self._expiry_bars and not zone.tested:
                zone.active = False
                continue

            # Testeo: precio entró en la zona
            if bar.low <= zone.price_top and bar.high >= zone.price_bottom:
                zone.tested = True

                # Absorción: delta opuesto fuerte dentro de la zona
                if zone.direction == "BUY" and bar.delta < -300:
                    zone.absorbed = True
                    zone.active   = False
                elif zone.direction == "SELL" and bar.delta > 300:
                    zone.absorbed = True
                    zone.active   = False

    # ──────────────────────────────────────────────────────────────
    #  INTERNAL: BARRA ACTUAL
    # ──────────────────────────────────────────────────────────────

    def _accumulate(self, raw: dict):
        """Acumula el tick en la barra actual en construcción."""
        price = float(raw.get("price", 0))
        vol   = float(raw.get("volume", 0))
        ask   = float(raw.get("ask_volume", 0))
        bid   = float(raw.get("bid_volume", 0))
        delta = float(raw.get("delta", ask - bid))

        if self._current is None:
            self._current = {
                "open":  price, "high": price,
                "low":   price, "close": price,
                "volume": 0.0, "ask_volume": 0.0,
                "bid_volume": 0.0, "delta": 0.0,
            }

        self._current["high"]       = max(self._current["high"], price)
        self._current["low"]        = min(self._current["low"],  price)
        self._current["close"]      = price
        self._current["volume"]    += vol
        self._current["ask_volume"]+= ask
        self._current["bid_volume"]+= bid
        self._current["delta"]     += delta

    def _close_bar(self) -> BarSnapshot:
        """Cierra la barra actual y retorna BarSnapshot."""
        c = self._current
        bar = BarSnapshot(
            bar_index  = self._bar_index,
            open       = c["open"],
            high       = c["high"],
            low        = c["low"],
            close      = c["close"],
            volume     = c["volume"],
            ask_volume = c["ask_volume"],
            bid_volume = c["bid_volume"],
            delta      = c["delta"],
        )
        self._current = None
        return bar