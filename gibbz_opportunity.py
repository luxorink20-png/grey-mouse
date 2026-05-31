# gibbz_opportunity.py
# Opportunity Classification System v1.0

from dataclasses import dataclass


@dataclass
class OpportunityResult:
    grade:       str  = "NONE"   # A / B / C / NONE
    reason:      str  = ""
    actionable:  bool = False
    priority:    int  = 0        # 1=highest


class OpportunityClassifier:
    """
    Clasifica setups en A / B / C.

    A: ETS alto + conf temprana + timing óptimo
    B: ETS alto pero conf tardía
    C: conf alta sin ETS previo (entrada tardía)
    """

    def classify(self,
                 etil_r:   "ETILResult",
                 timing_r: "TimingResult",
                 decay_r:  "EdgeDecayResult",
                 conf_r,
                 validation) -> OpportunityResult:

        ets        = getattr(etil_r,   "ets_score",         0)
        ets_class  = getattr(etil_r,   "classification",    "NOISE")
        t_score    = getattr(timing_r, "entry_timing_score", 0)
        t_grade    = getattr(timing_r, "timing_grade",      "MISSED")
        edge_str   = getattr(decay_r,  "edge_strength",     0)
        expired    = getattr(decay_r,  "edge_expired",      True)
        conf_sc    = getattr(conf_r,   "confirmation_score", 0)
        validated  = getattr(validation, "validated",       False)
        early_opp  = getattr(timing_r, "early_opportunity", False)
        late_det   = getattr(timing_r, "late_detection",    False)

        if expired:
            return OpportunityResult(
                grade="NONE", reason="Edge expirado", actionable=False)

        # ── GRADE A ─────────────────────────────────────────────
        # ETS alto + timing óptimo + edge fuerte
        if (ets >= 65 and
                t_grade in ("OPTIMAL", "ACCEPTABLE") and
                edge_str >= 60 and
                not late_det):
            reason = (f"ETS={ets} timing={t_grade} "
                      f"edge={edge_str}")
            return OpportunityResult(
                grade="A", reason=reason,
                actionable=validated, priority=1)

        # ── GRADE B ─────────────────────────────────────────────
        # ETS alto pero timing tardío o conf no llegó aún
        if ets >= 65 and edge_str >= 40:
            reason = (f"ETS={ets} early={early_opp} "
                      f"timing={t_grade}")
            return OpportunityResult(
                grade="B", reason=reason,
                actionable=validated and not late_det,
                priority=2)

        # ── GRADE C ─────────────────────────────────────────────
        # Conf alta pero sin ETS — entrada tardía
        if conf_sc >= 65 and ets < 35:
            reason = f"conf={conf_sc} sin ETS previo (late entry)"
            return OpportunityResult(
                grade="C", reason=reason,
                actionable=validated,
                priority=3)

        # WATCH — ETS moderado
        if ets_class == "WATCH":
            return OpportunityResult(
                grade="NONE", reason=f"ETS={ets} WATCH — esperar",
                actionable=False)

        return OpportunityResult(
            grade="NONE", reason="Sin setup válido", actionable=False)