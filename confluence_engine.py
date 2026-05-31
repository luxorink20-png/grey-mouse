# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — confluence_engine.py
#  Institutional Confluence Scoring Engine v9.0
#
#  META: wins avg score > losses avg score por ≥ 8-12 puntos
#
#  NUEVA JERARQUÍA v9.0:
#  1. market_environment      (penalización si tóxico)
#  2. session_regime          (contexto macro)
#  3. breakout_type           (calidad del movimiento)
#  4. continuation/adaptive   (inercia post-breakout)
#  5. structure + acceptance  (confirmación)
#  6. edge_learning adjustment (historial)
#  7. VAH/VAL/POC context
#
#  SCORE CAP DINÁMICO:
#  score > 75 NO permitido si:
#  - breakout != REAL/EXPLOSIVE
#  - continuation_prob < 80
#  - environment.tradeable == False
# ╚══════════════════════════════════════════════════════════════════╝

from dataclasses import dataclass, field
from collections import deque
from typing import Optional
import math


SCORE_BANDS = [
    (85, 100, "INSTITUTIONAL GRADE", "ENTER"),
    (68,  84, "HIGH QUALITY",        "WATCH"),
    (48,  67, "MEDIUM QUALITY",      "OBSERVE"),
    (0,   47, "LOW QUALITY",         "IGNORE"),
]

def classify_score(score: int) -> tuple[str, str]:
    for lo, hi, label, action in SCORE_BANDS:
        if lo <= score <= hi:
            return label, action
    return "LOW QUALITY", "IGNORE"


CONFLUENCE_MATRIX = {
    ("INTENTO",     "AT_VAL"):        (80, "BULLISH", "INTENTO soporte VA"),
    ("INTENTO",     "BELOW_VAL"):     (74, "BULLISH", "INTENTO bajo VA"),
    ("INTENTO",     "AT_POC"):        (58, "NEUTRAL", "INTENTO en POC"),
    ("INTENTO",     "IN_VALUE_AREA"): (38, "NEUTRAL", "INTENTO en VA"),
    ("INTENTO",     "AT_VAH"):        (52, "BEARISH", "INTENTO contra VAH"),
    ("INTENTO",     "ABOVE_VAH"):     (68, "BULLISH", "INTENTO extensión"),
    ("FALLO",       "AT_VAH"):        (78, "BEARISH", "FALLO resistencia VAH"),
    ("FALLO",       "ABOVE_VAH"):     (72, "BEARISH", "FALLO extensión"),
    ("FALLO",       "AT_VAL"):        (54, "BULLISH", "FALLO bajista soporte"),
    ("FALLO",       "AT_POC"):        (40, "NEUTRAL", "FALLO balance"),
    ("FALLO",       "IN_VALUE_AREA"): (32, "NEUTRAL", "FALLO fair value"),
    ("FALLO",       "BELOW_VAL"):     (66, "BULLISH", "FALLO bajo VA"),
    ("AGOTAMIENTO", "AT_VAH"):        (86, "BEARISH", "AGOTAMIENTO resistencia"),
    ("AGOTAMIENTO", "ABOVE_VAH"):     (84, "BEARISH", "AGOTAMIENTO extensión"),
    ("AGOTAMIENTO", "AT_VAL"):        (85, "BULLISH", "AGOTAMIENTO soporte"),
    ("AGOTAMIENTO", "BELOW_VAL"):     (82, "BULLISH", "AGOTAMIENTO climax"),
    ("AGOTAMIENTO", "AT_POC"):        (60, "NEUTRAL", "AGOTAMIENTO POC"),
    ("AGOTAMIENTO", "IN_VALUE_AREA"): (44, "NEUTRAL", "AGOTAMIENTO fair value"),
    ("ACUMULACION", "AT_VAH"):        (36, "BEARISH", "ACUMULACION VAH"),
    ("ACUMULACION", "AT_VAL"):        (44, "BULLISH", "ACUMULACION VAL"),
    ("ACUMULACION", "AT_POC"):        (32, "NEUTRAL", "ACUMULACION POC"),
    ("ACUMULACION", "IN_VALUE_AREA"): (20, "NEUTRAL", "ACUMULACION VA"),
    ("ACUMULACION", "ABOVE_VAH"):     (34, "BEARISH", "ACUMULACION sobre VAH"),
    ("ACUMULACION", "BELOW_VAL"):     (40, "BULLISH", "ACUMULACION bajo VAL"),
    ("INIT",        "AT_VAH"):        (14, "NEUTRAL", "Inicializando"),
    ("INIT",        "AT_VAL"):        (14, "NEUTRAL", "Inicializando"),
    ("INIT",        "AT_POC"):        (14, "NEUTRAL", "Inicializando"),
    ("INIT",        "IN_VALUE_AREA"): (5,  "NEUTRAL", "Inicializando"),
    ("INIT",        "ABOVE_VAH"):     (5,  "NEUTRAL", "Inicializando"),
    ("INIT",        "BELOW_VAL"):     (5,  "NEUTRAL", "Inicializando"),
}


@dataclass
class ConfluenceResult:
    event:           str
    zone:            str
    confluence:      str
    bias:            str
    score:           int
    classification:  str
    action:          str
    reason:          str
    hpz_bonus:       bool
    bias_aligned:    bool
    consecutive:     int
    trade_allowed:   bool = True
    trigger_type:    str  = "SCORED"
    score_breakdown: dict = field(default_factory=dict)


class ConfluenceEngine:
    """Confluence Engine v9.0 — score predictivo con environment + edge learning."""

    HPZ_BONUS         = 6
    BIAS_ALIGN_BONUS  = 5
    CONSECUTIVE_BONUS = 2
    MAX_CONSEC_BONUS  = 8

    def __init__(self, history_size: int = 10):
        self._history     = deque(maxlen=history_size)
        self._consecutive = 0
        self._last_class  = ""

    def evaluate(self,
                 event_result:         dict,
                 level_context,
                 confirmation=         None,
                 session_regime=       None,
                 continuation=         None,
                 adaptive_continuation=None,
                 market_env=           None,
                 edge_learning=        None,
                 poc_acceptance=       None) -> ConfluenceResult:

        event      = event_result.get("event",      "NONE")
        confidence = event_result.get("confidence", 0)
        ctx        = event_result.get("context",    {})
        delta      = ctx.get("delta",      0)
        price_move = ctx.get("price_move", 0)
        absorption = ctx.get("absorption", False)
        dead_zone  = ctx.get("dead_zone",  False)

        zone     = level_context.zone
        hpz      = level_context.high_prob_zone
        lvl_bias = level_context.reaction_bias
        tick     = 0.25

        event_key = event
        if "ACUMULACI" in event:
            event_key = "ACUMULACION"

        base_score, conf_bias, matrix_reason = CONFLUENCE_MATRIX.get(
            (event_key, zone),
            (18, "NEUTRAL", event + " en " + zone)
        )

        breakdown = {"event": event, "zone": zone,
                     "score_base": base_score, "adjustments": {}}
        bonus    = 0
        hard_cap = None

        # ══ FACTOR 1: MARKET ENVIRONMENT ═════════════════════════
        if market_env is not None:
            env_name  = getattr(market_env, "environment",             "ROTATIONAL")
            env_conf  = getattr(market_env, "confidence",              50)
            dir_eff   = getattr(market_env, "directional_efficiency",  50)
            trap_d    = getattr(market_env, "trap_density",            0)
            bfr       = getattr(market_env, "breakout_failure_rate",   0)
            rot_f     = getattr(market_env, "rotation_factor",         0)
            tradeable = getattr(market_env, "tradeable",               True)

            if not tradeable:
                bonus -= 30
                breakdown["adjustments"]["env_blocked"] = -30
            elif env_name == "EFFICIENT_TREND":
                bonus += 12
                breakdown["adjustments"]["env_efficient"] = +12
            elif env_name in ("TRAPPY", "CHOPPY"):
                bonus -= 18
                breakdown["adjustments"]["env_trappy"] = -18
            elif env_name == "DEAD_MARKET":
                bonus -= 25
                breakdown["adjustments"]["env_dead"] = -25
            elif env_name == "LIQUIDATION":
                bonus -= 25
                breakdown["adjustments"]["env_liquidation"] = -25

            if dir_eff >= 70:
                bonus += 10
                breakdown["adjustments"]["dir_eff_high"] = +10
            elif dir_eff < 35:
                bonus -= 12
                breakdown["adjustments"]["dir_eff_low"] = -12

            if trap_d > 65:
                bonus -= 10
                breakdown["adjustments"]["trap_density"] = -10
            if bfr > 60:
                bonus -= 18
                breakdown["adjustments"]["bfr_high"] = -18
            if rot_f > 75:
                bonus -= 12
                breakdown["adjustments"]["rotation_high"] = -12

            breakdown["environment"] = env_name

        # ══ FACTOR 2: SESSION REGIME ══════════════════════════════
        if session_regime is not None:
            regime      = getattr(session_regime, "session_regime",          "BALANCED_DAY")
            trend_str   = getattr(session_regime, "trend_strength",          40)
            vol_state   = getattr(session_regime, "volatility_state",        "NORMAL")
            reg_conf    = getattr(session_regime, "regime_confidence",        50)

            is_trend  = regime in ("TREND_DAY", "EXPANSION_DAY",
                                   "SHORT_COVERING", "LIQUIDATION")
            is_range  = getattr(session_regime, "is_range_regime",
                                lambda: False)()

            if is_trend and trend_str >= 60:
                r_bonus = min(15, int(trend_str * 0.18))
                bonus  += r_bonus
                breakdown["adjustments"]["regime_trend"] = r_bonus
            elif is_range:
                bonus -= 12
                breakdown["adjustments"]["regime_range"] = -12

            if vol_state == "LOW":
                bonus -= 8
                breakdown["adjustments"]["low_vol"] = -8
            elif vol_state in ("HIGH", "EXTREME"):
                bonus += 4
                breakdown["adjustments"]["high_vol"] = +4

            # SHORT_COVERING bonus
            if regime == "SHORT_COVERING":
                bonus += 24
                breakdown["adjustments"]["short_covering"] = +24

            # Low regime confidence = unclassified regime = penalize score
            # This was the factor causing score=96 on BALANCED_DAY conf=30%
            if reg_conf < 40:
                rc_pen = int((40 - reg_conf) * 0.6)   # máx ~24 puntos a conf=0
                bonus -= rc_pen
                breakdown["adjustments"]["regime_conf_low"] = -rc_pen
            elif reg_conf >= 75:
                rc_bon = min(6, int((reg_conf - 75) / 5))
                bonus += rc_bon
                breakdown["adjustments"]["regime_conf_high"] = rc_bon

            breakdown["session_regime"]    = regime
            breakdown["regime_confidence"] = reg_conf

        # ══ FACTOR 3: CONFIRMATION (breakout quality) ═════════════
        if confirmation is not None:
            bq_type   = getattr(confirmation, "breakout_type",       "NONE")
            acc_type  = getattr(confirmation, "acceptance_type",     "NONE")
            conf_sc   = getattr(confirmation, "confirmation_score",   50)
            dp_sc     = getattr(confirmation, "delta_persistence",    50)
            exp_eff   = getattr(confirmation, "expansion_efficiency", 0.5)
            struct    = getattr(confirmation, "structure_bias",      "NEUTRAL")
            comp_str  = getattr(confirmation, "compression_strength", 0)

            # CAPS DUROS por breakout type
            if bq_type == "FAKE":
                hard_cap = 25
                bonus   -= 28
                breakdown["adjustments"]["fake"] = -28
            elif bq_type == "WEAK":
                regime_nm    = getattr(session_regime, "session_regime", "") if session_regime else ""
                _wk_cont_src = adaptive_continuation if adaptive_continuation is not None else continuation
                _wk_cont_p   = getattr(_wk_cont_src, "continuation_probability", 0) if _wk_cont_src else 0
                _wk_dp       = getattr(confirmation, "delta_persistence",    50)
                _wk_eff      = getattr(confirmation, "expansion_efficiency",  0.5)

                if regime_nm in ("TREND_DAY", "EXPANSION_DAY"):
                    if _wk_cont_p >= 80:
                        bonus -= 2
                        breakdown["adjustments"]["weak_trend_cont"] = -2
                    elif _wk_cont_p < 70:
                        bonus -= 8
                        breakdown["adjustments"]["weak_trend_low_cont"] = -8
                    else:
                        bonus -= 5
                        breakdown["adjustments"]["weak_trend"] = -5
                elif regime_nm == "SHORT_COVERING":
                    # WEAK in SHORT_COVERING: cap 60 — deliberately below MODERATE (65+)
                    # to maintain separation WEAK < MODERATE < REAL < EXPLOSIVE
                    hard_cap = 60
                    bonus   -= 8
                    breakdown["adjustments"]["weak_sc"] = -8
                else:
                    hard_cap = 50
                    bonus   -= 12
                    breakdown["adjustments"]["weak_breakout"] = -12
            elif bq_type == "MODERATE":
                regime_nm = getattr(session_regime, "session_regime", "") if session_regime else ""
                if regime_nm not in ("TREND_DAY", "EXPANSION_DAY"):
                    # MODERATE fuera de trend: penalización base -10
                    bonus -= 10
                    breakdown["adjustments"]["moderate_nontrend"] = -10
                    # MODERATE in SHORT_COVERING with accumulated weaknesses
                    # WR=50% signals that marginal setups destroy edge
                    # Extra penalty only when multiple signals are weak
                    if regime_nm == "SHORT_COVERING":
                        _mod_dp  = getattr(confirmation, "delta_persistence",    60)
                        _mod_acc = getattr(confirmation, "acceptance_score",     60)
                        _mod_eff = getattr(confirmation, "expansion_efficiency", 0.6)
                        _weak_signals = sum([
                            _mod_dp  < 70,    # dp moderado-bajo
                            _mod_acc < 65,    # acceptance borderline
                            _mod_eff < 0.65,  # efficiency moderada
                        ])
                        if _weak_signals >= 2:
                            # Two or more weaknesses = marginal MODERATE setup in SC
                            bonus -= 8
                            breakdown["adjustments"]["moderate_sc_weak"] = -8
                else:
                    bonus += 2
                    breakdown["adjustments"]["moderate_trend"] = +2
            elif bq_type == "REAL":
                # REAL — keep the boost but don't let it dominate (+5 over existing base)
                _ac_p = 0
                if adaptive_continuation is not None:
                    _ac_p = getattr(adaptive_continuation, "continuation_probability", 0)
                elif continuation is not None:
                    _ac_p = getattr(continuation, "continuation_probability", 0)

                if conf_sc >= 85 and _ac_p >= 85:
                    bonus += 32 + 5   # PATCH 1: +5 sobre institutional
                    breakdown["adjustments"]["real_institutional"] = +37
                elif conf_sc < 75:
                    bonus += 12 + 5
                    breakdown["adjustments"]["real_low_conf"] = +17
                elif _ac_p < 80:
                    bonus += 14 + 5
                    breakdown["adjustments"]["real_low_cont"] = +19
                else:
                    bonus += 22 + 5
                    breakdown["adjustments"]["real_breakout"] = +27
            elif bq_type == "EXPLOSIVE":
                # PATCH 3: EXPLOSIVE sample stabilization
                # Sin acceso a sample histórico en tiempo real, usamos
                # confirmation_score como proxy de calidad del sample actual.
                # conf_sc alto = señal sólida = peso completo
                # conf_sc bajo = señal débil = stability mode reducido
                _exp_regime   = getattr(session_regime, "session_regime", "") if session_regime else ""
                _exp_cont_src = adaptive_continuation if adaptive_continuation is not None else continuation
                _exp_cont_p   = getattr(_exp_cont_src, "continuation_probability", 0) if _exp_cont_src else 0

                # Stability mode: conf_sc < 70 = señal poco fiable = reducir boost
                if conf_sc < 70:
                    base_explosive = 8   # PATCH 3: stability mode (sample débil)
                    breakdown["adjustments"]["explosive_stability_mode"] = +8
                else:
                    base_explosive = 43 + 18   # pleno: base + edge concentration
                    breakdown["adjustments"]["explosive_full"] = base_explosive

                    # Bonus adaptativo por régimen (solo en full mode)
                    if _exp_regime in ("BALANCED_DAY", "SHORT_COVERING"):
                        base_explosive += 10
                        breakdown["adjustments"]["explosive_regime_boost"] = +10

                    # Bonus por continuation alta (solo en full mode)
                    if _exp_cont_p >= 85:
                        base_explosive += 5
                        breakdown["adjustments"]["explosive_cont_boost"] = +5

                bonus += base_explosive
                breakdown["adjustments"]["explosive"] = base_explosive

            # Acceptance
            if acc_type == "RECLAIM":
                bonus -= 22
                breakdown["adjustments"]["reclaim"] = -22
            elif acc_type == "ACCEPTED":
                acc_num = getattr(confirmation, "acceptance_score", 55)
                if acc_num >= 75:
                    # Acceptance fuerte: bonus moderado (era +16, reducido para evitar saturación)
                    bonus += 10
                    breakdown["adjustments"]["acc_strong"] = +10
                elif acc_num >= 62:
                    bonus += 5
                    breakdown["adjustments"]["accepted"] = +5
                else:
                    # ACCEPTED nominal con score bajo = borderline
                    bonus -= 5
                    breakdown["adjustments"]["acc_weak_nominal"] = -5
            elif acc_type in ("WEAK_ACC", "NONE"):
                bonus -= 10
                breakdown["adjustments"]["weak_acc"] = -10
            if dp_sc >= 85:
                bonus += 10
                breakdown["adjustments"]["delta_persist_strong"] = +10
            elif dp_sc < 35:
                # EXPLOSIVE con dp bajo = fake explosive — penalizar extra
                if bq_type == "EXPLOSIVE":
                    bonus -= 25
                    breakdown["adjustments"]["explosive_low_dp"] = -25
                else:
                    bonus -= 10
                    breakdown["adjustments"]["delta_persist_weak"] = -10
            elif dp_sc < 55 and bq_type == "EXPLOSIVE":
                # EXPLOSIVE necesita delta sostenido — dp moderado también penaliza
                bonus -= 12
                breakdown["adjustments"]["explosive_moderate_dp"] = -12

            # Efficiency
            if exp_eff >= 0.65:
                bonus += 8
                breakdown["adjustments"]["eff_high"] = +8
            elif exp_eff < 0.40 and bq_type == "EXPLOSIVE":
                # EXPLOSIVE absorbido = score debe bajar mucho
                pen = int((0.40 - exp_eff) * 60)  # 0.35 -> -3, 0.20 -> -12
                bonus -= pen
                breakdown["adjustments"]["explosive_absorbed"] = -pen
            elif exp_eff < 0.25:
                bonus -= 14
                breakdown["adjustments"]["eff_low"] = -14

            # Structure — peso ajustado con contexto institucional
            # SHORT_COVERING + BEARISH struct + squeeze narrative = institutional reversal
            # No penalizar como "estructura opuesta" en ese contexto
            _regime_for_struct = getattr(session_regime, "session_regime", "") if session_regime else ""
            _is_squeeze_context = (
                _regime_for_struct == "SHORT_COVERING" and
                conf_bias in ("BULLISH",)
            )

            if struct == "BULLISH" and conf_bias == "BULLISH":
                bonus += 10
                breakdown["adjustments"]["struct_aligned"] = +10
            elif struct == "BEARISH" and conf_bias == "BEARISH":
                bonus += 10
                breakdown["adjustments"]["struct_aligned"] = +10
            elif struct != "NEUTRAL":
                if _is_squeeze_context:
                    # SC LONG con struct BEARISH: penalización moderada
                    # -10 (era -5 anterior y -15 original) — punto medio
                    bonus -= 10
                    breakdown["adjustments"]["struct_squeeze_override"] = -10
                else:
                    bonus -= 15
                    breakdown["adjustments"]["struct_opposed"] = -15
            if struct == "NEUTRAL":
                bonus -= 8
                breakdown["adjustments"]["struct_neutral"] = -8

            # Confirmation score
            if conf_sc < 35:
                pen   = int(base_score * 0.20)
                bonus -= pen
                breakdown["adjustments"]["low_conf"] = -pen
            elif conf_sc >= 70:
                adj   = min(10, int((conf_sc - 70) / 3))
                bonus += adj
                breakdown["adjustments"]["high_conf"] = adj

            if comp_str >= 70:
                bonus += 5
                breakdown["adjustments"]["comp_strong"] = +5

        else:
            pen    = int(base_score * 0.20)
            bonus -= pen
            breakdown["adjustments"]["no_confirmation"] = -pen

        # ══ FACTOR 4: ADAPTIVE CONTINUATION ══════════════════════
        if adaptive_continuation is not None:
            ac_qual   = getattr(adaptive_continuation, "continuation_quality",    "UNKNOWN")
            ac_prob   = getattr(adaptive_continuation, "continuation_probability", 50)
            ac_risk   = getattr(adaptive_continuation, "continuation_risk",        30)
            ac_decay  = getattr(adaptive_continuation, "momentum_decay",           0)
            ac_exhaust= getattr(adaptive_continuation, "exhaustion",               False)

            if ac_qual == "STRONG":
                bonus += 12
                breakdown["adjustments"]["ac_strong"] = +12
            elif ac_qual == "MODERATE":
                bonus += 8
                breakdown["adjustments"]["ac_moderate"] = +8
            elif ac_qual == "WEAK":
                bonus -= 12
                breakdown["adjustments"]["ac_weak"] = -12
            elif ac_qual in ("EXHAUSTED", "NONE"):
                bonus -= 20
                breakdown["adjustments"]["ac_exhaust"] = -20

            if ac_prob > 85:
                bonus += 12
                breakdown["adjustments"]["cont_prob_high"] = +12
            elif ac_prob < 40:
                bonus -= 10
                breakdown["adjustments"]["cont_prob_low"] = -10

            if ac_exhaust:
                bonus -= 15
                breakdown["adjustments"]["exhaustion"] = -15

            if ac_decay >= 70:
                bonus -= 8
                breakdown["adjustments"]["momentum_decay"] = -8

            breakdown["continuation_quality"] = ac_qual

        # Fallback: continuation_engine legacy
        elif continuation is not None:
            cont_prob = getattr(continuation, "continuation_probability", 50)
            cont_qual = getattr(continuation, "continuation_quality",     "UNKNOWN")

            if cont_qual == "STRONG":
                bonus += 10
                breakdown["adjustments"]["cont_strong"] = +10
            elif cont_qual == "WEAK":
                bonus -= 8
                breakdown["adjustments"]["cont_weak"] = -8
            elif cont_qual == "NONE":
                bonus -= 15
                breakdown["adjustments"]["cont_none"] = -15

            if cont_prob > 85:
                bonus += 12
                breakdown["adjustments"]["cont_prob85"] = +12

        # ══ FACTOR 5: EDGE LEARNING ═══════════════════════════════
        if edge_learning is not None:
            bq_type_str = getattr(confirmation, "breakout_type", "") if confirmation else ""
            regime_str  = getattr(session_regime, "session_regime", "") if session_regime else ""
            env_str     = getattr(market_env, "environment", "") if market_env else ""
            ac_qual_str = getattr(adaptive_continuation, "continuation_quality", "") if adaptive_continuation else ""

            el_adj = edge_learning.get_score_adjustment(
                breakout_type        = bq_type_str,
                session_regime       = regime_str,
                zone                 = zone,
                event                = event_key,
                environment          = env_str,
                continuation_quality = ac_qual_str,
            )
            if abs(el_adj) > 0:
                bonus += el_adj
                breakdown["adjustments"]["edge_learning"] = el_adj

            # Penalización automática por historial bajo
            el_pen, el_reason = edge_learning.should_penalize(
                breakout_type  = bq_type_str,
                session_regime = regime_str,
            )
            if el_pen > 0:
                bonus -= el_pen
                breakdown["adjustments"]["edge_auto_pen"] = -el_pen

        # ══ FACTOR 6: POC ACCEPTANCE (refinement layer) ══════════
        if poc_acceptance is not None:
            poc_sc    = getattr(poc_acceptance, "poc_acceptance_score",    50)
            absorb    = getattr(poc_acceptance, "absorption_score",         0)
            auc_fail  = getattr(poc_acceptance, "auction_failure",      False)
            trap      = getattr(poc_acceptance, "trap_active",          False)
            exhaust   = getattr(poc_acceptance, "breakout_exhaustion",  False)
            acc_conf  = getattr(poc_acceptance, "acceptance_confirmed", False)
            agg_no_r  = getattr(poc_acceptance, "aggression_without_result", False)
            auc_score = getattr(poc_acceptance, "auction_failure_score",    0)

            # Favorable: acceptance confirmed
            if acc_conf and poc_sc >= 65:
                bonus += 8
                breakdown["adjustments"]["poc_accepted"] = +8
            # Warning: absorption or trap
            if absorb >= 65:
                bonus -= 10
                breakdown["adjustments"]["poc_absorption"] = -10
            if trap:
                bonus -= 12
                breakdown["adjustments"]["poc_trap"] = -12
            if auc_fail:
                bonus -= 14
                breakdown["adjustments"]["poc_auction_fail"] = -14
            if exhaust:
                bonus -= 8
                breakdown["adjustments"]["poc_exhaustion"] = -8
            if agg_no_r:
                bonus -= 6
                breakdown["adjustments"]["poc_agg_no_result"] = -6
            # EXPLOSIVE con auction failure = penalizar fuerte
            if poc_acceptance is not None:
                bq_t = getattr(confirmation, "breakout_type", "") if confirmation else ""
                if bq_t == "EXPLOSIVE" and (auc_fail or absorb >= 60):
                    bonus -= 18
                    breakdown["adjustments"]["explosive_auction_fail"] = -18

            breakdown["poc_acceptance_score"] = poc_sc

        # ══ FACTORES ADICIONALES ══════════════════════════════════

        # SOFT CONTEXTUAL MICRO-PENALTIES
        # Cumulative, non-blocking, structurally justified.
        # Create organic score dispersion without targeting specific trades.
        _mp_total = 0
        if session_regime is not None:
            _vol   = getattr(session_regime, "volatility_state", "NORMAL")
            _rconf = getattr(session_regime, "regime_confidence", 70)
            _tstr  = getattr(session_regime, "trend_strength",    50)
            # HIGH volatility on non-EXPLOSIVE setup = soft penalty
            if _vol in ("HIGH", "EXTREME") and bq_type not in ("EXPLOSIVE",):
                _mp_total -= 3
                breakdown["adjustments"]["ctx_vol_high"] = -3
            # Mid-confidence regime = unconfirmed context
            if 40 <= _rconf < 60:
                _mp_total -= 2
                breakdown["adjustments"]["ctx_regime_uncertain"] = -2
            # Weak trend on TREND_DAY = false trend
            if getattr(session_regime, "session_regime", "") == "TREND_DAY" and _tstr < 65:
                _mp_total -= 3
                breakdown["adjustments"]["ctx_weak_trend"] = -3

        if confirmation is not None:
            _eff_ctx = getattr(confirmation, "expansion_efficiency", 0.6)
            _dp_ctx  = getattr(confirmation, "delta_persistence",    60)
            # Moderate efficiency — not low enough to penalize yet, not high enough to reward
            if 0.55 <= _eff_ctx < 0.70 and bq_type not in ("EXPLOSIVE", "REAL"):
                _mp_total -= 2
                breakdown["adjustments"]["ctx_moderate_eff"] = -2
            # Moderate delta persistence on non-WEAK setup
            if 50 <= _dp_ctx < 70 and bq_type in ("REAL", "EXPLOSIVE"):
                _mp_total -= 2
                breakdown["adjustments"]["ctx_moderate_dp"] = -2

        if _mp_total != 0:
            bonus += _mp_total

        # UNCLEAR narrative penalizes score (received via event_result when available)

        # ACUMULACIÓN bonuses
        if event_key == "ACUMULACION":
            if absorption:
                bonus += 8
                breakdown["adjustments"]["acum_absorcion"] = +8
            if abs(price_move) < tick * 2 and abs(delta) > 100:
                bonus += 4
                breakdown["adjustments"]["acum_compresion"] = +4

        # AT_VAH
        if zone in ("AT_VAH", "ABOVE_VAH"):
            if event_key == "AGOTAMIENTO":
                bonus += 10; breakdown["adjustments"]["vah_agot"] = +10
            elif event_key == "FALLO":
                bonus += 6;  breakdown["adjustments"]["vah_fallo"] = +6
            elif event_key == "INTENTO":
                bonus -= 12; breakdown["adjustments"]["vah_intento"] = -12
            elif event_key == "ACUMULACION":
                bonus -= 10; breakdown["adjustments"]["vah_acum"] = -10

        # IN_VALUE_AREA
        if zone == "IN_VALUE_AREA":
            bonus -= 12; breakdown["adjustments"]["iva"] = -12

        # DEAD ZONE
        if dead_zone:
            bonus -= 18; breakdown["adjustments"]["dead_zone"] = -18

        # HPZ
        if hpz:
            bonus += self.HPZ_BONUS
            breakdown["adjustments"]["hpz"] = self.HPZ_BONUS

        # Bias alignment
        bias_aligned = (conf_bias != "NEUTRAL" and lvl_bias != "NEUTRAL" and
                        conf_bias == lvl_bias)
        if bias_aligned:
            bonus += self.BIAS_ALIGN_BONUS
            breakdown["adjustments"]["bias_align"] = self.BIAS_ALIGN_BONUS

        # Confidence modifier
        conf_modifier = int((confidence / 100) * base_score * 0.07)
        if conf_modifier:
            bonus += conf_modifier
            breakdown["adjustments"]["conf_mod"] = conf_modifier

        # Consecutive
        tentative_class, _ = classify_score(min(base_score + bonus, 100))
        if tentative_class == self._last_class:
            self._consecutive += 1
        else:
            self._consecutive = 0
        self._last_class = tentative_class
        consec_bonus = min(self._consecutive * self.CONSECUTIVE_BONUS,
                           self.MAX_CONSEC_BONUS)
        if consec_bonus and confirmation is not None:
            bonus += consec_bonus
            breakdown["adjustments"]["consecutive"] = consec_bonus

        # ══ SCORE FINAL con CAP DINÁMICO ══════════════════════════
        raw_combined = base_score + bonus

        # DIMINISHING RETURNS: soft compression for high raw scores
        # Prevents saturation at 95-100 and restores organic dispersion.
        # Scores below 80 are untouched; higher scores compress progressively.
        # raw=90 → ~87, raw=100 → ~91, raw=110 → ~94
        if raw_combined > 80:
            excess    = raw_combined - 80
            compressed_excess = excess * (1.0 - (excess / (excess + 40.0)))
            raw_combined = 80 + compressed_excess
            raw_combined = 80 + compressed_excess

        final_score = max(0, min(int(round(raw_combined)), 100))

        # CAP DURO estático
        if hard_cap is not None:
            final_score = min(final_score, hard_cap)

        # CAP DINÁMICO: score > 75 requiere condiciones estrictas
        if final_score > 75:
            bq_t   = getattr(confirmation, "breakout_type", "NONE") if confirmation else "NONE"
            dp_val = getattr(confirmation, "delta_persistence",    0) if confirmation else 0
            eff_val= getattr(confirmation, "expansion_efficiency", 0.5) if confirmation else 0.5
            cp     = 0
            if adaptive_continuation is not None:
                cp = getattr(adaptive_continuation, "continuation_probability", 0)
            elif continuation is not None:
                cp = getattr(continuation, "continuation_probability", 0)
            env_t = getattr(market_env, "tradeable", True) if market_env else True

            meets_cap = (
                bq_t in ("REAL", "EXPLOSIVE") and
                cp >= 80 and
                env_t
            )
            if not meets_cap:
                final_score = 75
                breakdown["adjustments"]["dynamic_cap_75"] = "applied"

        # CAP ADICIONAL: EXPLOSIVE score > 85 requiere dp y eff institucionales
        # Previene score=100 en EXPLOSIVE con quality mediocre (Trade #26 pattern)
        if final_score > 85:
            bq_t2   = getattr(confirmation, "breakout_type",         "NONE") if confirmation else "NONE"
            dp_val2 = getattr(confirmation, "delta_persistence",        50)  if confirmation else 50
            eff_v2  = getattr(confirmation, "expansion_efficiency",    0.5)  if confirmation else 0.5
            if bq_t2 == "EXPLOSIVE":
                if dp_val2 < 65 or eff_v2 < 0.55:
                    final_score = 85
                    breakdown["adjustments"]["explosive_quality_cap_85"] = "applied"

        classification, action = classify_score(final_score)

        breakdown["total_bonus"] = bonus
        breakdown["hard_cap"]    = hard_cap
        breakdown["final_score"] = final_score

        adj_parts = [f"{k}:{'+' if v > 0 else ''}{v}"
                     for k, v in breakdown["adjustments"].items()
                     if isinstance(v, (int, float))]
        bonus_str = (" [" + " | ".join(adj_parts) + "]") if adj_parts else ""

        result = ConfluenceResult(
            event=event, zone=zone,
            confluence=event + " + " + zone,
            bias=conf_bias, score=final_score,
            classification=classification, action=action,
            reason=matrix_reason + bonus_str,
            hpz_bonus=hpz, bias_aligned=bias_aligned,
            consecutive=self._consecutive,
            trade_allowed=True, trigger_type="SCORED",
            score_breakdown=breakdown,
        )
        self._history.append(result)
        return result

    def recent_quality(self, n: int = 5) -> str:
        if not self._history:
            return "NO DATA"
        avg = sum(r.score for r in list(self._history)[-n:]) / min(n, len(self._history))
        if avg >= 85: return "INSTITUTIONAL SUSTAINED"
        if avg >= 68: return "HIGH QUALITY SUSTAINED"
        if avg >= 48: return "MEDIUM QUALITY"
        return "LOW QUALITY / NOISE"

    def last_high_quality(self) -> Optional[ConfluenceResult]:
        for r in reversed(list(self._history)):
            if r.score >= 68:
                return r
        return None