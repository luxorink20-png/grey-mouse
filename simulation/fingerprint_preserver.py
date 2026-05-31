"""
GIBBZ V3 — simulation/fingerprint_preserver.py
Fingerprint Preserver v1.0

Garantiza que el regime_morpher NUNCA combine fragmentos
que destruyan la causalidad institucional del mercado.

Funciones:
  1. Validar compatibilidad entre fragmentos
  2. Score de coherencia de una secuencia
  3. Detectar combinaciones inválidas
  4. Sugerir fragmentos compatibles para completar una sesión
  5. Preservar distribución real del dataset (anti-overfitting)

NO modifica fragmentos.
NO toca el core.
Solo valida y sugiere.

USO:
  from simulation.fingerprint_preserver import FingerprintPreserver
  fp = FingerprintPreserver()
  ok, reason = fp.validate_transition(frag_a, frag_b)
  score = fp.session_coherence_score(fragment_list)
  candidates = fp.suggest_next(current_frag, catalog)
"""

import json
import os
import glob
import random
from dataclasses import dataclass
from typing import List, Tuple, Dict, Optional


# ── COMPATIBILITY RULES ───────────────────────────────────────────
# Reglas institucionales de transición entre fragmentos.
# Basadas en comportamiento real del mercado, NO en optimización.

# Regla 1: Compatibilidad de tipos (qué puede seguir a qué)
TYPE_COMPAT = {
    "PREMARKET":        ["OPENING_DRIVE", "VOL_RELEASE", "MIDDAY_ROTATION",
                         "EARLY_EXPANSION"],
    "OPENING_DRIVE":    ["EARLY_EXPANSION", "VOL_RELEASE", "MIDDAY_ROTATION"],
    "EARLY_EXPANSION":  ["MIDDAY_ROTATION", "VOL_RELEASE", "EXHAUSTION"],
    "MIDDAY_ROTATION":  ["VOL_RELEASE", "MIDDAY_ROTATION", "POWER_HOUR",
                         "EXHAUSTION", "HB_SPIKE_ZONE"],
    "VOL_RELEASE":      ["MIDDAY_ROTATION", "EARLY_EXPANSION", "EXHAUSTION",
                         "HB_SPIKE_ZONE"],
    "HB_SPIKE_ZONE":    ["MIDDAY_ROTATION", "VOL_RELEASE", "EXHAUSTION"],
    "POWER_HOUR":       ["EXHAUSTION", "VOL_RELEASE", "MIDDAY_ROTATION"],
    "EXHAUSTION":       ["MIDDAY_ROTATION"],   # puede continuar en rotación
}

# Regla 2: Max eficiencia delta entre fragmentos
MAX_EFF_DELTA = 40   # |exit_eff(A) - entry_eff(B)| <= 40 (default)

# Excepciones de delta por tipo de transición
# PREMARKET→OPENING_DRIVE es el evento más importante — permitir salto grande
EFF_DELTA_EXCEPTIONS = {
    ("PREMARKET",       "OPENING_DRIVE"):   80,  # salto natural en ELITE sessions
    ("PREMARKET",       "VOL_RELEASE"):     60,  # gap días
    ("MIDDAY_ROTATION", "VOL_RELEASE"):     55,  # vol release desde rotación
    ("HB_SPIKE_ZONE",   "MIDDAY_ROTATION"): 50,  # recovery post-spike
}

# Regla 3: Regime transition limits
REGIME_TRANSITION_OK = {
    "ROTATIONAL":       ["ROTATIONAL", "CHOPPY", "EFFICIENT_TREND", "TRAPPY"],
    "CHOPPY":           ["CHOPPY", "ROTATIONAL", "TRAPPY"],
    "EFFICIENT_TREND":  ["EFFICIENT_TREND", "ROTATIONAL"],
    "TRAPPY":           ["TRAPPY", "ROTATIONAL", "CHOPPY"],
}

# Regla 4: HB contamination threshold para transición
# No combinar fragmento CLEAN con fragmento HIGH_HB_CONTAMINATED directamente
MAX_HB_JUMP = 0.50  # salto máximo en HB rate entre fragmentos contiguos

# Regla 5: Distribución target (refleja dataset real)
TARGET_DISTRIBUTION = {
    "MIDDAY_ROTATION":  0.50,
    "HB_SPIKE_ZONE":    0.20,
    "VOL_RELEASE":      0.11,
    "PREMARKET":        0.10,
    "EXHAUSTION":       0.08,
    "OPENING_DRIVE":    0.01,
}

# Regla 6: Fingerprint incompatibilidades duras
# Combinaciones que NUNCA deben ocurrir
HARD_INCOMPATIBILITIES = [
    # (fingerprint_A, fingerprint_B) → never combine
    ("GTAL_VALID",            "HIGH_HB_CONTAMINATED"),
    ("HIGH_EFF_OPEN",         "HIGH_HB_CONTAMINATED"),
    ("ETS_STRONG",            "HIGH_HB_CONTAMINATED"),
]


@dataclass
class CompatibilityResult:
    is_compatible:  bool
    score:          float   # 0-100
    reason:         str
    warnings:       List[str]


class FingerprintPreserver:
    """
    Valida y preserva la integridad institucional de fragmentos
    cuando son recombinados por el regime_morpher.
    """

    def __init__(self):
        self.catalog: Dict[str, dict] = {}
        self._load_catalog()

    def _load_catalog(self):
        """Carga todos los fragmentos disponibles en memoria."""
        for f in glob.glob(os.path.join("simulation", "fragments",
                                         "*_fragments.json")):
            try:
                d = json.load(open(f, encoding="utf-8"))
                date = d["session_date"]
                for frag in d["fragments"]:
                    fid = frag["fragment_id"]
                    self.catalog[fid] = frag
            except Exception as e:
                pass

    def validate_transition(self,
                             frag_a: dict,
                             frag_b: dict) -> CompatibilityResult:
        """
        Valida si frag_b puede seguir a frag_a en una sesión sintética.
        Retorna CompatibilityResult con score y razón.
        """
        warnings = []
        score    = 100.0

        type_a = frag_a.get("fragment_type", "")
        type_b = frag_b.get("fragment_type", "")

        # ── REGLA 1: Type compatibility ───────────────────────────
        allowed = TYPE_COMPAT.get(type_a, [])
        if type_b not in allowed:
            return CompatibilityResult(
                is_compatible = False,
                score         = 0,
                reason        = f"Type transition {type_a}→{type_b} not allowed",
                warnings      = []
            )

        # ── REGLA 2: Efficiency delta ─────────────────────────────
        exit_eff  = frag_a.get("exit_eff",  frag_a.get("eff_avg", 50))
        entry_eff = frag_b.get("entry_eff", frag_b.get("eff_avg", 50))
        eff_delta = abs(exit_eff - entry_eff)
        max_delta = EFF_DELTA_EXCEPTIONS.get((type_a, type_b), MAX_EFF_DELTA)
        if eff_delta > max_delta:
            return CompatibilityResult(
                is_compatible = False,
                score         = 0,
                reason        = f"Efficiency delta too large ({eff_delta} > {max_delta})",
                warnings      = []
            )
        score -= (eff_delta / max_delta) * 20

        # ── REGLA 3: Regime transition ────────────────────────────
        exit_env  = frag_a.get("exit_env",  "ROTATIONAL")
        entry_env = frag_b.get("entry_env", "ROTATIONAL")
        ok_envs   = REGIME_TRANSITION_OK.get(exit_env, [])
        if entry_env not in ok_envs:
            warnings.append(
                f"Regime transition {exit_env}→{entry_env} unusual")
            score -= 15

        # ── REGLA 4: HB jump ──────────────────────────────────────
        hb_a    = frag_a.get("hb_rate", 0)
        hb_b    = frag_b.get("hb_rate", 0)
        hb_jump = abs(hb_a - hb_b)
        if hb_jump > MAX_HB_JUMP:
            warnings.append(
                f"HB rate jump too large ({hb_a:.0%}→{hb_b:.0%})")
            score -= 20

        # ── REGLA 5: Same session (avoid self-concat) ─────────────
        date_a = frag_a.get("session_date", "")
        date_b = frag_b.get("session_date", "")
        if date_a == date_b:
            # Permitido pero penalizar levemente (preferir cross-session)
            score -= 5
            warnings.append("Same-session combination (prefer cross-session)")

        # ── REGLA 6: Hard fingerprint incompatibilities ───────────
        fps_a = set(frag_a.get("fingerprints", []))
        fps_b = set(frag_b.get("fingerprints", []))
        for fp_a, fp_b in HARD_INCOMPATIBILITIES:
            if fp_a in fps_a and fp_b in fps_b:
                return CompatibilityResult(
                    is_compatible = False,
                    score         = 0,
                    reason        = f"Hard incompatibility: {fp_a} cannot precede {fp_b}",
                    warnings      = []
                )
            if fp_b in fps_a and fp_a in fps_b:
                return CompatibilityResult(
                    is_compatible = False,
                    score         = 0,
                    reason        = f"Hard incompatibility: {fp_b} cannot precede {fp_a}",
                    warnings      = []
                )

        # ── BONUS: Quality alignment ──────────────────────────────
        ets_a = frag_a.get("ets_max", 0)
        ets_b = frag_b.get("ets_max", 0)
        # ETS drop from high to low is natural (edge expired → rotation)
        if ets_a >= 65 and ets_b <= 30:
            score += 5   # natural edge expiry
        # ETS jump from zero to high without OPENING_DRIVE is suspicious
        if ets_a <= 10 and ets_b >= 65 and type_b not in ("OPENING_DRIVE",
                                                            "VOL_RELEASE"):
            warnings.append("Suspicious ETS jump without structural cause")
            score -= 10

        score = max(0.0, min(100.0, score))
        ok    = score >= 40

        return CompatibilityResult(
            is_compatible = ok,
            score         = round(score, 1),
            reason        = "OK" if ok else f"Low coherence score ({score:.0f})",
            warnings      = warnings
        )

    def session_coherence_score(self, fragments: List[dict]) -> float:
        """
        Calcula el score de coherencia institucional de una secuencia
        de fragmentos. 0=incoherente, 100=perfectamente coherente.
        """
        if len(fragments) < 2:
            return 100.0

        scores = []
        for i in range(len(fragments) - 1):
            result = self.validate_transition(fragments[i], fragments[i+1])
            scores.append(result.score)

        return round(sum(scores) / len(scores), 1)

    def suggest_next(self,
                     current_frag: dict,
                     n: int = 5,
                     min_score: float = 60.0,
                     exclude_dates: List[str] = None) -> List[Tuple[dict, float]]:
        """
        Sugiere los mejores fragmentos para seguir al fragmento actual.
        Retorna lista de (fragmento, score) ordenada por score desc.
        """
        exclude_dates = exclude_dates or []
        candidates    = []

        for fid, frag in self.catalog.items():
            if frag.get("session_date") in exclude_dates:
                continue
            result = self.validate_transition(current_frag, frag)
            if result.is_compatible and result.score >= min_score:
                candidates.append((frag, result.score))

        candidates.sort(key=lambda x: -x[1])
        return candidates[:n]

    def validate_session(self,
                          fragments: List[dict]) -> Tuple[bool, List[str]]:
        """
        Valida una sesión sintética completa.
        Retorna (is_valid, list_of_issues).
        """
        issues = []

        # Debe empezar con PREMARKET u OPENING_DRIVE
        if fragments and fragments[0]["fragment_type"] not in (
                "PREMARKET", "OPENING_DRIVE"):
            issues.append(
                f"Session should start with PREMARKET or OPENING_DRIVE, "
                f"got {fragments[0]['fragment_type']}")

        # Debe terminar con EXHAUSTION o MIDDAY_ROTATION
        if fragments and fragments[-1]["fragment_type"] not in (
                "EXHAUSTION", "MIDDAY_ROTATION", "POWER_HOUR"):
            issues.append(
                f"Session should end with EXHAUSTION/MIDDAY_ROTATION, "
                f"got {fragments[-1]['fragment_type']}")

        # Mínimo de barras (sesión mínima = 150 barras)
        total_bars = sum(f.get("bar_count", 0) for f in fragments)
        if total_bars < 150:
            issues.append(f"Session too short ({total_bars} bars < 150 min)")

        # Validar transiciones
        for i in range(len(fragments) - 1):
            result = self.validate_transition(fragments[i], fragments[i+1])
            if not result.is_compatible:
                issues.append(
                    f"Transition {i}→{i+1} "
                    f"({fragments[i]['fragment_type']}→"
                    f"{fragments[i+1]['fragment_type']}): "
                    f"{result.reason}")

        # Distribución check (anti-overfitting)
        type_counts = {}
        for f in fragments:
            t = f.get("fragment_type", "")
            type_counts[t] = type_counts.get(t, 0) + 1
        total = len(fragments)
        for ftype, target in TARGET_DISTRIBUTION.items():
            actual = type_counts.get(ftype, 0) / max(total, 1)
            if abs(actual - target) > 0.25:   # 25% tolerance
                issues.append(
                    f"Distribution drift: {ftype} "
                    f"actual={actual:.0%} target={target:.0%}")

        return len(issues) == 0, issues

    def check_overfitting_risk(self,
                                fragments: List[dict]) -> Tuple[str, List[str]]:
        """
        Evalúa el riesgo de overfitting de una secuencia sintética.
        Returns ('LOW'|'MEDIUM'|'HIGH', reasons)
        """
        reasons = []

        # ¿Demasiados fragmentos de la misma sesión?
        dates = [f.get("session_date", "") for f in fragments]
        for d in set(dates):
            pct = dates.count(d) / len(dates)
            if pct > 0.40:
                reasons.append(
                    f"Over-represented session: {d} ({pct:.0%} of fragments)")

        # ¿ETS rate sintética vs real?
        ets_active = sum(1 for f in fragments if f.get("ets_max", 0) >= 65)
        ets_pct    = ets_active / max(len(fragments), 1)
        real_ets_pct = 24 / 410  # from catalog
        if ets_pct > real_ets_pct * 3:
            reasons.append(
                f"ETS_ACTIVE overrepresented: {ets_pct:.1%} vs real {real_ets_pct:.1%}")

        # ¿Opening drives excesivos?
        od_count = sum(1 for f in fragments
                       if f.get("fragment_type") == "OPENING_DRIVE")
        if od_count > 1:
            reasons.append(
                f"Too many OPENING_DRIVE fragments ({od_count}) — "
                f"should be max 1 per synthetic session")

        if len(reasons) >= 2:
            return "HIGH", reasons
        elif len(reasons) == 1:
            return "MEDIUM", reasons
        else:
            return "LOW", []

    def get_fragment_by_id(self, fid: str) -> Optional[dict]:
        return self.catalog.get(fid)

    def get_fragments_by_type(self,
                               ftype: str,
                               min_ets: int = 0,
                               max_hb: float = 1.0,
                               require_fingerprints: List[str] = None) -> List[dict]:
        """Filtra fragmentos del catálogo por criterios."""
        require_fingerprints = require_fingerprints or []
        results = []
        for frag in self.catalog.values():
            if frag.get("fragment_type") != ftype:
                continue
            if frag.get("ets_max", 0) < min_ets:
                continue
            if frag.get("hb_rate", 1.0) > max_hb:
                continue
            fps = set(frag.get("fingerprints", []))
            if not all(fp in fps for fp in require_fingerprints):
                continue
            results.append(frag)
        return results

    def print_catalog_stats(self):
        """Imprime estadísticas del catálogo cargado."""
        total = len(self.catalog)
        types = {}
        fps   = {}
        for frag in self.catalog.values():
            t = frag.get("fragment_type", "")
            types[t] = types.get(t, 0) + 1
            for fp in frag.get("fingerprints", []):
                fps[fp] = fps.get(fp, 0) + 1

        print(f"\n  FingerprintPreserver — catalog loaded")
        print(f"  Total fragments: {total}")
        print(f"\n  Type distribution:")
        for t, n in sorted(types.items(), key=lambda x: -x[1]):
            pct = n / total * 100
            print(f"    {t:<22} {n:3d}  ({pct:4.1f}%)")
        print(f"\n  Fingerprint frequency:")
        for fp, n in sorted(fps.items(), key=lambda x: -x[1])[:10]:
            pct = n / total * 100
            print(f"    {fp:<28} {n:3d}  ({pct:4.1f}%)")

    def demo_validation(self):
        """Demuestra la validación con fragmentos reales del catálogo."""
        if len(self.catalog) < 2:
            print("Catálogo insuficiente para demo.")
            return

        frags = list(self.catalog.values())
        print(f"\n{'='*65}")
        print(f"  FINGERPRINT PRESERVER — DEMO VALIDATION")
        print(f"{'─'*65}")

        # Demo 1: transición válida
        premarket = next(
            (f for f in frags if f.get("fragment_type") == "PREMARKET"
             and "LOW_HB_CLEAN" in f.get("fingerprints", [])), None)
        opening = next(
            (f for f in frags if f.get("fragment_type") == "OPENING_DRIVE"),
            None)
        midday = next(
            (f for f in frags if f.get("fragment_type") == "MIDDAY_ROTATION"
             and "LOW_HB_CLEAN" in f.get("fingerprints", [])), None)

        if premarket and opening:
            r = self.validate_transition(premarket, opening)
            print(f"\n  PREMARKET → OPENING_DRIVE")
            print(f"    compatible: {r.is_compatible}  score: {r.score}")
            print(f"    reason: {r.reason}")
            if r.warnings:
                for w in r.warnings:
                    print(f"    ⚠ {w}")

        # Demo 2: transición inválida (exhaustion → opening)
        exhaust = next(
            (f for f in frags if f.get("fragment_type") == "EXHAUSTION"),
            None)
        if exhaust and opening:
            r = self.validate_transition(exhaust, opening)
            print(f"\n  EXHAUSTION → OPENING_DRIVE  (should be INVALID)")
            print(f"    compatible: {r.is_compatible}  score: {r.score}")
            print(f"    reason: {r.reason}")

        # Demo 3: coherence score de secuencia real
        real_seq = [f for f in frags
                    if f.get("session_date") == "2026-03-11"]
        if len(real_seq) >= 3:
            real_seq_sorted = sorted(real_seq, key=lambda x: x.get("start_bar", 0))
            score = self.session_coherence_score(real_seq_sorted[:5])
            print(f"\n  Coherence score (2026-03-11 first 5 fragments): {score}")

        # Demo 4: suggest next
        if midday:
            print(f"\n  Top 3 fragments to follow a MIDDAY_ROTATION:")
            candidates = self.suggest_next(midday, n=3)
            for frag, sc in candidates:
                print(f"    [{frag['fragment_id']}] "
                      f"{frag['fragment_type']:<18} "
                      f"score={sc:5.1f}  "
                      f"ETS={frag.get('ets_max',0):3d}  "
                      f"HB={frag.get('hb_rate',0):.0%}")

        print(f"\n{'='*65}\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(
        description="GIBBZ V3 — Fingerprint Preserver")
    parser.add_argument("--stats",  action="store_true",
                        help="Estadísticas del catálogo")
    parser.add_argument("--demo",   action="store_true",
                        help="Demo de validación")
    parser.add_argument("--query",  type=str, default="",
                        help="Buscar fragmentos: TYPE:ETS_MIN:HB_MAX "
                             "(e.g. VOL_RELEASE:65:0.10)")
    args = parser.parse_args()

    fp = FingerprintPreserver()

    if args.stats:
        fp.print_catalog_stats()

    if args.demo:
        fp.demo_validation()

    if args.query:
        parts = args.query.split(":")
        ftype    = parts[0] if len(parts) > 0 else "VOL_RELEASE"
        ets_min  = int(parts[1]) if len(parts) > 1 else 0
        hb_max   = float(parts[2]) if len(parts) > 2 else 1.0
        results  = fp.get_fragments_by_type(ftype, ets_min, hb_max)
        print(f"\n  Query: {ftype}  ETS>={ets_min}  HB<={hb_max:.0%}")
        print(f"  Results: {len(results)} fragments")
        for f in sorted(results, key=lambda x: -x.get("ets_max", 0))[:10]:
            fps_str = " | ".join(f.get("fingerprints", [])[:3])
            print(f"    [{f['fragment_id']}]  "
                  f"ETS={f.get('ets_max',0):3d}  "
                  f"HB={f.get('hb_rate',0):.0%}  "
                  f"bars={f.get('bar_count',0):3d}  "
                  f"{fps_str}")

    if not any([args.stats, args.demo, args.query]):
        fp.print_catalog_stats()
        fp.demo_validation()