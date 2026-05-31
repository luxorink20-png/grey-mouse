"""
set_session.py — Carga historical_context/YYYY-MM-DD.json → levels.json

Uso:
    python set_session.py 2026-04-29
    python set_session.py 2026-02-13

Actualiza levels.json con los niveles exactos de la sesión objetivo
antes de correr engine.py con ATAS replay.
"""

import json
import os
import sys
from pathlib import Path

CORE_DIR    = Path(__file__).parent
CTX_DIR     = CORE_DIR / "historical_context"
LEVELS_PATH = CORE_DIR / "levels.json"


def load_context(date: str) -> dict:
    path = CTX_DIR / f"{date}.json"
    if not path.exists():
        sys.exit(f"ERROR: No existe historical_context/{date}.json")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def build_levels(ctx: dict) -> dict:
    vp  = ctx["volume_profile"]
    sg  = ctx["spotgamma"]
    ses = ctx["session"]

    return {
        "volume_profile": {
            "VAH": vp["vah"],
            "POC": vp["poc"],
            "VAL": vp["val"],
        },
        "spotgamma": {
            "call_wall":          sg["call_wall"],
            "put_wall":           sg["put_wall"],
            "zero_gamma":         sg["zero_gamma"],
            "volatility_trigger": sg["volatility_trigger"],
            "hpz":                sg["hpz"],
        },
        "session": {
            "prev_high":  ses["prev_high"],
            "prev_low":   ses["prev_low"],
            "prev_close": ses["prev_close"],
            "open_price": ses["open_price"],
            "onh":        ses["onh"],
            "onl":        ses["onl"],
            "ibh":        ses["ibh"],
            "ibl":        ses["ibl"],
        },
    }


def main():
    if len(sys.argv) < 2:
        sys.exit("Uso: python set_session.py YYYY-MM-DD")

    date = sys.argv[1].strip()

    ctx    = load_context(date)
    levels = build_levels(ctx)

    with open(LEVELS_PATH, "w", encoding="utf-8") as f:
        json.dump(levels, f, indent=2)

    vp  = levels["volume_profile"]
    ses = levels["session"]
    sg  = levels["spotgamma"]

    print(f"\n  Session cargada: {date}")
    print(f"  VAH={vp['VAH']}  POC={vp['POC']}  VAL={vp['VAL']}")
    print(f"  Call Wall={sg['call_wall']}  Put Wall={sg['put_wall']}")
    print(f"  HPZ={sg['hpz']}  Open={ses['open_price']}")
    print(f"  PDH={ses['prev_high']}  PDL={ses['prev_low']}")
    print(f"  IBH={ses['ibh']}  IBL={ses['ibl']}")
    print(f"\n  levels.json actualizado. Corre: python -X utf8 engine.py\n")


if __name__ == "__main__":
    main()
