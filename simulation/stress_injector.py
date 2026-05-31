"""
GIBBZ V3 — simulation/stress_injector.py
Stress Injector v1.0

Inyecta condiciones adversas reales en sesiones sintéticas
para descubrir los límites del sistema sin tocar el core.

PRINCIPIO: todo stress deriva de patrones REALES observados
en el dataset. Cero ruido artificial.

TIPOS DE STRESS:
  HB_SURGE       — aumenta contaminación HB en fragmentos clave
  LATE_WARMUP    — retrasa el calentamiento del continuation engine
  ETS_DECAY      — simula decaimiento prematuro del edge
  REGIME_TRAP    — inyecta fragmentos TRAPPY entre expansiones
  VOL_COLLAPSE   — colapsa volatilidad en el peor momento
  TIMING_SHIFT   — desplaza el edge hacia barras tardías

REGLAS:
  - NUNCA modifica ticks originales
  - NUNCA altera el core pipeline
  - Solo reordena / substituye fragmentos del catálogo real
  - Cada stress tiene intensidad LOW / MEDIUM / HIGH

USO:
  python simulation/stress_injector.py --session SYN_ELITE_..._001
  python simulation/stress_injector.py --type HB_SURGE --intensity HIGH
  python simulation/stress_injector.py --batch --n 10
  python simulation/stress_injector.py --report
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
from simulation.regime_morpher import RegimeMorpher, SyntheticSession

SYNTHETIC_DIR  = os.path.join("simulation", "synthetic_sessions")
STRESSED_DIR   = os.path.join("simulation", "stressed_sessions")
STRESS_REPORT  = os.path.join("simulation", "stress_report.json")

# Intensidad → multiplicador de fragmentos afectados
INTENSITY_MAP = {
    "LOW":    0.20,   # 20% de fragmentos afectados
    "MEDIUM": 0.40,   # 40%
    "HIGH":   0.65,   # 65%
}

# Stress types disponibles
STRESS_TYPES = [
    "HB_SURGE",
    "LATE_WARMUP",
    "ETS_DECAY",
    "REGIME_TRAP",
    "VOL_COLLAPSE",
    "TIMING_SHIFT",
]


@dataclass
class StressResult:
    """Resultado de aplicar stress a una sesión."""
    original_id:        str   = ""
    stressed_id:        str   = ""
    stress_type:        str   = ""
    intensity:          str   = ""

    # Cambios observados
    original_ets_max:   int   = 0
    stressed_ets_max:   int   = 0
    original_hb_rate:   float = 0.0
    stressed_hb_rate:   float = 0.0
    original_coherence: float = 0.0
    stressed_coherence: float = 0.0

    fragments_modified: int   = 0
    fragments_total:    int   = 0

    # Sistema sobrevivió?
    system_stable:      bool  = True
    system_notes:       List  = field(default_factory=list)

    def delta_ets(self) -> int:
        return self.stressed_ets_max - self.original_ets_max

    def delta_hb(self) -> float:
        return self.stressed_hb_rate - self.original_hb_rate

    def to_dict(self) -> dict:
        d = asdict(self)
        d["delta_ets"] = self.delta_ets()
        d["delta_hb"]  = round(self.delta_hb(), 3)
        return d


class StressInjector:
    """
    Inyecta condiciones adversas reales en sesiones sintéticas.
    Usa fragmentos del catálogo real — cero datos inventados.
    """

    def __init__(self, seed: Optional[int] = None):
        self.fp    = FingerprintPreserver()
        self.rng   = random.Random(seed)
        self.morph = RegimeMorpher(seed=seed)
        os.makedirs(STRESSED_DIR, exist_ok=True)

        # Pre-indexar por tipo y por fingerprint
        self._by_type: Dict[str, List[dict]] = {}
        self._hb_heavy: List[dict] = []
        self._trappy:   List[dict] = []
        self._low_ets:  List[dict] = []

        for frag in self.fp.catalog.values():
            t = frag.get("fragment_type", "")
            if t not in self._by_type:
                self._by_type[t] = []
            self._by_type[t].append(frag)
            if frag.get("hb_rate", 0) >= 0.30:
                self._hb_heavy.append(frag)
            if "HIGH_HB_CONTAMINATED" in frag.get("fingerprints", []):
                self._trappy.append(frag)
            if frag.get("ets_max", 0) <= 20:
                self._low_ets.append(frag)

    # ── APPLY STRESS ─────────────────────────────────────────────

    def apply(self,
              session:    SyntheticSession,
              stress_type: str  = "HB_SURGE",
              intensity:  str   = "MEDIUM") -> Tuple[SyntheticSession, StressResult]:
        """
        Aplica un tipo de stress a una sesión sintética.
        Retorna (nueva_sesión_estresada, StressResult).
        """
        if stress_type not in STRESS_TYPES:
            raise ValueError(f"Unknown stress type: {stress_type}. "
                             f"Options: {STRESS_TYPES}")
        if intensity not in INTENSITY_MAP:
            raise ValueError(f"Unknown intensity: {intensity}. "
                             f"Options: {list(INTENSITY_MAP.keys())}")

        result = StressResult(
            original_id       = session.session_id,
            stress_type       = stress_type,
            intensity         = intensity,
            original_ets_max  = session.ets_max,
            original_hb_rate  = session.hb_rate,
            original_coherence= session.coherence_score,
            fragments_total   = len(session.fragments),
        )

        # Clonar fragmentos
        stressed_frags = list(session.fragments)
        pct            = INTENSITY_MAP[intensity]
        n_affected     = max(1, int(len(stressed_frags) * pct))

        if stress_type == "HB_SURGE":
            stressed_frags = self._stress_hb_surge(
                stressed_frags, n_affected)

        elif stress_type == "LATE_WARMUP":
            stressed_frags = self._stress_late_warmup(
                stressed_frags, n_affected)

        elif stress_type == "ETS_DECAY":
            stressed_frags = self._stress_ets_decay(
                stressed_frags, n_affected)

        elif stress_type == "REGIME_TRAP":
            stressed_frags = self._stress_regime_trap(
                stressed_frags, n_affected)

        elif stress_type == "VOL_COLLAPSE":
            stressed_frags = self._stress_vol_collapse(
                stressed_frags, n_affected)

        elif stress_type == "TIMING_SHIFT":
            stressed_frags = self._stress_timing_shift(
                stressed_frags, n_affected)

        # Construir sesión estresada
        ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
        sid = f"{session.session_id}_STRESS_{stress_type}_{intensity}"

        stressed = SyntheticSession(
            session_id    = sid,
            template      = session.template,
            mode          = f"STRESS_{stress_type}",
            created_at    = datetime.now().isoformat(),
            fragments     = stressed_frags,
            fragment_ids  = [f["fragment_id"] for f in stressed_frags],
            source_dates  = list(set(f.get("session_date","")
                                     for f in stressed_frags)),
            total_bars    = sum(f.get("bar_count",0) for f in stressed_frags),
        )

        # Métricas
        stressed.ets_max = max(
            (f.get("ets_max",0) for f in stressed_frags), default=0)
        total_hb = sum(round(f.get("hb_rate",0) * f.get("bar_count",0))
                       for f in stressed_frags)
        stressed.hb_rate = round(
            total_hb / max(stressed.total_bars, 1), 3)
        stressed.et_bar_count = sum(
            f.get("et_bar_count",0) for f in stressed_frags)
        stressed.gtal_valid_count = sum(
            f.get("gtal_valid_count",0) for f in stressed_frags)
        stressed.coherence_score = self.fp.session_coherence_score(
            stressed_frags)
        risk, _ = self.fp.check_overfitting_risk(stressed_frags)
        stressed.overfitting_risk = risk

        fp_counts = {}
        for f in stressed_frags:
            for fp in f.get("fingerprints", []):
                fp_counts[fp] = fp_counts.get(fp, 0) + 1
        stressed.fingerprints = [fp for fp, _ in
                                  sorted(fp_counts.items(),
                                         key=lambda x: -x[1])[:6]]

        valid, issues = self.fp.validate_session(stressed_frags)
        bar_ok = stressed.total_bars >= 80
        stressed.replay_ready = valid and stressed.coherence_score >= 55 and bar_ok
        stressed.issues = [i for i in issues if "too short" not in i.lower()]

        # Completar StressResult
        result.stressed_id        = sid
        result.stressed_ets_max   = stressed.ets_max
        result.stressed_hb_rate   = stressed.hb_rate
        result.stressed_coherence = stressed.coherence_score
        result.fragments_modified = n_affected

        # ¿Sistema estable? coherencia >= 55 bajo stress es muy bueno
        result.system_stable = stressed.coherence_score >= 55
        if stressed.coherence_score < 60:
            result.system_notes.append(
                f"Coherence dropped to {stressed.coherence_score:.1f}")
        if stressed.hb_rate > 0.35:
            result.system_notes.append(
                f"Severe HB contamination ({stressed.hb_rate:.0%})")
        if stressed.ets_max < 30:
            result.system_notes.append(
                "Edge signal completely suppressed")

        return stressed, result

    # ── STRESS IMPLEMENTATIONS ────────────────────────────────────

    def _stress_hb_surge(self,
                          frags: List[dict],
                          n: int) -> List[dict]:
        """
        Substituye N fragmentos por versiones con alta contaminación HB.
        Simula sesiones donde el mercado genera muchos spikes falsos.
        """
        result = list(frags)
        # Seleccionar índices no-PREMARKET y no-OPENING_DRIVE para sustituir
        eligible = [i for i, f in enumerate(result)
                    if f.get("fragment_type") not in
                    ("PREMARKET", "OPENING_DRIVE", "EXHAUSTION")]
        targets = self.rng.sample(eligible, min(n, len(eligible)))

        for idx in targets:
            orig = result[idx]
            orig_type = orig.get("fragment_type", "MIDDAY_ROTATION")
            # Buscar fragmento del mismo tipo con alta HB
            heavy = [f for f in self._hb_heavy
                     if f.get("fragment_type") == orig_type
                     and f["fragment_id"] != orig["fragment_id"]]
            if heavy:
                result[idx] = self.rng.choice(heavy)
            elif self._hb_heavy:
                # Cambiar a HB_SPIKE_ZONE si no hay alternativa
                result[idx] = self.rng.choice(
                    self._by_type.get("HB_SPIKE_ZONE", [self._hb_heavy[0]]))

        return result

    def _stress_late_warmup(self,
                             frags: List[dict],
                             n: int) -> List[dict]:
        """
        Simula recordings tardíos — reemplaza fragmentos iniciales
        con fragmentos de menor calidad (como si el cont engine no estuviera calentado).
        """
        result = list(frags)
        # Solo afectar el primer 40% de la sesión
        cutoff  = max(2, int(len(result) * 0.40))
        targets = list(range(min(n, cutoff)))

        for idx in targets:
            orig = result[idx]
            # Reemplazar con fragmento de ETS bajo (simula warmup insuficiente)
            low_quality = [f for f in self._low_ets
                           if f.get("fragment_type") ==
                           orig.get("fragment_type", "MIDDAY_ROTATION")]
            if low_quality:
                result[idx] = self.rng.choice(low_quality)

        return result

    def _stress_ets_decay(self,
                           frags: List[dict],
                           n: int) -> List[dict]:
        """
        Simula decaimiento prematuro del edge — reemplaza fragmentos
        con ETS alto por versiones de menor calidad del mismo tipo.
        """
        result = list(frags)
        # Identificar fragmentos con ETS alto
        high_ets_idx = [i for i, f in enumerate(result)
                        if f.get("ets_max", 0) >= 50]
        targets = self.rng.sample(
            high_ets_idx, min(n, len(high_ets_idx)))

        for idx in targets:
            orig      = result[idx]
            orig_type = orig.get("fragment_type", "MIDDAY_ROTATION")
            # Buscar mismo tipo pero ETS más bajo
            decay_frags = [f for f in self._by_type.get(orig_type, [])
                           if f.get("ets_max", 0) < orig.get("ets_max", 50)
                           and f["fragment_id"] != orig["fragment_id"]]
            if decay_frags:
                result[idx] = self.rng.choice(decay_frags)

        return result

    def _stress_regime_trap(self,
                             frags: List[dict],
                             n: int) -> List[dict]:
        """
        Inyecta fragmentos TRAPPY entre expansiones.
        Simula mercados donde el contexto institucional se contamina.
        """
        result = list(frags)
        trappy_pool = self._by_type.get("HB_SPIKE_ZONE", [])
        if not trappy_pool:
            return result

        # Insertar fragmentos HB_SPIKE_ZONE en posiciones intermedias
        # (no al principio ni al final)
        positions = sorted(
            self.rng.sample(
                range(1, len(result) - 1),
                min(n, len(result) - 2)),
            reverse=True)   # insertar de atrás para adelante

        for pos in positions:
            trap = self.rng.choice(trappy_pool)
            result.insert(pos, trap)

        return result

    def _stress_vol_collapse(self,
                              frags: List[dict],
                              n: int) -> List[dict]:
        """
        Colapsa volatilidad en el peor momento — reemplaza VOL_RELEASE
        y OPENING_DRIVE con versiones de baja eficiencia.
        """
        result = list(frags)
        # Afectar VOL_RELEASE y OPENING_DRIVE
        expansion_idx = [i for i, f in enumerate(result)
                         if f.get("fragment_type") in
                         ("VOL_RELEASE", "OPENING_DRIVE", "EARLY_EXPANSION")]
        targets = self.rng.sample(
            expansion_idx, min(n, len(expansion_idx)))

        for idx in targets:
            # Reemplazar con MIDDAY_ROTATION de baja eficiencia
            low_eff = [f for f in self._by_type.get("MIDDAY_ROTATION", [])
                       if f.get("eff_max", 100) < 40
                       and f.get("hb_rate", 1.0) < 0.15]
            if low_eff:
                result[idx] = self.rng.choice(low_eff)

        return result

    def _stress_timing_shift(self,
                              frags: List[dict],
                              n: int) -> List[dict]:
        """
        Desplaza el edge hacia barras tardías — mueve fragmentos de alta
        calidad al final de la sesión, simulando opening drives tardíos.
        """
        result = list(frags)
        # Identificar fragmentos de alta calidad en la primera mitad
        half    = len(result) // 2
        early_high = [i for i, f in enumerate(result[:half])
                      if f.get("ets_max", 0) >= 50]
        if not early_high:
            return result

        # Mover al final (después del punto medio)
        to_move = self.rng.sample(early_high, min(n, len(early_high)))
        fragments_to_move = []
        for idx in sorted(to_move, reverse=True):
            fragments_to_move.append(result.pop(idx))
            # Insertar MIDDAY en su lugar
            replacement = self.rng.choice(
                self._by_type.get("MIDDAY_ROTATION", [result[0]]))
            result.insert(idx, replacement)

        # Insertar los fragmentos movidos en la segunda mitad
        insert_pos = len(result) - 1  # antes del EXHAUSTION final
        for frag in fragments_to_move:
            result.insert(insert_pos, frag)
            insert_pos += 1

        return result

    # ── BATCH STRESS ─────────────────────────────────────────────

    def stress_batch(self,
                      n:          int  = 10,
                      stress_type: str = "HB_SURGE",
                      intensity:  str  = "MEDIUM",
                      base_mode:  str  = "ELITE_SIM") -> List[StressResult]:
        """
        Genera N sesiones base y aplica stress a cada una.
        """
        print(f"\n  Generating {n} base sessions ({base_mode}) "
              f"+ {stress_type}/{intensity} stress...")
        base_sessions = self.morph.generate_batch(n=n, mode=base_mode)
        results       = []

        for i, sess in enumerate(base_sessions):
            stressed, result = self.apply(sess, stress_type, intensity)
            results.append(result)
            print(f"  [{i+1:2d}/{n}] {result.original_id[-30:]:<32} "
                  f"ETS: {result.original_ets_max:3d}→{result.stressed_ets_max:3d}  "
                  f"HB: {result.original_hb_rate:.0%}→{result.stressed_hb_rate:.0%}  "
                  f"coh: {result.original_coherence:.1f}→{result.stressed_coherence:.1f}  "
                  f"{'✓' if result.system_stable else '✗'}")

            # Guardar sesión estresada
            path = os.path.join(STRESSED_DIR,
                                f"{stressed.session_id}.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(stressed.to_dict(), f, indent=2)

        return results

    # ── REPORT ────────────────────────────────────────────────────

    def print_stress_report(self, results: List[StressResult]):
        n = len(results)
        if not n:
            return

        stable      = [r for r in results if r.system_stable]
        avg_ets_d   = sum(r.delta_ets() for r in results) / n
        avg_hb_d    = sum(r.delta_hb()  for r in results) / n
        avg_coh_d   = sum(r.stressed_coherence - r.original_coherence
                          for r in results) / n

        print(f"\n{'='*70}")
        print(f"  STRESS TEST REPORT — {n} sessions  "
              f"[{results[0].stress_type} / {results[0].intensity}]")
        print(f"{'─'*70}")
        print(f"  System stability rate:   {len(stable)}/{n}  "
              f"({len(stable)/n:.0%})")
        print(f"  avg ETS delta:           {avg_ets_d:+.1f} pts")
        print(f"  avg HB delta:            {avg_hb_d:+.1%}")
        print(f"  avg coherence delta:     {avg_coh_d:+.1f}")

        if avg_coh_d > -5:
            verdict = "ROBUST — sistema resistente al stress"
        elif avg_coh_d > -15:
            verdict = "MODERATE — degradación controlada"
        else:
            verdict = "FRAGILE — revisar arquitectura"
        print(f"  verdict:                 {verdict}")

        # Guardar reporte
        report_data = {
            "stress_type":      results[0].stress_type,
            "intensity":        results[0].intensity,
            "n_sessions":       n,
            "stability_rate":   len(stable) / n,
            "avg_ets_delta":    round(avg_ets_d, 1),
            "avg_hb_delta":     round(avg_hb_d, 3),
            "avg_coh_delta":    round(avg_coh_d, 1),
            "verdict":          verdict,
            "results":          [r.to_dict() for r in results],
        }
        with open(STRESS_REPORT, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2)
        print(f"  → Saved: {STRESS_REPORT}")
        print(f"{'='*70}\n")

    def run_full_battery(self, n_per_type: int = 5):
        """
        Corre todos los tipos de stress y produce reporte consolidado.
        """
        print(f"\n{'='*70}")
        print(f"  STRESS BATTERY — {len(STRESS_TYPES)} types × "
              f"{n_per_type} sessions each")
        print(f"{'='*70}")

        all_verdicts = {}
        for stype in STRESS_TYPES:
            print(f"\n  ── {stype} ──")
            results = self.stress_batch(
                n=n_per_type, stress_type=stype,
                intensity="MEDIUM", base_mode="ELITE_SIM")
            stable_pct = sum(1 for r in results
                             if r.system_stable) / len(results)
            all_verdicts[stype] = round(stable_pct * 100, 1)

        print(f"\n{'='*70}")
        print(f"  BATTERY SUMMARY")
        print(f"{'─'*70}")
        for stype, pct in sorted(all_verdicts.items(),
                                  key=lambda x: x[1]):
            bar     = "█" * int(pct / 5)
            verdict = ("ROBUST" if pct >= 80
                       else "MODERATE" if pct >= 60
                       else "FRAGILE")
            print(f"  {stype:<18} {pct:5.1f}%  {bar:<20}  {verdict}")
        print(f"{'='*70}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="GIBBZ V3 — Stress Injector")
    parser.add_argument("--type",      type=str, default="HB_SURGE",
                        choices=STRESS_TYPES,
                        help="Tipo de stress a inyectar")
    parser.add_argument("--intensity", type=str, default="MEDIUM",
                        choices=["LOW", "MEDIUM", "HIGH"],
                        help="Intensidad del stress")
    parser.add_argument("--n",         type=int, default=5,
                        help="Número de sesiones a estresar")
    parser.add_argument("--battery",   action="store_true",
                        help="Correr batería completa de todos los stress types")
    parser.add_argument("--base-mode", type=str, default="ELITE_SIM",
                        choices=["RANDOM", "ELITE_SIM", "STRUCTURED"],
                        help="Modo de generación de sesiones base")
    parser.add_argument("--seed",      type=int, default=42,
                        help="Seed para reproducibilidad")
    args = parser.parse_args()

    injector = StressInjector(seed=args.seed)

    if args.battery:
        injector.run_full_battery(n_per_type=args.n)
    else:
        results = injector.stress_batch(
            n           = args.n,
            stress_type = args.type,
            intensity   = args.intensity,
            base_mode   = args.base_mode,
        )
        injector.print_stress_report(results)