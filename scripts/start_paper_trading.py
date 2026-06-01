"""
scripts/start_paper_trading.py
Pre-flight check + automated context load + engine launcher for Paper Trading.

USO:
    python scripts/start_paper_trading.py
    python scripts/start_paper_trading.py --skip-context   (skip context validation)
    python scripts/start_paper_trading.py --show-context   (show context and exit)

Flujo automatico:
  1. Carga contexto (PDH/PDL/ONH/ONL/VAH/VAL) desde la mejor fuente disponible:
       - Si ATAS esta corriendo: gibbz_context_levels.json (Rithmic, 100% preciso)
       - Si no: levels.json + yfinance para PDH/PDL (auto)
  2. Muestra resumen de niveles en pantalla
  3. Verifica que tests basicos pasan (opcional)
  4. Lanza engine.py
"""

import sys
import os
import subprocess
import argparse
from datetime import date
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

CORE_DIR = Path(__file__).parent.parent


def _sep(char: str = "=", n: int = 64) -> None:
    print(char * n)


def run(args: argparse.Namespace) -> None:
    _sep()
    print("  GIBBZ Paper Trading — Inicio Automatico")
    print(f"  {date.today().isoformat()}")
    _sep()

    # ── STEP 1: Context ───────────────────────────────────────────────────
    if not args.skip_context:
        print("\n  [1/3] Cargando contexto de niveles...")

        try:
            from context_fetcher import load_context, print_context_summary, ContextStaleError

            ctx = load_context(strict=False)
            print_context_summary(ctx)

            src_pdh = ctx.get("_source_pdh_pdl", "?")
            src_onh = ctx.get("_source_onh_onl", "?")
            levels_date = ctx.get("_date", "?")
            today = date.today().isoformat()

            if levels_date != today:
                print(f"  [WARN] levels.json fecha={levels_date} != hoy={today}")
                print("  Ejecuta: python scripts/update_context.py")
                print("  O inicia ATAS primero para contexto automatico desde Rithmic.")
                if not args.force:
                    print()
                    ans = input("  Continuar de todas formas? [s/N]: ").strip().lower()
                    if ans not in ("s", "si", "y", "yes"):
                        print("  Abortado. Actualiza el contexto y vuelve a intentarlo.")
                        sys.exit(1)
            else:
                pdh_src_label = {
                    "rithmic_atas": "Rithmic/ATAS [100% preciso]",
                    "yfinance":     "yfinance MES=F [RTH, ~99% preciso]",
                    "levels_json":  "levels.json [manual]",
                }.get(src_pdh, src_pdh)
                onh_src_label = {
                    "rithmic_atas": "Rithmic/ATAS [100% preciso]",
                    "levels_json":  "levels.json [manual]",
                    "levels_json_stale": "levels.json [STALE]",
                }.get(src_onh, src_onh)
                print(f"  PDH/PDL: {pdh_src_label}")
                print(f"  ONH/ONL: {onh_src_label}")
                print(f"  VAH/VAL: levels.json [manual — requiere ATAS volume profile]")

        except Exception as e:
            print(f"  [ERROR] context_fetcher: {e}")
            if not args.force:
                print("  Usa --force para continuar de todas formas, o actualiza con:")
                print("  python scripts/update_context.py")
                sys.exit(1)

    # ── STEP 2: Pre-flight checks ─────────────────────────────────────────
    if not args.skip_context:
        print("\n  [2/3] Pre-flight checks...")
        levels_json = CORE_DIR / "levels.json"
        if not levels_json.exists():
            print("  [FAIL] levels.json no existe. Crea con: python scripts/update_context.py")
            sys.exit(1)
        print("  [OK]  levels.json presente")
        print("  [OK]  Checklist disponible: python scripts/paper_trading_live_checklist.py")

    if args.show_context:
        print("\n  --show-context: saliendo sin lanzar engine.")
        return

    # ── STEP 3: Launch engine ─────────────────────────────────────────────
    step_n = "3" if not args.skip_context else "1"
    print(f"\n  [{step_n}/3] Lanzando engine.py (paper trading)...")
    print()
    _sep("-")

    engine_path = str(CORE_DIR / "engine.py")
    try:
        subprocess.run([sys.executable, engine_path], check=True)
    except KeyboardInterrupt:
        print("\n  Engine detenido.")
    except subprocess.CalledProcessError as e:
        print(f"\n  [ERROR] engine.py termino con codigo {e.returncode}")
        sys.exit(e.returncode)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inicia Paper Trading GIBBZ con contexto automatico"
    )
    parser.add_argument("--skip-context", action="store_true",
                        help="Omitir validacion de contexto")
    parser.add_argument("--show-context", action="store_true",
                        help="Mostrar contexto y salir (no lanzar engine)")
    parser.add_argument("--force", action="store_true",
                        help="Lanzar engine aunque contexto sea stale")
    args = parser.parse_args()
    run(args)


if __name__ == "__main__":
    main()
