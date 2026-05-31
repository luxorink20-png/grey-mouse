"""
GIBBZ — batch_shadow.py
Shadow Trading Batch Processor v1.0

Pipeline completo por sesión candidata:
  1. gibbz_launcher.py  — graba si no existe recording
  2. replay_debug_v3.py — genera observation file (--save-outcomes)
  3. gibbz_uei_core     — evaluación UEI en shadow mode
  4. export_shadow_expectancy — actualiza shadow score global al final

Criterios de reporte por sesión:
  GTAL_VALID >= 1  →  confirmación institucional presente
  ESI >= 60        →  edge estable para live (umbral del GATE)

USO:
  python batch_shadow.py               # top 10 candidatas automático
  python batch_shadow.py --top 5       # solo top 5
  python batch_shadow.py --date 2026-03-20  # sesión específica
  python batch_shadow.py --bars 1500   # max_bars para replay_debug
  python batch_shadow.py --force       # re-procesar aunque ya tenga obs
  python batch_shadow.py --all         # incluye sesiones con obs existente
"""

from __future__ import annotations
import argparse
import glob
import io
import json
import os
import subprocess
import sys
import contextlib
from datetime import datetime
from typing import Optional

# ── COLORES ───────────────────────────────────────────────────────
G  = "\033[92m"
R  = "\033[91m"
Y  = "\033[93m"
B  = "\033[94m"
C  = "\033[96m"
W  = "\033[97m"
DIM= "\033[2m"
RST= "\033[0m"
BOLD="\033[1m"

SEPARATOR = "─" * 66


def banner():
    print(f"\n{BOLD}{'═'*66}{RST}")
    print(f"{BOLD}  GIBBZ BATCH SHADOW PROCESSOR v1.0{RST}")
    print(f"{BOLD}{'═'*66}{RST}\n")


# ── DESCUBRIMIENTO DE CANDIDATAS ──────────────────────────────────

def load_expansion_outcomes() -> dict:
    outcomes = {}
    for f in sorted(glob.glob("expansion_outcomes/*_expansion.json")):
        with open(f, encoding="utf-8") as fp:
            d = json.load(fp)
        outcomes[d["session_date"]] = d
    return outcomes


def get_existing_obs() -> set:
    obs = set()
    for f in glob.glob("outcomes/*_observation.json"):
        date = os.path.basename(f).replace("_observation.json", "")
        obs.add(date)
    return obs


def find_candidates(top: int, force: bool, include_all: bool) -> list[dict]:
    """
    Devuelve lista de candidatas ordenadas por ep_score desc.
    Cada item: {date, ep, recording, session_type, has_obs, total_bars}
    """
    outcomes  = load_expansion_outcomes()
    has_obs   = get_existing_obs()
    candidates = []

    for date, d in outcomes.items():
        rec  = d.get("recording_file", "")
        ep   = d.get("ep_score", 0)
        bars = d.get("total_bars", 0)
        st   = d.get("session_type", "")

        # Filtros: recording existe, bars suficientes
        if ep < 30:
            continue
        if bars < 200:
            continue
        rec_path = f"recordings/{rec}" if rec else ""
        has_recording = rec and os.path.exists(rec_path)

        already_obs = date in has_obs

        if already_obs and not force and not include_all:
            continue  # ya procesada

        candidates.append({
            "date":         date,
            "ep":           ep,
            "recording":    rec,
            "rec_path":     rec_path if has_recording else "",
            "has_recording": has_recording,
            "session_type": st,
            "total_bars":   bars,
            "has_obs":      already_obs,
        })

    candidates.sort(key=lambda x: -x["ep"])
    return candidates[:top]


# ── SHADOW MODE UEI PARAMS ────────────────────────────────────────

def load_golive_esi() -> float:
    """Lee ESI del último golive_report del treadmill."""
    path = os.path.join("simulation", "golive_report.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f).get("esi", 0.0)
    return 0.0


SHADOW_EXTRA = {
    "pre_open_complete":   True,
    "is_macro_event":      False,
    "early_bar_efficiency": 35,
    "hb_rate":             0.07,
    "stress_survival_pct": 0.83,
    "treadmill_cycles":    50,
    "go_live_score":       84.1,
    "synthetic_real_delta": 8.0,
    "multi_regime_survival": True,
    # etil_lag_bars=20: simula ETIL que activa dentro de los primeros 20 bars
    # (shadow mode: asumimos apertura limpia con confirmación temprana)
    "etil_lag_bars":       20,
    # esi_tracker=0.0: deja que la fórmula calcule desde ets_score + etil_bonus
    # para que ETIL activo suba el ESI realísticamente
    "esi_tracker":         0.0,
}


# ── STEP 1: GRABACIÓN ─────────────────────────────────────────────

def ensure_recording(session: dict, timeout: int = 120) -> bool:
    """
    Si no hay recording, lanza gibbz_launcher.py y espera.
    Devuelve True si hay recording disponible al finalizar.
    """
    if session["has_recording"]:
        return True

    date = session["date"]
    print(f"\n  {Y}[LAUNCHER]{RST} No hay recording para {date}.")
    print(f"  Lanzando gibbz_launcher.py — carga {date} en ATAS y dale Play.")
    print()

    ret = subprocess.call(
        [sys.executable, "gibbz_launcher.py", date,
         "--timeout", str(timeout)],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    if ret != 0:
        print(f"  {R}[ERROR]{RST} Launcher terminó con error. Sesión omitida.")
        return False

    # Buscar el archivo más reciente en recordings/
    recs = sorted(glob.glob("recordings/*.jsonl"), key=os.path.getmtime, reverse=True)
    if recs:
        session["rec_path"] = recs[0]
        session["has_recording"] = True
        print(f"  {G}[OK]{RST} Recording: {os.path.basename(recs[0])}")
        return True

    return False


# ── STEP 2: REPLAY_DEBUG_V3 ───────────────────────────────────────

def run_replay_debug(session: dict, bars: int) -> bool:
    """
    Corre replay_debug_v3.py silenciosamente y guarda observation file.
    Devuelve True si el observation file fue creado.
    """
    date     = session["date"]
    rec_path = session["rec_path"]
    obs_path = f"outcomes/{date}_observation.json"

    print(f"  {C}[REPLAY]{RST} replay_debug_v3 → {date} ({bars} bars) ...", end="", flush=True)

    result = subprocess.run(
        [sys.executable, "replay_debug_v3.py", rec_path,
         "--date", date, "--bars", str(bars), "--save-outcomes"],
        capture_output=True,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    if os.path.exists(obs_path):
        with open(obs_path, encoding="utf-8") as f:
            obs = json.load(f)
        gtal = obs.get("gtal_valid_bars", 0)
        et   = obs.get("efficient_trend_bars", 0)
        print(f" {G}OK{RST}  (GTAL_V={gtal}  ET_bars={et})")
        return True
    else:
        print(f" {R}FAIL{RST}")
        if result.stderr:
            err = result.stderr.decode("utf-8", errors="replace").strip().split("\n")
            for line in err[-3:]:
                print(f"    {DIM}{line}{RST}")
        return False


# ── STEP 3: UEI EVALUATION ────────────────────────────────────────

def run_uei(session: dict) -> Optional[dict]:
    """
    Evalúa la sesión con UEI en shadow mode.
    Devuelve dict con métricas clave o None si falla.
    """
    date     = session["date"]
    obs_path = f"outcomes/{date}_observation.json"

    try:
        from gibbz_uei_core import UEICore
        core   = UEICore()
        result = core.evaluate_from_outcome(obs_path, extra=SHADOW_EXTRA)

        with open(obs_path, encoding="utf-8") as f:
            obs = json.load(f)

        gtal_valid = obs.get("gtal_valid_bars", 0)
        et_bars    = obs.get("efficient_trend_bars", 0)
        max_ets    = obs.get("max_ets", 0)

        return {
            "date":       date,
            "ep":         session["ep"],
            "session_type": session["session_type"],
            "gtal_valid": gtal_valid,
            "et_bars":    et_bars,
            "max_ets":    max_ets,
            "ecl":        result.ecl.classification,
            "esi":        round(result.ecl.esi, 1),
            "gate":       result.gate.decision,
            "lcs":        result.lcl.live_confidence_score,
            "esf":        result.edge_score_final,
            "stability":  result.lcl.decision_stability,
            "etil_active": et_bars > 0,
            "gtal_pass":  gtal_valid >= 1,
            "esi_pass":   result.ecl.esi >= 60,
            "shadow_ready": gtal_valid >= 1 and result.ecl.esi >= 60,
        }

    except Exception as e:
        print(f"  {R}[UEI ERROR]{RST} {e}")
        return None


# ── STEP 4: EXPORT SHADOW EXPECTANCY ─────────────────────────────

def run_export_shadow() -> Optional[dict]:
    """Actualiza logs/shadow_expectancy.json y devuelve datos clave."""
    try:
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            import importlib, export_shadow_expectancy as ese
            importlib.reload(ese)
            ese.run_and_export()

        if os.path.exists(os.path.join("logs", "shadow_expectancy.json")):
            with open(os.path.join("logs", "shadow_expectancy.json"),
                      encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"  {R}[SHADOW EXPORT ERROR]{RST} {e}")
    return None


# ── REPORTE POR SESIÓN ────────────────────────────────────────────

def print_session_report(r: dict, idx: int, total: int):
    gtal_icon = G + "✅ GTAL_VALID" + RST if r["gtal_pass"]   else R + "✗ GTAL_NONE"  + RST
    esi_icon  = G + "✅ ESI≥60"    + RST if r["esi_pass"]    else Y + f"⚠ ESI={r['esi']}" + RST
    gate_icon = G + "APPROVED"     + RST if r["gate"] == "APPROVED" \
                else Y + "SHADOW"  + RST if r["gate"] == "SHADOW_ONLY" \
                else R + "REJECTED" + RST

    shadow_verdict = (G + BOLD + "SHADOW READY" + RST) if r["shadow_ready"] \
                     else (DIM + "NO SHADOW" + RST)

    print(f"\n  [{idx}/{total}]  {BOLD}{r['date']}{RST}  "
          f"ep={r['ep']}  {r['session_type']}")
    print(f"  {SEPARATOR}")
    print(f"  {gtal_icon:<30}  GTAL_V={r['gtal_valid']}  ET_bars={r['et_bars']}")
    print(f"  {esi_icon:<30}  ESF={r['esf']}  ECL={r['ecl'].replace('_EDGE','')}")
    print(f"  GATE: {gate_icon:<20}  LCS={r['lcs']}  Stability={r['stability']}")
    print(f"  VEREDICTO SHADOW: {shadow_verdict}")


# ── RESUMEN FINAL ─────────────────────────────────────────────────

def print_final_summary(results: list[dict], shadow_data: Optional[dict]):
    print(f"\n\n{'═'*66}")
    print(f"{BOLD}  RESUMEN FINAL — BATCH SHADOW{RST}")
    print(f"{'═'*66}")
    print(f"\n  {'Fecha':<14} {'EP':>4}  {'GTAL':>5}  {'ESI':>6}  {'ESF':>4}  {'Gate':<12}  Shadow")
    print(f"  {'-'*62}")

    shadow_ready = []
    for r in results:
        gtal_m = G+"✅"+RST if r["gtal_pass"] else R+"✗ "+RST
        esi_m  = G+"✅"+RST if r["esi_pass"]  else Y+f"{r['esi']:4.0f}"+RST
        gate_m = (G+"APPROVED" +RST if r["gate"]=="APPROVED"
                  else Y+"SHADOW"   +RST if r["gate"]=="SHADOW_ONLY"
                  else DIM+"REJECTED"+RST)
        sv_m   = G+BOLD+"READY"+RST if r["shadow_ready"] else DIM+"---"+RST
        print(f"  {r['date']:<14} {r['ep']:>4}  {gtal_m}  {esi_m}   "
              f"{r['esf']:>4}  {gate_m:<20}  {sv_m}")
        if r["shadow_ready"]:
            shadow_ready.append(r["date"])

    print(f"\n  Sesiones SHADOW READY (GTAL≥1 + ESI≥60): "
          f"{G}{BOLD}{len(shadow_ready)}/{len(results)}{RST}")
    for d in shadow_ready:
        print(f"    {G}→ {d}{RST}")

    if shadow_data:
        sr  = shadow_data.get("shadow_readiness", {})
        sc  = sr.get("score", 0)
        rdy = sr.get("ready", False)
        wr  = shadow_data.get("win_rate_pct", 0)
        exp = shadow_data.get("expectancy_pts", 0)
        sc_col = G if sc >= 0.80 else Y if sc >= 0.65 else R
        print(f"\n  {'─'*50}")
        print(f"  Shadow Score Global : {sc_col}{BOLD}{sc:.3f}{RST}  "
              f"({'OK READY' if rdy else 'NOT READY'})")
        print(f"  Win rate (sim)      : {wr}%")
        print(f"  Expectancy          : {exp} pts/trade")

    print(f"\n{'═'*66}\n")


# ── MAIN ──────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="GIBBZ Batch Shadow Processor")
    p.add_argument("--top",    type=int, default=10,
                   help="Número de sesiones a procesar (default: 10)")
    p.add_argument("--bars",   type=int, default=1500,
                   help="Max bars para replay_debug_v3 (default: 1500)")
    p.add_argument("--date",   type=str, default="",
                   help="Procesar solo esta fecha (YYYY-MM-DD)")
    p.add_argument("--force",  action="store_true",
                   help="Re-procesar aunque ya tenga observation file")
    p.add_argument("--all",    action="store_true",
                   help="Incluir sesiones que ya tienen observation file")
    p.add_argument("--no-export", action="store_true",
                   help="Saltar export_shadow_expectancy al final")
    p.add_argument("--timeout", type=int, default=120,
                   help="Timeout segundos para gibbz_launcher (default: 120)")
    return p.parse_args()


def main():
    args   = parse_args()
    banner()

    # ── Selección de candidatas ────────────────────────────────────
    if args.date:
        outcomes = load_expansion_outcomes()
        has_obs  = get_existing_obs()
        if args.date not in outcomes:
            print(f"{R}[ERROR]{RST} {args.date} no encontrada en expansion_outcomes.")
            sys.exit(1)
        d = outcomes[args.date]
        rec = d.get("recording_file", "")
        candidates = [{
            "date":         args.date,
            "ep":           d.get("ep_score", 0),
            "recording":    rec,
            "rec_path":     f"recordings/{rec}" if rec and os.path.exists(f"recordings/{rec}") else "",
            "has_recording": rec and os.path.exists(f"recordings/{rec}"),
            "session_type": d.get("session_type", ""),
            "total_bars":   d.get("total_bars", 0),
            "has_obs":      args.date in has_obs,
        }]
    else:
        candidates = find_candidates(
            top=args.top,
            force=args.force,
            include_all=getattr(args, "all", False),
        )

    if not candidates:
        print(f"{Y}No hay candidatas disponibles.{RST}")
        print("  Todas las sesiones con ep≥30 ya tienen observation file.")
        print("  Usa --force o --all para re-procesar.\n")
        sys.exit(0)

    print(f"  {BOLD}Candidatas seleccionadas: {len(candidates)}{RST}")
    print(f"  {'Fecha':<14} {'EP':>4}  {'Bars':>6}  {'Session Type':<22} {'Obs':>5}")
    print(f"  {'-'*60}")
    for s in candidates:
        obs_flag = f"{G}SÍ{RST}" if s["has_obs"] else DIM+"NO"+RST
        rec_flag = f"{G}OK{RST}" if s["has_recording"] else f"{R}NONE{RST}"
        print(f"  {s['date']:<14} {s['ep']:>4}  {s['total_bars']:>6}  "
              f"{s['session_type']:<22} {obs_flag}  rec={rec_flag}")
    print()

    # ── Pipeline por sesión ───────────────────────────────────────
    all_results = []
    total = len(candidates)

    for idx, session in enumerate(candidates, 1):
        date = session["date"]
        print(f"\n{SEPARATOR}")
        print(f"  SESIÓN {idx}/{total}: {BOLD}{date}{RST}  "
              f"ep={session['ep']}  {session['session_type']}")
        print(SEPARATOR)

        # Step 1: garantizar recording
        if not ensure_recording(session, timeout=args.timeout):
            print(f"  {R}[SKIP]{RST} Sin recording — sesión omitida.\n")
            continue

        # Step 2: replay_debug_v3 → observation file
        obs_exists = os.path.exists(f"outcomes/{date}_observation.json")
        if obs_exists and not args.force:
            print(f"  {DIM}[REPLAY]{RST} Observation ya existe — omitiendo replay_debug.")
        else:
            ok = run_replay_debug(session, bars=args.bars)
            if not ok:
                print(f"  {R}[SKIP]{RST} replay_debug_v3 falló — sesión omitida.\n")
                continue

        # Step 3: UEI en shadow mode
        print(f"  {B}[UEI]{RST}   Evaluando shadow mode ...", end="", flush=True)
        r = run_uei(session)
        if r is None:
            print(f" {R}FAIL{RST}\n")
            continue
        print(f" {G}OK{RST}")

        # Reporte inmediato
        print_session_report(r, idx, total)
        all_results.append(r)

    # ── Step 4: export shadow expectancy global ───────────────────
    shadow_data = None
    if not args.no_export and all_results:
        print(f"\n{SEPARATOR}")
        print(f"  {C}[EXPORT]{RST} Actualizando shadow_expectancy.json ...",
              end="", flush=True)
        shadow_data = run_export_shadow()
        if shadow_data:
            sc = shadow_data.get("shadow_readiness", {}).get("score", 0)
            print(f" {G}OK{RST}  score={sc:.3f}")
        else:
            print(f" {Y}WARN — datos no disponibles{RST}")

    # ── Resumen final ──────────────────────────────────────────────
    if all_results:
        print_final_summary(all_results, shadow_data)
    else:
        print(f"\n{R}No se procesó ninguna sesión correctamente.{RST}\n")


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
