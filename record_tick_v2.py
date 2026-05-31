import socket
import json
import time
from pathlib import Path
from datetime import datetime, timedelta, timezone

OUTPUT_DIR = Path("recordings_tick")
OUTPUT_DIR.mkdir(exist_ok=True)

# 🔥 NY timezone SIN pytz (UTC offset fijo base)
NY_OFFSET = timedelta(hours=-5)


def get_session_date():
    """
    Calcula sesión lógica NY sin dependencias externas
    """
    now_utc = datetime.utcnow()
    now_ny = now_utc + NY_OFFSET

    # regla de corte de sesión
    if now_ny.hour >= 17:
        now_ny = now_ny + timedelta(days=1)

    return now_ny.strftime("%Y-%m-%d")


session_date = get_session_date()
filename = f"{session_date}_RTH.jsonl"
filepath = OUTPUT_DIR / filename

UDP_IP = "127.0.0.1"
UDP_PORT = 9999

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind((UDP_IP, UDP_PORT))
sock.settimeout(1.0)

print(f"[RECORDER] Session file: {filepath}")
print("[RECORDER] Listening on 127.0.0.1:9999")

count = 0

f = open(filepath, "a", encoding="utf-8")

try:
    while True:
        try:
            data, addr = sock.recvfrom(65535)

            msg = data.decode("utf-8").strip()
            parts = msg.split(",")

            if len(parts) < 6:
                continue

            tick = {
                "session_date": session_date,
                "timestamp_ms": int(time.time() * 1000),
                "price": float(parts[0]),
                "size": float(parts[1]),
                "delta": float(parts[2]),
                "bid_volume": float(parts[3]),
                "ask_volume": float(parts[4]),
                "symbol": parts[5],
            }

            f.write(json.dumps(tick) + "\n")

            count += 1

        except socket.timeout:
            continue

except KeyboardInterrupt:
    print("\n[RECORDER] CTRL+C detected, shutting down...")

finally:
    print("[RECORDER] closing resources")
    f.flush()
    f.close()
    sock.close()