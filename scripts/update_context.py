"""
scripts/update_context.py
Interactive pre-session helper to update levels.json for today.

Auto-fetches:  PDH / PDL  via yfinance (MES=F — RTH OHLC, accurate to 2 ticks)
Manual entry:  ONH / ONL  from ATAS Globex chart (or ATAS Overnight Range indicator)
               VAH / VAL  from ATAS Volume Profile (TPO / Footprint) indicator
               POC        same as VAH/VAL

Rithmic path (fully automatic when ATAS is running):
  GibbzBridge.cs writes ~/gibbz_context_levels.json with real-time PDH/PDL/ONH/ONL.
  When that file is present and dated today, this script skips manual PDH/PDL/ONH/ONL
  and only asks for VAH/VAL/POC.

USO:
    python scripts/update_context.py
    python scripts/update_context.py --show     (show current levels, don't edit)
    python scripts/update_context.py --auto     (non-interactive: auto-fetch only, keep manual fields)
"""

import sys
import os
import json
import argparse
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

from context_fetcher import (
    LEVELS_JSON, BRIDGE_CONTEXT,
    _read_levels_json, _read_bridge_context, _fetch_pdh_pdl_yfinance,
    save_context, print_context_summary,
)


def _ask(prompt: str, current: float) -> float:
    """Prompt user for a float value; returns current if user just hits Enter."""
    try:
        raw = input(f"  {prompt} [{current}]: ").strip()
        return float(raw) if raw else current
    except (ValueError, EOFError):
        return current


def _sep(n: int = 60) -> None:
    print("  " + "-" * n)


def run_interactive(auto: bool = False) -> None:
    today = date.today().isoformat()
    print()
    print("=" * 64)
    print("  GIBBZ — Actualizar Contexto Pre-Sesion")
    print(f"  Fecha: {today}")
    print("=" * 64)

    # Load existing levels
    ctx = _read_levels_json()
    levels_date = ctx.get("_date", "N/A")
    vp  = ctx.get("volume_profile", {})
    ses = ctx.get("session", {})
    sg  = ctx.get("spotgamma", {})

    print(f"\n  Niveles actuales en levels.json (fecha: {levels_date})")
    _sep()
    print(f"  PDH={ses.get('prev_high','?')}  PDL={ses.get('prev_low','?')}")
    print(f"  ONH={ses.get('onh','?')}  ONL={ses.get('onl','?')}")
    print(f"  VAH={vp.get('VAH','?')}  VAL={vp.get('VAL','?')}  POC={vp.get('POC','?')}")

    # ── STEP 1: Auto-fetch or read PDH/PDL ───────────────────────────────
    print()
    print("  [1/3] PDH / PDL")
    _sep()

    bridge_data = _read_bridge_context()
    if bridge_data and bridge_data.get("date") == today:
        pdh = float(bridge_data.get("pdh") or ses.get("prev_high", 0))
        pdl = float(bridge_data.get("pdl") or ses.get("prev_low", 0))
        print(f"  Source: GibbzBridge / Rithmic  [100% preciso]")
        print(f"  PDH = {pdh}   PDL = {pdl}")
    else:
        print("  GibbzBridge file not found or stale — fetching from yfinance (MES=F)...")
        yf_pdh, yf_pdl = _fetch_pdh_pdl_yfinance()
        if yf_pdh and yf_pdl:
            print(f"  yfinance (MES=F, RTH): PDH={yf_pdh}  PDL={yf_pdl}")
            pdh, pdl = yf_pdh, yf_pdl
        else:
            print("  yfinance failed — enter manually.")
            pdh = ses.get("prev_high", 0.0)
            pdl = ses.get("prev_low", 0.0)

        if not auto:
            pdh = _ask("PDH (Previous Day High)", pdh)
            pdl = _ask("PDL (Previous Day Low)", pdl)

    ses["prev_high"] = pdh
    ses["prev_low"]  = pdl

    # ── STEP 2: ONH / ONL ────────────────────────────────────────────────
    print()
    print("  [2/3] ONH / ONL  (Overnight High / Low)")
    _sep()

    if bridge_data and bridge_data.get("date") == today and bridge_data.get("onh"):
        onh = float(bridge_data["onh"])
        onl = float(bridge_data["onl"])
        print(f"  Source: GibbzBridge / Rithmic  [100% preciso]")
        print(f"  ONH = {onh}   ONL = {onl}")
    else:
        print("  ATAS: Chart > Globex session | o usa 'Overnight Range' indicator")
        print("  yfinance NO tiene datos de sesion overnight (solo RTH)")
        onh = ses.get("onh", 0.0)
        onl = ses.get("onl", 0.0)
        if not auto:
            onh = _ask("ONH (Overnight High)", onh)
            onl = _ask("ONL (Overnight Low)", onl)
        else:
            print(f"  (--auto: manteniendo ONH={onh}  ONL={onl})")

    ses["onh"] = onh
    ses["onl"] = onl

    # ── STEP 3: VAH / VAL / POC ──────────────────────────────────────────
    print()
    print("  [3/3] VAH / VAL / POC  (Volume Profile)")
    _sep()
    print("  ATAS: Footprint chart > Volume Profile indicator")
    print("  Buscar: 70% Value Area High, Low, y Point of Control")
    print("  (No hay fuente automatica para VAH/VAL/POC sin ATAS)")

    vah = vp.get("VAH", 0.0)
    val = vp.get("VAL", 0.0)
    poc = vp.get("POC", 0.0)

    if not auto:
        vah = _ask("VAH (Value Area High)", vah)
        val = _ask("VAL (Value Area Low)",  val)
        poc = _ask("POC (Point of Control)", poc)
    else:
        print(f"  (--auto: manteniendo VAH={vah}  VAL={val}  POC={poc})")

    # Basic sanity check
    if val > 0 and poc > 0 and vah > 0:
        if not (val < poc < vah):
            print(f"\n  [WARN] VAL={val} < POC={poc} < VAH={vah} — orden incorrecto. Verifica los valores.")

    vp["VAH"] = vah
    vp["VAL"] = val
    vp["POC"] = poc

    # ── STEP 4: Optional open_price / ibh / ibl ──────────────────────────
    if not auto:
        print()
        print("  [Opcional] Open / IBH / IBL")
        _sep()
        print("  (Enter para mantener valores actuales)")
        ses["open_price"] = _ask("Open Price (session open)", ses.get("open_price", 0.0))
        ses["ibh"]        = _ask("IBH (Initial Balance High)", ses.get("ibh", 0.0))
        ses["ibl"]        = _ask("IBL (Initial Balance Low)",  ses.get("ibl", 0.0))

    # ── Save ──────────────────────────────────────────────────────────────
    ctx["volume_profile"] = vp
    ctx["session"]        = ses
    ctx["spotgamma"]      = sg
    ctx["_date"]          = today

    save_context(ctx)

    print()
    print("=" * 64)
    print("  Niveles guardados en levels.json")
    print_context_summary(ctx)
    print("  Listo para iniciar engine.py")
    print()


def run_show() -> None:
    ctx = _read_levels_json()
    ctx_today: dict = {}
    bridge = _read_bridge_context()
    if bridge and bridge.get("date") == date.today().isoformat():
        ctx_today["_source_pdh_pdl"] = "rithmic_atas"
        ctx_today["_source_onh_onl"] = "rithmic_atas"
    else:
        ctx_today["_source_pdh_pdl"] = "levels_json"
        ctx_today["_source_onh_onl"] = "levels_json"
    ctx_today["_source_vah_val"] = "levels_json"
    ctx_today["_date"] = ctx.get("_date", "N/A")
    ctx_today.update({k: v for k, v in ctx.items() if not k.startswith("_")})
    print_context_summary(ctx_today)


def main() -> None:
    parser = argparse.ArgumentParser(description="Actualizar contexto pre-sesion GIBBZ")
    parser.add_argument("--show", action="store_true", help="Mostrar niveles actuales sin editar")
    parser.add_argument("--auto", action="store_true", help="No interactivo: auto-fetch PDH/PDL, mantener resto")
    args = parser.parse_args()

    if args.show:
        run_show()
    else:
        run_interactive(auto=args.auto)


if __name__ == "__main__":
    main()
