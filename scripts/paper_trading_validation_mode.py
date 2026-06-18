"""
paper_trading_validation_mode.py — GIBBZ Paper Trading Validation Launcher

Arranca engine.py con PAPER_VALIDATION_MODE activado:
  - ContextFilter.should_skip() se ejecuta normalmente (tracking + logs)
  - Si hubiera bloqueado: log [VALIDATION SKIP] — trade abre igualmente
  - Si NO hubiera bloqueado: trade abre normalmente (sin cambios)
  - OVERRIDE_SESSION=1 por defecto para operar fuera de horario normal

Uso:
    python scripts/paper_trading_validation_mode.py

Equivalente manual:
    $env:GIBBZ_PAPER_VALIDATION_MODE = "1"
    $env:GIBBZ_OVERRIDE_SESSION      = "1"
    python engine.py

Qué buscar en logs después:
    [VALIDATION SKIP] → setup que ContextFilter habría bloqueado pero pasó
    CONTEXT SKIP      → (no aparece en este modo)
    [CALIBRATION]     → solo si también activas CALIBRATION_MODE

Análisis post-sesión:
    python scripts/daily_paper_trading_report.py
    Get-Content logs\\gibbz.log | Select-String "VALIDATION SKIP"
"""

import os
import sys

# ── Activar modo antes de cualquier import del engine ─────────────────────────
# config.py lee estos valores en tiempo de módulo, así que deben estar presentes
# antes de que engine.py sea importado.
os.environ.setdefault("GIBBZ_PAPER_VALIDATION_MODE", "1")
os.environ.setdefault("GIBBZ_OVERRIDE_SESSION",      "1")

# ── Añadir core/ al path ──────────────────────────────────────────────────────
_core_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _core_dir not in sys.path:
    sys.path.insert(0, _core_dir)

# ── Banner ────────────────────────────────────────────────────────────────────
print("=" * 56)
print("  GIBBZ — PAPER TRADING VALIDATION MODE")
print("=" * 56)
print("  PAPER_VALIDATION_MODE : ON")
print("  OVERRIDE_SESSION      : ON")
print()
print("  ContextFilter skips → [VALIDATION SKIP] in log")
print("  Todos los setups quality-approved abren trade.")
print()
print("  Detener con Ctrl+C")
print("=" * 56)
print()

# ── Importar y arrancar engine ────────────────────────────────────────────────
# El import ocurre DESPUÉS de setear los env vars, así que config.py
# leerá PAPER_VALIDATION_MODE=True correctamente.
import engine  # noqa: E402  (importado después del os.environ setup)
engine.run_engine()
