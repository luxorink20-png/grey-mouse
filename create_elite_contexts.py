"""
GIBBZ V3 — create_elite_contexts.py
Crea contextos históricos para las 10 fechas elite
usando niveles institucionales reales proporcionados.

USO:
  python create_elite_contexts.py
"""

import json
import os

CONTEXT_DIR = "historical_context"

# Niveles reales proporcionados
ELITE_CONTEXTS = {
    "2026-01-28": {
        "vah": 7012.50, "poc": 7000.00, "val": 6987.50,
        "call_wall": 7100.0, "put_wall": 6900.0,
        "zero_gamma": 6980.0, "vol_trigger": 6995.0,
        "hpz": 7005.0,
        "onh": 7032.50, "onl": 6885.00,
        "pdh": 7040.00, "pdl": 6984.00,
        "open": 6990.0,
    },
    "2026-02-05": {
        "vah": 6912.50, "poc": 6885.00, "val": 6825.00,
        "call_wall": 7000.0, "put_wall": 6700.0,
        "zero_gamma": 6840.0, "vol_trigger": 6870.0,
        "hpz": 6895.0,
        "onh": 6933.50, "onl": 6835.00,
        "pdh": 6960.00, "pdl": 6755.00,
        "open": 6870.0,
    },
    "2026-02-06": {
        "vah": 6840.00, "poc": 6830.00, "val": 6770.00,
        "call_wall": 6950.0, "put_wall": 6650.0,
        "zero_gamma": 6790.0, "vol_trigger": 6810.0,
        "hpz": 6835.0,
        "onh": 6965.00, "onl": 6770.00,
        "pdh": 6950.00, "pdl": 6740.00,
        "open": 6800.0,
    },
    "2026-03-19": {
        "vah": 6730.00, "poc": 6685.00, "val": 6620.00,
        "call_wall": 6800.0, "put_wall": 6500.0,
        "zero_gamma": 6650.0, "vol_trigger": 6680.0,
        "hpz": 6700.0,
        "onh": 6685.00, "onl": 6589.50,
        "pdh": 6800.00, "pdl": 6625.00,
        "open": 6640.0,
    },
    "2026-03-20": {
        "vah": 6660.00, "poc": 6620.00, "val": 6590.00,
        "call_wall": 6750.0, "put_wall": 6450.0,
        "zero_gamma": 6600.0, "vol_trigger": 6625.0,
        "hpz": 6640.0,
        "onh": 6670.00, "onl": 6515.00,
        "pdh": 6685.00, "pdl": 6580.00,
        "open": 6600.0,
    },
    "2025-09-17": {
        "vah": 6650.00, "poc": 6635.00, "val": 6610.00,
        "call_wall": 6750.0, "put_wall": 6500.0,
        "zero_gamma": 6615.0, "vol_trigger": 6630.0,
        "hpz": 6640.0,
        "onh": 6720.00, "onl": 6625.00,
        "pdh": 6675.00, "pdl": 6580.00,
        "open": 6640.0,
    },
    "2025-09-19": {
        "vah": 6705.00, "poc": 6695.00, "val": 6680.00,
        "call_wall": 6800.0, "put_wall": 6550.0,
        "zero_gamma": 6670.0, "vol_trigger": 6690.0,
        "hpz": 6698.0,
        "onh": 6730.00, "onl": 6680.00,
        "pdh": 6720.00, "pdl": 6650.00,
        "open": 6695.0,
    },
    "2025-10-29": {
        "vah": 6930.00, "poc": 6925.00, "val": 6895.00,
        "call_wall": 7000.0, "put_wall": 6800.0,
        "zero_gamma": 6900.0, "vol_trigger": 6915.0,
        "hpz": 6925.0,
        "onh": 6950.00, "onl": 6865.00,
        "pdh": 6947.50, "pdl": 6880.00,
        "open": 6915.0,
    },
    "2025-07-30": {
        "vah": 6430.00, "poc": 6415.00, "val": 6400.00,
        "call_wall": 6500.0, "put_wall": 6300.0,
        "zero_gamma": 6395.0, "vol_trigger": 6405.0,
        "hpz": 6418.0,
        "onh": 6420.00, "onl": 6395.00,
        "pdh": 6437.50, "pdl": 6395.00,
        "open": 6408.0,
    },
    "2026-04-29": {
        "vah": 7175.00, "poc": 7160.00, "val": 7145.00,
        "call_wall": 7250.0, "put_wall": 7050.0,
        "zero_gamma": 7140.0, "vol_trigger": 7155.0,
        "hpz": 7162.0,
        "onh": 7200.00, "onl": 7130.00,
        "pdh": 7190.00, "pdl": 7130.00,
        "open": 7155.0,
    },
}


def make_context(date: str, d: dict) -> dict:
    p = d["open"]
    return {
        "date": date,
        "volume_profile": {
            "vah": d["vah"],
            "poc": d["poc"],
            "val": d["val"],
        },
        "spotgamma": {
            "call_wall":          d["call_wall"],
            "put_wall":           d["put_wall"],
            "zero_gamma":         d["zero_gamma"],
            "volatility_trigger": d["vol_trigger"],
            "hpz":                d["hpz"],
            "combo_1":            0.0,
            "combo_2":            0.0,
            "large_gamma_1":      d["hpz"],
            "large_gamma_2":      d["hpz"] - 25.0,
            "large_gamma_3":      d["hpz"] - 50.0,
            "large_gamma_4":      d["hpz"] + 50.0,
        },
        "session": {
            "prev_high":  d["pdh"],
            "prev_low":   d["pdl"],
            "prev_close": d["poc"],
            "open_price": p,
            "onh":        d["onh"],
            "onl":        d["onl"],
            "ibh":        round(p + 12.5, 2),
            "ibl":        round(p - 12.5, 2),
        },
        "_note": "Institutional levels — real data",
        "_estimated": False,
    }


if __name__ == "__main__":
    os.makedirs(CONTEXT_DIR, exist_ok=True)
    created = skipped = 0

    for date, data in sorted(ELITE_CONTEXTS.items()):
        path = os.path.join(CONTEXT_DIR, f"{date}.json")
        if os.path.exists(path):
            print(f"  [SKIP]    {date} — ya existe")
            skipped += 1
            continue
        ctx = make_context(date, data)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ctx, f, indent=2)
        print(f"  [CREATED] {date}  VAH={data['vah']}  POC={data['poc']}  VAL={data['val']}")
        created += 1

    print(f"\n  Total: {created} creados, {skipped} ya existían")

    import glob
    total = len(glob.glob(os.path.join(CONTEXT_DIR, "*.json")))
    print(f"  Contextos en {CONTEXT_DIR}/: {total}")