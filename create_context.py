"""
GIBBZ Create Historical Context
Helper para crear archivos de contexto histórico.

USO:
  python create_context.py 2026-05-05
  → crea historical_context/2026-05-05.json con template
  → luego editás los niveles manualmente
"""

import sys
import json
import os

CONTEXT_DIR = "historical_context"

TEMPLATE = {
    "date": "",
    "volume_profile": {
        "vah": 0.0,
        "poc": 0.0,
        "val": 0.0
    },
    "spotgamma": {
        "call_wall":          0.0,
        "put_wall":           0.0,
        "zero_gamma":         0.0,
        "volatility_trigger": 0.0,
        "hpz":                0.0,
        "combo_1":            0.0,
        "combo_2":            0.0,
        "large_gamma_1":      0.0,
        "large_gamma_2":      0.0,
        "large_gamma_3":      0.0,
        "large_gamma_4":      0.0
    },
    "session": {
        "prev_high":   0.0,
        "prev_low":    0.0,
        "prev_close":  0.0,
        "open_price":  0.0,
        "onh":         0.0,
        "onl":         0.0,
        "ibh":         0.0,
        "ibl":         0.0
    }
}

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("USO: python create_context.py YYYY-MM-DD")
        sys.exit(1)

    replay_date = sys.argv[1]

    os.makedirs(CONTEXT_DIR, exist_ok=True)
    path = os.path.join(CONTEXT_DIR, replay_date + ".json")

    if os.path.exists(path):
        print("Ya existe: " + path)
        print("Editalo directamente con los niveles correctos.")
        sys.exit(0)

    template        = dict(TEMPLATE)
    template["date"] = replay_date

    with open(path, "w", encoding="utf-8") as f:
        json.dump(template, f, indent=2)

    print("╔══ CONTEXT CREADO ══════════════════════════╗")
    print("  Archivo : " + path)
    print("  Fecha   : " + replay_date)
    print("╠════════════════════════════════════════════╣")
    print("  Completar manualmente:")
    print("  1. volume_profile → VAH, POC, VAL (de ATAS)")
    print("  2. spotgamma      → niveles SpotGamma Alpha")
    print("  3. session        → PDH, PDL, ONH, ONL")
    print("╚════════════════════════════════════════════╝")