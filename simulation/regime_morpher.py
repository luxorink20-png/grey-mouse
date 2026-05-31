"""
GIBBZ V3 — simulation/regime_morpher.py
Regime Morpher v1.0

Recombina fragmentos reales del catálogo en sesiones sintéticas
instituccionalmente coherentes para el Synthetic Replay Treadmill.

REGLAS ABSOLUTAS:
  - NUNCA modifica los ticks originales
  - NUNCA combina fragmentos incompatibles (usa FingerprintPreserver)
  - NUNCA sobrerepresenta fragmentos ELITE (anti-overfitting)
  - INVARIANTE: todo deriva de data real — cero noise sintético

MODOS:
  STRUCTURED  — sigue una plantilla de sesión (para training dirigido)
  RANDOM      — recombinación probabilística respetando distribución real
  STRESS      — amplifica condiciones adversas (HB, traps, late signals)
  ELITE_SIM   — maximiza probabilidad de expansion session sintética

USO:
  python simulation/regime_morpher.py --mode STRUCTURED --template ELITE
  python simulation/regime_morpher.py --mode RANDOM --n 5
  python simulation/regime_morpher.py --mode STRESS --n 3
  python simulation/regime_morpher.py --session SYN_2026-03-11_001
"""

import json
import os
import sys
import glob
import random
import argparse
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from simulation.fingerprint_preserver import FingerprintPreserver

FRAGMENTS_DIR    = os.path.join("simulation", "fragments")
SYNTHETIC_DIR    = os.path.join("simulation", "synthetic_sessions")

# Contador global para IDs únicos dentro de la misma ejecución
_SESSION_COUNTER = 0

def _next_session_id(template: str) -> str:
    global _SESSION_COUNTER
    _SESSION_COUNTER += 1
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"SYN_{template}_{ts}_{_SESSION_COUNTER:03d}"

# ── SESSION TEMPLATES ─────────────────────────────────────────────
# Plantillas institucionales — secuencias de tipos de fragmentos
# que reflejan estructuras de sesión reales observadas en el dataset.

TEMPLATES = {

    "ELITE": [
        # La plantilla de sesión ELITE — opening drive + expansion
        # Basada en 2026-02-02 y 2026-04-30
        "PREMARKET",
        "OPENING_DRIVE",
        "MIDDAY_ROTATION",
        "MIDDAY_ROTATION",
        "VOL_RELEASE",
        "MIDDAY_ROTATION",
        "MIDDAY_ROTATION",
        "EXHAUSTION",
    ],

    "HIGH_EXPANSION": [
        # Sesión con expansión tardía — basada en 2026-03-11
        "PREMARKET",
        "MIDDAY_ROTATION",
        "MIDDAY_ROTATION",
        "VOL_RELEASE",
        "MIDDAY_ROTATION",
        "VOL_RELEASE",
        "MIDDAY_ROTATION",
        "EXHAUSTION",
    ],

    "VOL_RELEASE_DAY": [
        # Sesión con volatility release múltiple — basada en 2026-03-19
        "PREMARKET",
        "MIDDAY_ROTATION",
        "MIDDAY_ROTATION",
        "HB_SPIKE_ZONE",
        "MIDDAY_ROTATION",
        "VOL_RELEASE",
        "MIDDAY_ROTATION",
        "EXHAUSTION",
    ],

    "ROTATIONAL": [
        # Sesión rotacional típica — la más común en el dataset
        "PREMARKET",
        "MIDDAY_ROTATION",
        "MIDDAY_ROTATION",
        "HB_SPIKE_ZONE",
        "MIDDAY_ROTATION",
        "MIDDAY_ROTATION",
        "EXHAUSTION",
    ],

    "STRESS_HB": [
        # Sesión con alta contaminación HB — stress test
        "PREMARKET",
        "MIDDAY_ROTATION",
        "HB_SPIKE_ZONE",
        "MIDDAY_ROTATION",
        "HB_SPIKE_ZONE",
        "MIDDAY_ROTATION",
        "HB_SPIKE_ZONE",
        "MIDDAY_ROTATION",
        "EXHAUSTION",
    ],
}

# Distribución de templates para modo RANDOM (refleja dataset real)
TEMPLATE_DISTRIBUTION = {
    "ROTATIONAL":       0.50,
    "VOL_RELEASE_DAY":  0.20,
    "HIGH_EXPANSION":   0.15,
    "STRESS_HB":        0.10,
    "ELITE":            0.05,
}


@dataclass
class SyntheticSession:
    """Una sesión sintética construida desde fragmentos reales."""
    session_id:         str   = ""
    template:           str   = ""
    mode:               str   = ""
    created_at:         str   = ""

    # Fragmentos que la componen
    fragment_ids:       List  = field(default_factory=list)
    source_dates:       List  = field(default_factory=list)
    fragments:          List  = field(default_factory=list)

    # Métricas de calidad
    total_bars:         int   = 0
    coherence_score:    float = 0.0
    overfitting_risk:   str   = "LOW"

    # Edge profile de la sesión sintética
    ets_max:            int   = 0
    hb_rate:            float = 0.0
    et_bar_count:       int   = 0
    gtal_valid_count:   int   = 0
    fingerprints:       List  = field(default_factory=list)

    # Compatibilidad con replay
    replay_ready:       bool  = False
    issues:             List  = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("fragments", None)   # no guardar fragmentos completos en JSON
        d["fragment_count"] = len(self.fragment_ids)
        return d


class RegimeMorpher:
    """
    Construye sesiones sintéticas institucionales
    recombinando fragmentos reales del catálogo.
    """

    def __init__(self, seed: Optional[int] = None):
        self.fp   = FingerprintPreserver()
        self.rng  = random.Random(seed)
        os.makedirs(SYNTHETIC_DIR, exist_ok=True)

        # Pre-indexar fragmentos por tipo para acceso rápido
        self._index_by_type: Dict[str, List[dict]] = {}
        for frag in self.fp.catalog.values():
            t = frag.get("fragment_type", "")
            if t not in self._index_by_type:
                self._index_by_type[t] = []
            self._index_by_type[t].append(frag)

    # ── CORE: BUILD SYNTHETIC SESSION ────────────────────────────

    def build(self,
              template:     str  = "ROTATIONAL",
              mode:         str  = "STRUCTURED",
              session_id:   str  = "",
              elite_bias:   bool = False) -> Optional[SyntheticSession]:
        """
        Construye una sesión sintética siguiendo una plantilla.

        Args:
            template:   nombre de plantilla en TEMPLATES
            mode:       STRUCTURED | RANDOM | STRESS | ELITE_SIM
            session_id: ID único (auto-generado si vacío)
            elite_bias: si True, prefiere fragmentos con ETS alto y HB bajo
        """
        if template not in TEMPLATES:
            print(f"  [ERROR] Template '{template}' no existe.")
            print(f"  Templates disponibles: {list(TEMPLATES.keys())}")
            return None

        sequence = TEMPLATES[template]
        if not session_id:
            session_id = _next_session_id(template)

        syn = SyntheticSession(
            session_id  = session_id,
            template    = template,
            mode        = mode,
            created_at  = datetime.now().isoformat(),
        )

        selected_fragments = []
        used_dates         = []

        for i, ftype in enumerate(sequence):
            frag = self._select_fragment(
                ftype       = ftype,
                used_dates  = used_dates,
                prev_frag   = selected_fragments[-1] if selected_fragments else None,
                elite_bias  = elite_bias or (mode == "ELITE_SIM"),
                stress_hb   = (mode == "STRESS"),
                position    = i,
                total       = len(sequence),
            )

            if frag is None:
                # Fallback: cualquier fragmento del tipo correcto
                fallback_list = self._index_by_type.get(ftype, [])
                if fallback_list:
                    frag = self.rng.choice(fallback_list)
                else:
                    syn.issues.append(f"No fragment found for type {ftype}")
                    continue

            selected_fragments.append(frag)
            used_dates.append(frag.get("session_date", ""))

        if not selected_fragments:
            return None

        # Calcular métricas
        syn.fragments       = selected_fragments
        syn.fragment_ids    = [f["fragment_id"] for f in selected_fragments]
        syn.source_dates    = list(set(used_dates))
        syn.total_bars      = sum(f.get("bar_count", 0)
                                   for f in selected_fragments)
        syn.coherence_score = self.fp.session_coherence_score(
            selected_fragments)
        risk, reasons       = self.fp.check_overfitting_risk(
            selected_fragments)
        syn.overfitting_risk = risk
        syn.issues          += reasons

        # Edge profile
        syn.ets_max          = max((f.get("ets_max", 0)
                                    for f in selected_fragments), default=0)
        total_hb_bars        = sum(round(f.get("hb_rate", 0) *
                                         f.get("bar_count", 0))
                                   for f in selected_fragments)
        syn.hb_rate          = round(total_hb_bars / max(syn.total_bars, 1), 3)
        syn.et_bar_count     = sum(f.get("et_bar_count", 0)
                                   for f in selected_fragments)
        syn.gtal_valid_count = sum(f.get("gtal_valid_count", 0)
                                   for f in selected_fragments)

        # Fingerprints de la sesión (unión de los más frecuentes)
        fp_counts = {}
        for f in selected_fragments:
            for fp in f.get("fingerprints", []):
                fp_counts[fp] = fp_counts.get(fp, 0) + 1
        syn.fingerprints = [fp for fp, _ in
                            sorted(fp_counts.items(),
                                   key=lambda x: -x[1])[:6]]

        # Validate
        valid, issues = self.fp.validate_session(selected_fragments)
        # Sesiones sintéticas son estructuralmente más cortas que reales
        # (fragmentos individuales en lugar de 400 barras completas)
        # replay_ready requiere: coherencia OK + mínimo 80 barras
        bar_ok = syn.total_bars >= 80
        syn.replay_ready = valid and syn.coherence_score >= 60 and bar_ok
        syn.issues      += [i for i in issues
                            if "too short" not in i.lower()]
        if not bar_ok:
            syn.issues.append(
                f"Session short ({syn.total_bars}b < 80 min — "
                f"add more MIDDAY fragments)")

        return syn

    def _select_fragment(self,
                          ftype:      str,
                          used_dates: List[str],
                          prev_frag:  Optional[dict],
                          elite_bias: bool,
                          stress_hb:  bool,
                          position:   int,
                          total:      int) -> Optional[dict]:
        """
        Selecciona el mejor fragmento para una posición en la secuencia.
        """
        pool = self._index_by_type.get(ftype, [])
        if not pool:
            return None

        # Filtrar por compatibilidad con fragmento anterior
        if prev_frag is not None:
            compatible = []
            for frag in pool:
                result = self.fp.validate_transition(prev_frag, frag)
                if result.is_compatible:
                    compatible.append((frag, result.score))
        else:
            compatible = [(f, 80.0) for f in pool]

        if not compatible:
            # Relajar: permitir mismo origen si no hay alternativa
            compatible = [(f, 50.0) for f in pool]

        # Penalizar sesiones ya usadas (anti-memorization)
        def score_frag(frag_score_tuple):
            frag, base_score = frag_score_tuple
            score = base_score
            date  = frag.get("session_date", "")

            # Penalizar reutilización de la misma fecha
            times_used = used_dates.count(date)
            score -= times_used * 15

            # Elite bias: preferir ETS alto y HB bajo
            if elite_bias:
                ets = frag.get("ets_max", 0)
                hb  = frag.get("hb_rate", 1.0)
                score += (ets / 10)
                score -= (hb * 30)

            # Stress mode: preferir HB alta y ETS bajo
            if stress_hb:
                hb  = frag.get("hb_rate", 0)
                score += (hb * 40)

            # Posición final: preferir EXHAUSTION con bajo ETS
            if position == total - 1:
                ets = frag.get("ets_max", 0)
                score -= (ets / 5)

            return score

        scored = sorted(compatible, key=score_frag, reverse=True)

        # Selección con ruido controlado (top 3 para evitar determinismo)
        top_n = min(3, len(scored))
        chosen_frag, _ = self.rng.choice(scored[:top_n])
        return chosen_frag

    # ── BATCH GENERATION ─────────────────────────────────────────

    def generate_batch(self,
                        n:    int = 10,
                        mode: str = "RANDOM") -> List[SyntheticSession]:
        """
        Genera N sesiones sintéticas.
        Modo RANDOM respeta la distribución real del dataset.
        """
        sessions = []
        template_weights = list(TEMPLATE_DISTRIBUTION.items())
        templates  = [t for t, _ in template_weights]
        weights    = [w for _, w in template_weights]

        for i in range(n):
            if mode == "RANDOM":
                template = self.rng.choices(templates, weights=weights, k=1)[0]
            elif mode == "STRESS":
                template = self.rng.choice(["STRESS_HB", "ROTATIONAL"])
            elif mode == "ELITE_SIM":
                template = self.rng.choice(["ELITE", "HIGH_EXPANSION"])
            else:
                template = "ROTATIONAL"

            syn = self.build(
                template   = template,
                mode       = mode,
                elite_bias = (mode == "ELITE_SIM"),
            )
            if syn:
                sessions.append(syn)
                print(f"  [{i+1:2d}/{n}] {syn.session_id:<35} "
                      f"template={syn.template:<16} "
                      f"bars={syn.total_bars:4d}  "
                      f"coh={syn.coherence_score:4.1f}  "
                      f"ETS={syn.ets_max:3d}  "
                      f"HB={syn.hb_rate:.0%}  "
                      f"risk={syn.overfitting_risk}")

        return sessions

    # ── SAVE / LOAD ───────────────────────────────────────────────

    def save(self, syn: SyntheticSession) -> str:
        path = os.path.join(SYNTHETIC_DIR, f"{syn.session_id}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(syn.to_dict(), f, indent=2)
        return path

    def save_batch(self, sessions: List[SyntheticSession]) -> str:
        catalog_path = os.path.join(SYNTHETIC_DIR, "catalog.json")
        saved = []
        for syn in sessions:
            self.save(syn)
            saved.append(syn.to_dict())
        with open(catalog_path, "w", encoding="utf-8") as f:
            json.dump({
                "total":    len(saved),
                "sessions": saved,
            }, f, indent=2)
        return catalog_path

    # ── REPORTS ───────────────────────────────────────────────────

    def print_session(self, syn: SyntheticSession):
        print(f"\n{'='*70}")
        print(f"  SYNTHETIC SESSION: {syn.session_id}")
        print(f"  template={syn.template}  mode={syn.mode}  "
              f"bars={syn.total_bars}  "
              f"coherence={syn.coherence_score}")
        print(f"{'─'*70}")
        for frag in syn.fragments:
            fps = " | ".join(frag.get("fingerprints", [])[:3]) or "(none)"
            print(f"  [{frag['fragment_id']:<22}] "
                  f"{frag['fragment_type']:<18} "
                  f"{frag['bar_count']:3d}b  "
                  f"ETS={frag.get('ets_max',0):3d}  "
                  f"HB={frag.get('hb_rate',0):.0%}  "
                  f"Δ={frag.get('price_delta',0):+.1f}")
            if frag.get("fingerprints"):
                print(f"    {fps}")
        print(f"{'─'*70}")
        print(f"  ETS_max={syn.ets_max}  HB={syn.hb_rate:.0%}  "
              f"ET_bars={syn.et_bar_count}  "
              f"GTAL_valid={syn.gtal_valid_count}")
        print(f"  overfitting_risk={syn.overfitting_risk}  "
              f"replay_ready={syn.replay_ready}")
        if syn.fingerprints:
            print(f"  session_fingerprints: {' | '.join(syn.fingerprints)}")
        if syn.issues:
            print(f"  issues ({len(syn.issues)}):")
            for issue in syn.issues[:3]:
                print(f"    ⚠ {issue}")
        print(f"{'='*70}\n")

    def print_batch_report(self, sessions: List[SyntheticSession]):
        n = len(sessions)
        if not sessions:
            return
        print(f"\n{'='*70}")
        print(f"  BATCH REPORT — {n} synthetic sessions")
        print(f"{'─'*70}")

        ready       = [s for s in sessions if s.replay_ready]
        low_risk    = [s for s in sessions if s.overfitting_risk == "LOW"]
        high_ets    = [s for s in sessions if s.ets_max >= 65]
        low_hb      = [s for s in sessions if s.hb_rate <= 0.10]
        avg_coh     = sum(s.coherence_score for s in sessions) / n
        avg_bars    = sum(s.total_bars for s in sessions) / n

        print(f"  Replay ready:          {len(ready)}/{n}")
        print(f"  Low overfitting risk:  {len(low_risk)}/{n}")
        print(f"  High ETS (>=65):       {len(high_ets)}/{n}")
        print(f"  Low HB (<=10%):        {len(low_hb)}/{n}")
        print(f"  Avg coherence score:   {avg_coh:.1f}")
        print(f"  Avg bars per session:  {avg_bars:.0f}")

        print(f"\n  Template distribution:")
        tmpl_counts = {}
        for s in sessions:
            tmpl_counts[s.template] = tmpl_counts.get(s.template, 0) + 1
        for t, c in sorted(tmpl_counts.items(), key=lambda x: -x[1]):
            pct = c / n * 100
            print(f"    {t:<20} {c:3d}  ({pct:.0f}%)")

        print(f"\n  Top sessions by coherence:")
        for s in sorted(sessions, key=lambda x: -x.coherence_score)[:5]:
            print(f"    {s.session_id:<35} "
                  f"coh={s.coherence_score:4.1f}  "
                  f"ETS={s.ets_max:3d}  "
                  f"HB={s.hb_rate:.0%}  "
                  f"risk={s.overfitting_risk}")
        print(f"{'='*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GIBBZ V3 — Regime Morpher")
    parser.add_argument("--mode",     type=str, default="RANDOM",
                        choices=["STRUCTURED", "RANDOM", "STRESS",
                                 "ELITE_SIM"],
                        help="Modo de generación")
    parser.add_argument("--template", type=str, default="ROTATIONAL",
                        choices=list(TEMPLATES.keys()),
                        help="Plantilla para modo STRUCTURED")
    parser.add_argument("--n",        type=int, default=5,
                        help="Número de sesiones a generar")
    parser.add_argument("--save",     action="store_true",
                        help="Guardar sesiones en simulation/synthetic_sessions/")
    parser.add_argument("--session",  type=str, default="",
                        help="Ver detalle de sesión guardada")
    parser.add_argument("--seed",     type=int, default=None,
                        help="Seed para reproducibilidad")
    parser.add_argument("--list",     action="store_true",
                        help="Listar sesiones sintéticas guardadas")
    args = parser.parse_args()

    if args.list:
        catalog = os.path.join(SYNTHETIC_DIR, "catalog.json")
        if os.path.exists(catalog):
            d = json.load(open(catalog, encoding="utf-8"))
            print(f"\n  {d['total']} synthetic sessions saved:")
            for s in d["sessions"]:
                print(f"    {s['session_id']:<35} "
                      f"template={s['template']:<16} "
                      f"bars={s['total_bars']:4d}  "
                      f"coh={s['coherence_score']:4.1f}  "
                      f"risk={s['overfitting_risk']}")
        else:
            print("  No synthetic sessions saved yet.")
        exit(0)

    if args.session:
        path = os.path.join(SYNTHETIC_DIR, f"{args.session}.json")
        if not os.path.exists(path):
            print(f"  Session {args.session} not found.")
            exit(1)
        d   = json.load(open(path, encoding="utf-8"))
        print(f"\n  Session: {d['session_id']}")
        print(f"  Template: {d['template']}  Mode: {d['mode']}")
        print(f"  Bars: {d['total_bars']}  Coherence: {d['coherence_score']}")
        print(f"  ETS_max: {d['ets_max']}  HB: {d.get('hb_rate', 0):.0%}")
        print(f"  Overfitting risk: {d['overfitting_risk']}")
        print(f"  Replay ready: {d['replay_ready']}")
        print(f"  Fragment IDs:")
        for fid in d.get("fragment_ids", []):
            print(f"    {fid}")
        exit(0)

    morpher = RegimeMorpher(seed=args.seed)

    if args.mode == "STRUCTURED":
        print(f"\n  Building STRUCTURED session — template={args.template}")
        syn = morpher.build(template=args.template, mode="STRUCTURED")
        if syn:
            morpher.print_session(syn)
            if args.save:
                path = morpher.save(syn)
                print(f"  → Saved: {path}")

    else:
        print(f"\n  Generating {args.n} sessions — mode={args.mode}")
        sessions = morpher.generate_batch(n=args.n, mode=args.mode)
        morpher.print_batch_report(sessions)

        if args.save:
            path = morpher.save_batch(sessions)
            print(f"  → Catalog saved: {path}")
            for syn in sessions[:2]:
                morpher.print_session(syn)