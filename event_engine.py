# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — event_engine.py
#  Institutional Order Flow Classifier v3.0
#
#  CAMBIOS v3.0:
#  - Micro rango integrado directamente en el engine
#  - Breakout desde micro rango detectado aquí
#  - price_move, dead_zone, micro_range en context
#  - Clasificación más precisa de INTENTO vs ruido
#  - Delta coherente con dirección = bonus de confianza
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque


class EventEngine:

    THRESHOLD_INTENTO = 2.0
    THRESHOLD_FALLO   = 2.0
    THRESHOLD_ACUMULACION  = 1.5
    WARMUP_BARS            = 3

    # Rango muerto
    DEAD_ZONE_BARS         = 5
    DEAD_ZONE_MAX_MOVE     = 1.0
    DEAD_ZONE_MAX_MOMENTUM = 0.5

    # Micro rango (≤ 8 ticks de 0.25 = 2.0 puntos, mínimo 5 velas)
    MICRO_RANGE_MAX_SIZE   = 2.0    # 8 ticks × 0.25
    MICRO_RANGE_MIN_BARS   = 5
    MICRO_BREAKOUT_TICKS   = 1.0    # 4 ticks mínimo para breakout (4 × 0.25)

    def __init__(self, window: int = 10):
        self._prices      = deque(maxlen=window)
        self._deltas      = deque(maxlen=window)
        self._price_moves = deque(maxlen=self.DEAD_ZONE_BARS)
        self._highs       = deque(maxlen=self.MICRO_RANGE_MIN_BARS + 5)
        self._lows        = deque(maxlen=self.MICRO_RANGE_MIN_BARS + 5)
        self.last_event   = "INIT"
        self._bar_count   = 0

        # Estado micro rango
        self._micro_range_high  = 0.0
        self._micro_range_low   = 0.0
        self._micro_range_bars  = 0
        self._micro_active      = False

    def process(self, raw_data: dict) -> dict:
        price      = float(raw_data.get("price",      0))
        high       = float(raw_data.get("high",       price))
        low        = float(raw_data.get("low",        price))
        bid_volume = float(raw_data.get("bid_volume", 0))
        ask_volume = float(raw_data.get("ask_volume", 0))

        self._bar_count += 1

        if not self._prices:
            self._prices.append(price)
            self._highs.append(high)
            self._lows.append(low)
            return self._result("INIT", 0,
                "Sistema inicializado.", 0, 0, False, 0, 0, False,
                False, 0.0, 0.0, None)

        prev_price = self._prices[-1]
        price_move = price - prev_price

        if ask_volume > 0 or bid_volume > 0:
            delta = ask_volume - bid_volume
        else:
            delta = price_move * 100

        volume = (ask_volume + bid_volume
                  if (ask_volume + bid_volume) > 0
                  else abs(delta))

        self._prices.append(price)
        self._deltas.append(delta)
        self._price_moves.append(abs(price_move))
        self._highs.append(high)
        self._lows.append(low)

        if self._bar_count < self.WARMUP_BARS:
            return self._result("INIT", 0,
                f"Calentando ({self._bar_count}/{self.WARMUP_BARS})",
                delta, volume, False, 0, price_move, False,
                False, 0.0, 0.0, None)

        prices_list = list(self._prices)
        deltas_list = list(self._deltas)

        recent_moves = [
            prices_list[i] - prices_list[i-1]
            for i in range(max(1, len(prices_list)-3), len(prices_list))
        ]
        momentum   = sum(recent_moves) / len(recent_moves) if recent_moves else 0.0
        absorption = (abs(delta) > 150
                      and abs(price_move) < self.THRESHOLD_ACUMULACION)
        dead_zone  = self._detect_dead_zone(momentum)

        # ── MICRO RANGO ────────────────────────────────────────────
        micro_active, micro_high, micro_low, micro_breakout = \
            self._detect_micro_range(price, high, low)

        recent_deltas = (deltas_list[-5:]
                         if len(deltas_list) >= 5
                         else deltas_list)

        event, confidence, reason = self._classify(
            price_move, delta, momentum, absorption,
            recent_deltas, volume, micro_breakout
        )

        self.last_event = event

        return self._result(
            event, confidence, reason,
            delta, volume, absorption, momentum, price_move,
            dead_zone, micro_active, micro_high, micro_low, micro_breakout
        )

    # ──────────────────────────────────────────────────────────────
    #  MICRO RANGO
    # ──────────────────────────────────────────────────────────────

    def _detect_micro_range(self, price: float,
                             high: float, low: float) -> tuple:
        """
        Detecta micro rango institucional:
        - Rango ≤ 8 ticks (2.0 puntos en MES/MNQ)
        - Duración ≥ 5 velas
        - Precio lateral

        Detecta breakout:
        - Precio rompe rango + 4 ticks (1.0 punto)

        Retorna (active, range_high, range_low, breakout_dir)
        breakout_dir: "UP" | "DOWN" | None
        """
        highs = list(self._highs)
        lows  = list(self._lows)

        if len(highs) < self.MICRO_RANGE_MIN_BARS:
            return False, 0.0, 0.0, None

        # Calcular rango de últimas N velas
        recent_highs = highs[-self.MICRO_RANGE_MIN_BARS:]
        recent_lows  = lows[-self.MICRO_RANGE_MIN_BARS:]
        r_high       = max(recent_highs)
        r_low        = min(recent_lows)
        r_size       = r_high - r_low

        # Verificar si está en micro rango
        if r_size <= self.MICRO_RANGE_MAX_SIZE:
            self._micro_active    = True
            self._micro_range_high = r_high
            self._micro_range_low  = r_low
            self._micro_range_bars += 1
        else:
            # Verificar breakout si estábamos en rango
            if self._micro_active:
                if price > self._micro_range_high + self.MICRO_BREAKOUT_TICKS:
                    self._micro_active    = False
                    self._micro_range_bars = 0
                    return False, self._micro_range_high, self._micro_range_low, "UP"
                if price < self._micro_range_low - self.MICRO_BREAKOUT_TICKS:
                    self._micro_active    = False
                    self._micro_range_bars = 0
                    return False, self._micro_range_high, self._micro_range_low, "DOWN"
            self._micro_active    = False
            self._micro_range_bars = 0
            return False, 0.0, 0.0, None

        return (self._micro_active,
                self._micro_range_high,
                self._micro_range_low,
                None)

    # ──────────────────────────────────────────────────────────────
    #  DEAD ZONE
    # ──────────────────────────────────────────────────────────────

    def _detect_dead_zone(self, momentum: float) -> bool:
        if len(self._price_moves) < self.DEAD_ZONE_BARS:
            return False
        avg_move = sum(self._price_moves) / len(self._price_moves)
        return (avg_move < self.DEAD_ZONE_MAX_MOVE
                and abs(momentum) < self.DEAD_ZONE_MAX_MOMENTUM)

    # ──────────────────────────────────────────────────────────────
    #  CLASIFICACIÓN
    # ──────────────────────────────────────────────────────────────

    def _classify(self, price_move, delta, momentum,
                  absorption, recent_deltas, volume,
                  micro_breakout) -> tuple:

        # Breakout de micro rango → INTENTO con alta confianza
        if micro_breakout == "UP":
            return ("INTENTO", 88,
                    f"Breakout micro rango alcista. Mov:{price_move:+.2f}")
        if micro_breakout == "DOWN":
            return ("INTENTO", 88,
                    f"Breakout micro rango bajista. Mov:{price_move:+.2f}")

        # AGOTAMIENTO
        if (self.last_event == "INTENTO"
                and abs(price_move) > self.THRESHOLD_FALLO
                and (price_move < 0 if delta > 0 else price_move > 0)):
            conf = min(int(abs(price_move) * 15), 95)
            return ("AGOTAMIENTO", conf,
                    f"Reversión tras INTENTO. Mov:{price_move:+.2f}")

        # INTENTO ALCISTA
        if price_move > self.THRESHOLD_INTENTO and momentum > 0:
            delta_bonus = 10 if delta > 100 else 0
            conf = min(int(price_move * 15) + delta_bonus, 90)
            return ("INTENTO", conf,
                    f"Impulso alcista. Δ:{delta:+.0f} Mov:{price_move:+.2f}")

        # INTENTO BAJISTA
        if price_move < -self.THRESHOLD_INTENTO and momentum < 0:
            delta_bonus = 10 if delta < -100 else 0
            conf = min(int(abs(price_move) * 15) + delta_bonus, 90)
            return ("INTENTO", conf,
                    f"Impulso bajista. Δ:{delta:+.0f} Mov:{price_move:+.2f}")

        # FALLO ALCISTA
        if price_move > self.THRESHOLD_FALLO and delta < -50:
            return ("FALLO", 70,
                    f"Intento alcista absorbido. Δ:{delta:+.0f}")

        # FALLO BAJISTA
        if price_move < -self.THRESHOLD_FALLO and delta > 50:
            return ("FALLO", 70,
                    f"Intento bajista absorbido. Δ:{delta:+.0f}")

        # ABSORCIÓN
        if absorption:
            return ("ACUMULACIÓN", 75,
                    "Absorción detectada. Precio sin desplazamiento.")

        # RANGO ESTRECHO
        if abs(price_move) < self.THRESHOLD_ACUMULACION:
            conf = max(40, 75 - int(abs(price_move) * 10))
            return ("ACUMULACIÓN", conf,
                    f"Rango estrecho. Mov:{price_move:+.2f}")

        return ("ACUMULACIÓN", 30,
                f"Sin señal dominante. Mov:{price_move:+.2f}")

    # ──────────────────────────────────────────────────────────────
    #  RESULT
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _result(event, confidence, reason,
                delta, volume, absorption, momentum,
                price_move, dead_zone,
                micro_active, micro_high, micro_low,
                micro_breakout) -> dict:
        return {
            "event":      event,
            "confidence": confidence,
            "reason":     reason,
            "context": {
                "delta":           round(delta,      2),
                "volume":          round(volume,     2),
                "absorption":      absorption,
                "momentum":        round(momentum,   4),
                "price_move":      round(price_move, 2),
                "dead_zone":       dead_zone,
                "micro_active":    micro_active,
                "micro_high":      round(micro_high, 2),
                "micro_low":       round(micro_low,  2),
                "micro_breakout":  micro_breakout,
            }
        }
