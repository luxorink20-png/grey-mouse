"""
Patch KNOWN_DATES en expansion_session_miner.py
Agrega todos los recordings nuevos del 2026-05-10
"""
import re

PATH = "expansion_session_miner.py"

NEW_ENTRIES = """
        # 2026-05-10 batch 1 (mañana temprano)
        "2026-05-10_0609": "2026-03-23",
        "2026-05-10_0614": "2025-05-30",
        "2026-05-10_0618": "2026-01-28",
        "2026-05-10_0631": "2026-03-23",
        "2026-05-10_0637": "2025-05-30",
        "2026-05-10_0641": "2026-01-28",
        # 2026-05-10 batch 2 (históricos 8 sesiones)
        "2026-05-10_1032": "2026-03-19",
        "2026-05-10_1037": "2025-02-13",
        "2026-05-10_1039": "2026-01-28",
        "2026-05-10_1045": "2026-04-30",
        "2026-05-10_1047": "2025-05-30",
        "2026-05-10_1051": "2024-12-18",
        "2026-05-10_1055": "2025-03-19",
        "2026-05-10_1100": "2024-04-10",
        # 2026-05-10 batch 3 (sesiones nuevas)
        "2026-05-10_1147": "2025-02-06",
        "2026-05-10_1159": "2026-06-17",
        "2026-05-10_1207": "2025-06-11",
        "2026-05-10_1212": "2024-09-18",
        "2026-05-10_1216": "2026-03-18",
        "2026-05-10_1221": "2025-03-19",
        "2026-05-10_1225": "2024-12-18","""

with open(PATH, "r", encoding="utf-8") as f:
    content = f.read()

# Insertar antes del cierre del diccionario
TARGET = '        "2026-05-09_1655": "2026-04-29",'
if TARGET not in content:
    print(f"ERROR: no se encontró el anchor '{TARGET}'")
    exit(1)

if "2026-05-10_0609" in content:
    print("Ya patcheado — nada que hacer.")
    exit(0)

content = content.replace(TARGET, TARGET + NEW_ENTRIES)

with open(PATH, "w", encoding="utf-8") as f:
    f.write(content)

print(f"✅ KNOWN_DATES actualizado — 21 entradas nuevas agregadas")
print(f"   Corre: python expansion_session_miner.py --mine-all")