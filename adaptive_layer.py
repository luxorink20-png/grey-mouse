# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — adaptive_layer.py
#  Real-Time Adaptive Score & Threshold Adjuster v1.1
#
#  v1.1 CAMBIOS:
#  - CONSECUTIVE_LOSS_LIMIT aumentado a 4 (era 3)
#  - SESSION_BIAS_TRADES aumentado a 10 (era 5)
#  - Penalización más gradual: -3pts por loss extra (era -5)
#  - Sesgo de sesión requiere WR < 25% para activarse (era 30%)
#  - Umbral de sesgo requiere mínimo 4 trades (era 2)
#  - MAX_SCORE_PENALTY reducido a 12 (era 15)
#
#  FILOSOFÍA:
#  El sistema debe ser ESTABLE. Una racha de 2-3 losses es normal.
#  Solo penalizar cuando hay evidencia clara y sostenida de problema.
#
#  PIPELINE POSITION:
#  confluence → validator → [ADAPTIVE LAYER] → intent → risk
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque
from dataclasses import dataclass, field
from typing import Optional
import time


# ══════════════════════════════════════════════════════════════════
#  RESULT DATACLASS
# ══════════════════════════════════════════════════════════════════

@dataclass
class AdaptiveResult:
    adjusted_score:    int    = 0
    original_score:    int    = 0
    min_score_dynamic: int    = 45
    session_bias:      str    = "NEUTRAL"
    direction_penalty: str    = "NONE"
    adjustments:       list   = field(default_factory=list)
    reason:            str    = ""

    def __str__(self) -> str:
        return (
            f"score {self.original_score} → {self.adjusted_score} "
            f"| min={self.min_score_dynamic} "
            f"| bias={self.session_bias} "
            f"| penalty={self.direction_penalty}"
        )


# ══════════════════════════════════════════════════════════════════
#  TRADE SNAPSHOT
# ══════════════════════════════════════════════════════════════════

@dataclass
class TradeSnapshot:
    direction: str
    result:    str
    zone:      str
    score:     int
    timestamp: float = field(default_factory=time.time)


# ══════════════════════════════════════════════════════════════════
#  ADAPTIVE LAYER v1.1
# ══════════════════════════════════════════════════════════════════

class AdaptiveLayer:
    """
    Capa adaptativa v1.1 — más conservadora y estable.

    Cambios clave vs v1.0:
    - Necesita 4 losses seguidos para penalizar (era 3)
    - Necesita 10 trades por dirección para sesgo (era 5)
    - Sesgo solo activa con WR < 25% (era 30%)
    - Penalización más gradual (-3 por loss, era -5)
    - Threshold dinámico sube más lento (+2 por batch malo, era +3)
    """

    BASE_MIN_SCORE         = 45
    MAX_MIN_SCORE          = 60    # techo más conservador (era 65)
    MIN_MIN_SCORE          = 38    # piso ligeramente más bajo
    CONSECUTIVE_LOSS_LIMIT = 4     # losses seguidos antes de penalizar (era 3)
    MAX_SCORE_PENALTY      = 12    # penalización máxima (era 15)
    MAX_SCORE_BONUS        = 8     # bonus máximo (era 10)
    RECENT_TRADES_WINDOW   = 15    # ventana de trades recientes (era 10)
    SESSION_BIAS_TRADES    = 10    # trades mínimos por dirección para sesgo (era 5)
    SESSION_BIAS_WR_THRESH = 0.25  # WR máximo para activar sesgo (era 0.30)
    MIN_TRADES_FOR_BIAS    = 4     # mínimo absoluto para considerar sesgo (era 2)

    def __init__(self):
        self._trades:           deque = deque(maxlen=self.RECENT_TRADES_WINDOW)
        self._consecutive_loss: int   = 0
        self._consecutive_win:  int   = 0
        self._session_long_wr:  float = 0.5
        self._session_short_wr: float = 0.5
        self._long_trades:      list  = []
        self._short_trades:     list  = []
        self._current_min_score: int  = self.BASE_MIN_SCORE

    # ──────────────────────────────────────────────────────────────
    #  MAIN ENTRY POINT
    # ──────────────────────────────────────────────────────────────

    def adjust(self,
               confluence,
               validation,
               microstructure,
               level_context) -> AdaptiveResult:

        score          = getattr(confluence,    "score",          0)
        bias           = getattr(confluence,    "bias",           "NEUTRAL")
        zone           = getattr(level_context, "zone",           "UNKNOWN")
        adj_validation = getattr(validation,    "adjusted_score", score)

        base_score  = min(score, adj_validation)
        adjustments = []
        total_adj   = 0

        # ── AJUSTE 1: PERFORMANCE ──────────────────────────────────
        perf_adj = self._performance_adjustment()
        if perf_adj != 0:
            total_adj   += perf_adj
            adjustments.append(
                f"performance={'+'if perf_adj>0 else ''}{perf_adj}"
            )

        # ── AJUSTE 2: SESGO DE SESIÓN ──────────────────────────────
        session_bias, dir_penalty, bias_adj = self._session_bias_adjustment(bias)
        if bias_adj != 0:
            total_adj   += bias_adj
            adjustments.append(
                f"session_bias={'+'if bias_adj>0 else ''}{bias_adj}"
            )

        # ── AJUSTE 3: ZONA ─────────────────────────────────────────
        zone_adj = self._zone_adjustment(zone)
        if zone_adj != 0:
            total_adj   += zone_adj
            adjustments.append(
                f"zone={'+'if zone_adj>0 else ''}{zone_adj}"
            )

        # ── AJUSTE 4: MICROESTRUCTURA ──────────────────────────────
        micro_adj = self._microstructure_adjustment(microstructure)
        if micro_adj != 0:
            total_adj   += micro_adj
            adjustments.append(
                f"microstructure={'+'if micro_adj>0 else ''}{micro_adj}"
            )

        adjusted = base_score + total_adj
        adjusted = max(0, min(adjusted, 100))

        dynamic_min = self._dynamic_threshold()
        reason      = " | ".join(adjustments) if adjustments else "sin ajustes"

        return AdaptiveResult(
            adjusted_score    = adjusted,
            original_score    = base_score,
            min_score_dynamic = dynamic_min,
            session_bias      = session_bias,
            direction_penalty = dir_penalty,
            adjustments       = adjustments,
            reason            = reason,
        )

    # ──────────────────────────────────────────────────────────────
    #  REGISTRAR TRADE
    # ──────────────────────────────────────────────────────────────

    def register_trade(self, direction: str, result: str,
                       zone: str, score: int) -> None:
        snap = TradeSnapshot(
            direction = direction,
            result    = result,
            zone      = zone,
            score     = score,
        )
        self._trades.append(snap)

        if result == "WIN":
            self._consecutive_loss = 0
            self._consecutive_win += 1
        elif result in ("LOSS", "TIMEOUT"):
            self._consecutive_win  = 0
            self._consecutive_loss += 1

        if direction == "LONG":
            self._long_trades.append(result)
            if len(self._long_trades) > self.SESSION_BIAS_TRADES * 2:
                self._long_trades.pop(0)
        elif direction == "SHORT":
            self._short_trades.append(result)
            if len(self._short_trades) > self.SESSION_BIAS_TRADES * 2:
                self._short_trades.pop(0)

        self._session_long_wr  = self._calc_wr(self._long_trades)
        self._session_short_wr = self._calc_wr(self._short_trades)
        self._update_dynamic_threshold()

    # ──────────────────────────────────────────────────────────────
    #  AJUSTES INTERNOS
    # ──────────────────────────────────────────────────────────────

    def _performance_adjustment(self) -> int:
        """
        Penaliza solo después de 4 losses seguidos.
        Penalización gradual: -3 por cada loss adicional (máx -12).
        """
        if self._consecutive_loss >= self.CONSECUTIVE_LOSS_LIMIT:
            extra   = self._consecutive_loss - self.CONSECUTIVE_LOSS_LIMIT + 1
            penalty = min(extra * 3, self.MAX_SCORE_PENALTY)
            return -penalty

        if self._consecutive_win >= 4:
            bonus = min(self._consecutive_win * 2, self.MAX_SCORE_BONUS)
            return bonus

        return 0

    def _session_bias_adjustment(self, current_bias: str) -> tuple:
        """
        Sesgo de sesión — requiere:
        - Mínimo MIN_TRADES_FOR_BIAS trades por dirección
        - WR < SESSION_BIAS_WR_THRESH (25%) para penalizar
        - Mínimo SESSION_BIAS_TRADES trades para confianza alta
        """
        long_n  = len(self._long_trades)
        short_n = len(self._short_trades)

        # No hay datos suficientes — mínimo absoluto
        if long_n < self.MIN_TRADES_FOR_BIAS and short_n < self.MIN_TRADES_FOR_BIAS:
            return "NEUTRAL", "NONE", 0

        long_bad  = (long_n  >= self.MIN_TRADES_FOR_BIAS
                     and self._session_long_wr  < self.SESSION_BIAS_WR_THRESH)
        short_bad = (short_n >= self.MIN_TRADES_FOR_BIAS
                     and self._session_short_wr < self.SESSION_BIAS_WR_THRESH)

        # Penalización más suave si tenemos pocos trades (< SESSION_BIAS_TRADES)
        penalty_full    = -8
        penalty_partial = -5   # pocos datos = penalización menor

        if long_bad and not short_bad:
            session_bias = "SHORT_BIAS"
            if current_bias == "BULLISH":
                penalty = (penalty_full if long_n >= self.SESSION_BIAS_TRADES
                           else penalty_partial)
                return session_bias, "LONG", penalty
            return session_bias, "NONE", 0

        if short_bad and not long_bad:
            session_bias = "LONG_BIAS"
            if current_bias == "BEARISH":
                penalty = (penalty_full if short_n >= self.SESSION_BIAS_TRADES
                           else penalty_partial)
                return session_bias, "SHORT", penalty
            return session_bias, "NONE", 0

        if long_bad and short_bad:
            return "NEUTRAL", "BOTH", -8

        return "NEUTRAL", "NONE", 0

    def _zone_adjustment(self, zone: str) -> int:
        """
        Ajuste por zona — requiere mínimo 4 trades en esa zona.
        """
        if not self._trades:
            return 0

        zone_trades = [t for t in self._trades if t.zone == zone]
        if len(zone_trades) < 4:   # mínimo 4 trades en la zona (era 2)
            return 0

        zone_wr = self._calc_wr([t.result for t in zone_trades])

        if zone_wr >= 0.75:    # umbral más alto para bonus (era 0.70)
            return 4
        if zone_wr <= 0.20:    # umbral más estricto para penalizar (era 0.25)
            return -4
        return 0

    def _microstructure_adjustment(self, microstructure) -> int:
        if microstructure is None:
            return 0

        active    = getattr(microstructure, "active",             False)
        breakout  = getattr(microstructure, "breakout",           None)
        compress  = getattr(microstructure, "compression_active", False)
        confidence= getattr(microstructure, "confidence",         0)

        if not active:
            return 0

        if breakout is not None and confidence >= 70:
            return 8
        if compress and confidence >= 60:
            return 4
        return 2

    def _dynamic_threshold(self) -> int:
        return self._current_min_score

    def _update_dynamic_threshold(self) -> None:
        recent = list(self._trades)
        if len(recent) < 4:   # necesita mínimo 4 trades (era 3)
            self._current_min_score = self.BASE_MIN_SCORE
            return

        recent_wr = self._calc_wr([t.result for t in recent[-8:]])

        if recent_wr < 0.25:   # umbral más estricto para subir (era 0.30)
            # Mal rendimiento sostenido → ser más selectivo
            self._current_min_score = min(
                self._current_min_score + 2,   # sube más lento (era +3)
                self.MAX_MIN_SCORE
            )
        elif recent_wr > 0.65:
            # Buen rendimiento → relajar
            self._current_min_score = max(
                self._current_min_score - 1,
                self.MIN_MIN_SCORE
            )
        else:
            # Normal → volver gradualmente al base
            if self._current_min_score > self.BASE_MIN_SCORE:
                self._current_min_score -= 1
            elif self._current_min_score < self.BASE_MIN_SCORE:
                self._current_min_score += 1

    # ──────────────────────────────────────────────────────────────
    #  HELPERS
    # ──────────────────────────────────────────────────────────────

    @staticmethod
    def _calc_wr(results: list) -> float:
        if not results:
            return 0.5
        wins = sum(1 for r in results if r == "WIN")
        return wins / len(results)

    # ──────────────────────────────────────────────────────────────
    #  STATS PÚBLICOS
    # ──────────────────────────────────────────────────────────────

    @property
    def current_min_score(self) -> int:
        return self._current_min_score

    @property
    def consecutive_losses(self) -> int:
        return self._consecutive_loss

    @property
    def session_long_wr(self) -> float:
        return round(self._session_long_wr, 3)

    @property
    def session_short_wr(self) -> float:
        return round(self._session_short_wr, 3)

    def get_summary(self) -> dict:
        return {
            "min_score_dynamic":  self._current_min_score,
            "consecutive_losses": self._consecutive_loss,
            "consecutive_wins":   self._consecutive_win,
            "session_long_wr":    self.session_long_wr,
            "session_short_wr":   self.session_short_wr,
            "total_trades":       len(self._trades),
        }