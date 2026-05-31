# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — poc_acceptance.py
#  POC Acceptance & Institutional Context Engine v1.0
#
#  Actúa como REFINEMENT LAYER — no reemplaza lógica existente.
#  Alimenta scores adicionales al confluence engine.
#
#  DETECTA:
#  1. POC Position Analysis     — dónde vive el POC en la vela
#  2. Aggression Without Result — delta alto, rango pequeño = absorción
#  3. Trapped Aggressors        — compradores/vendedores atrapados
#  4. Acceptance Failure        — imbalance sin desplazamiento posterior
#  5. Effort vs Result          — eficiencia real del movimiento
#
#  OUTPUT: PocAcceptanceResult con scores 0-100 y flags booleanos
#  CONEXIÓN: confluence_engine.evaluate() recibe poc_acceptance=result
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque
from dataclasses import dataclass, field
from typing import Optional


# ══════════════════════════════════════════════════════════════════
#  RESULT
# ══════════════════════════════════════════════════════════════════

@dataclass
class PocAcceptanceResult:
    # POC position
    poc_state:            str   = "NEUTRAL"   # ACCEPTED_HIGH/ACCEPTED_LOW/REJECTED_HIGH/REJECTED_LOW/NEUTRAL
    poc_position:         str   = "MID"       # TOP_THIRD/MID/BOTTOM_THIRD

    # Absorption / effort vs result
    absorption_score:          int   = 0      # 0-100. Alto = institución absorbiendo
    aggression_efficiency:     float = 0.0    # rango / delta_abs. Bajo = ineficiente
    aggression_without_result: bool  = False  # delta extremo, rango mínimo

    # Trapped aggressors
    trapped_buyers_score:   int   = 0   # 0-100
    trapped_sellers_score:  int   = 0   # 0-100
    trap_active:            bool  = False

    # Acceptance / auction
    acceptance_confirmed:   bool  = False
    auction_failure:        bool  = False
    breakout_exhaustion:    bool  = False

    # Score compuesto para confluence
    poc_acceptance_score:   int   = 50   # 0-100 (50=neutral, >50=favorable, <50=warning)
    auction_failure_score:  int   = 0    # 0-100 (alto = auction fallida)
    trap_probability_score: int   = 0    # 0-100

    reason: str = ""

    def is_favorable(self) -> bool:
        return (self.poc_acceptance_score >= 55 and
                not self.trap_active and
                not self.auction_failure)

    def is_warning(self) -> bool:
        return (self.absorption_score >= 65 or
                self.trap_active or
                self.auction_failure or
                self.aggression_without_result)

    def __str__(self) -> str:
        return (f"poc={self.poc_state} absorb={self.absorption_score} "
                f"eff={round(self.aggression_efficiency,2)} "
                f"trap={self.trap_active} "
                f"accept={self.acceptance_confirmed} "
                f"score={self.poc_acceptance_score}")


# ══════════════════════════════════════════════════════════════════
#  BAR SNAPSHOT
# ══════════════════════════════════════════════════════════════════

@dataclass
class PocBar:
    price:      float
    high:       float
    low:        float
    open:       float
    close:      float
    delta:      float
    volume:     float
    price_move: float
    absorption: bool
    ask_vol:    float
    bid_vol:    float


# ══════════════════════════════════════════════════════════════════
#  ENGINE
# ══════════════════════════════════════════════════════════════════

class PocAcceptanceEngine:
    """
    Refinement layer institucional v1.0.

    Analiza las últimas N velas para detectar:
    - Si el precio acepta realmente sobre/bajo el POC
    - Si hay absorción pasiva (institución tomando el otro lado)
    - Si los agresores están atrapados
    - Si el breakout tiene efficiency real

    Se conecta DESPUÉS del confirmation engine y ANTES del confluence.
    No modifica ningún engine existente — solo agrega contexto.
    """

    WINDOW = 12

    def __init__(self, vah: float = 0.0, poc: float = 0.0,
                 val: float = 0.0, tick: float = 0.25):
        self._vah        = vah
        self._poc        = poc
        self._val        = val
        self._tick       = tick
        self._bars: deque= deque(maxlen=self.WINDOW)
        self._avg_vol    = 0.0
        self._vol_s:deque= deque(maxlen=25)
        self._avg_delta  = 0.0
        self._delta_s:deque = deque(maxlen=20)

    def update_levels(self, vah: float, poc: float, val: float) -> None:
        """Actualizar niveles de sesión. Llamar cuando cambien VAH/POC/VAL."""
        self._vah = vah
        self._poc = poc
        self._val = val

    def analyze(self, raw_data: dict,
                event_result: dict,
                confirmation=None) -> PocAcceptanceResult:
        """
        Analiza el tick actual y retorna PocAcceptanceResult.

        Args:
            raw_data:     dict del market feed
            event_result: dict del EventEngine
            confirmation: ConfirmationResult (opcional)
        """
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
        ask_vol    = float(raw_data.get("ask_volume", 0))
        bid_vol    = float(raw_data.get("bid_volume", 0))

        if volume > 0:
            self._vol_s.append(volume)
        self._avg_vol = (sum(self._vol_s) / len(self._vol_s)
                         if self._vol_s else volume)

        if abs(delta) > 0:
            self._delta_s.append(abs(delta))
        self._avg_delta = (sum(self._delta_s) / len(self._delta_s)
                           if self._delta_s else abs(delta))

        bar = PocBar(price=price, high=high, low=low, open=open_,
                     close=close, delta=delta, volume=volume,
                     price_move=price_move, absorption=absorption,
                     ask_vol=ask_vol, bid_vol=bid_vol)
        self._bars.append(bar)

        if len(self._bars) < 3:
            return PocAcceptanceResult(reason="Calentando buffer")

        bars = list(self._bars)

        # ── 1. POC POSITION ANALYSIS ──────────────────────────────
        poc_pos, poc_state = self._analyze_poc_position(bars, price)

        # ── 2. AGGRESSION WITHOUT RESULT ─────────────────────────
        absorb_score, agg_eff, agg_no_result = self._aggression_without_result(bars)

        # ── 3. TRAPPED AGGRESSORS ─────────────────────────────────
        trapped_buy, trapped_sell = self._trapped_aggressors(bars)
        trap_active = (trapped_buy >= 65 or trapped_sell >= 65)

        # ── 4. ACCEPTANCE / AUCTION FAILURE ──────────────────────
        accept_confirmed, auction_fail, exhaust = self._acceptance_auction(
            bars, poc_state, poc_pos
        )

        # ── 5. TRAP PROBABILITY ───────────────────────────────────
        trap_prob = self._trap_probability(
            bars, absorb_score, agg_no_result, poc_state
        )

        # ── SCORE COMPUESTO ───────────────────────────────────────
        # Base: 50 (neutral)
        poc_score = 50

        # Aceptación confirmada = favorable
        if accept_confirmed and poc_state in ("ACCEPTED_HIGH", "ACCEPTED_LOW"):
            poc_score += 20
        elif poc_state in ("REJECTED_HIGH", "REJECTED_LOW"):
            poc_score -= 20

        # Absorción alta = warning
        if absorb_score >= 70:
            poc_score -= 18
        elif absorb_score >= 50:
            poc_score -= 8

        # Agresión ineficiente = warning
        if agg_no_result:
            poc_score -= 15
        elif agg_eff > 0.65:
            poc_score += 8

        # Atrapados = warning fuerte
        if trap_active:
            poc_score -= 20
        elif max(trapped_buy, trapped_sell) >= 40:
            poc_score -= 10

        # Auction failure
        if auction_fail:
            poc_score -= 15

        # Exhaustion
        if exhaust:
            poc_score -= 12

        poc_score = max(0, min(poc_score, 100))

        # Auction failure score independiente (para confluence)
        auction_fail_score = 0
        if auction_fail:  auction_fail_score += 50
        if exhaust:       auction_fail_score += 25
        if absorb_score >= 70: auction_fail_score += 25
        auction_fail_score = min(auction_fail_score, 100)

        # Razón
        parts = []
        if poc_state != "NEUTRAL":  parts.append(f"poc={poc_state}")
        if absorb_score >= 50:      parts.append(f"absorb={absorb_score}")
        if agg_no_result:           parts.append("agg_no_result")
        if trap_active:             parts.append("TRAP")
        if auction_fail:            parts.append("AUCTION_FAIL")
        if exhaust:                 parts.append("EXHAUST")
        reason = " | ".join(parts) if parts else "neutral"

        return PocAcceptanceResult(
            poc_state              = poc_state,
            poc_position           = poc_pos,
            absorption_score       = absorb_score,
            aggression_efficiency  = round(agg_eff, 3),
            aggression_without_result = agg_no_result,
            trapped_buyers_score   = trapped_buy,
            trapped_sellers_score  = trapped_sell,
            trap_active            = trap_active,
            acceptance_confirmed   = accept_confirmed,
            auction_failure        = auction_fail,
            breakout_exhaustion    = exhaust,
            poc_acceptance_score   = poc_score,
            auction_failure_score  = auction_fail_score,
            trap_probability_score = trap_prob,
            reason                 = reason,
        )

    # ──────────────────────────────────────────────────────────────
    #  1. POC POSITION ANALYSIS
    # ──────────────────────────────────────────────────────────────

    def _analyze_poc_position(self, bars: list,
                               price: float) -> tuple:
        """
        Analiza posición del POC relativa al rango reciente.
        POC en tercio superior con precio arriba = ACCEPTED_HIGH.
        POC en tercio superior con precio abajo = posible rechazo.
        """
        if self._poc <= 0:
            return "MID", "NEUTRAL"

        # Rango de los últimos 5 bars
        recent = bars[-min(5, len(bars)):]
        r_high = max(b.high  for b in recent)
        r_low  = min(b.low   for b in recent)
        r_size = r_high - r_low

        if r_size < self._tick * 2:
            return "MID", "NEUTRAL"

        # Posición del POC en el rango
        poc_rel = (self._poc - r_low) / r_size
        if poc_rel >= 0.67:    poc_pos = "TOP_THIRD"
        elif poc_rel <= 0.33:  poc_pos = "BOTTOM_THIRD"
        else:                  poc_pos = "MID"

        curr     = bars[-1]
        bullish  = curr.price_move > self._tick
        bearish  = curr.price_move < -self._tick

        # POC en top + precio sobre POC = aceptación alcista
        if poc_pos == "TOP_THIRD" and price > self._poc - self._tick:
            poc_state = "ACCEPTED_HIGH"
        # POC en bottom + precio bajo POC = aceptación bajista
        elif poc_pos == "BOTTOM_THIRD" and price < self._poc + self._tick:
            poc_state = "ACCEPTED_LOW"
        # Impulso alcista pero POC bajo = absorción sospechosa
        elif bullish and poc_pos == "BOTTOM_THIRD":
            poc_state = "REJECTED_HIGH"   # precio sube pero POC queda abajo
        # Impulso bajista pero POC alto = absorción sospechosa
        elif bearish and poc_pos == "TOP_THIRD":
            poc_state = "REJECTED_LOW"
        else:
            poc_state = "NEUTRAL"

        return poc_pos, poc_state

    # ──────────────────────────────────────────────────────────────
    #  2. AGGRESSION WITHOUT RESULT
    # ──────────────────────────────────────────────────────────────

    def _aggression_without_result(self, bars: list) -> tuple:
        """
        Detecta absorción: delta extremo pero rango pequeño.
        efficiency = candle_range / (abs_delta / avg_delta)
        Baja efficiency = institución absorbiendo agresión.
        """
        if len(bars) < 2:
            return 0, 0.5, False

        curr        = bars[-1]
        candle_range= curr.high - curr.low
        abs_delta   = abs(curr.delta)

        if abs_delta < 50 or candle_range < self._tick:
            return 0, 0.5, False

        # Efficiency: cuánto se movió por unidad de delta
        delta_norm  = abs_delta / max(self._avg_delta, 1)
        efficiency  = candle_range / (delta_norm * self._tick * 4) if delta_norm > 0 else 0.5
        efficiency  = min(efficiency, 1.0)

        # Absorción: volumen alto + rango pequeño + delta extremo
        absorb_score = 0
        vol_ratio    = curr.volume / self._avg_vol if self._avg_vol > 0 else 1.0

        if vol_ratio >= 1.5 and candle_range < self._tick * 4 and abs_delta > 200:
            absorb_score += 40
        if curr.absorption:
            absorb_score += 30
        if efficiency < 0.25:
            absorb_score += 20
        if abs_delta > self._avg_delta * 2 and candle_range < self._tick * 3:
            absorb_score += 10

        absorb_score = min(absorb_score, 100)

        # Flag: agresión sin resultado
        agg_no_result = (absorb_score >= 60 and efficiency < 0.30)

        return absorb_score, efficiency, agg_no_result

    # ──────────────────────────────────────────────────────────────
    #  3. TRAPPED AGGRESSORS
    # ──────────────────────────────────────────────────────────────

    def _trapped_aggressors(self, bars: list) -> tuple:
        """
        Detecta compradores/vendedores atrapados.

        Trapped buyers: delta comprador fuerte + rechazo del high
                        + siguiente vela falla continuación
        Trapped sellers: delta vendedor fuerte + rechazo del low
                         + siguiente vela falla continuación
        """
        if len(bars) < 3:
            return 0, 0

        trapped_buy  = 0
        trapped_sell = 0

        # Analizar últimas 3 barras
        for i in range(max(0, len(bars)-3), len(bars)-1):
            curr = bars[i]
            nxt  = bars[i+1]

            # Trapped buyers: compra fuerte → precio regresa
            if (curr.delta > 150 and
                    curr.close < curr.high - self._tick * 2 and
                    nxt.price_move < 0):
                trapped_buy += 35

            # Compra + wick superior grande = rechazo del high
            wick_up = curr.high - max(curr.open, curr.close)
            if wick_up > (curr.high - curr.low) * 0.5 and curr.delta > 100:
                trapped_buy += 25

            # Trapped sellers: venta fuerte → precio regresa
            if (curr.delta < -150 and
                    curr.close > curr.low + self._tick * 2 and
                    nxt.price_move > 0):
                trapped_sell += 35

            # Venta + wick inferior grande = rechazo del low
            wick_dn = min(curr.open, curr.close) - curr.low
            if wick_dn > (curr.high - curr.low) * 0.5 and curr.delta < -100:
                trapped_sell += 25

        # POC en zona incorrecta = señal adicional de trampa
        if self._poc > 0 and len(bars) >= 2:
            last_price = bars[-1].price
            if trapped_buy > 0 and last_price < self._poc - self._tick:
                trapped_buy += 20   # precio cayó bajo POC = compradores atrapados
            if trapped_sell > 0 and last_price > self._poc + self._tick:
                trapped_sell += 20  # precio subió sobre POC = vendedores atrapados

        return min(trapped_buy, 100), min(trapped_sell, 100)

    # ──────────────────────────────────────────────────────────────
    #  4. ACCEPTANCE / AUCTION FAILURE
    # ──────────────────────────────────────────────────────────────

    def _acceptance_auction(self, bars: list,
                             poc_state: str,
                             poc_pos: str) -> tuple:
        """
        Detecta si hay aceptación real o si la subasta falló.

        Acceptance: precio permanece del mismo lado del POC
                    en 2+ velas consecutivas.
        Auction failure: precio empuja en una dirección pero
                         vuelve al rango — failed auction.
        Exhaustion: movimiento grande seguido de velas muy pequeñas.
        """
        if len(bars) < 3:
            return False, False, False

        recent = bars[-3:]
        prices = [b.price for b in recent]
        moves  = [b.price_move for b in recent]

        # Acceptance: misma dirección 2+ velas sobre/bajo POC
        accept_confirmed = False
        if self._poc > 0:
            above = sum(1 for b in recent if b.price > self._poc + self._tick)
            below = sum(1 for b in recent if b.price < self._poc - self._tick)
            if above >= 2 or below >= 2:
                accept_confirmed = True

        # Auction failure: impulso seguido de reversión al punto de inicio
        auction_fail = False
        if len(moves) >= 3:
            # Movimiento inicial fuerte + reversión total
            if (abs(moves[0]) > self._tick * 4 and
                    moves[-1] * moves[0] < 0 and
                    abs(prices[-1] - prices[0]) < self._tick * 2):
                auction_fail = True
            # Delta comprador fuerte + POC no sube = failed auction alcista
            if (bars[-2].delta > 200 and
                    poc_state == "REJECTED_HIGH"):
                auction_fail = True

        # Breakout exhaustion: vela grande + 2 velas muy pequeñas
        exhaust = False
        if len(bars) >= 3:
            b0, b1, b2 = bars[-3], bars[-2], bars[-1]
            r0 = b0.high - b0.low
            r1 = b1.high - b1.low
            r2 = b2.high - b2.low
            if (r0 > self._tick * 6 and
                    r1 < self._tick * 3 and
                    r2 < self._tick * 3 and
                    abs(b0.delta) > 200):
                exhaust = True

        return accept_confirmed, auction_fail, exhaust

    # ──────────────────────────────────────────────────────────────
    #  5. TRAP PROBABILITY
    # ──────────────────────────────────────────────────────────────

    def _trap_probability(self, bars: list,
                           absorb_score: int,
                           agg_no_result: bool,
                           poc_state: str) -> int:
        """Score combinado de probabilidad de trampa 0-100."""
        score = 0
        if absorb_score >= 60:      score += 30
        if agg_no_result:           score += 25
        if poc_state in ("REJECTED_HIGH", "REJECTED_LOW"):
            score += 25
        # Velas con wick ratio alto en últimas 3
        if len(bars) >= 3:
            wicks = 0
            for b in bars[-3:]:
                candle_r = b.high - b.low
                body     = abs(b.close - b.open)
                if candle_r > self._tick * 3 and body < candle_r * 0.30:
                    wicks += 1
            score += wicks * 10
        return min(score, 100)