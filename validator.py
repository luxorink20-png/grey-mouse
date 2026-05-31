# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — validator.py
#  Institutional Signal Validation Layer v9.1
#
#  CAMBIOS v9.1 vs v9.0:
#  ─ Penalización struct_opposed ahora es DINÁMICA por régimen
#  ─ TREND_DAY:      struct_opposed = -15 (señal real, penalizar fuerte)
#  ─ ROTATIONAL /
#    BALANCED_DAY /
#    COMPRESSION /
#    LOW_VOL:        struct_opposed = -6  (oposición = noise probable)
#  ─ DEFAULT resto:  struct_opposed = -10
#  ─ struct_aligned BONUS: +8 en TREND_DAY, +5 resto
#  ─ NUEVO filtro: TREND_DAY_NO_STRUCT ahora respeta régimen antes
#    de penalizar (evita doble penalización con struct_opposed)
#  ─ Score breakdown incluye struct_penalty para trazabilidad
#
#  PROBLEMA RESUELTO:
#  struct_opposed en v9.0 no existía como penalización explícita —
#  el filtro TREND_DAY_NO_STRUCT rechazaba cuando structure_bias
#  era NEUTRAL, pero no diferenciaba NEUTRAL de OPPOSED.
#  Ahora se penaliza dinámicamente según régimen en lugar de
#  rechazar ciegamente, preservando edge en trend days.
#
#  SIN CAMBIOS:
#  ─ TOXIC_REGIME gate
#  ─ TOXIC_ENV gate
#  ─ BFR filter
#  ─ FAKE_BREAKOUT gate
#  ─ WEAK / MODERATE filters
#  ─ CONTINUATION gate
#  ─ TRAP detection
#  ─ EXPANSION check
#  ─ MIN_SCORE_TO_TRADE = 45
# ╚══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass, field
from collections import deque
from typing import Optional


@dataclass
class ValidationResult:
    validated:       bool
    adjusted_score:  int
    original_score:  int
    filters_passed:  list
    filters_failed:  list
    reason:          str
    warning:         str  = ""
    score_breakdown: dict = field(default_factory=dict)

    def __str__(self):
        s = "VALIDATED" if self.validated else "REJECTED"
        return (s + " score=" + str(self.adjusted_score) +
                " passed=" + str(len(self.filters_passed)) +
                "/4 reason=" + self.reason)


@dataclass
class PriceBar:
    price: float; high: float; low: float; delta: float; volume: float


class PriceBuffer:

    def __init__(self, size: int = 20):
        self._bars: deque = deque(maxlen=size)

    def push(self, bar: PriceBar):
        self._bars.append(bar)

    def last(self, n: int = 5) -> list:
        d = list(self._bars)
        return d[-n:] if len(d) >= n else d

    def recent_highs(self, n: int = 5) -> list:
        return [b.high for b in self.last(n)]

    def recent_lows(self, n: int = 5) -> list:
        return [b.low for b in self.last(n)]

    def recent_prices(self, n: int = 5) -> list:
        return [b.price for b in self.last(n)]

    def has_enough(self, n: int = 3) -> bool:
        return len(self._bars) >= n


def adjust_gamma(price, call_wall, put_wall, tick=0.25):
    if call_wall is None or put_wall is None:
        return 0, "GAMMA: sin niveles"
    if put_wall >= call_wall:
        return 0, "GAMMA: config inválida"
    if put_wall < price < call_wall:
        return 15, "GAMMA: dentro de muros GEX"
    if abs(price - call_wall) <= tick*4 or abs(price - put_wall) <= tick*4:
        return 5, "GAMMA: cerca de muro"
    return 0, "GAMMA: libre"


def adjust_expansion(event_result, buffer, tick=0.25):
    if not buffer.has_enough(3):
        return 0, "EXPANSION: calentando"
    ctx   = event_result.get("context", {})
    delta = abs(ctx.get("delta",  0))
    vol   = ctx.get("volume", 0)
    event = event_result.get("event", "NONE")
    if "ACUMULACI" in event or event in ("INIT", "NONE"):
        return 0, "EXPANSION: contexto"
    recent = buffer.recent_prices(3)
    if len(recent) < 2:
        return 0, "EXPANSION: insuficiente"
    disp = abs(recent[-1] - recent[0])
    pen = 0; issues = []
    if delta < 30:    pen += 5;  issues.append(f"delta bajo ({int(delta)})")
    if disp < tick*2: pen += 5;  issues.append(f"disp bajo ({round(disp,2)}pts)")
    if vol < 100:     pen += 3;  issues.append(f"vol bajo ({int(vol)})")
    return pen, ("EXPANSION débil: " + " | ".join(issues)) if issues else "EXPANSION: OK"


def adjust_liquidity(price, bias, buffer, min_ticks=4, tick=0.25):
    if not buffer.has_enough(3):
        return 0, "LIQUIDITY: calentando"
    min_dist = tick * min_ticks
    if bias == "BULLISH":
        lows = buffer.recent_lows(5)
        if not lows: return 0, "LIQUIDITY: sin lows"
        dist = price - min(lows)
        if dist < min_dist: return 7, f"LIQUIDITY: low cercano ({round(dist,2)}pts)"
        return 0, f"LIQUIDITY: libre ({round(dist,2)}pts)"
    if bias == "BEARISH":
        highs = buffer.recent_highs(5)
        if not highs: return 0, "LIQUIDITY: sin highs"
        dist = max(highs) - price
        if dist < min_dist: return 7, f"LIQUIDITY: high cercano ({round(dist,2)}pts)"
        return 0, f"LIQUIDITY: libre ({round(dist,2)}pts)"
    return 0, "LIQUIDITY: neutral"


def check_trap(event_result, buffer, tick=0.25):
    if not buffer.has_enough(4):
        return False, 0, "TRAP: calentando"
    ctx    = event_result.get("context", {})
    delta  = ctx.get("delta", 0)
    recent = buffer.last(4)
    if len(recent) < 4:
        return False, 0, "TRAP: insuficiente"
    prices = [b.price for b in recent]
    highs  = [b.high  for b in recent]
    lows   = [b.low   for b in recent]
    if len(prices) >= 3:
        prev_high  = max(highs[-3:-1])
        prev_low   = min(lows[-3:-1])
        curr_price = prices[-1]
        prev_range = prev_high - prev_low
        if prev_range > tick * 10:
            mid = (prev_high + prev_low) / 2
            if highs[-2] == prev_high and curr_price < mid and delta < -100:
                return True, 0, "TRAP HARD: bearish spike reversal"
            if lows[-2] == prev_low and curr_price > mid and delta > 100:
                return True, 0, "TRAP HARD: bullish spike reversal"
    if delta > 350 and len(prices) >= 2:
        if prices[-1] < prices[-2] - tick*4:
            return False, 10, "TRAP soft: delta buy, precio bajó"
    if delta < -350 and len(prices) >= 2:
        if prices[-1] > prices[-2] + tick*4:
            return False, 10, "TRAP soft: delta sell, precio subió"
    return False, 0, "TRAP: limpio"


# ══════════════════════════════════════════════════════════════════
#  HELPERS PARA REGÍMENES Y ENTORNOS
# ══════════════════════════════════════════════════════════════════

TOXIC_REGIMES     = {"LIQUIDATION", "HIGH_VOL_DAY"}
TOXIC_ENVS        = {"TRAPPY", "CHOPPY", "LIQUIDATION", "DEAD_MARKET"}
PREMIUM_BREAKOUTS = {"REAL", "EXPLOSIVE"}

# ── v9.1 — Clasificación de regímenes para struct_opposed ─────────
TREND_REGIMES      = {"TREND_DAY", "STRONG_TREND", "MOMENTUM",
                      "EXPANSION_DAY", "SHORT_COVERING"}
ROTATIONAL_REGIMES = {"ROTATIONAL", "BALANCED_DAY", "COMPRESSION",
                      "LOW_VOL", "RANGE_DAY"}

# Penalizaciones dinámicas struct_opposed por régimen
_STRUCT_OPPOSED_PENALTY = {
    "TREND":      -15,   # Oposición en trend = señal real, penalizar fuerte
    "ROTATIONAL": -6,    # Oposición en rotational = noise probable
    "DEFAULT":    -10,   # Resto de regímenes
}

# Bonus struct_aligned por régimen
_STRUCT_ALIGNED_BONUS = {
    "TREND":   +8,
    "DEFAULT": +5,
}
# ─────────────────────────────────────────────────────────────────


def _is_premium_exception(confirmation, continuation) -> bool:
    """
    EXCEPCIÓN a bloqueos tóxicos:
    REAL/EXPLOSIVE + conf>=90 + cont_prob>=90 siempre pasan.
    """
    if confirmation is None:
        return False
    bq_type  = getattr(confirmation, "breakout_type",     "NONE")
    conf_sc  = getattr(confirmation, "confirmation_score", 0)
    if bq_type not in PREMIUM_BREAKOUTS:
        return False
    if conf_sc < 90:
        return False
    cont_prob = 0
    if continuation is not None:
        cont_prob = getattr(continuation, "continuation_probability", 0)
    return cont_prob >= 90


def _get_regime_class(regime_nm: str) -> str:
    """
    Clasifica el régimen en TREND / ROTATIONAL / DEFAULT
    para aplicar penalizaciones dinámicas.
    """
    if regime_nm in TREND_REGIMES:
        return "TREND"
    if regime_nm in ROTATIONAL_REGIMES:
        return "ROTATIONAL"
    return "DEFAULT"


def _calc_struct_adjustment(struct_bias: str, confluence_bias: str,
                             regime_nm: str) -> tuple[int, str]:
    """
    v9.1 — Calcula penalización/bonus de structure_bias de forma dinámica.

    Retorna (delta_score, descripción)

    Lógica:
    - struct_bias NEUTRAL       → sin ajuste
    - struct_bias ALINEADO      → bonus según régimen
    - struct_bias OPUESTO       → penalización según régimen
    """
    if struct_bias == "NEUTRAL":
        return 0, "STRUCT: neutral — sin ajuste"

    reg_class = _get_regime_class(regime_nm)

    # Determinar si está alineado u opuesto con el bias de confluencia
    aligned = (
        (struct_bias == "BULLISH" and confluence_bias == "BULLISH") or
        (struct_bias == "BEARISH" and confluence_bias == "BEARISH")
    )
    opposed = (
        (struct_bias == "BULLISH" and confluence_bias == "BEARISH") or
        (struct_bias == "BEARISH" and confluence_bias == "BULLISH")
    )

    if aligned:
        bonus = _STRUCT_ALIGNED_BONUS.get(reg_class,
                _STRUCT_ALIGNED_BONUS["DEFAULT"])
        return bonus, f"STRUCT: alineado ({struct_bias}) régimen={reg_class} bonus={bonus:+d}"

    if opposed:
        penalty = _STRUCT_OPPOSED_PENALTY.get(reg_class,
                  _STRUCT_OPPOSED_PENALTY["DEFAULT"])
        return penalty, (f"STRUCT: opuesto ({struct_bias} vs confluence={confluence_bias}) "
                         f"régimen={reg_class} penalty={penalty:+d}")

    return 0, "STRUCT: sin conflicto directo"


# ══════════════════════════════════════════════════════════════════
#  VALIDATOR v9.1
# ══════════════════════════════════════════════════════════════════

class Validator:
    """
    Validador institucional v9.1

    NUEVO v9.1:
    - Penalización struct_opposed dinámica por régimen
      · TREND_DAY / STRONG_TREND / MOMENTUM → -15
      · ROTATIONAL / BALANCED / COMPRESSION / LOW_VOL → -6
      · DEFAULT → -10
    - Bonus struct_aligned por régimen
      · TREND_DAY → +8
      · DEFAULT → +5
    - Score breakdown incluye struct_adjustment para trazabilidad
    - Filtro TREND_DAY_NO_STRUCT mejorado: no rechaza si struct
      solo es NEUTRAL (solo rechaza si cont_p < 75 Y struct opuesto)

    SIN CAMBIOS vs v9.0:
    - TOXIC_REGIME gate
    - TOXIC_ENV gate
    - BFR filter
    - FAKE_BREAKOUT gate
    - WEAK / MODERATE filters
    - CONTINUATION gate
    - TRAP detection
    - EXPANSION check
    - MIN_SCORE_TO_TRADE = 45
    """

    MIN_SCORE_TO_TRADE = 45
    MIN_BASE_SCORE     = 25

    def __init__(self, tick=0.25, call_wall=None, put_wall=None,
                 min_liq_ticks=4, buffer_size=20):
        self._tick          = tick
        self._call_wall     = call_wall
        self._put_wall      = put_wall
        self._min_liq_ticks = min_liq_ticks
        self._buffer        = PriceBuffer(size=buffer_size)
        self._total         = 0
        self._validated     = 0
        self._rejected      = 0

    def validate(self, confluence, event_result: dict,
                 raw_data: dict,
                 confirmation=None,
                 session_regime=None,
                 continuation=None,
                 adaptive_continuation=None,
                 market_env=None,
                 poc_acceptance=None) -> ValidationResult:

        self._total += 1

        price  = float(raw_data.get("price", 0))
        high   = float(raw_data.get("high",  price))
        low    = float(raw_data.get("low",   price))
        delta  = event_result.get("context", {}).get("delta",  0)
        volume = event_result.get("context", {}).get("volume", 0)
        bias   = confluence.bias
        score  = confluence.score
        zone   = getattr(confluence, "zone", "UNKNOWN")

        self._buffer.push(PriceBar(price=price, high=high, low=low,
                                   delta=delta, volume=volume))

        breakdown = {"base_score": score, "penalties": {}, "bonuses": {}}
        penalty   = 0
        bonus     = 0
        passed    = []
        failed    = []
        warnings  = []

        # ── PRE-CHECK ─────────────────────────────────────────────
        if score < self.MIN_BASE_SCORE:
            self._rejected += 1
            return ValidationResult(
                validated=False, adjusted_score=score,
                original_score=score, filters_passed=[],
                filters_failed=["PRE_CHECK"],
                reason=f"Score base muy bajo ({score})",
                score_breakdown=breakdown,
            )

        # ── GATE 1: RÉGIMEN TÓXICO ────────────────────────────────
        regime_nm = ""
        if session_regime is not None:
            regime_nm = getattr(session_regime, "session_regime", "")

        if regime_nm in TOXIC_REGIMES:
            if _is_premium_exception(confirmation, continuation):
                passed.append("TOXIC_REGIME_EXCEPTION")
                warnings.append(f"Régimen tóxico {regime_nm} — excepción PREMIUM")
            else:
                self._rejected += 1
                reason = (f"Validator: {'liquidation regime' if regime_nm == 'LIQUIDATION' else 'high vol weak setup'} "
                          f"({regime_nm})")
                return ValidationResult(
                    validated=False, adjusted_score=0,
                    original_score=score, filters_passed=[],
                    filters_failed=["TOXIC_REGIME"],
                    reason=reason,
                    score_breakdown=breakdown,
                )

        # ── GATE 2: ENTORNO TÓXICO ────────────────────────────────
        if market_env is not None:
            env_name  = getattr(market_env, "environment",            "ROTATIONAL")
            tradeable = getattr(market_env, "tradeable",              True)
            trap_d    = getattr(market_env, "trap_density",           0)
            bfr       = getattr(market_env, "breakout_failure_rate",  0)
            dir_eff   = getattr(market_env, "directional_efficiency", 50)
            danger    = getattr(market_env, "danger_level",           0)

            env_toxic = (env_name in TOXIC_ENVS or not tradeable)

            if env_toxic:
                if _is_premium_exception(confirmation, continuation):
                    passed.append("TOXIC_ENV_EXCEPTION")
                    warnings.append(f"Entorno tóxico {env_name} — excepción PREMIUM")
                else:
                    self._rejected += 1
                    return ValidationResult(
                        validated=False, adjusted_score=0,
                        original_score=score, filters_passed=[],
                        filters_failed=["TOXIC_ENV"],
                        reason=f"Validator: entorno tóxico — {env_name} "
                               f"danger={danger} dir_eff={dir_eff}",
                        score_breakdown=breakdown,
                    )

            # Sub-filtro: breakout_failure_rate alto bloquea WEAK/MODERATE
            if bfr > 55 or trap_d > 60:
                bq_type = getattr(confirmation, "breakout_type", "NONE") if confirmation else "NONE"
                if bq_type not in PREMIUM_BREAKOUTS:
                    self._rejected += 1
                    return ValidationResult(
                        validated=False, adjusted_score=0,
                        original_score=score, filters_passed=[],
                        filters_failed=["BFR_ENV"],
                        reason=(f"Validator: breakout failure environment — "
                                f"bfr={bfr}% trap={trap_d} bq={bq_type}"),
                        score_breakdown=breakdown,
                    )
                else:
                    warnings.append(f"BFR alto ({bfr}%) pero PREMIUM breakout pasa")

            passed.append("MARKET_ENV")

        # ── GATE 3: CONFIRMATION ──────────────────────────────────
        if confirmation is not None:
            bq_type   = getattr(confirmation, "breakout_type",     "NONE")
            acc_type  = getattr(confirmation, "acceptance_type",   "NONE")
            conf_sc   = getattr(confirmation, "confirmation_score", 50)
            # ── v9.1: leer structure_bias del confirmation engine ─
            _struct   = getattr(confirmation, "structure_bias",    "NEUTRAL")

            # FAKE → siempre rechazar
            if bq_type == "FAKE":
                self._rejected += 1
                return ValidationResult(
                    validated=False, adjusted_score=0,
                    original_score=score, filters_passed=[],
                    filters_failed=["FAKE_BREAKOUT"],
                    reason="FAKE breakout — rechazado",
                    score_breakdown=breakdown,
                )

            # ── v9.1 — STRUCTURE ADJUSTMENT DINÁMICO ─────────────
            # Reemplaza la lógica estática de struct_opposed/-15
            struct_adj, struct_desc = _calc_struct_adjustment(
                struct_bias=_struct,
                confluence_bias=bias,
                regime_nm=regime_nm,
            )
            if struct_adj < 0:
                penalty += abs(struct_adj)
                breakdown["penalties"]["struct_opposed"] = struct_adj
                failed.append("STRUCT_OPPOSED")
                warnings.append(struct_desc)
            elif struct_adj > 0:
                bonus += struct_adj
                breakdown["bonuses"]["struct_aligned"] = struct_adj
                passed.append("STRUCT_ALIGNED")
                warnings.append(struct_desc)
            else:
                passed.append("STRUCT_NEUTRAL")
            # ─────────────────────────────────────────────────────

            # ── EDGE CONCENTRATION v4.2 ──────────────────────────
            is_range = (session_regime is not None and
                        hasattr(session_regime, "is_range_regime") and
                        session_regime.is_range_regime())

            _trap_d   = getattr(market_env, "trap_density",         0) if market_env else 0
            _bfr      = getattr(market_env, "breakout_failure_rate", 0) if market_env else 0
            _cont_src = adaptive_continuation if adaptive_continuation is not None else continuation
            _cont_p   = getattr(_cont_src, "continuation_probability", 0) if _cont_src else 0

            # PATCH 4: TREND_DAY clean edge filter
            # v9.1: solo rechaza si struct OPUESTO Y cont_p bajo
            # (no rechaza por NEUTRAL solo — la penalización dinámica ya lo maneja)
            if regime_nm == "TREND_DAY":
                if _trap_d > 65 or _bfr > 60:
                    if bq_type not in PREMIUM_BREAKOUTS:
                        self._rejected += 1
                        return ValidationResult(
                            validated=False, adjusted_score=0,
                            original_score=score, filters_passed=[],
                            filters_failed=["TREND_DAY_NOISE"],
                            reason=f"EDGE CONCENTRATION: TREND_DAY noise filtered "
                                   f"(trap={_trap_d} bfr={_bfr} bq={bq_type})",
                            score_breakdown=breakdown,
                        )

                # v9.1: rechazar solo si struct OPUESTO + cont bajo
                # Si struct es NEUTRAL, la penalización dinámica ya aplicó
                struct_opposed = (
                    (_struct == "BULLISH" and bias == "BEARISH") or
                    (_struct == "BEARISH" and bias == "BULLISH")
                )
                if struct_opposed and bq_type not in PREMIUM_BREAKOUTS:
                    if _cont_p < 75:
                        self._rejected += 1
                        return ValidationResult(
                            validated=False, adjusted_score=0,
                            original_score=score, filters_passed=[],
                            filters_failed=["TREND_DAY_STRUCT_OPPOSED"],
                            reason=f"EDGE CONCENTRATION: TREND_DAY struct opuesto + cont bajo "
                                   f"(struct={_struct} bias={bias} cont={_cont_p})",
                            score_breakdown=breakdown,
                        )

            # PATCH 2: WEAK contribution limiter
            if bq_type == "WEAK":
                if regime_nm == "TREND_DAY" and _trap_d > 70 and _bfr > 65:
                    self._rejected += 1
                    return ValidationResult(
                        validated=False, adjusted_score=0,
                        original_score=score, filters_passed=[],
                        filters_failed=["REGIME_BALANCE_WEAK"],
                        reason=f"REGIME BALANCE: WEAK blocked in toxic trend structure "
                               f"(trap={_trap_d} bfr={_bfr})",
                        score_breakdown=breakdown,
                    )

                if regime_nm == "TREND_DAY":
                    _poc_acc       = getattr(poc_acceptance, "acceptance_confirmed", False) if poc_acceptance else False
                    cont_threshold = 72 if _poc_acc else 78
                    conf_threshold = 65 if _poc_acc else 70
                    if _cont_p < cont_threshold or conf_sc < conf_threshold:
                        self._rejected += 1
                        return ValidationResult(
                            validated=False, adjusted_score=0,
                            original_score=score, filters_passed=[],
                            filters_failed=["WEAK_TREND_QUALITY"],
                            reason=f"EDGE CONCENTRATION: WEAK contribution capped "
                                   f"(cont={_cont_p} conf={conf_sc} poc_acc={_poc_acc})",
                            score_breakdown=breakdown,
                        )

                if is_range and _trap_d > 65:
                    self._rejected += 1
                    return ValidationResult(
                        validated=False, adjusted_score=0,
                        original_score=score, filters_passed=[],
                        filters_failed=["WEAK_RANGE_TOXIC"],
                        reason=f"WEAK en RANGE toxico (trap={_trap_d})",
                        score_breakdown=breakdown,
                    )

                if regime_nm in ("TREND_DAY", "BALANCED_DAY", "SHORT_COVERING"):
                    warnings.append("EDGE CONCENTRATION: WEAK allowed in contextual edge zone")

            # PATCH 2: MODERATE controlled
            if bq_type == "MODERATE":
                if conf_sc < 65 or regime_nm == "LIQUIDATION":
                    self._rejected += 1
                    return ValidationResult(
                        validated=False, adjusted_score=0,
                        original_score=score, filters_passed=[],
                        filters_failed=["MODERATE_LOW_QUALITY"],
                        reason=f"MODERATE rechazado: conf={conf_sc} regime={regime_nm}",
                        score_breakdown=breakdown,
                    )
                if conf_sc >= 75 and _struct != "NEUTRAL":
                    warnings.append("REGIME BALANCE: MODERATE accepted as secondary edge")

            # RECLAIM fuerte
            if acc_type == "RECLAIM":
                penalty += 20
                warnings.append("Reclaim detectado")
                breakdown["penalties"]["reclaim"] = -20
                failed.append("RECLAIM")

            # Confirmation score bajo
            if conf_sc < 35:
                pen = int((35 - conf_sc) * 0.4)
                penalty += pen
                warnings.append(f"Confirmación débil ({conf_sc})")
                breakdown["penalties"]["low_conf"] = -pen
                failed.append("CONF_WEAK")
            else:
                passed.append("CONFIRMATION")

            # REAL/EXPLOSIVE: saltarse gamma y liquidity
            skip_filters = bq_type in PREMIUM_BREAKOUTS
            if skip_filters:
                passed.extend(["GAMMA_FRICTION", "LIQUIDITY_DIST"])
            else:
                gp, gr = adjust_gamma(price, self._call_wall, self._put_wall, self._tick)
                if gp > 0:
                    penalty += gp; warnings.append(gr)
                    breakdown["penalties"]["gamma"] = -gp; failed.append("GAMMA")
                else:
                    passed.append("GAMMA_FRICTION")
                lp, lr = adjust_liquidity(price, bias, self._buffer,
                                          self._min_liq_ticks, self._tick)
                if lp > 0:
                    penalty += lp; warnings.append(lr)
                    breakdown["penalties"]["liquidity"] = -lp; failed.append("LIQUIDITY")
                else:
                    passed.append("LIQUIDITY_DIST")

        else:
            gp, gr = adjust_gamma(price, self._call_wall, self._put_wall, self._tick)
            if gp > 0:
                penalty += gp; warnings.append(gr)
                breakdown["penalties"]["gamma"] = -gp; failed.append("GAMMA")
            else:
                passed.append("GAMMA_FRICTION")
            lp, lr = adjust_liquidity(price, bias, self._buffer,
                                       self._min_liq_ticks, self._tick)
            if lp > 0:
                penalty += lp; warnings.append(lr)
                breakdown["penalties"]["liquidity"] = -lp; failed.append("LIQUIDITY")
            else:
                passed.append("LIQUIDITY_DIST")

        # ── GATE 4: CONTINUATION QUALITY ─────────────────────────
        cont_source = adaptive_continuation if adaptive_continuation is not None else continuation
        if cont_source is not None:
            cont_qual = getattr(cont_source, "continuation_quality",    "UNKNOWN")
            cont_prob = getattr(cont_source, "continuation_probability", 50)
            cont_risk = getattr(cont_source, "continuation_risk",        0)

            is_trend_regime = regime_nm in ("TREND_DAY", "EXPANSION_DAY",
                                             "SHORT_COVERING", "LIQUIDATION")
            if cont_qual == "WEAK" and not is_trend_regime:
                self._rejected += 1
                return ValidationResult(
                    validated=False, adjusted_score=0,
                    original_score=score, filters_passed=passed,
                    filters_failed=failed + ["CONT_WEAK"],
                    reason=f"Continuation WEAK en régimen no-trend ({regime_nm})",
                    score_breakdown=breakdown,
                )

            if cont_prob < 70 and cont_qual not in ("STRONG", "MODERATE"):
                penalty += 10
                warnings.append(f"Continuation probability baja ({cont_prob}%)")
                breakdown["penalties"]["low_cont_prob"] = -10
                failed.append("CONT_PROB_LOW")

            if cont_risk > 75:
                penalty += 12
                warnings.append(f"Continuation risk alto ({cont_risk})")
                breakdown["penalties"]["cont_risk"] = -12
                failed.append("CONT_RISK")

            if cont_qual == "STRONG":
                bonus += 8
                breakdown["bonuses"]["cont_strong"] = +8
                passed.append("CONT_STRONG")
            elif cont_prob >= 90:
                bonus += 6
                breakdown["bonuses"]["cont_prob_90"] = +6
            else:
                passed.append("CONTINUATION")

        # ── TRAP — siempre ────────────────────────────────────────
        trap_hard, trap_pen, trap_reason = check_trap(
            event_result, self._buffer, self._tick)
        if trap_hard:
            self._rejected += 1
            return ValidationResult(
                validated=False, adjusted_score=0,
                original_score=score, filters_passed=[],
                filters_failed=["TRAP_DETECTION"],
                reason=trap_reason, score_breakdown=breakdown,
            )
        if trap_pen > 0:
            penalty += trap_pen; warnings.append(trap_reason)
            breakdown["penalties"]["trap_soft"] = -trap_pen; failed.append("TRAP_SOFT")
        else:
            passed.append("TRAP_DETECTION")

        # ── EXPANSION ─────────────────────────────────────────────
        ep, er = adjust_expansion(event_result, self._buffer, self._tick)
        if ep > 0:
            penalty += ep; warnings.append(er)
            breakdown["penalties"]["expansion"] = -ep; failed.append("EXPANSION")
        else:
            passed.append("EXPANSION_CHECK")

        # ── SCORE FINAL ───────────────────────────────────────────
        adjusted = max(0, score - penalty + bonus)
        breakdown.update({"total_penalty": -penalty,
                           "total_bonus":   bonus,
                           "adjusted_score": adjusted})

        if adjusted < self.MIN_SCORE_TO_TRADE:
            self._rejected += 1
            return ValidationResult(
                validated=False, adjusted_score=adjusted,
                original_score=score,
                filters_passed=passed, filters_failed=failed,
                reason=f"Score ajustado {adjusted} < {self.MIN_SCORE_TO_TRADE}",
                warning=" | ".join(warnings),
                score_breakdown=breakdown,
            )

        self._validated += 1
        return ValidationResult(
            validated=True, adjusted_score=adjusted,
            original_score=score,
            filters_passed=passed, filters_failed=failed,
            reason=f"{len(passed)}/4 OK" +
                   (f" | pen:-{penalty}" if penalty else "") +
                   (f" | boost:+{bonus}" if bonus else ""),
            warning=" | ".join(warnings),
            score_breakdown=breakdown,
        )

    def set_gex_walls(self, call_wall, put_wall):
        self._call_wall = call_wall
        self._put_wall  = put_wall

    @property
    def stats(self) -> dict:
        return {
            "total":     self._total,
            "validated": self._validated,
            "rejected":  self._rejected,
            "pass_rate": round(self._validated / self._total * 100, 1)
                         if self._total > 0 else 0.0,
        }
