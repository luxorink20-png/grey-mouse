"""
GIBBZ V3 — create_missing_contexts.py
Crea contextos históricos para fechas sin archivo .json

Usa el precio de apertura del recording para estimar
VAH/POC/VAL del día anterior (aproximación institucional).

USO:
  python create_missing_contexts.py
"""

import json
import os
import glob

CONTEXT_DIR = "historical_context"

# Datos conocidos: fecha → (price_open, VAH, POC, VAL, extras)
# VAH/POC/VAL estimados del día anterior basados en precio apertura
# Regla: VAH ≈ open + 12.5, POC ≈ open - 2.5, VAL ≈ open - 17.5
# Para sesiones 2025 usamos precio real de apertura del recording

MISSING_CONTEXTS = {
    # 2025 sessions — precios reales del primer tick
    "2025-02-13": {
        "price_open": 6087.0,
        "vah": 6100.0, "poc": 6087.5, "val": 6075.0,
        "call_wall": 6200.0, "put_wall": 5900.0,
        "zero_gamma": 6050.0, "vol_trigger": 6075.0,
        "hpz": 6095.0, "onh": 6110.0, "onl": 6070.0,
        "pdh": 6115.0, "pdl": 6060.0,
        "note": "Estimated from opening price 6087"
    },
    "2025-03-19": {
        "price_open": 5676.0,
        "vah": 5700.0, "poc": 5675.0, "val": 5650.0,
        "call_wall": 5800.0, "put_wall": 5500.0,
        "zero_gamma": 5640.0, "vol_trigger": 5660.0,
        "hpz": 5680.0, "onh": 5710.0, "onl": 5650.0,
        "pdh": 5720.0, "pdl": 5640.0,
        "note": "Estimated from opening price 5676"
    },
    "2025-04-04": {
        "price_open": 5423.75,
        "vah": 5450.0, "poc": 5425.0, "val": 5400.0,
        "call_wall": 5550.0, "put_wall": 5200.0,
        "zero_gamma": 5380.0, "vol_trigger": 5400.0,
        "hpz": 5430.0, "onh": 5460.0, "onl": 5390.0,
        "pdh": 5475.0, "pdl": 5380.0,
        "note": "Estimated from opening price 5423.75"
    },
    "2025-04-10": {
        "price_open": 5472.5,
        "vah": 5500.0, "poc": 5475.0, "val": 5450.0,
        "call_wall": 5600.0, "put_wall": 5300.0,
        "zero_gamma": 5440.0, "vol_trigger": 5460.0,
        "hpz": 5480.0, "onh": 5510.0, "onl": 5440.0,
        "pdh": 5525.0, "pdl": 5430.0,
        "note": "Estimated from opening price 5472.5"
    },
    "2025-05-02": {
        "price_open": 5664.25,
        "vah": 5687.5, "poc": 5662.5, "val": 5637.5,
        "call_wall": 5775.0, "put_wall": 5475.0,
        "zero_gamma": 5630.0, "vol_trigger": 5650.0,
        "hpz": 5670.0, "onh": 5700.0, "onl": 5637.5,
        "pdh": 5712.5, "pdl": 5625.0,
        "note": "Estimated from opening price 5664.25"
    },
    "2025-05-30": {
        "price_open": 5900.0,   # estimado — no tenemos tick data
        "vah": 5925.0, "poc": 5900.0, "val": 5875.0,
        "call_wall": 6000.0, "put_wall": 5700.0,
        "zero_gamma": 5870.0, "vol_trigger": 5890.0,
        "hpz": 5910.0, "onh": 5940.0, "onl": 5875.0,
        "pdh": 5950.0, "pdl": 5862.5,
        "note": "Estimated — no tick data available"
    },
    # 2026 nuevos
    "2026-01-29": {
        "price_open": 6040.0,   # estimado entre 01/22 y 02/02
        "vah": 6062.5, "poc": 6040.0, "val": 6017.5,
        "call_wall": 6150.0, "put_wall": 5850.0,
        "zero_gamma": 6010.0, "vol_trigger": 6035.0,
        "hpz": 6045.0, "onh": 6075.0, "onl": 6010.0,
        "pdh": 6087.5, "pdl": 6000.0,
        "note": "Estimated between 2026-01-22 and 2026-02-02"
    },
    "2026-02-13": {
        "price_open": 6120.0,   # estimado post 02/02
        "vah": 6137.5, "poc": 6112.5, "val": 6087.5,
        "call_wall": 6250.0, "put_wall": 5950.0,
        "zero_gamma": 6085.0, "vol_trigger": 6100.0,
        "hpz": 6120.0, "onh": 6150.0, "onl": 6087.5,
        "pdh": 6162.5, "pdl": 6075.0,
        "note": "Estimated post 2026-02-02"
    },
    "2026-03-12": {
        "price_open": 5650.0,   # día después del 03/11 expansion
        "vah": 5675.0, "poc": 5650.0, "val": 5625.0,
        "call_wall": 5750.0, "put_wall": 5450.0,
        "zero_gamma": 5615.0, "vol_trigger": 5635.0,
        "hpz": 5655.0, "onh": 5685.0, "onl": 5620.0,
        "pdh": 5700.0, "pdl": 5610.0,
        "note": "Day after 2026-03-11 expansion session"
    },
    "2026-04-30": {
        "price_open": 5570.0,   # estimado post 04/09
        "vah": 5587.5, "poc": 5562.5, "val": 5537.5,
        "call_wall": 5650.0, "put_wall": 5350.0,
        "zero_gamma": 5530.0, "vol_trigger": 5550.0,
        "hpz": 5570.0, "onh": 5600.0, "onl": 5537.5,
        "pdh": 5612.5, "pdl": 5525.0,
        "note": "Estimated post 2026-04-09"
    },
    "2026-05-04": {
        "price_open": 5650.0,   # estimado mayo 2026
        "vah": 5662.5, "poc": 5637.5, "val": 5612.5,
        "call_wall": 5750.0, "put_wall": 5450.0,
        "zero_gamma": 5615.0, "vol_trigger": 5635.0,
        "hpz": 5650.0, "onh": 5675.0, "onl": 5625.0,
        "pdh": 5687.5, "pdl": 5612.5,
        "note": "Estimated May 2026"
    },
}

def create_context(date: str, data: dict) -> dict:
    """Crea un contexto histórico en el formato exacto de GIBBZ."""
    p = data["price_open"]
    return {
        "date": date,
        "volume_profile": {
            "vah": data["vah"],
            "poc": data["poc"],
            "val": data["val"]
        },
        "spotgamma": {
            "call_wall":          data["call_wall"],
            "put_wall":           data["put_wall"],
            "zero_gamma":         data["zero_gamma"],
            "volatility_trigger": data["vol_trigger"],
            "hpz":                data["hpz"],
            "combo_1":            0.0,
            "combo_2":            0.0,
            "large_gamma_1":      data["hpz"],
            "large_gamma_2":      data["hpz"] - 25.0,
            "large_gamma_3":      data["hpz"] - 50.0,
            "large_gamma_4":      data["hpz"] + 50.0,
        },
        "session": {
            "prev_high":  data["pdh"],
            "prev_low":   data["pdl"],
            "prev_close": data["poc"],
            "open_price": p,
            "onh":        data["onh"],
            "onl":        data["onl"],
            "ibh":        round(p + 12.5, 2),
            "ibl":        round(p - 12.5, 2),
        },
        "_note":      data.get("note", "auto-generated"),
        "_estimated": True
    }


if __name__ == "__main__":
    os.makedirs(CONTEXT_DIR, exist_ok=True)

    created = 0
    skipped = 0

    for date, data in sorted(MISSING_CONTEXTS.items()):
        path = os.path.join(CONTEXT_DIR, f"{date}.json")
        if os.path.exists(path):
            print(f"  [SKIP]    {date} — ya existe")
            skipped += 1
            continue

        ctx = create_context(date, data)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(ctx, f, indent=2)

        print(f"  [CREATED] {date}  VAH={data['vah']}  POC={data['poc']}  "
              f"VAL={data['val']}  ({data.get('note','')})")
        created += 1

    print(f"\n  Total: {created} creados, {skipped} ya existían")
    print(f"  Contextos en {CONTEXT_DIR}/: "
          f"{len(glob.glob(os.path.join(CONTEXT_DIR, '*.json')))}")