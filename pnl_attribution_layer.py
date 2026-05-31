# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ V3 — pnl_attribution_layer.py
#  PNL Attribution Layer v1.0
#
#  FUNCIÓN:
#  Atribuye el edge (o la pérdida de edge) a cada módulo del pipeline
#  por bar y por cluster de setup. Post-hoc, sin modificar decisiones.
#
#  INPUTS:  ETS, GTAL, Timing, ESL, OPP, validation flags
#  OUTPUTS:
#  - pnl_contribution_by_module
#  - edge_efficiency_score (0-100)
#  - false_edge_detection_flag
#  - total_edge_estimate_per_setup
#
#  RESTRICCIÓN: solo datos observados hasta barra actual.
#  NO forecasting. NO modifica trade decisions.
# ╚══════════════════════════════════════════════════════════════════╝

from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class PNLAttributionResult:
    # Contribución por módulo (-100 a +100)
    etil_contribution:    int   = 0
    gtal_filter_impact:   int   = 0
    timing_contribution:  int   = 0
    exec_cost_impact:     int   = 0   # ESL slippage/fill

    # Score global
    edge_efficiency_score:     int   = 0   # 0-100
    total_edge_estimate:       int   = 0   # puntos edge estimados
    false_edge_detection_flag: bool  = False

    # Breakdown texto
    attribution_summary: str  = ""

    def __str__(self) -> str:
        return (f"EdgeEff={self.edge_efficiency_score} "
                f"ETIL={self.etil_contribution:+d} "
                f"GTAL={self.gtal_filter_impact:+d} "
                f"Timing={self.timing_contribution:+d} "
                f"ExecCost={self.exec_cost_impact:+d}")


class PNLAttributionLayer:
    """
    Atribuye el edge real a cada módulo del pipeline V3.

    Principio: cada módulo o AGREGA edge (detectó correctamente)
    o DESTRUYE edge (bloqueó una señal real o permitió una falsa).

    No modifica ninguna decisión. Solo audita.
    """

    WINDOW = 20

    def __init__(self):
        self._bar_logs:     List[dict] = []
        self._setup_count:  int = 0
        self._valid_count:  int = 0
        self._hb_count:     int = 0
        self._ets_history:  deque = deque(maxlen=self.WINDOW)
        self._edge_history: deque = deque(maxlen=self.WINDOW)

    def analyze(self,
                etil_r,
                gtal_r,
                timing_r,
                esl_r,
                opp_r,
                conf_r,
                cont_r,
                validation,
                bar_count: int) -> PNLAttributionResult:

        # Extraer valores
        ets        = getattr(etil_r,    "ets_score",              0)
        ets_class  = getattr(etil_r,    "classification",         "NOISE")
        rt_score   = getattr(gtal_r,    "real_tradeability_score", 0)
        hb_flag    = getattr(gtal_r,    "hindsight_bias_flag",    False)
        ev         = getattr(gtal_r,    "execution_validity",     "INVALID")
        t_score    = getattr(timing_r,  "entry_timing_score",     0)
        t_grade    = getattr(timing_r,  "timing_grade",           "MISSED")
        esl_active = getattr(esl_r,     "active",                 False)
        esl_score  = getattr(esl_r,     "final_executable_score", 0)
        fill       = getattr(esl_r,     "fill_likelihood",        "LOW")
        slip       = getattr(esl_r,     "slippage_estimate",      0.0)
        opp_grade  = getattr(opp_r,     "grade",                  "NONE")
        validated  = getattr(validation, "validated",             False)
        val_reason = getattr(validation, "reason",                "")
        conf_sc    = getattr(conf_r,    "confirmation_score",     0)
        cont_p     = getattr(cont_r,    "continuation_probability", 0)

        self._ets_history.append(ets)

        # Tracking
        if opp_grade != "NONE":
            self._setup_count += 1
        if ev == "VALID":
            self._valid_count += 1
        if hb_flag:
            self._hb_count += 1

        # ── 1. ETIL CONTRIBUTION ─────────────────────────────────
        # Positivo si detectó correctamente, negativo si fue ruido
        if ets >= 65 and not hb_flag:
            etil_contrib = 25    # detección real confirmada
        elif ets >= 65 and hb_flag:
            etil_contrib = -10   # ETS alto pero hindsight
        elif ets >= 35:
            etil_contrib = 5     # WATCH — neutro positivo
        else:
            etil_contrib = 0     # NOISE — no penalizar

        # ── 2. GTAL FILTER IMPACT ────────────────────────────────
        # Positivo si filtró correctamente, negativo si bloqueó edge real
        if ev == "VALID" and not hb_flag:
            gtal_impact = 20     # validó correctamente
        elif ev == "INVALID" and hb_flag:
            gtal_impact = 15     # filtró hindsight correctamente
        elif ev == "INVALID" and ets >= 65 and not hb_flag:
            gtal_impact = -15    # bloqueó señal potencialmente real
        else:
            gtal_impact = 0      # neutro

        # ── 3. TIMING CONTRIBUTION ──────────────────────────────
        if t_grade == "OPTIMAL":
            timing_contrib = 15
        elif t_grade == "ACCEPTABLE":
            timing_contrib = 8
        elif t_grade == "LATE":
            timing_contrib = -10
        elif t_grade == "MISSED":
            timing_contrib = -20
        else:
            timing_contrib = 0

        # ── 4. EXECUTION COST IMPACT (ESL) ──────────────────────
        if esl_active:
            slip_cost = int(slip * 4)   # 1 pt slip = -4 edge pts
            if fill == "HIGH":
                exec_impact = 10 - slip_cost
            elif fill == "MEDIUM":
                exec_impact = 5 - slip_cost
            else:
                exec_impact = -10 - slip_cost
        else:
            exec_impact = 0

        # ── EDGE EFFICIENCY SCORE ────────────────────────────────
        raw_edge = 50  # base

        # ETIL detectó correctamente
        if ets >= 65 and not hb_flag:         raw_edge += 20
        # GTAL validó
        if ev == "VALID":                      raw_edge += 15
        # Timing óptimo
        if t_grade in ("OPTIMAL", "ACCEPTABLE"): raw_edge += 10
        # ESL ejecutable
        if esl_active and esl_score >= 70:     raw_edge += 10
        # Penalizaciones
        if hb_flag:                            raw_edge -= 20
        if t_grade in ("LATE", "MISSED"):      raw_edge -= 15
        if val_reason and "capped" in val_reason: raw_edge -= 10

        edge_eff = max(0, min(raw_edge, 100))

        # ── FALSE EDGE FLAG ──────────────────────────────────────
        # Señal que parecía buena pero tenía hindsight o timing malo
        false_edge = (
            opp_grade == "A" and
            (hb_flag or t_grade == "MISSED" or
             (ev == "INVALID" and ets >= 65))
        )

        # ── TOTAL EDGE ESTIMATE ──────────────────────────────────
        # Estimación en "puntos de edge" que el setup tenía
        if ev == "VALID" and esl_active:
            total_edge = int(rt_score * 0.5 + esl_score * 0.3 +
                             t_score * 0.2) - int(slip * 4)
        elif ev == "VALID":
            total_edge = int(rt_score * 0.6 + t_score * 0.4)
        else:
            total_edge = 0

        total_edge = max(0, total_edge)

        # ── SUMMARY ──────────────────────────────────────────────
        parts = []
        if etil_contrib > 0:
            parts.append(f"ETIL+{etil_contrib}")
        elif etil_contrib < 0:
            parts.append(f"ETIL{etil_contrib}")
        if gtal_impact != 0:
            parts.append(f"GTAL{'+' if gtal_impact > 0 else ''}{gtal_impact}")
        if timing_contrib != 0:
            parts.append(f"T{'+' if timing_contrib > 0 else ''}{timing_contrib}")
        if exec_impact != 0:
            parts.append(f"ESL{'+' if exec_impact > 0 else ''}{exec_impact}")

        summary = " | ".join(parts) if parts else "neutral"

        # Log interno
        self._bar_logs.append({
            "bar": bar_count, "ets": ets, "ev": ev,
            "hb": hb_flag, "opp": opp_grade,
            "edge_eff": edge_eff, "total_edge": total_edge
        })
        self._edge_history.append(edge_eff)

        return PNLAttributionResult(
            etil_contribution          = etil_contrib,
            gtal_filter_impact         = gtal_impact,
            timing_contribution        = timing_contrib,
            exec_cost_impact           = exec_impact,
            edge_efficiency_score      = edge_eff,
            total_edge_estimate        = total_edge,
            false_edge_detection_flag  = false_edge,
            attribution_summary        = summary,
        )

    def session_summary(self) -> dict:
        """Resumen de atribución por sesión completa."""
        if not self._bar_logs:
            return {}

        valid_bars  = [l for l in self._bar_logs if l["ev"] == "VALID"]
        a_bars      = [l for l in self._bar_logs if l["opp"] == "A"]
        hb_bars     = [l for l in self._bar_logs if l["hb"]]
        avg_edge    = (sum(l["edge_eff"] for l in self._bar_logs) /
                       len(self._bar_logs)) if self._bar_logs else 0
        max_edge    = max((l["total_edge"] for l in self._bar_logs), default=0)

        return {
            "total_bars":          len(self._bar_logs),
            "gtal_valid_bars":     len(valid_bars),
            "a_setup_bars":        len(a_bars),
            "hindsight_bars":      len(hb_bars),
            "hindsight_rate_pct":  round(len(hb_bars) / max(len(self._bar_logs), 1) * 100, 1),
            "avg_edge_efficiency": round(avg_edge, 1),
            "max_total_edge":      max_edge,
            "setups_detected":     self._setup_count,
            "gtal_validated":      self._valid_count,
        }