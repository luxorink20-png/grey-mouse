# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — risk_engine.py
#  Institutional Risk Structuring Engine v4.0
#
#  CAMBIOS v4.0:
#  ─ MIN_SCORE = 42 (alineado con soft scoring)
#  ─ NARRATIVE UNCLEAR penaliza en lugar de bloquear
#  ─ MIN_RR = 1.5
#  ─ MAX_RISK_PTS = 20
#  ─ STOP_BUFFER = 0.75
#  ─ reason_detail con logging institucional completo
#  ─ Sizing desde score 42 (0.25 contratos)
# ╚══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass, field


@dataclass
class RiskResult:
    approved:       bool
    # Multiplier applied to the base contract size configured in the broker/launcher.
    # Range: 0.25x (minimum) → 2.0x (institutional grade).
    # 0.25 = quarter size, 1.0 = full base size, 2.0 = double base size.
    # The broker/launcher translates this multiplier to actual contracts.
    position_size:  float
    size_unit:      str   = "multiplier"   # always "multiplier" — see docstring above
    stop:           float = 0.0
    target_1:       float = 0.0
    target_2:       float = 0.0
    risk_reward:    float = 0.0
    direction:      str   = "NONE"
    risk_pts:       float = 0.0
    reward_pts:     float = 0.0
    reason:         str   = ""
    reason_detail:  dict  = field(default_factory=dict)

    def __str__(self) -> str:
        status = "APPROVED" if self.approved else "REJECTED"
        return (
            status + " | " + self.direction +
            " | size=" + str(self.position_size) + "x" +
            " | stop=" + str(round(self.stop, 2)) +
            " | T1=" + str(round(self.target_1, 2)) +
            " | R:R=" + str(round(self.risk_reward, 2))
        )

    def entry_log(self) -> str:
        if not self.approved:
            return f"REJECTED: {self.reason}"
        d = self.reason_detail
        return (
            f"TRADE APROBADO — {self.direction}\n"
            f"  Evento    : {d.get('event','?')} | Zona: {d.get('zone','?')}\n"
            f"  Score     : base={d.get('score_base','?')} adj={d.get('score_adj','?')}\n"
            f"  Narrativa : {d.get('narrative','?')} ({d.get('conviction','?')}%)\n"
            f"  Confirmación: {d.get('confirmation','?')}\n"
            f"  Stop={self.stop} | T1={self.target_1} | R:R={self.risk_reward}\n"
            f"  Size={self.position_size}x (multiplier)"
        )


# Sizing calibrado para soft scoring
SIZING_TABLE = [
    (86, 100, 2.0),   # INSTITUTIONAL GRADE
    (70,  85, 1.0),   # HIGH QUALITY
    (55,  69, 0.5),   # MEDIUM
    (42,  54, 0.25),  # LOW — entrada mínima
    (0,   41, 0.0),
]

def get_position_size(score: int) -> float:
    for lo, hi, size in SIZING_TABLE:
        if lo <= score <= hi:
            return size
    return 0.0


class RiskEngine:

    MIN_RR          = 1.5
    STOP_BUFFER_PTS = 0.75
    MIN_RISK_PTS    = 0.5
    MAX_RISK_PTS    = 20.0

    def __init__(self, tick: float = 0.25):
        self._tick = tick

    def analyze(self,
                price:        float,
                confluence,
                validation,
                intent,
                level_context) -> RiskResult:

        # ── GATE 1: validator ─────────────────────────────────────
        if not getattr(validation, "validated", False):
            return self._reject(
                "Validator: " + getattr(validation, "reason", "unknown"), {}
            )

        score      = getattr(confluence,    "score",          0)
        bias       = getattr(confluence,    "bias",           "NEUTRAL")
        adj_score  = getattr(validation,    "adjusted_score", score)
        narrative  = getattr(intent,        "narrative",      "UNCLEAR")
        conviction = getattr(intent,        "conviction",     0)
        event      = getattr(confluence,    "event",          "NONE")
        zone       = getattr(level_context, "zone",           "UNKNOWN")
        nearest_p  = getattr(level_context, "nearest_price",  price)
        nearest_l  = getattr(level_context, "nearest_level",  "")

        effective_score = min(adj_score, score)

        # ── NARRATIVE UNCLEAR — penaliza score ────────────────────
        # No bloquea, pero reduce el score efectivo
        if narrative == "UNCLEAR":
            effective_score = max(0, effective_score - 10)

        # ── GATE 2: sizing ────────────────────────────────────────
        position_size = get_position_size(effective_score)
        if position_size == 0.0:
            return self._reject(
                f"Score insuficiente ({effective_score} < 42)",
                {"event": event, "zone": zone,
                 "score_base": score, "score_adj": effective_score}
            )

        # ── DIRECCIÓN ─────────────────────────────────────────────
        direction = self._resolve_direction(bias, narrative, zone)
        if direction == "NONE":
            return self._reject(
                f"Sin dirección — bias={bias} narrative={narrative} zone={zone}",
                {"event": event, "zone": zone}
            )

        # ── STOP ──────────────────────────────────────────────────
        stop     = self._calculate_stop(price, direction, zone,
                                        nearest_p, nearest_l, level_context)
        if stop <= 0:
            return self._reject("Stop inválido", {"event": event, "zone": zone})

        risk_pts = abs(price - stop)
        if risk_pts < self.MIN_RISK_PTS:
            return self._reject(
                f"Stop demasiado ajustado ({round(risk_pts,2)}pts)",
                {"event": event, "zone": zone}
            )
        if risk_pts > self.MAX_RISK_PTS:
            return self._reject(
                f"Stop demasiado ancho ({round(risk_pts,2)}pts > {self.MAX_RISK_PTS})",
                {"event": event, "zone": zone}
            )

        # ── TARGETS ───────────────────────────────────────────────
        target_1, target_2 = self._calculate_targets(
            price, direction, zone, nearest_p, nearest_l,
            level_context, risk_pts
        )
        if target_1 <= 0:
            return self._reject("Target inválido",
                                {"event": event, "zone": zone})

        reward_pts = abs(target_1 - price)
        if risk_pts == 0:
            return self._reject("Risk = 0", {})

        rr = round(reward_pts / risk_pts, 2)
        if rr < self.MIN_RR:
            return self._reject(
                f"R:R insuficiente ({rr} < {self.MIN_RR})",
                {"event": event, "zone": zone,
                 "risk_pts": risk_pts, "reward_pts": reward_pts}
            )

        # ── CONVICTION BONUS ──────────────────────────────────────
        if conviction >= 75:
            position_size = min(position_size + 0.25, 2.0)

        # ── REASON DETAIL ─────────────────────────────────────────
        reason_detail = {
            "event":        event,
            "zone":         zone,
            "score_base":   score,
            "score_adj":    effective_score,
            "narrative":    narrative,
            "conviction":   conviction,
            "bias":         bias,
            "direction":    direction,
            "confirmation": getattr(confluence, "reason", "")[-60:],
        }

        reason = (
            f"{direction} | {event} | {zone} | "
            f"score={effective_score} | R:R={rr} | "
            f"{narrative}({conviction}%)"
        )

        return RiskResult(
            approved      = True,
            position_size = position_size,
            stop          = round(stop, 2),
            target_1      = round(target_1, 2),
            target_2      = round(target_2, 2) if target_2 > 0 else 0.0,
            risk_reward   = rr,
            direction     = direction,
            risk_pts      = round(risk_pts, 2),
            reward_pts    = round(reward_pts, 2),
            reason        = reason,
            reason_detail = reason_detail,
        )

    # ──────────────────────────────────────────────────────────────
    #  DIRECCIÓN
    # ──────────────────────────────────────────────────────────────

    def _resolve_direction(self, bias, narrative, zone) -> str:
        if narrative == "INDUCTION":
            if zone in ("ABOVE_VAH", "AT_VAH"):   return "SHORT"
            if zone in ("BELOW_VAL", "AT_VAL"):   return "LONG"
        if narrative == "DISTRIBUTION":            return "SHORT"
        if narrative == "ACCUMULATION":            return "LONG"
        if narrative == "REBALANCE":
            if zone in ("ABOVE_VAH", "AT_VAH"):   return "SHORT"
            if zone in ("BELOW_VAL", "AT_VAL"):   return "LONG"
            return "NONE"
        if narrative == "SQUEEZE":
            if bias == "BULLISH":                  return "LONG"
            if bias == "BEARISH":                  return "SHORT"
            if zone in ("AT_VAL",   "BELOW_VAL"): return "LONG"
            if zone in ("AT_VAH",   "ABOVE_VAH"): return "SHORT"
            return "LONG"
        if bias == "BULLISH":                      return "LONG"
        if bias == "BEARISH":                      return "SHORT"
        return "NONE"

    # ──────────────────────────────────────────────────────────────
    #  STOP / TARGETS
    # ──────────────────────────────────────────────────────────────

    def _calculate_stop(self, price, direction, zone,
                        nearest_p, nearest_l, level_context) -> float:
        buf = self.STOP_BUFFER_PTS
        if direction == "LONG":
            if zone in ("AT_VAL", "BELOW_VAL", "IN_VALUE_AREA"):
                if "VAL" in nearest_l:   return nearest_p - buf
                return price - (buf * 4)
            if zone == "AT_VAH":         return price - (buf * 6)
            if zone == "AT_POC":         return price - (buf * 3)
            return nearest_p - buf if nearest_p < price else price - (buf * 4)
        if direction == "SHORT":
            if zone in ("AT_VAH", "ABOVE_VAH", "IN_VALUE_AREA"):
                if "VAH" in nearest_l:   return nearest_p + buf
                return price + (buf * 4)
            if zone == "AT_VAL":         return price + (buf * 6)
            if zone == "AT_POC":         return price + (buf * 3)
            return nearest_p + buf if nearest_p > price else price + (buf * 4)
        return 0.0

    def _calculate_targets(self, price, direction, zone,
                            nearest_p, nearest_l,
                            level_context, risk_pts) -> tuple:
        t1 = 0.0
        t2 = 0.0
        if direction == "LONG":
            if "POC" in nearest_l and nearest_p > price:
                t1 = nearest_p
            elif "VAH" in nearest_l:
                t1 = nearest_p
            else:
                t1 = price + (risk_pts * self.MIN_RR)
            t2 = price + (risk_pts * 3.0)
        if direction == "SHORT":
            if "POC" in nearest_l and nearest_p < price:
                t1 = nearest_p
            elif "VAL" in nearest_l:
                t1 = nearest_p
            else:
                t1 = price - (risk_pts * self.MIN_RR)
            t2 = price - (risk_pts * 3.0)
        return t1, t2

    def _reject(self, reason: str, detail: dict) -> RiskResult:
        return RiskResult(
            approved      = False,
            position_size = 0.0,
            stop          = 0.0,
            target_1      = 0.0,
            target_2      = 0.0,
            risk_reward   = 0.0,
            direction     = "NONE",
            risk_pts      = 0.0,
            reward_pts    = 0.0,
            reason        = reason,
            reason_detail = detail,
        )