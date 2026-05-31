# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ V3 — outcome_engine.py
#  Outcome Observation Framework v1.0
#
#  FASE: OBSERVATIONAL MODE
#  OBJETIVO: descubrir estadísticamente dónde existe
#            (o no existe) el edge institucional.
#
#  NO mide profitability todavía.
#  NO optimiza thresholds.
#  NO modifica nada del core.
#
#  Registra TODAS las oportunidades detectadas:
#  A/B/C setups, GTAL_VALID, ETS clusters,
#  ACG activations, MICRO_OR events.
#
#  Responde:
#  - ¿Con qué frecuencia aparece EFFICIENT_TREND?
#  - ¿En qué regímenes aparecen A-setups?
#  - ¿GTAL filtra correctamente o hay signal starvation?
#  - ¿El edge vive en RTH, overnight o opening drive?
#  - ¿El sistema es correctamente selectivo o excesivamente restrictivo?
#
#  OUTPUT: outcomes/YYYY-MM-DD_observation.json
# ╚══════════════════════════════════════════════════════════════════╝

import json
import os
from dataclasses import dataclass, field, asdict
from collections import defaultdict
from typing import List, Dict, Optional


# ── CONSTANTES ────────────────────────────────────────────────────
OUTCOMES_DIR = "outcomes"


# ── BAR OBSERVATION ───────────────────────────────────────────────

@dataclass
class BarObservation:
    """Snapshot de una barra relevante para el análisis."""
    bar:            int   = 0
    price:          float = 0.0
    env:            str   = "ROTATIONAL"
    eff:            int   = 0
    trap:           int   = 0
    ets:            int   = 0
    ets_class:      str   = "NOISE"
    conf:           int   = 0
    cont:           int   = 0
    opp_grade:      str   = "NONE"
    rt_score:       int   = 0
    hb_flag:        bool  = False
    ev:             str   = "INVALID"
    esl_score:      int   = 0
    fill:           str   = "LOW"
    slip:           float = 0.0
    edge_eff:       int   = 0
    prs:            int   = 0
    stability:      int   = 0
    frag_idx:       int   = 0
    congestion:     str   = "NONE"
    micro_or:       bool  = False
    acg_relaxed:    bool  = False
    acg_mode:       str   = "STANDARD"
    filter_quality: str   = "OK"
    # Bloqueadores específicos
    blocked_by_conf:      bool = False
    blocked_by_cont:      bool = False
    blocked_by_hb:        bool = False
    blocked_by_validator: bool = False
    block_reason:         str  = ""


# ── SESSION OBSERVATION ───────────────────────────────────────────

@dataclass
class SessionObservation:
    """Observación completa de una sesión."""
    session_date:       str   = ""
    total_bars:         int   = 0

    # Frequency metrics
    efficient_trend_bars:   int = 0
    ets_cluster_bars:       int = 0   # barras con ETS >= 65
    gtal_valid_bars:        int = 0
    a_setup_bars:           int = 0
    b_setup_bars:           int = 0
    c_setup_bars:           int = 0
    micro_or_bars:          int = 0
    acg_activation_bars:    int = 0
    acg_would_change:       int = 0

    # Starvation metrics
    conf_block_count:       int = 0   # bloqueados por conf < threshold
    cont_block_count:       int = 0   # bloqueados por cont < threshold
    hb_block_count:         int = 0   # bloqueados por HB=True
    validator_block_count:  int = 0   # bloqueados por validator (score bajo)
    gtal_rejection_count:   int = 0   # GTAL=INVALID cuando OPP != NONE

    # Regime analytics
    regime_distribution:    Dict = field(default_factory=dict)
    max_ets:                int = 0
    max_conf:               int = 0
    max_rt:                 int = 0
    avg_ets:                float = 0.0
    avg_conf:               float = 0.0
    avg_prs:                float = 0.0

    # Filter analytics
    over_rejection_bars:    int = 0
    under_filtering_bars:   int = 0
    ets_drift_events:       int = 0   # barras con ETS_D MED/HIGH

    # Notable events (barras específicas)
    notable_bars:           List = field(default_factory=list)

    # Summary scores
    signal_starvation_score:    float = 0.0   # 0-100, alto = escasez severa
    edge_opportunity_score:     float = 0.0   # 0-100, alto = mucha oportunidad
    regime_quality_score:       float = 0.0   # 0-100, alto = régimen favorable

    def to_dict(self) -> dict:
        return asdict(self)


# ── OUTCOME ENGINE ────────────────────────────────────────────────

class OutcomeEngine:
    """
    Outcome Observation Framework v1.0

    Registra y analiza estadísticamente TODAS las oportunidades
    detectadas por GIBBZ V3, independientemente de si ejecutan.

    MODO: OBSERVACIONAL PURO
    NO modifica nada del pipeline.
    NO toma decisiones de trading.
    """

    # ETS mínimo para considerar "cluster activo"
    ETS_CLUSTER_THRESHOLD = 50
    ETS_ACTIVE_THRESHOLD  = 65

    def __init__(self, session_date: str = ""):
        self._session_date = session_date
        self._bar_count    = 0

        # Acumuladores de sesión
        self._ets_sum:  float = 0.0
        self._conf_sum: float = 0.0
        self._prs_sum:  float = 0.0

        self._ets_values:  List[int] = []
        self._conf_values: List[int] = []

        self._regime_counts: Dict[str, int] = defaultdict(int)
        self._notable:       List[dict]     = []

        # Contadores
        self._et_bars     = 0
        self._ets_cluster = 0
        self._gtal_valid  = 0
        self._a_setups    = 0
        self._b_setups    = 0
        self._c_setups    = 0
        self._micro_or    = 0
        self._acg_act     = 0
        self._acg_change  = 0

        # Bloqueadores
        self._conf_block      = 0
        self._cont_block      = 0
        self._hb_block        = 0
        self._validator_block = 0
        self._gtal_reject     = 0

        # Filtros
        self._over_rejection  = 0
        self._under_filtering = 0
        self._ets_drift_events= 0

        # Max/min
        self._max_ets  = 0
        self._max_conf = 0
        self._max_rt   = 0

    def observe(self,
                bar_count:  int,
                raw:        dict,
                env_r,
                etil_r,
                gtal_r,
                conf_r,
                cont_r,
                opp_r,
                esl_r,
                pnl_r,
                port_r,
                adapt_r,
                acg_r,
                validation,
                analysis) -> BarObservation:
        """
        Registra una barra. Llamar cada barra del pipeline.
        Retorna BarObservation con el snapshot.
        """
        self._bar_count = bar_count

        # ── EXTRAER VALORES ───────────────────────────────────────
        env      = getattr(env_r,    "environment",              "ROTATIONAL")
        eff      = getattr(env_r,    "directional_efficiency",   0)
        trap     = getattr(env_r,    "trap_density",             0)
        ets      = getattr(etil_r,   "ets_score",                0)
        ets_cls  = getattr(etil_r,   "classification",           "NOISE")
        conf_sc  = getattr(conf_r,   "confirmation_score",       0)
        cont_p   = getattr(cont_r,   "continuation_probability", 0)
        opp      = getattr(opp_r,    "grade",                    "NONE")
        rt       = getattr(gtal_r,   "real_tradeability_score",  0)
        hb       = getattr(gtal_r,   "hindsight_bias_flag",      False)
        ev       = getattr(gtal_r,   "execution_validity",       "INVALID")
        esl_sc   = getattr(esl_r,    "final_executable_score",   0) if esl_r else 0
        fill     = getattr(esl_r,    "fill_likelihood",          "LOW") if esl_r else "LOW"
        slip     = getattr(esl_r,    "slippage_estimate",        0.0) if esl_r else 0.0
        edge_eff = getattr(pnl_r,    "edge_efficiency_score",    0)
        prs      = getattr(port_r,   "prs",                      0)
        stab     = getattr(adapt_r,  "system_stability_score",   0)
        frag     = getattr(port_r,   "fragmentation_index",      0)
        cong     = getattr(port_r,   "signal_congestion",        "NONE")
        filt_q   = getattr(adapt_r,  "filtering_quality",        "OK")
        micro_or = getattr(adapt_r,  "micro_over_rejection",     False)
        acg_rel  = getattr(acg_r,    "relaxed_mode_active",      False)
        acg_mode = getattr(acg_r,    "effective_conf_threshold",  65)
        acg_chg  = getattr(acg_r,    "would_change_outcome",     False)
        val_ok   = getattr(validation, "validated",              False) if validation else False
        val_rsn  = getattr(validation, "reason",                 "") if validation else ""

        # ── ACUMULADORES ─────────────────────────────────────────
        self._ets_sum   += ets
        self._conf_sum  += conf_sc
        self._prs_sum   += prs
        self._ets_values.append(ets)
        self._conf_values.append(conf_sc)
        self._regime_counts[env] += 1

        # Max
        self._max_ets  = max(self._max_ets,  ets)
        self._max_conf = max(self._max_conf, conf_sc)
        self._max_rt   = max(self._max_rt,   rt)

        # ── FREQUENCY METRICS ─────────────────────────────────────
        if env == "EFFICIENT_TREND":
            self._et_bars += 1

        if ets >= self.ETS_CLUSTER_THRESHOLD:
            self._ets_cluster += 1

        if ev == "VALID":
            self._gtal_valid += 1

        if opp == "A": self._a_setups += 1
        elif opp == "B": self._b_setups += 1
        elif opp == "C": self._c_setups += 1

        if micro_or:
            self._micro_or += 1

        if acg_rel:
            self._acg_act += 1
            if acg_chg:
                self._acg_change += 1

        # ── STARVATION METRICS ────────────────────────────────────
        # Detectar por qué setups prometedores no ejecutaron
        if opp != "NONE" and ev == "INVALID":
            self._gtal_reject += 1

            if hb:
                self._hb_block += 1
            elif cont_p < 72:
                self._cont_block += 1

        if opp != "NONE" and ev == "VALID" and not val_ok:
            # GTAL pasó pero validator bloqueó
            self._validator_block += 1
            if conf_sc < 65:
                self._conf_block += 1

        # ── FILTER ANALYTICS ──────────────────────────────────────
        if filt_q == "OVER_REJECTION":
            self._over_rejection += 1
        elif filt_q == "UNDER_FILTERING":
            self._under_filtering += 1

        # ── NOTABLE EVENTS ────────────────────────────────────────
        # Registrar barras con edge potencial
        is_notable = (
            ets >= self.ETS_ACTIVE_THRESHOLD or
            ev == "VALID" or
            micro_or or
            acg_rel or
            (opp != "NONE" and rt >= 50)
        )

        block_reason = ""
        blocked_conf = blocked_cont = blocked_hb = blocked_val = False

        if is_notable and not val_ok:
            if hb:
                blocked_hb = True
                block_reason = f"HB=True"
            elif ev == "INVALID" and cont_p < 72:
                blocked_cont = True
                block_reason = f"cont={cont_p} < 72"
            elif ev == "VALID" and conf_sc < 65:
                blocked_conf = True
                block_reason = f"conf={conf_sc} < 65"
            elif ev == "INVALID":
                block_reason = f"GTAL_INVALID ({getattr(gtal_r,'bias_source','')})"
            else:
                blocked_val = True
                block_reason = val_rsn[:60]

            self._notable.append({
                "bar":          bar_count,
                "price":        raw.get("price", 0),
                "env":          env,
                "ets":          ets,
                "conf":         conf_sc,
                "cont":         cont_p,
                "opp":          opp,
                "rt":           rt,
                "hb":           hb,
                "ev":           ev,
                "micro_or":     micro_or,
                "acg_relaxed":  acg_rel,
                "block_reason": block_reason,
            })

        return BarObservation(
            bar=bar_count, price=raw.get("price", 0),
            env=env, eff=eff, trap=trap,
            ets=ets, ets_class=ets_cls,
            conf=conf_sc, cont=cont_p,
            opp_grade=opp, rt_score=rt,
            hb_flag=hb, ev=ev,
            esl_score=esl_sc, fill=fill, slip=slip,
            edge_eff=edge_eff, prs=prs, stability=stab,
            frag_idx=frag, congestion=cong,
            micro_or=micro_or,
            acg_relaxed=acg_rel,
            acg_mode="RELAXED" if acg_rel else "STANDARD",
            filter_quality=filt_q,
            blocked_by_conf=blocked_conf,
            blocked_by_cont=blocked_cont,
            blocked_by_hb=blocked_hb,
            blocked_by_validator=blocked_val,
            block_reason=block_reason,
        )

    def get_session_observation(self) -> SessionObservation:
        """Genera el resumen observacional de la sesión."""
        n = max(self._bar_count, 1)

        avg_ets  = round(self._ets_sum  / n, 1)
        avg_conf = round(self._conf_sum / n, 1)
        avg_prs  = round(self._prs_sum  / n, 1)

        # ── SIGNAL STARVATION SCORE (0-100) ───────────────────────
        # Alto = escasez severa de señales
        starvation = 0.0

        # Sin EFFICIENT_TREND → penalización fuerte
        if self._et_bars == 0:
            starvation += 40
        elif self._et_bars / n < 0.05:
            starvation += 25
        elif self._et_bars / n < 0.10:
            starvation += 10

        # Sin A-setups
        if self._a_setups == 0:
            starvation += 30
        elif self._a_setups / n < 0.02:
            starvation += 15

        # Sin GTAL_VALID
        if self._gtal_valid == 0:
            starvation += 20
        elif self._gtal_valid / n < 0.01:
            starvation += 10

        # ETS máximo bajo
        if self._max_ets < 50:
            starvation += 10

        starvation = min(starvation, 100)

        # ── EDGE OPPORTUNITY SCORE (0-100) ────────────────────────
        # Alto = mucha oportunidad institucional
        edge_opp = 0.0

        if self._et_bars > 0:
            edge_opp += min(self._et_bars / n * 200, 30)

        if self._a_setups > 0:
            edge_opp += min(self._a_setups * 20, 30)

        if self._gtal_valid > 0:
            edge_opp += min(self._gtal_valid * 15, 25)

        if self._micro_or > 0:
            edge_opp += min(self._micro_or * 5, 10)

        edge_opp = min(edge_opp, 100)

        # ── REGIME QUALITY SCORE (0-100) ──────────────────────────
        # Alto = régimen favorable para el sistema
        et_pct = self._et_bars / n
        rq = min(et_pct * 500, 50)   # máx 50 por ET presence

        if self._max_ets >= 65:
            rq += 20
        if self._max_conf >= 65:
            rq += 15
        if avg_ets >= 20:
            rq += 15

        regime_quality = min(rq, 100)

        return SessionObservation(
            session_date          = self._session_date,
            total_bars            = self._bar_count,
            efficient_trend_bars  = self._et_bars,
            ets_cluster_bars      = self._ets_cluster,
            gtal_valid_bars       = self._gtal_valid,
            a_setup_bars          = self._a_setups,
            b_setup_bars          = self._b_setups,
            c_setup_bars          = self._c_setups,
            micro_or_bars         = self._micro_or,
            acg_activation_bars   = self._acg_act,
            acg_would_change      = self._acg_change,
            conf_block_count      = self._conf_block,
            cont_block_count      = self._cont_block,
            hb_block_count        = self._hb_block,
            validator_block_count = self._validator_block,
            gtal_rejection_count  = self._gtal_reject,
            regime_distribution   = dict(self._regime_counts),
            max_ets               = self._max_ets,
            max_conf              = self._max_conf,
            max_rt                = self._max_rt,
            avg_ets               = avg_ets,
            avg_conf              = avg_conf,
            avg_prs               = avg_prs,
            over_rejection_bars   = self._over_rejection,
            under_filtering_bars  = self._under_filtering,
            ets_drift_events      = self._ets_drift_events,
            notable_bars          = self._notable,
            signal_starvation_score   = round(starvation, 1),
            edge_opportunity_score    = round(edge_opp, 1),
            regime_quality_score      = round(regime_quality, 1),
        )

    def save(self, session_date: str = "") -> str:
        """Guarda observación en outcomes/YYYY-MM-DD_observation.json"""
        date = session_date or self._session_date or "unknown"
        obs  = self.get_session_observation()

        os.makedirs(OUTCOMES_DIR, exist_ok=True)
        path = os.path.join(OUTCOMES_DIR, f"{date}_observation.json")

        with open(path, "w", encoding="utf-8") as f:
            json.dump(obs.to_dict(), f, indent=2, ensure_ascii=False)

        return path

    def observation_summary_lines(self) -> List[str]:
        """Líneas de texto para el logger/summary del replay."""
        obs = self.get_session_observation()
        n   = max(obs.total_bars, 1)

        lines = []
        lines.append(f"  [OUTCOME OBSERVATION ENGINE v1.0]")
        lines.append(f"    {'session_date':<32} {obs.session_date}")
        lines.append(f"    {'total_bars':<32} {obs.total_bars}")
        lines.append(f"")
        lines.append(f"    ── FREQUENCY METRICS ──────────────────────────")
        lines.append(f"    {'efficient_trend_bars':<32} {obs.efficient_trend_bars}  ({obs.efficient_trend_bars/n*100:.1f}%)")
        lines.append(f"    {'ets_cluster_bars (ETS>=50)':<32} {obs.ets_cluster_bars}  ({obs.ets_cluster_bars/n*100:.1f}%)")
        lines.append(f"    {'ets_active_bars  (ETS>=65)':<32} {sum(1 for e in self._ets_values if e >= 65)}  ({sum(1 for e in self._ets_values if e >= 65)/n*100:.1f}%)")
        lines.append(f"    {'gtal_valid_bars':<32} {obs.gtal_valid_bars}  ({obs.gtal_valid_bars/n*100:.1f}%)")
        lines.append(f"    {'a_setup_bars':<32} {obs.a_setup_bars}")
        lines.append(f"    {'b_setup_bars':<32} {obs.b_setup_bars}")
        lines.append(f"    {'c_setup_bars':<32} {obs.c_setup_bars}")
        lines.append(f"    {'micro_or_bars':<32} {obs.micro_or_bars}")
        lines.append(f"    {'acg_activations':<32} {obs.acg_activation_bars}")
        lines.append(f"    {'acg_would_change_outcome':<32} {obs.acg_would_change}")
        lines.append(f"")
        lines.append(f"    ── STARVATION METRICS ─────────────────────────")
        lines.append(f"    {'gtal_rejection_count':<32} {obs.gtal_rejection_count}")
        lines.append(f"    {'conf_block_count':<32} {obs.conf_block_count}")
        lines.append(f"    {'cont_block_count':<32} {obs.cont_block_count}")
        lines.append(f"    {'hb_block_count':<32} {obs.hb_block_count}")
        lines.append(f"    {'validator_block_count':<32} {obs.validator_block_count}")
        lines.append(f"    {'over_rejection_bars':<32} {obs.over_rejection_bars}")
        lines.append(f"    {'signal_starvation_score':<32} {obs.signal_starvation_score}/100")
        lines.append(f"")
        lines.append(f"    ── DISTRIBUTION ───────────────────────────────")
        lines.append(f"    {'max_ets / avg_ets':<32} {obs.max_ets} / {obs.avg_ets}")
        lines.append(f"    {'max_conf / avg_conf':<32} {obs.max_conf} / {obs.avg_conf}")
        lines.append(f"    {'max_rt':<32} {obs.max_rt}")
        lines.append(f"")
        lines.append(f"    ── SCORES ─────────────────────────────────────")
        lines.append(f"    {'signal_starvation_score':<32} {obs.signal_starvation_score}/100  ← alto = escasez severa")
        lines.append(f"    {'edge_opportunity_score':<32} {obs.edge_opportunity_score}/100  ← alto = oportunidad real")
        lines.append(f"    {'regime_quality_score':<32} {obs.regime_quality_score}/100  ← alto = régimen favorable")
        lines.append(f"")
        lines.append(f"    ── REGIME DISTRIBUTION ────────────────────────")
        for regime, count in sorted(obs.regime_distribution.items(),
                                     key=lambda x: -x[1]):
            lines.append(f"    {regime:<32} {count} bars  ({count/n*100:.1f}%)")

        if obs.notable_bars:
            lines.append(f"")
            lines.append(f"    ── NOTABLE EVENTS ({len(obs.notable_bars)} barras) ───────────")
            for nb in obs.notable_bars[:10]:  # máx 10 en pantalla
                lines.append(f"    Bar {nb['bar']:4d} | {nb['env']:<16} "
                              f"ETS={nb['ets']:3d} conf={nb['conf']:3d} "
                              f"opp={nb['opp']} ev={nb['ev']:<8} "
                              f"→ {nb['block_reason'][:40]}")

        return lines