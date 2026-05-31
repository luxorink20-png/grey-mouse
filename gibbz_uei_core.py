"""
GIBBZ V3 — gibbz_uei_core.py
Unified Edge Intelligence Core v1.0

Sistema unificado de 4 capas de decisión institucional.

NO optimiza profit.
Optimiza realidad estructural, consistencia institucional,
separación signal/noise y validez de edge en tiempo continuo.

ARQUITECTURA:
  CAPA 1 — LEFS  Live Event Filter System
  CAPA 2 — ECL   Edge Confirmation Layer
  CAPA 3 — LIVE GATE  Live Edge Decision Gate
  CAPA 4 — LCL   Live Confidence Layer

OUTPUT FINAL:
  LEFS classification
  ECL classification
  LIVE GATE decision
  EDGE SCORE FINAL (0-100)
  ESI + OVERFITTING RISK
  REASONING CHAIN
  GO / NO-GO para treadmill o live simulation

USO:
  from gibbz_uei_core import UEICore, UEIInput
  core   = UEICore()
  result = core.evaluate(UEIInput(...))
  print(result)

  python gibbz_uei_core.py --demo
  python gibbz_uei_core.py --session 2026-01-28
"""

from __future__ import annotations
import json
import os
import math
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Tuple
from datetime import datetime

# ── CLASSIFICATION ENUMS ──────────────────────────────────────────

# LEFS outputs
LEFS_VALID_EDGE       = "VALID_EDGE_EVENT"
LEFS_SHADOW_VALID     = "SHADOW_VALID_EVENT"
LEFS_ROTATIONAL       = "ROTATIONAL_IGNORE"
LEFS_LIQUIDATION      = "LIQUIDATION_INVALID"
LEFS_INSUFFICIENT     = "INSUFFICIENT_DATA"

# ECL outputs
ECL_CONFIRMED         = "CONFIRMED_EDGE"
ECL_PROBABLE          = "PROBABLE_EDGE"
ECL_NO_EDGE           = "NO_EDGE"
ECL_OVERFITTED        = "OVERFITTED_EDGE"

# LIVE GATE decisions
GATE_APPROVED         = "APPROVED"
GATE_SHADOW_ONLY      = "SHADOW_ONLY"
GATE_REJECTED         = "REJECTED"

# DECISION STABILITY
STAB_HIGH             = "HIGH"
STAB_MEDIUM           = "MEDIUM"
STAB_LOW              = "LOW"


# ── INPUT DATACLASS ───────────────────────────────────────────────

@dataclass
class UEIInput:
    """
    Todos los inputs necesarios para el ciclo completo de decisión.
    Derive estos valores del output de replay_debug_v3.py o en vivo.
    """
    # ── Identidad ─────────────────────────────
    session_date:          str   = ""       # YYYY-MM-DD
    bar_evaluated:         int   = 0        # barra actual

    # ── LEFS inputs ───────────────────────────
    pre_open_complete:     bool  = False    # grabación desde 06:30 CR
    ets_score:             int   = 0        # ETS score actual (0-100)
    hb_rate:               float = 0.0     # HB contamination %
    gap_size_pts:          float = 0.0     # gap overnight en puntos
    is_macro_event:        bool  = False    # CPI / FOMC / NFP
    early_bar_efficiency:  int   = 0        # avg eff barras 1-20
    etil_active:           bool  = False    # ETIL detectado
    liquidity_displacement:int   = 0        # RT score al momento
    env_regime:            str   = "ROTATIONAL"  # env del bar
    volatility_regime:     str   = "NORMAL"      # HIGH/NORMAL/LOW

    # ── ECL inputs ────────────────────────────
    multi_regime_survival: bool  = False    # sobrevivió stress battery
    stress_survival_pct:   float = 0.0     # % sesiones stress superadas
    synthetic_real_delta:  float = 0.0     # diferencia ETS sintético vs real
    etil_lag_bars:         int   = 999      # barras entre ETIL y ET
    gtal_rejection_count:  int   = 0        # rechazos GTAL en sesión
    gtal_valid_count:      int   = 0        # validaciones GTAL en sesión
    conf_score:            int   = 0        # confluence score actual
    ets_active_bars:       int   = 0        # barras con ETS>=65
    hindsight_rate_pct:    float = 0.0     # % barras con hindsight

    # ── LCL inputs ────────────────────────────
    esi_tracker:           float = 0.0     # ESI del drift tracker
    regime_change_count:   int   = 0        # cambios de régimen en sesión
    starvation_score:      float = 50.0    # signal starvation score
    treadmill_cycles:      int   = 0        # ciclos de treadmill completados
    go_live_score:         float = 0.0     # score del replay treadmill


# ── OUTPUT DATACLASSES ────────────────────────────────────────────

@dataclass
class LEFSResult:
    classification:  str   = LEFS_INSUFFICIENT
    score:           int   = 0            # 0-100
    hard_rejections: List  = field(default_factory=list)
    soft_warnings:   List  = field(default_factory=list)
    reasoning:       str   = ""


@dataclass
class ECLResult:
    classification:     str   = ECL_NO_EDGE
    edge_strength:      int   = 0         # 0-100
    esi:                float = 0.0       # Edge Stability Index
    overfitting_risk:   int   = 0         # 0-100 (bajo = bueno)
    regime_consistency: int   = 0         # 0-100
    reasoning:          str   = ""


@dataclass
class LiveGateResult:
    live_simulation_approved:  bool  = False
    edge_usable_for_treadmill: bool  = False
    forward_test_ready:        bool  = False
    system_confidence_score:   int   = 0
    decision:                  str   = GATE_REJECTED
    hard_blocks:               List  = field(default_factory=list)
    reasoning:                 str   = ""


@dataclass
class LCLResult:
    live_confidence_score:    int   = 0   # 0-100
    decision_stability:       str   = STAB_LOW
    edge_execution_confidence:int   = 0   # 0-100
    regime_resilience_score:  int   = 0   # 0-100
    final_system_confidence:  int   = 0   # 0-100
    shadow_only_required:     bool  = True
    reasoning:                str   = ""


@dataclass
class UEIResult:
    """Output unificado del sistema completo."""
    # Metadata
    session_date:          str  = ""
    evaluated_at:          str  = ""
    bar_evaluated:         int  = 0

    # Capas
    lefs:                  LEFSResult   = field(default_factory=LEFSResult)
    ecl:                   ECLResult    = field(default_factory=ECLResult)
    gate:                  LiveGateResult= field(default_factory=LiveGateResult)
    lcl:                   LCLResult    = field(default_factory=LCLResult)

    # Scores finales
    edge_score_final:      int   = 0    # 0-100
    esi_final:             float = 0.0
    overfitting_risk:      int   = 0    # 0-100

    # Decisión final
    go_nogo:               str   = "NO-GO"   # GO / SHADOW / NO-GO
    go_nogo_reason:        str   = ""

    # Reasoning chain completo
    reasoning_chain:       List  = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "session_date":      self.session_date,
            "evaluated_at":      self.evaluated_at,
            "bar_evaluated":     self.bar_evaluated,
            "lefs_classification":  self.lefs.classification,
            "lefs_score":           self.lefs.score,
            "ecl_classification":   self.ecl.classification,
            "edge_strength":        self.ecl.edge_strength,
            "esi_final":            round(self.esi_final, 1),
            "overfitting_risk":     self.overfitting_risk,
            "regime_consistency":   self.ecl.regime_consistency,
            "live_sim_approved":    self.gate.live_simulation_approved,
            "edge_treadmill":       self.gate.edge_usable_for_treadmill,
            "forward_test_ready":   self.gate.forward_test_ready,
            "live_confidence":      self.lcl.live_confidence_score,
            "decision_stability":   self.lcl.decision_stability,
            "final_confidence":     self.lcl.final_system_confidence,
            "edge_score_final":     self.edge_score_final,
            "go_nogo":              self.go_nogo,
            "go_nogo_reason":       self.go_nogo_reason,
            "reasoning_chain":      self.reasoning_chain,
        }

    def __str__(self) -> str:
        lines = [
            "",
            "=" * 72,
            "  GIBBZ UEI CORE — UNIFIED EDGE INTELLIGENCE REPORT",
            f"  Session: {self.session_date}  |  Bar: {self.bar_evaluated}  |  {self.evaluated_at[:19]}",
            "─" * 72,
            f"  CAPA 1 — LEFS:  {self.lefs.classification:<28}  score={self.lefs.score:3d}",
        ]
        if self.lefs.hard_rejections:
            for r in self.lefs.hard_rejections:
                lines.append(f"    ✗ {r}")
        if self.lefs.soft_warnings:
            for w in self.lefs.soft_warnings:
                lines.append(f"    ⚠ {w}")

        lines += [
            f"  CAPA 2 — ECL:   {self.ecl.classification:<28}  strength={self.ecl.edge_strength:3d}",
            f"    ESI={self.ecl.esi:.1f}  OFit={self.ecl.overfitting_risk}  RegCons={self.ecl.regime_consistency}",
            f"  CAPA 3 — GATE:  {self.gate.decision:<28}  sys_conf={self.gate.system_confidence_score:3d}",
        ]
        if self.gate.hard_blocks:
            for b in self.gate.hard_blocks:
                lines.append(f"    ✗ {b}")

        lines += [
            f"  CAPA 4 — LCL:   {self.lcl.decision_stability:<28}  live_conf={self.lcl.live_confidence_score:3d}",
            f"    exe_conf={self.lcl.edge_execution_confidence}  reg_res={self.lcl.regime_resilience_score}  "
            f"final_conf={self.lcl.final_system_confidence}",
            "─" * 72,
            f"  EDGE SCORE FINAL:        {self.edge_score_final:3d} / 100",
            f"  ESI FINAL:               {self.esi_final:.1f} / 100",
            f"  OVERFITTING RISK:        {self.overfitting_risk:3d} / 100  "
            f"({'LOW' if self.overfitting_risk < 30 else 'MED' if self.overfitting_risk < 60 else 'HIGH'})",
            f"  LIVE SIMULATION:         {'✅ APPROVED' if self.gate.live_simulation_approved else '❌ REJECTED'}",
            f"  TREADMILL USABLE:        {'✅ YES' if self.gate.edge_usable_for_treadmill else '❌ NO'}",
            f"  FORWARD TEST READY:      {'✅ YES' if self.gate.forward_test_ready else '❌ NO'}",
            f"  SHADOW ONLY REQUIRED:    {'⚠ YES' if self.lcl.shadow_only_required else '✅ NO'}",
            "═" * 72,
            f"  DECISIÓN FINAL:   {self.go_nogo}",
            f"  RAZÓN:            {self.go_nogo_reason}",
            "─" * 72,
            "  REASONING CHAIN:",
        ]
        for i, step in enumerate(self.reasoning_chain, 1):
            lines.append(f"    [{i:2d}] {step}")
        lines.append("=" * 72)
        return "\n".join(lines)


# ── CAPA 1: LEFS ─────────────────────────────────────────────────

class LiveEventFilterSystem:
    """
    Capa 1 — Filtrar si un evento de mercado es válido
    para análisis institucional.
    """

    # HARD REJECTION thresholds
    MIN_ETS_MACRO    = 50    # ETS mínimo en macro events (WATCH level)
    MAX_HB_RATE      = 0.20  # máx HB contamination
    MIN_EARLY_EFF    = 25    # eficiencia mínima en barras 1-20
    MIN_GAP_FOR_MACRO= 10.0  # gap mínimo para macro event clásico

    def evaluate(self, inp: UEIInput) -> LEFSResult:
        r = LEFSResult()
        hard  = []
        soft  = []
        score = 100

        # ── HARD REJECTIONS ───────────────────────────────────────

        # 1. Pre-open incompleto
        if not inp.pre_open_complete:
            hard.append("pre-open incompleto — grabación no comenzó a 06:30 CR")
            score -= 40

        # 2. ETS insuficiente en macro events
        if inp.is_macro_event and inp.ets_score < self.MIN_ETS_MACRO:
            hard.append(
                f"ETS={inp.ets_score} < {self.MIN_ETS_MACRO} en macro event — "
                f"señal institucional insuficiente")
            score -= 30

        # 3. HB contamination alta
        if inp.hb_rate > self.MAX_HB_RATE:
            hard.append(
                f"HB={inp.hb_rate:.0%} > {self.MAX_HB_RATE:.0%} — "
                f"contaminación severa")
            score -= 25

        # 4. Sin expansión en primeros 20 bars
        if inp.early_bar_efficiency < self.MIN_EARLY_EFF:
            hard.append(
                f"early_eff={inp.early_bar_efficiency} < {self.MIN_EARLY_EFF} — "
                f"sin expansión institucional en apertura")
            score -= 20

        # 5. ROTATIONAL sin catalyst
        is_rotational = (inp.env_regime in ("ROTATIONAL", "CHOPPY")
                         and not inp.is_macro_event
                         and inp.gap_size_pts < 10
                         and inp.ets_score < 50)
        if is_rotational:
            hard.append(
                "régimen ROTATIONAL sin catalyst macro ni gap — "
                "sesión de baja calidad institucional")
            score -= 20

        # 6. LIQUIDATION en apertura
        is_liquidation = (inp.env_regime == "EFFICIENT_TREND"
                          and inp.early_bar_efficiency > 70
                          and inp.ets_score < 40
                          and inp.gap_size_pts > 20)
        if is_liquidation:
            hard.append(
                "EFFICIENT_TREND con drop > 20pts sin ETS — "
                "evento de liquidación, no expansión")
            score -= 35

        # ── SOFT WARNINGS ─────────────────────────────────────────

        if not inp.etil_active and inp.ets_score >= 50:
            soft.append("ETS activo pero ETIL no confirmado")
            score -= 5

        if inp.liquidity_displacement < 30 and inp.ets_score >= 50:
            soft.append(
                f"RT={inp.liquidity_displacement} bajo para ETS={inp.ets_score}")
            score -= 5

        if inp.volatility_regime == "LOW":
            soft.append("volatilidad baja — edge episódico menos probable")
            score -= 5

        score = max(0, min(100, score))
        r.score = score

        # ── CLASIFICACIÓN FINAL ───────────────────────────────────

        if is_liquidation:
            r.classification = LEFS_LIQUIDATION
        elif len(hard) == 0 and score >= 75:
            r.classification = LEFS_VALID_EDGE
        elif len(hard) <= 1 and score >= 50:
            r.classification = LEFS_SHADOW_VALID
        elif is_rotational or score < 30:
            r.classification = LEFS_ROTATIONAL
        elif not inp.pre_open_complete or inp.ets_score == 0:
            r.classification = LEFS_INSUFFICIENT
        else:
            r.classification = LEFS_ROTATIONAL

        r.hard_rejections = hard
        r.soft_warnings   = soft
        r.reasoning       = (
            f"LEFS: {r.classification} | score={score} | "
            f"hard={len(hard)} soft={len(soft)}")
        return r


# ── CAPA 2: ECL ──────────────────────────────────────────────────

class EdgeConfirmationLayer:
    """
    Capa 2 — Validar si el edge es real, falso o sobreajustado.
    """

    def evaluate(self, inp: UEIInput, lefs: LEFSResult) -> ECLResult:
        r = ECLResult()

        # ── EDGE STRENGTH SCORE ───────────────────────────────────
        strength = 0

        # ETS contribuye hasta 35 pts
        strength += min(35, inp.ets_score * 35 // 100)

        # ETIL activo +15
        if inp.etil_active:
            strength += 15

        # GTAL validation +20, rejection pattern -10
        if inp.gtal_valid_count > 0:
            strength += 20
        elif inp.gtal_rejection_count > 3:
            strength -= 10

        # Confluence score contribuye hasta 10 pts
        strength += min(10, inp.conf_score * 10 // 100)

        # Pre-open completo +10
        if inp.pre_open_complete:
            strength += 10

        # Stress survival +10
        if inp.stress_survival_pct >= 0.80:
            strength += 10
        elif inp.stress_survival_pct >= 0.60:
            strength += 5

        strength = max(0, min(100, strength))

        # ── EDGE STABILITY INDEX ──────────────────────────────────
        # ESI: estabilidad del edge a través del tiempo
        esi = inp.esi_tracker  # del drift tracker real

        if esi == 0.0:
            # Calcular desde inputs si no viene del tracker
            ets_ratio   = inp.ets_score / 100
            etil_bonus  = 15 if inp.etil_active else 0
            # Limitar penalidad de lag a máximo 20 pts
            lag_penalty = min(20, max(0, (inp.etil_lag_bars - 50) / 20))
            esi = max(0.0, min(100.0,
                               ets_ratio * 60 + etil_bonus - lag_penalty))

        # ── OVERFITTING RISK ──────────────────────────────────────
        # 0-100, donde 0 = sin overfitting
        of_risk = 0

        # Sintético demasiado mejor que real
        if inp.synthetic_real_delta > 15:
            of_risk += 30
        elif inp.synthetic_real_delta > 8:
            of_risk += 15

        # Hindsight rate alta
        if inp.hindsight_rate_pct > 5.0:
            of_risk += 20
        elif inp.hindsight_rate_pct > 2.5:
            of_risk += 10

        # Sin GTAL validation nunca
        if inp.gtal_valid_count == 0 and inp.ets_active_bars == 0:
            of_risk += 15

        # Multi-regime survival no probado
        if not inp.multi_regime_survival:
            of_risk += 15

        of_risk = max(0, min(100, of_risk))

        # ── REGIME CONSISTENCY ────────────────────────────────────
        # Cuán consistente es el edge entre regímenes
        reg_cons = 100
        if inp.regime_change_count > 50:
            reg_cons -= 20
        elif inp.regime_change_count > 30:
            reg_cons -= 10

        if inp.starvation_score > 85:
            reg_cons -= 20
        elif inp.starvation_score > 70:
            reg_cons -= 10

        reg_cons = max(0, min(100, reg_cons))

        # ── CLASIFICACIÓN ─────────────────────────────────────────
        if lefs.classification == LEFS_ROTATIONAL:
            classification = ECL_NO_EDGE
        elif of_risk >= 60:
            classification = ECL_OVERFITTED
        elif (strength >= 65 and esi >= 60
              and of_risk < 30 and inp.gtal_valid_count > 0):
            classification = ECL_CONFIRMED
        elif strength >= 45 and esi >= 40 and of_risk < 50:
            classification = ECL_PROBABLE
        else:
            classification = ECL_NO_EDGE

        r.classification     = classification
        r.edge_strength      = strength
        r.esi                = round(esi, 1)
        r.overfitting_risk   = of_risk
        r.regime_consistency = reg_cons
        r.reasoning = (
            f"ECL: {classification} | strength={strength} esi={esi:.1f} "
            f"ofRisk={of_risk} regCons={reg_cons}")
        return r


# ── CAPA 3: LIVE GATE ────────────────────────────────────────────

class LiveEdgeDecisionGate:
    """
    Capa 3 — Decidir si el evento entra al sistema operativo.
    """

    MIN_ESI_FOR_LIVE = 60
    MIN_STRENGTH_FWD = 55
    MIN_TREADMILL_CYCLES = 20

    def evaluate(self, inp: UEIInput,
                  lefs: LEFSResult,
                  ecl: ECLResult) -> LiveGateResult:
        r = LiveGateResult()
        blocks = []
        sys_confidence = 100

        # ── HARD BLOCKS ───────────────────────────────────────────

        # 1. LEFS rechazado
        if lefs.classification in (LEFS_ROTATIONAL, LEFS_LIQUIDATION,
                                    LEFS_INSUFFICIENT):
            blocks.append(
                f"LEFS={lefs.classification} — evento rechazado en Capa 1")
            sys_confidence -= 40

        # 2. ECL no edge o sobreajustado
        if ecl.classification in (ECL_NO_EDGE, ECL_OVERFITTED):
            blocks.append(
                f"ECL={ecl.classification} — edge no confirmado")
            sys_confidence -= 35

        # 3. ESI bajo
        if ecl.esi < self.MIN_ESI_FOR_LIVE:
            blocks.append(
                f"ESI={ecl.esi:.1f} < {self.MIN_ESI_FOR_LIVE} — "
                f"edge inestable para live")
            sys_confidence -= 20

        # 4. ETIL inactivo en macro event
        if inp.is_macro_event and not inp.etil_active:
            blocks.append(
                "macro event sin ETIL activo — señal institucional ausente")
            sys_confidence -= 20

        # 5. Inconsistencia estructural (precios incorrectos)
        if (inp.session_date and inp.ets_score == 0
                and inp.pre_open_complete):
            blocks.append(
                "contexto histórico posiblemente incorrecto — "
                "verificar POC/VAH/VAL del día")
            sys_confidence -= 15

        sys_confidence = max(0, min(100, sys_confidence))
        r.system_confidence_score = sys_confidence
        r.hard_blocks             = blocks

        # ── DECISIONS ─────────────────────────────────────────────

        # Live simulation: sin hard blocks, ECL confirmed o probable, ESI ok
        r.live_simulation_approved = (
            len(blocks) == 0
            and ecl.classification in (ECL_CONFIRMED, ECL_PROBABLE)
            and ecl.esi >= self.MIN_ESI_FOR_LIVE
            and inp.etil_active
        )

        # Treadmill: siempre usable si no es liquidación o rotacional puro
        r.edge_usable_for_treadmill = (
            lefs.classification not in (LEFS_ROTATIONAL, LEFS_LIQUIDATION)
            and ecl.classification != ECL_OVERFITTED
            and inp.treadmill_cycles >= self.MIN_TREADMILL_CYCLES
        )

        # Forward test: edge fuerte y ESI alto
        r.forward_test_ready = (
            ecl.edge_strength >= self.MIN_STRENGTH_FWD
            and ecl.esi >= 55
            and ecl.classification in (ECL_CONFIRMED, ECL_PROBABLE)
            and lefs.classification in (LEFS_VALID_EDGE, LEFS_SHADOW_VALID)
        )

        # Decisión de gate
        if r.live_simulation_approved:
            r.decision = GATE_APPROVED
        elif (r.forward_test_ready or
              lefs.classification == LEFS_SHADOW_VALID):
            r.decision = GATE_SHADOW_ONLY
        else:
            r.decision = GATE_REJECTED

        r.reasoning = (
            f"GATE: {r.decision} | live={r.live_simulation_approved} "
            f"treadmill={r.edge_usable_for_treadmill} "
            f"fwd={r.forward_test_ready} blocks={len(blocks)}")
        return r


# ── CAPA 4: LCL ──────────────────────────────────────────────────

class LiveConfidenceLayer:
    """
    Capa 4 — Evaluar confiabilidad institucional de toda la cadena.
    No clasifica eventos. Evalúa estabilidad probabilística
    bajo incertidumbre real.
    """

    CONFIDENCE_THRESHOLD = 70  # mínimo para live simulation

    def evaluate(self, inp: UEIInput,
                  lefs: LEFSResult,
                  ecl: ECLResult,
                  gate: LiveGateResult) -> LCLResult:
        r = LCLResult()

        # ── LIVE CONFIDENCE SCORE ─────────────────────────────────
        lcs = 100

        # LEFS quality
        lefs_map = {
            LEFS_VALID_EDGE:    0,
            LEFS_SHADOW_VALID: -10,
            LEFS_ROTATIONAL:   -40,
            LEFS_LIQUIDATION:  -50,
            LEFS_INSUFFICIENT: -30,
        }
        lcs += lefs_map.get(lefs.classification, -30)

        # ECL quality
        ecl_map = {
            ECL_CONFIRMED:  0,
            ECL_PROBABLE:  -10,
            ECL_NO_EDGE:   -40,
            ECL_OVERFITTED:-50,
        }
        lcs += ecl_map.get(ecl.classification, -30)

        # ESI
        if ecl.esi >= 80:    pass
        elif ecl.esi >= 60:  lcs -= 5
        elif ecl.esi >= 40:  lcs -= 15
        else:                lcs -= 30

        # ETIL confianza
        if inp.etil_active:
            lag_ok = inp.etil_lag_bars < 100
            if lag_ok:  lcs += 5
            else:       lcs -= 10
        else:
            lcs -= 15

        # GTAL pattern
        if inp.gtal_valid_count > 0:
            lcs += 10
        elif inp.gtal_rejection_count > 5:
            lcs -= 10

        # Stress survival
        if inp.stress_survival_pct >= 0.80:   lcs += 5
        elif inp.stress_survival_pct >= 0.60:  pass
        else:                                  lcs -= 10

        # Synthetic vs real divergence
        if inp.synthetic_real_delta <= 5:      lcs += 5
        elif inp.synthetic_real_delta <= 15:   pass
        else:                                  lcs -= 15

        # Treadmill cycles completados
        if inp.treadmill_cycles >= 50:    lcs += 5
        elif inp.treadmill_cycles >= 20:  pass
        else:                             lcs -= 10

        # Go live score del treadmill
        if inp.go_live_score >= 80:    lcs += 5
        elif inp.go_live_score >= 60:  pass
        else:                          lcs -= 10

        lcs = max(0, min(100, lcs))
        r.live_confidence_score = lcs

        # ── DECISION STABILITY ────────────────────────────────────
        # ¿Cuán estable es la decisión bajo variaciones?
        stability_score = 0

        # Consistencia entre capas
        if (lefs.classification in (LEFS_VALID_EDGE, LEFS_SHADOW_VALID)
                and ecl.classification in (ECL_CONFIRMED, ECL_PROBABLE)):
            stability_score += 40

        if gate.live_simulation_approved == gate.forward_test_ready:
            stability_score += 20  # sin contradicción

        if ecl.regime_consistency >= 80:
            stability_score += 20
        elif ecl.regime_consistency >= 60:
            stability_score += 10

        if inp.regime_change_count < 30:
            stability_score += 20

        stability_score = max(0, min(100, stability_score))

        if stability_score >= 70:     r.decision_stability = STAB_HIGH
        elif stability_score >= 45:   r.decision_stability = STAB_MEDIUM
        else:                         r.decision_stability = STAB_LOW

        # ── EDGE EXECUTION CONFIDENCE ─────────────────────────────
        # ¿Cuán confiable es ejecutar basado en esta señal?
        eec = 0
        if ecl.classification == ECL_CONFIRMED:    eec += 50
        elif ecl.classification == ECL_PROBABLE:   eec += 30
        if inp.etil_active and inp.etil_lag_bars < 100: eec += 20
        if inp.gtal_valid_count > 0:               eec += 20
        if lefs.score >= 75:                       eec += 10
        eec = max(0, min(100, eec))
        r.edge_execution_confidence = eec

        # ── REGIME RESILIENCE ─────────────────────────────────────
        # ¿El edge sobrevive cambios de régimen?
        rrs = ecl.regime_consistency
        if inp.multi_regime_survival:    rrs = min(100, rrs + 15)
        if inp.stress_survival_pct >= 0.80: rrs = min(100, rrs + 10)
        if inp.starvation_score > 85:    rrs = max(0, rrs - 15)
        r.regime_resilience_score = rrs

        # ── FINAL SYSTEM CONFIDENCE ───────────────────────────────
        fsc = (
            lcs * 0.40
            + stability_score * 0.20
            + eec * 0.20
            + rrs * 0.20
        )
        r.final_system_confidence = round(fsc)

        # ── SHADOW ONLY REQUIRED ──────────────────────────────────
        r.shadow_only_required = lcs < self.CONFIDENCE_THRESHOLD

        r.reasoning = (
            f"LCL: lcs={lcs} stability={r.decision_stability} "
            f"eec={eec} rrs={rrs} final={r.final_system_confidence} "
            f"shadow={'YES' if r.shadow_only_required else 'NO'}")
        return r


# ── CORE ORCHESTRATOR ─────────────────────────────────────────────

class UEICore:
    """
    Orchestrador principal del Unified Edge Intelligence Core.
    Ejecuta las 4 capas en secuencia y produce el resultado unificado.
    """

    def __init__(self):
        self.lefs_layer = LiveEventFilterSystem()
        self.ecl_layer  = EdgeConfirmationLayer()
        self.gate_layer = LiveEdgeDecisionGate()
        self.lcl_layer  = LiveConfidenceLayer()

    def evaluate(self, inp: UEIInput) -> UEIResult:
        """Ejecuta el ciclo completo de decisión."""
        result = UEIResult(
            session_date  = inp.session_date,
            evaluated_at  = datetime.now().isoformat(),
            bar_evaluated = inp.bar_evaluated,
        )

        chain = []

        # ── CAPA 1: LEFS ──────────────────────────────────────────
        lefs = self.lefs_layer.evaluate(inp)
        result.lefs = lefs
        chain.append(lefs.reasoning)

        # ── CAPA 2: ECL ───────────────────────────────────────────
        ecl = self.ecl_layer.evaluate(inp, lefs)
        result.ecl = ecl
        chain.append(ecl.reasoning)

        # ── CAPA 3: LIVE GATE ─────────────────────────────────────
        gate = self.gate_layer.evaluate(inp, lefs, ecl)
        result.gate = gate
        chain.append(gate.reasoning)

        # ── CAPA 4: LCL ───────────────────────────────────────────
        lcl = self.lcl_layer.evaluate(inp, lefs, ecl, gate)
        result.lcl = lcl
        chain.append(lcl.reasoning)

        # ── SCORES FINALES ────────────────────────────────────────
        result.edge_score_final = round(
            ecl.edge_strength * 0.40
            + lefs.score * 0.20
            + lcl.final_system_confidence * 0.25
            + (100 - ecl.overfitting_risk) * 0.15
        )
        result.esi_final        = ecl.esi
        result.overfitting_risk = ecl.overfitting_risk

        # ── DECISIÓN FINAL GO / SHADOW / NO-GO ───────────────────
        if (gate.live_simulation_approved
                and lcl.live_confidence_score >= 70
                and not lcl.shadow_only_required):
            result.go_nogo = "GO"
            result.go_nogo_reason = (
                f"LEFS={lefs.classification} / ECL={ecl.classification} / "
                f"GATE=APPROVED / LCS={lcl.live_confidence_score}")

        elif (gate.decision == GATE_SHADOW_ONLY
              or (lcl.live_confidence_score >= 50
                  and lefs.classification != LEFS_ROTATIONAL)):
            result.go_nogo = "SHADOW"
            result.go_nogo_reason = (
                f"Edge presente pero LCS={lcl.live_confidence_score} < 70 "
                f"— solo shadow tracking permitido")

        else:
            result.go_nogo = "NO-GO"
            result.go_nogo_reason = (
                f"LEFS={lefs.classification} / ECL={ecl.classification} / "
                f"LCS={lcl.live_confidence_score} — sin condiciones")

        result.reasoning_chain = chain
        return result

    def evaluate_from_outcome(self, outcome_path: str,
                               extra: dict = None) -> UEIResult:
        """
        Construye UEIInput desde un archivo de observation guardado
        por el outcome_engine, y evalúa.
        """
        if not os.path.exists(outcome_path):
            raise FileNotFoundError(f"Outcome file not found: {outcome_path}")

        with open(outcome_path, encoding="utf-8") as f:
            obs = json.load(f)

        extra = extra or {}

        inp = UEIInput(
            session_date       = obs.get("session_date", ""),
            bar_evaluated      = obs.get("total_bars", 0),
            pre_open_complete  = extra.get("pre_open_complete", False),
            ets_score          = obs.get("max_ets", 0),
            hb_rate            = extra.get("hb_rate", 0.08),
            gap_size_pts       = extra.get("gap_size_pts", 0.0),
            is_macro_event     = extra.get("is_macro_event", False),
            early_bar_efficiency = extra.get("early_bar_efficiency", 20),
            etil_active        = obs.get("efficient_trend_bars", 0) > 0,
            liquidity_displacement = obs.get("max_rt", 0),
            env_regime         = obs.get("dominant_regime", "ROTATIONAL"),
            volatility_regime  = extra.get("volatility_regime", "NORMAL"),
            multi_regime_survival = extra.get("multi_regime_survival", False),
            stress_survival_pct = extra.get("stress_survival_pct", 0.80),
            synthetic_real_delta = extra.get("synthetic_real_delta", 10.0),
            etil_lag_bars      = extra.get("etil_lag_bars", 999),
            gtal_rejection_count = obs.get("gtal_rejection_count", 0),
            gtal_valid_count   = obs.get("gtal_valid_bars", 0),
            conf_score         = obs.get("max_conf", 0),
            ets_active_bars    = obs.get("ets_active_bars", 0),
            hindsight_rate_pct = obs.get("hindsight_rate_pct", 0.0),
            esi_tracker        = extra.get("esi_tracker", 0.0),
            regime_change_count = obs.get("regime_changes", 0),
            starvation_score   = obs.get("signal_starvation_score", 60.0),
            treadmill_cycles   = extra.get("treadmill_cycles", 50),
            go_live_score      = extra.get("go_live_score", 84.1),
        )

        return self.evaluate(inp)


# ── CLI ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="GIBBZ UEI Core — Unified Edge Intelligence")
    parser.add_argument("--demo",    action="store_true",
                        help="Correr demo con 3 escenarios")
    parser.add_argument("--session", type=str, default="",
                        help="Evaluar desde outcomes/YYYY-MM-DD_observation.json")
    parser.add_argument("--macro",   action="store_true",
                        help="Marcar como macro event")
    parser.add_argument("--preopen", action="store_true",
                        help="Pre-open completo disponible")
    args = parser.parse_args()

    core = UEICore()

    if args.session:
        path = os.path.join("outcomes",
                            f"{args.session}_observation.json")
        try:
            result = core.evaluate_from_outcome(path, extra={
                "pre_open_complete":  args.preopen,
                "is_macro_event":     args.macro,
                "early_bar_efficiency": 25,
                "hb_rate":            0.08,
                "stress_survival_pct":0.83,
                "treadmill_cycles":   50,
                "go_live_score":      84.1,
                "synthetic_real_delta": 10.0,
            })
            print(result)
        except FileNotFoundError as e:
            print(f"\n  Error: {e}")
            print(f"  Verifica que existe outcomes/{args.session}_observation.json")

    elif args.demo:
        print("\n  DEMO — 3 escenarios institucionales\n")

        scenarios = [
            # Escenario 1: sesión ELITE perfecta
            ("ELITE OPENING DRIVE", UEIInput(
                session_date="2026-02-02", bar_evaluated=15,
                pre_open_complete=True, ets_score=75,
                hb_rate=0.08, gap_size_pts=35.0,
                is_macro_event=True, early_bar_efficiency=65,
                etil_active=True, liquidity_displacement=73,
                env_regime="EFFICIENT_TREND",
                multi_regime_survival=True,
                stress_survival_pct=0.83,
                synthetic_real_delta=5.0,
                etil_lag_bars=0, gtal_valid_count=1,
                gtal_rejection_count=0, conf_score=79,
                ets_active_bars=5, hindsight_rate_pct=2.2,
                esi_tracker=74.6, regime_change_count=15,
                starvation_score=40.0, treadmill_cycles=50,
                go_live_score=84.1,
            )),
            # Escenario 2: near-shadow con warmup tardío
            ("NEAR-SHADOW LATE WARMUP", UEIInput(
                session_date="2025-05-30", bar_evaluated=310,
                pre_open_complete=False, ets_score=42,
                hb_rate=0.05, gap_size_pts=5.0,
                is_macro_event=False, early_bar_efficiency=15,
                etil_active=False, liquidity_displacement=30,
                env_regime="ROTATIONAL",
                multi_regime_survival=True,
                stress_survival_pct=0.83,
                synthetic_real_delta=10.0,
                etil_lag_bars=304, gtal_valid_count=0,
                gtal_rejection_count=2, conf_score=50,
                ets_active_bars=0, hindsight_rate_pct=2.2,
                esi_tracker=74.6, regime_change_count=47,
                starvation_score=90.0, treadmill_cycles=50,
                go_live_score=84.1,
            )),
            # Escenario 3: rotacional puro — no trade
            ("ROTATIONAL NO TRADE", UEIInput(
                session_date="2026-01-28", bar_evaluated=401,
                pre_open_complete=False, ets_score=12,
                hb_rate=0.07, gap_size_pts=2.0,
                is_macro_event=False, early_bar_efficiency=10,
                etil_active=False, liquidity_displacement=15,
                env_regime="CHOPPY",
                multi_regime_survival=True,
                stress_survival_pct=0.83,
                synthetic_real_delta=12.0,
                etil_lag_bars=999, gtal_valid_count=0,
                gtal_rejection_count=0, conf_score=20,
                ets_active_bars=0, hindsight_rate_pct=4.5,
                esi_tracker=74.6, regime_change_count=30,
                starvation_score=85.0, treadmill_cycles=50,
                go_live_score=84.1,
            )),
        ]

        for name, inp in scenarios:
            print(f"\n  ── ESCENARIO: {name} ──")
            result = core.evaluate(inp)
            print(result)