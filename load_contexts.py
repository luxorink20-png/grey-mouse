"""
GIBBZ V3 — load_contexts.py
Carga masiva de contextos históricos en historical_context/

USO:
  python load_contexts.py
"""

import json
import os

CONTEXT_DIR = "historical_context"
os.makedirs(CONTEXT_DIR, exist_ok=True)

CONTEXTS = {

    "2025-02-06": {
        "date": "2025-02-06",
        "volume_profile": {"vah": 6082.5, "poc": 6072.5, "val": 6062.5},
        "spotgamma": {
            "call_wall": 6150.0, "put_wall": 5950.0,
            "zero_gamma": 6060.0, "volatility_trigger": 6072.5, "hpz": 6080.0,
            "combo_1": 0.0, "combo_2": 0.0,
            "large_gamma_1": 6080.0, "large_gamma_2": 6072.5,
            "large_gamma_3": 6062.5, "large_gamma_4": 6097.5
        },
        "session": {
            "prev_high": 6097.5, "prev_low": 6043.5, "prev_close": 6072.5,
            "open_price": 6082.5, "onh": 6122.5, "onl": 6080.0,
            "ibh": 6095.0, "ibl": 6067.5
        }
    },

    "2026-06-17": {
        "date": "2026-06-17",
        "volume_profile": {"vah": 6067.5, "poc": 6057.5, "val": 6045.0},
        "spotgamma": {
            "call_wall": 6150.0, "put_wall": 5950.0,
            "zero_gamma": 6040.0, "volatility_trigger": 6055.0, "hpz": 6062.5,
            "combo_1": 0.0, "combo_2": 0.0,
            "large_gamma_1": 6062.5, "large_gamma_2": 6057.5,
            "large_gamma_3": 6045.0, "large_gamma_4": 6087.5
        },
        "session": {
            "prev_high": 6087.5, "prev_low": 6028.5, "prev_close": 6057.5,
            "open_price": 6050.0, "onh": 6075.0, "onl": 6037.5,
            "ibh": 6067.5, "ibl": 6042.5
        }
    },

    "2025-06-11": {
        "date": "2025-06-11",
        "volume_profile": {"vah": 6040.0, "poc": 6032.5, "val": 6022.5},
        "spotgamma": {
            "call_wall": 6100.0, "put_wall": 5900.0,
            "zero_gamma": 6020.0, "volatility_trigger": 6030.0, "hpz": 6038.0,
            "combo_1": 0.0, "combo_2": 0.0,
            "large_gamma_1": 6038.0, "large_gamma_2": 6032.5,
            "large_gamma_3": 6022.5, "large_gamma_4": 6055.0
        },
        "session": {
            "prev_high": 6055.0, "prev_low": 6008.0, "prev_close": 6032.5,
            "open_price": 6030.0, "onh": 6050.0, "onl": 6025.0,
            "ibh": 6042.5, "ibl": 6025.0
        }
    },

    "2024-09-18": {
        "date": "2024-09-18",
        "volume_profile": {"vah": 5715.0, "poc": 5698.0, "val": 5687.5},
        "spotgamma": {
            "call_wall": 5800.0, "put_wall": 5600.0,
            "zero_gamma": 5685.0, "volatility_trigger": 5698.0, "hpz": 5710.0,
            "combo_1": 0.0, "combo_2": 0.0,
            "large_gamma_1": 5710.0, "large_gamma_2": 5698.0,
            "large_gamma_3": 5687.5, "large_gamma_4": 5760.0
        },
        "session": {
            "prev_high": 5760.0, "prev_low": 5680.0, "prev_close": 5698.0,
            "open_price": 5695.0, "onh": 5740.0, "onl": 5685.0,
            "ibh": 5722.5, "ibl": 5687.5
        }
    },

    "2026-03-18": {
        "date": "2026-03-18",
        "volume_profile": {"vah": 6775.0, "poc": 6750.0, "val": 6720.0},
        "spotgamma": {
            "call_wall": 6900.0, "put_wall": 6550.0,
            "zero_gamma": 6720.0, "volatility_trigger": 6745.0, "hpz": 6755.0,
            "combo_1": 0.0, "combo_2": 0.0,
            "large_gamma_1": 6755.0, "large_gamma_2": 6750.0,
            "large_gamma_3": 6720.0, "large_gamma_4": 6807.5
        },
        "session": {
            "prev_high": 6807.5, "prev_low": 6695.0, "prev_close": 6750.0,
            "open_price": 6710.0, "onh": 6702.5, "onl": 6635.0,
            "ibh": 6722.5, "ibl": 6667.5
        }
    },

    "2025-03-19": {
        "date": "2025-03-19",
        "volume_profile": {"vah": 5715.0, "poc": 5695.0, "val": 5675.0},
        "spotgamma": {
            "call_wall": 5800.0, "put_wall": 5600.0,
            "zero_gamma": 5670.0, "volatility_trigger": 5690.0, "hpz": 5700.0,
            "combo_1": 0.0, "combo_2": 0.0,
            "large_gamma_1": 5700.0, "large_gamma_2": 5690.0,
            "large_gamma_3": 5670.0, "large_gamma_4": 5715.0
        },
        "session": {
            "prev_high": 5745.0, "prev_low": 5640.0, "prev_close": 5695.0,
            "open_price": 5680.0, "onh": 5700.0, "onl": 5645.0,
            "ibh": 5697.5, "ibl": 5662.5
        }
    },

    "2024-12-18": {
        "date": "2024-12-18",
        "volume_profile": {"vah": 6140.0, "poc": 5975.0, "val": 5920.0},
        "spotgamma": {
            "call_wall": 6200.0, "put_wall": 5800.0,
            "zero_gamma": 5950.0, "volatility_trigger": 5975.0, "hpz": 6000.0,
            "combo_1": 0.0, "combo_2": 0.0,
            "large_gamma_1": 6000.0, "large_gamma_2": 5975.0,
            "large_gamma_3": 5950.0, "large_gamma_4": 6150.0
        },
        "session": {
            "prev_high": 6150.0, "prev_low": 5900.0, "prev_close": 5975.0,
            "open_price": 5960.0, "onh": 6000.0, "onl": 5900.0,
            "ibh": 5990.0, "ibl": 5935.0
        }
    },

    # ── Fechas anteriores (sin cambios) ──────────────────────────

    "2026-03-19": {
        "date": "2026-03-19",
        "volume_profile": {"vah": 6730.0, "poc": 6685.0, "val": 6620.0},
        "spotgamma": {
            "call_wall": 6850.0, "put_wall": 6500.0,
            "zero_gamma": 6640.0, "volatility_trigger": 6670.0, "hpz": 6690.0,
            "combo_1": 0.0, "combo_2": 0.0,
            "large_gamma_1": 6690.0, "large_gamma_2": 6670.0,
            "large_gamma_3": 6640.0, "large_gamma_4": 6730.0
        },
        "session": {
            "prev_high": 6800.0, "prev_low": 6625.0, "prev_close": 6685.0,
            "open_price": 6650.0, "onh": 6685.0, "onl": 6589.5,
            "ibh": 6672.5, "ibl": 6627.5
        }
    },

    "2025-02-13": {
        "date": "2025-02-13",
        "volume_profile": {"vah": 6097.5, "poc": 6080.0, "val": 6062.5},
        "spotgamma": {
            "call_wall": 6150.0, "put_wall": 5950.0,
            "zero_gamma": 6060.0, "volatility_trigger": 6075.0, "hpz": 6085.0,
            "combo_1": 0.0, "combo_2": 0.0,
            "large_gamma_1": 6085.0, "large_gamma_2": 6075.0,
            "large_gamma_3": 6060.0, "large_gamma_4": 6100.0
        },
        "session": {
            "prev_high": 6100.0, "prev_low": 6025.0, "prev_close": 6080.0,
            "open_price": 6085.0, "onh": 6120.0, "onl": 6072.5,
            "ibh": 6100.0, "ibl": 6070.0
        }
    },

    "2026-01-28": {
        "date": "2026-01-28",
        "volume_profile": {"vah": 7012.5, "poc": 7004.0, "val": 6987.5},
        "spotgamma": {
            "call_wall": 7100.0, "put_wall": 6900.0,
            "zero_gamma": 6980.0, "volatility_trigger": 6995.0, "hpz": 7005.0,
            "combo_1": 0.0, "combo_2": 0.0,
            "large_gamma_1": 7005.0, "large_gamma_2": 6995.0,
            "large_gamma_3": 6980.0, "large_gamma_4": 7040.0
        },
        "session": {
            "prev_high": 7040.0, "prev_low": 6984.0, "prev_close": 7004.0,
            "open_price": 6995.0, "onh": 7032.5, "onl": 6984.5,
            "ibh": 7007.5, "ibl": 6982.5
        }
    },

    "2026-04-30": {
        "date": "2026-04-30",
        "volume_profile": {"vah": 7185.0, "poc": 7165.0, "val": 7145.0},
        "spotgamma": {
            "call_wall": 7300.0, "put_wall": 7000.0,
            "zero_gamma": 7150.0, "volatility_trigger": 7165.0, "hpz": 7175.0,
            "combo_1": 0.0, "combo_2": 0.0,
            "large_gamma_1": 7175.0, "large_gamma_2": 7165.0,
            "large_gamma_3": 7150.0, "large_gamma_4": 7200.0
        },
        "session": {
            "prev_high": 7200.0, "prev_low": 7130.0, "prev_close": 7165.0,
            "open_price": 7195.0, "onh": 7235.0, "onl": 7180.0,
            "ibh": 7210.0, "ibl": 7185.0
        }
    },

    "2025-05-30": {
        "date": "2025-05-30",
        "volume_profile": {"vah": 5950.0, "poc": 5925.0, "val": 5910.0},
        "spotgamma": {
            "call_wall": 6050.0, "put_wall": 5800.0,
            "zero_gamma": 5910.0, "volatility_trigger": 5925.0, "hpz": 5935.0,
            "combo_1": 0.0, "combo_2": 0.0,
            "large_gamma_1": 5935.0, "large_gamma_2": 5925.0,
            "large_gamma_3": 5910.0, "large_gamma_4": 5950.0
        },
        "session": {
            "prev_high": 6010.0, "prev_low": 5900.0, "prev_close": 5925.0,
            "open_price": 5910.0, "onh": 5930.0, "onl": 5890.0,
            "ibh": 5922.5, "ibl": 5897.5
        }
    },

    "2024-04-10": {
        "date": "2024-04-10",
        "volume_profile": {"vah": 5260.0, "poc": 5215.0, "val": 5205.0},
        "spotgamma": {
            "call_wall": 5350.0, "put_wall": 5100.0,
            "zero_gamma": 5200.0, "volatility_trigger": 5215.0, "hpz": 5230.0,
            "combo_1": 0.0, "combo_2": 0.0,
            "large_gamma_1": 5230.0, "large_gamma_2": 5215.0,
            "large_gamma_3": 5200.0, "large_gamma_4": 5260.0
        },
        "session": {
            "prev_high": 5290.0, "prev_low": 5190.0, "prev_close": 5215.0,
            "open_price": 5205.0, "onh": 5240.0, "onl": 5170.0,
            "ibh": 5232.5, "ibl": 5187.5
        }
    },
}

# ── WRITE ─────────────────────────────────────────────────────────

created  = []
updated  = []
skipped  = []

for date, data in CONTEXTS.items():
    path     = os.path.join(CONTEXT_DIR, f"{date}.json")
    exists   = os.path.exists(path)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    if exists:
        updated.append(date)
    else:
        created.append(date)

print(f"\n  GIBBZ — Context Loader")
print(f"  {'─'*40}")
if created:
    print(f"  Created  ({len(created)}):")
    for d in created:
        print(f"    + {d}")
if updated:
    print(f"  Updated  ({len(updated)}):")
    for d in updated:
        print(f"    ↺ {d}")
print(f"  {'─'*40}")
print(f"  Total: {len(CONTEXTS)} contexts → {CONTEXT_DIR}/")
print(f"\n  Ready to run replays:\n")

replays = [
    ("2026-05-10_1147.jsonl", "2025-02-06"),
    ("2026-05-10_1159.jsonl", "2026-06-17"),
    ("2026-05-10_1207.jsonl", "2025-06-11"),
    ("2026-05-10_1212.jsonl", "2024-09-18"),
    ("2026-05-10_1216.jsonl", "2026-03-18"),
    ("2026-05-10_1221.jsonl", "2025-03-19"),
    ("2026-05-10_1225.jsonl", "2024-12-18"),
]

for rec, date in replays:
    print(f"  python replay_debug_v3.py recordings/{rec} "
          f"--date {date} --bars 400 --save-outcomes")
print()