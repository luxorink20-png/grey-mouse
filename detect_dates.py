"""detect_dates.py — detecta fechas de los nuevos recordings"""
from datetime import datetime, timezone
import json

files = [
    'recordings/2026-05-09_1608.jsonl',
    'recordings/2026-05-09_1613.jsonl',
    'recordings/2026-05-09_1618.jsonl',
    'recordings/2026-05-09_1622.jsonl',
    'recordings/2026-05-09_1629.jsonl',
    'recordings/2026-05-09_1633.jsonl',
    'recordings/2026-05-09_1638.jsonl',
    'recordings/2026-05-09_1641.jsonl',
    'recordings/2026-05-09_1650.jsonl',
    'recordings/2026-05-09_1655.jsonl',
]

# Orden esperado según el prompt
EXPECTED = [
    "2026-01-28",
    "2026-02-05",
    "2026-02-06",
    "2026-03-19",
    "2026-03-20",
    "2025-09-17",
    "2025-09-19",
    "2025-10-29",
    "2025-07-30",
    "2026-04-29",
]

print(f"\n{'FILE':<40} {'TIMESTAMP':>15}  DATE_UTC            EXPECTED_DATE")
print("-" * 95)

for i, f in enumerate(files):
    try:
        with open(f) as fh:
            first = json.loads(fh.readline())
        ts = first.get('timestamp', 0)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        date_str = dt.strftime("%Y-%m-%d %H:%M UTC")
        expected = EXPECTED[i] if i < len(EXPECTED) else "?"
        match = "OK" if dt.strftime("%Y-%m-%d") == expected else "MISMATCH?"
        print(f"{f:<40} {ts:>15.0f}  {date_str}  {expected}  {match}")
    except Exception as e:
        print(f"{f:<40} ERROR: {e}")

print()
print("KNOW_DATES mapping para expansion_session_miner.py:")
print("-" * 95)
for i, f in enumerate(files):
    try:
        with open(f) as fh:
            first = json.loads(fh.readline())
        ts = first.get('timestamp', 0)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        base = f.replace('recordings/', '').replace('.jsonl', '').replace('recordings\\', '')
        expected = EXPECTED[i] if i < len(EXPECTED) else dt.strftime("%Y-%m-%d")
        print(f'    "{base}": "{expected}",')
    except Exception as e:
        print(f"    # ERROR {f}: {e}")