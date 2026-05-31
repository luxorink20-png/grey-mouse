# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — replay_recorder.py
#  ATAS UDP Stream Recorder v1.1
#
#  USO:
#  1. Abrí ATAS Historical Replay del día que querés grabar
#  2. Corré este script ANTES de darle play al replay
#  3. Dale play al replay en ATAS
#  4. Ctrl+C cuando termine la sesión
#  5. El archivo queda en: recordings/YYYY-MM-DD_HHMM.jsonl
#
#  LUEGO:
#  Corré replay_feed.py con ese archivo para procesar con GIBBZ
# ╚══════════════════════════════════════════════════════════════════╝

import socket
import json
import os
import sys
import signal
import time
from datetime import datetime


# ══════════════════════════════════════════════════════════════════
#  CONFIGURACION
# ══════════════════════════════════════════════════════════════════

UDP_HOST        = "127.0.0.1"
UDP_PORT        = 9999
BUFFER_SIZE     = 65535
RECORDINGS_DIR  = "recordings"
FLUSH_EVERY     = 50


# ══════════════════════════════════════════════════════════════════
#  PARSEAR CAMPOS DEL UDP BRIDGE
# ══════════════════════════════════════════════════════════════════

def parse_raw(data: bytes) -> dict:
    text = data.decode("utf-8").strip()

    if text.startswith("{"):
        return json.loads(text)

    parts = text.split(",")
    if len(parts) < 11:
        raise ValueError(f"CSV incompleto: {len(parts)} campos")

    return {
        "price":      float(parts[0]),
        "open":       float(parts[1]),
        "high":       float(parts[2]),
        "low":        float(parts[3]),
        "close":      float(parts[4]),
        "volume":     float(parts[5]),
        "delta":      float(parts[6]),
        "ask_volume": float(parts[7]),
        "bid_volume": float(parts[8]),
        "trades":     int(float(parts[9])),
        "timestamp":  float(parts[10]),
        "symbol":     parts[11] if len(parts) > 11 else "UNKNOWN",
    }


def normalize_tick(raw: dict) -> dict:
    tick = {}
    tick["price"]      = float(raw.get("price",      0))
    tick["open"]       = float(raw.get("open",        tick["price"]))
    tick["high"]       = float(raw.get("high",        tick["price"]))
    tick["low"]        = float(raw.get("low",         tick["price"]))
    tick["close"]      = float(raw.get("close",       tick["price"]))
    tick["volume"]     = float(raw.get("volume",      0))
    tick["trades"]     = int(raw.get("trades",        0))
    tick["symbol"]     = str(raw.get("symbol",        "UNKNOWN"))
    tick["timestamp"]  = float(raw.get("timestamp",   time.time()))
    tick["rec_timestamp"] = time.time()

    raw_ask   = float(raw.get("ask_volume", 0))
    raw_bid   = float(raw.get("bid_volume", 0))
    raw_delta = float(raw.get("delta",      0))

    # ── DELTA FIX v1.1 ────────────────────────────────────────────
    # Bridge v2.2 envía delta real via TradeDirection.Buy/Sell
    # Si ask==bid y delta==0 → bridge viejo → split neutro
    if raw_delta != 0:
        # Delta real del bridge v2.2
        tick["delta"]      = raw_delta
        tick["ask_volume"] = max(0.0, (tick["volume"] + raw_delta) / 2.0)
        tick["bid_volume"] = max(0.0, (tick["volume"] - raw_delta) / 2.0)
    elif raw_ask != raw_bid:
        # ask/bid distintos — calcular delta
        tick["ask_volume"] = raw_ask
        tick["bid_volume"] = raw_bid
        tick["delta"]      = raw_ask - raw_bid
    else:
        # Bridge roto / unknown — split neutro
        tick["ask_volume"] = tick["volume"] / 2.0
        tick["bid_volume"] = tick["volume"] / 2.0
        tick["delta"]      = 0.0

    return tick


def validate_tick(tick: dict) -> tuple:
    if tick["price"] <= 0:
        return False, "price=0"
    if tick["volume"] < 0:
        return False, "volume negativo"
    if abs(tick["delta"]) > tick["volume"] * 1.1 and tick["volume"] > 0:
        return False, f"delta({tick['delta']}) > volume({tick['volume']})"
    if tick["high"] < tick["low"]:
        return False, f"high({tick['high']}) < low({tick['low']})"
    return True, "ok"


# ══════════════════════════════════════════════════════════════════
#  RECORDER
# ══════════════════════════════════════════════════════════════════

class ReplayRecorder:

    def __init__(self):
        os.makedirs(RECORDINGS_DIR, exist_ok=True)
        ts        = datetime.now().strftime("%Y-%m-%d_%H%M")
        self.path = os.path.join(RECORDINGS_DIR, f"{ts}.jsonl")
        self.sock = None
        self.file = None
        self.count        = 0
        self.count_bad    = 0
        self.running      = True
        self.start_time   = time.time()

    def start(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((UDP_HOST, UDP_PORT))
        self.sock.settimeout(2.0)
        self.file = open(self.path, "w", encoding="utf-8")

        print(f"\n{'='*56}")
        print(f"  GIBBZ REPLAY RECORDER v1.1")
        print(f"{'='*56}")
        print(f"  Escuchando  : {UDP_HOST}:{UDP_PORT}")
        print(f"  Grabando en : {self.path}")
        print(f"  Ctrl+C para detener\n")

        signal.signal(signal.SIGINT, self._handle_stop)

        buf_count = 0

        while self.running:
            try:
                data, _ = self.sock.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                continue
            except OSError:
                break

            try:
                raw = parse_raw(data)
            except Exception:
                self.count_bad += 1
                continue

            if self.count == 0:
                print("  Formato detectado: CSV")
                print(f"  Ejemplo raw: {data.decode('utf-8')[:120]}")
                print(f"  Campos: price, open, high, low, close, volume, delta, ask_vol, bid_vol, trades, timestamp, symbol")
                print()

            tick = normalize_tick(raw)
            valid, reason = validate_tick(tick)

            if not valid:
                self.count_bad += 1
                continue

            self.file.write(json.dumps(tick) + "\n")
            self.count += 1
            buf_count  += 1

            if buf_count >= FLUSH_EVERY:
                self.file.flush()
                buf_count = 0

            if self.count % 500 == 0:
                elapsed = time.time() - self.start_time
                rate    = self.count / elapsed if elapsed > 0 else 0
                # Mostrar sample de delta para verificar
                d = tick.get("delta", 0)
                print(f"  Ticks grabados: {self.count:6d} | "
                      f"Tiempo: {int(elapsed):4d}s | "
                      f"Rate: {rate:.1f}/s | "
                      f"Bad: {self.count_bad} | "
                      f"Delta sample: {d:+.0f}")

        self._finish()

    def _handle_stop(self, sig, frame):
        print("\n  Deteniendo grabacion...")
        self.running = False

    def _finish(self):
        if self.file:
            self.file.flush()
            self.file.close()
        if self.sock:
            try: self.sock.close()
            except: pass

        elapsed = time.time() - self.start_time
        print(f"\n{'='*56}")
        print(f"  GRABACION COMPLETADA")
        print(f"{'='*56}")
        print(f"  Archivo     : {self.path}")
        print(f"  Ticks OK    : {self.count}")
        print(f"  Ticks bad   : {self.count_bad}")
        print(f"  Duracion    : {int(elapsed)}s")
        if elapsed > 0:
            print(f"  Rate prom   : {self.count/elapsed:.1f} ticks/s")
        print(f"\n  Listo para replay con:")
        print(f"  python replay_feed.py {self.path}\n")


if __name__ == "__main__":
    recorder = ReplayRecorder()
    recorder.start()