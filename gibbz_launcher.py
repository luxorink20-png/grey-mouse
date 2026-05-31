"""
GIBBZ Launcher v1.0 — Session Recording Automation

Automatiza todo el pipeline de grabación near-shadow excepto el
click inicial de Play en ATAS.

Usage:
  python gibbz_launcher.py [DATE] [--mine] [--timeout 30] [--autoclick]

DATE:         Session date YYYY-MM-DD (default: today)
--mine:       Auto-run expansion_session_miner después de grabar
--timeout N:  Segundos de silencio antes de auto-stop (default: 30)
--autoclick:  Intentar click automático en ATAS via pywinauto (opcional)

Requisito en ATAS:
  Tener GibbzBridge.cs compilado y agregado como indicador en el chart.
  El indicador envía datos via UDP 127.0.0.1:9999 cuando replay está activo.
"""

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Config ────────────────────────────────────────────────────────────
UDP_HOST       = "127.0.0.1"
UDP_PORT       = 9999
BUFFER_SIZE    = 65535
RECORDINGS_DIR = "recordings"
FLUSH_EVERY    = 50
HOME           = os.path.expanduser("~")
CMD_FILE       = os.path.join(HOME, "gibbz_bridge_cmd.txt")
STATUS_FILE    = os.path.join(HOME, "gibbz_bridge_status.txt")

# ANSI colors (compatibles con Windows 10/11 terminal moderno)
G   = "\033[92m"
R   = "\033[91m"
Y   = "\033[93m"
C   = "\033[96m"
W   = "\033[97m"
B   = "\033[1m"
DIM = "\033[2m"
RST = "\033[0m"


# ── Args ──────────────────────────────────────────────────────────────

def parse_args():
    p = argparse.ArgumentParser(
        description="GIBBZ Session Recording Launcher",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("date", nargs="?", default=None,
                   help="Session date YYYY-MM-DD (default: today)")
    p.add_argument("--mine", action="store_true",
                   help="Auto-run expansion miner después de grabar")
    p.add_argument("--timeout", type=int, default=30,
                   help="Segundos de silencio antes de auto-stop (default: 30)")
    p.add_argument("--autoclick", action="store_true",
                   help="Intentar click en ATAS replay via pywinauto")
    p.add_argument("--bars", type=int, default=400,
                   help="Máx barras a analizar en el miner (default: 400)")
    return p.parse_args()


# ── Historical context ────────────────────────────────────────────────

def load_context(session_date: str) -> dict | None:
    path = os.path.join("historical_context", f"{session_date}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except Exception:
        return None


# ── Bridge IPC ───────────────────────────────────────────────────────

def write_cmd(cmd: str):
    tmp = CMD_FILE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(cmd)
        shutil.move(tmp, CMD_FILE)
    except Exception as e:
        print(f"[IPC] write_cmd failed: {e}")


def read_bridge_status() -> str:
    if not os.path.exists(STATUS_FILE):
        return "NO_BRIDGE_FILE"
    try:
        for line in open(STATUS_FILE, encoding="utf-8").read().splitlines():
            if line.startswith("status="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return "UNKNOWN"


# ── UDP parse (misma lógica que replay_recorder.py) ──────────────────

def _parse_raw(data: bytes) -> dict:
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


def _normalize(raw: dict) -> dict:
    t                    = {}
    t["price"]           = float(raw.get("price",  0))
    t["open"]            = float(raw.get("open",   t["price"]))
    t["high"]            = float(raw.get("high",   t["price"]))
    t["low"]             = float(raw.get("low",    t["price"]))
    t["close"]           = float(raw.get("close",  t["price"]))
    t["volume"]          = float(raw.get("volume", 0))
    t["trades"]          = int(raw.get("trades",   0))
    t["symbol"]          = str(raw.get("symbol",   "UNKNOWN"))
    t["timestamp"]       = float(raw.get("timestamp", time.time()))
    t["rec_timestamp"]   = time.time()

    d   = float(raw.get("delta",      0))
    ask = float(raw.get("ask_volume", 0))
    bid = float(raw.get("bid_volume", 0))

    if d != 0:
        t["delta"]      = d
        t["ask_volume"] = max(0.0, (t["volume"] + d) / 2.0)
        t["bid_volume"] = max(0.0, (t["volume"] - d) / 2.0)
    elif ask != bid:
        t["delta"]      = ask - bid
        t["ask_volume"] = ask
        t["bid_volume"] = bid
    else:
        t["delta"]      = 0.0
        t["ask_volume"] = t["volume"] / 2.0
        t["bid_volume"] = t["volume"] / 2.0

    return t


def _validate(t: dict) -> tuple[bool, str]:
    if t["price"] <= 0:           return False, "price=0"
    if t["volume"] < 0:           return False, "volume negativo"
    if t["high"] < t["low"]:      return False, "high<low"
    return True, "ok"


# ── Autoclick via pywinauto ───────────────────────────────────────────

def autoclick_atas() -> bool:
    try:
        from pywinauto import Desktop
    except ImportError:
        print(f"  {Y}[AUTOCLICK]{RST} pywinauto no instalado.")
        print(f"  {DIM}→ pip install pywinauto{RST}")
        return False

    try:
        desktop = Desktop(backend="uia")
        wins    = [w for w in desktop.windows() if "ATAS" in w.window_text()]

        if not wins:
            print(f"  {Y}[AUTOCLICK]{RST} Ventana ATAS no encontrada.")
            return False

        atas_win = wins[0]
        atas_win.set_focus()
        time.sleep(0.5)

        # Nombres posibles del botón Play en distintas versiones de ATAS
        for name in ["Play", "Воспроизвести", "►", "Start replay", "Replay"]:
            try:
                btn = atas_win.child_window(title=name, control_type="Button")
                if btn.exists():
                    btn.click_input()
                    print(f"  {G}[AUTOCLICK]{RST} Hizo click en: '{name}'")
                    return True
            except Exception:
                continue

        # Fallback: Space bar (play/pause en ATAS replay)
        atas_win.type_keys(" ")
        print(f"  {Y}[AUTOCLICK]{RST} Enviado Space a ventana ATAS (play/pause)")
        return True

    except Exception as e:
        print(f"  {R}[AUTOCLICK ERROR]{RST} {e}")
        return False


# ── Miner ─────────────────────────────────────────────────────────────

def run_miner(rec_path: str, session_date: str,
              max_bars: int = 400) -> dict | None:
    """
    Invoca ExpansionSessionMiner.mine() directamente sobre el archivo grabado.
    KNOWN_DATES es local a mine_all_recordings() — usamos la clase directamente.
    """
    try:
        from expansion_session_miner import ExpansionSessionMiner
        m      = ExpansionSessionMiner()
        result = m.mine(rec_path, session_date, max_bars=max_bars, silent=True)
        m.save(result)
        return {
            "ep_score":       result.ep_score,
            "session_type":   result.session_type,
            "recommendation": result.recommendation,
            "output_path":    f"expansion_outcomes/{session_date}_expansion.json",
        }
    except Exception as e:
        print(f"  {R}[MINER ERROR]{RST} {e}")
        return None


# ── Helpers ───────────────────────────────────────────────────────────

def fmt_dur(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def silence_bar(elapsed_silence: float, timeout: int) -> str:
    filled  = min(int(elapsed_silence // 5), timeout // 5)
    empty   = max(0, timeout // 5 - filled)
    return "█" * filled + "░" * empty


# ── Main ──────────────────────────────────────────────────────────────

def main():
    args         = parse_args()
    session_date = args.date or date.today().isoformat()

    # Validate date format
    try:
        datetime.strptime(session_date, "%Y-%m-%d")
    except ValueError:
        print(f"{R}[ERROR]{RST} Fecha inválida: '{session_date}'. "
              f"Formato: YYYY-MM-DD")
        sys.exit(1)

    # ── Header ────────────────────────────────────────────────────────
    print(f"\n{B}{'═' * 58}{RST}")
    print(f"{B}  GIBBZ LAUNCHER v1.0  —  {session_date}{RST}")
    print(f"{B}{'═' * 58}{RST}\n")

    # ── Context check ─────────────────────────────────────────────────
    ctx = load_context(session_date)
    if ctx:
        vp  = ctx.get("volume_profile", {})
        sg  = ctx.get("spotgamma", {})
        ses = ctx.get("session", {})
        print(f"  Contexto histórico : {G}OK{RST}  "
              f"VAH={vp.get('vah',0)}  "
              f"POC={vp.get('poc',0)}  "
              f"VAL={vp.get('val',0)}  "
              f"VT={sg.get('volatility_trigger',0)}")
        print(f"  {DIM}Open={ses.get('open_price',0)}  "
              f"ONH={ses.get('onh',0)}  "
              f"ONL={ses.get('onl',0)}{RST}")
    else:
        print(f"  Contexto histórico : {Y}NO ENCONTRADO{RST}  "
              f"historical_context/{session_date}.json")
        print(f"  {Y}→ Creá el contexto antes de minar: "
              f"python create_context.py{RST}")

    # ── Bridge signal ─────────────────────────────────────────────────
    write_cmd("RECORD")
    bridge_st = read_bridge_status()
    bridge_ok = bridge_st not in ("NO_BRIDGE_FILE", "UNKNOWN", "DISPOSED")
    color     = G if bridge_ok else Y
    print(f"  Bridge GibbzBridge : {color}{bridge_st}{RST}")
    if not bridge_ok:
        print(f"  {DIM}→ Asegurate de que GibbzBridge.cs esté cargado "
              f"como indicador en ATAS{RST}")

    # ── Recording path ────────────────────────────────────────────────
    os.makedirs(RECORDINGS_DIR, exist_ok=True)
    rec_ts   = datetime.now().strftime("%Y-%m-%d_%H%M")
    rec_path = os.path.join(RECORDINGS_DIR, f"{rec_ts}.jsonl")

    print(f"  Grabando en        : {rec_path}")
    print(f"  Timeout silencio   : {args.timeout}s")
    print(f"  Auto-mining        : "
          f"{G+'habilitado'+RST if args.mine else DIM+'deshabilitado'+RST}")
    print()

    # ── Optional autoclick ────────────────────────────────────────────
    if args.autoclick:
        print(f"  {C}[AUTOCLICK]{RST} Buscando ventana ATAS...")
        time.sleep(1.0)
        clicked = autoclick_atas()
        if not clicked:
            print(f"  {Y}→ Hacé click manualmente en Play en ATAS{RST}")
        print()
    else:
        print(f"  {B}▶  PASO MANUAL: ATAS → Historical Replay → "
              f"{session_date} → PLAY{RST}")
        print()

    # ── UDP socket ────────────────────────────────────────────────────
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.bind((UDP_HOST, UDP_PORT))
        sock.settimeout(1.0)
    except OSError as e:
        print(f"  {R}[ERROR]{RST} No se pudo abrir UDP {UDP_HOST}:{UDP_PORT}")
        print(f"  {R}{e}{RST}")
        print(f"  {Y}→ ¿Está corriendo replay_recorder.py o engine.py?{RST}")
        sys.exit(1)

    print(f"  Escuchando {UDP_HOST}:{UDP_PORT}  "
          f"{DIM}(Ctrl+C para detener manualmente){RST}\n")

    # ── Record loop ───────────────────────────────────────────────────
    out_file    = open(rec_path, "w", encoding="utf-8")
    count       = 0
    count_bad   = 0
    buf_count   = 0
    start_time  = None
    last_packet = None
    last_price  = 0.0
    last_delta  = 0.0
    connected   = False

    try:
        while True:
            try:
                data, _ = sock.recvfrom(BUFFER_SIZE)
            except socket.timeout:
                if connected and last_packet is not None:
                    silence = time.time() - last_packet
                    if silence >= args.timeout:
                        print(f"\r{' ' * 78}\r"
                              f"  {Y}[AUTO-STOP]{RST} {args.timeout}s sin datos — "
                              f"sesión completada.")
                        break
                    # Barra de silencio (actualizar cada segundo entero)
                    bar = silence_bar(silence, args.timeout)
                    print(f"\r  {DIM}Silencio: [{bar}] "
                          f"{int(silence):2d}s/{args.timeout}s{RST}   ",
                          end="", flush=True)
                continue

            # Parse
            try:
                raw  = _parse_raw(data)
                tick = _normalize(raw)
                ok, _ = _validate(tick)
            except Exception:
                count_bad += 1
                continue

            if not ok:
                count_bad += 1
                continue

            # First packet
            if not connected:
                connected  = True
                start_time = time.time()
                print(f"  {G}[CONNECTED]{RST} Recibiendo datos de ATAS...\n")

            out_file.write(json.dumps(tick) + "\n")
            count       += 1
            buf_count   += 1
            last_packet  = time.time()
            last_price   = tick["price"]
            last_delta   = tick["delta"]

            if buf_count >= FLUSH_EVERY:
                out_file.flush()
                buf_count = 0

            if count % 20 == 0:
                elapsed = time.time() - start_time if start_time else 0
                rate    = count / elapsed if elapsed > 0 else 0
                d_str   = f"{last_delta:+.0f}"
                print(f"\r  {G}■{RST} Bars:{count:5d}  "
                      f"Price:{last_price:9.2f}  "
                      f"Delta:{d_str:>7}  "
                      f"Rate:{rate:4.1f}/s  "
                      f"Dur:{fmt_dur(elapsed)}  "
                      f"Bad:{count_bad}   ",
                      end="", flush=True)

    except KeyboardInterrupt:
        print(f"\r{' ' * 78}\r"
              f"  {Y}[CTRL+C]{RST} Detenido manualmente.")

    finally:
        out_file.flush()
        out_file.close()
        sock.close()
        write_cmd("STOP")

    # ── Summary ───────────────────────────────────────────────────────
    elapsed_total = (time.time() - start_time) if start_time else 0
    print(f"\n{'─' * 58}")
    print(f"  Bars grabadas  : {count}")
    print(f"  Bars inválidas : {count_bad}")
    print(f"  Duración       : {fmt_dur(elapsed_total)}")
    print(f"  Archivo        : {rec_path}")

    if count == 0:
        print(f"\n  {R}[ERROR]{RST} Sin datos grabados.")
        print(f"  Verificar:")
        print(f"    1. GibbzBridge.cs cargado en ATAS como indicador")
        print(f"    2. Replay está activo y enviando barras")
        print(f"    3. Puerto {UDP_PORT} no bloqueado por firewall")
        return

    # ── Auto-mine ─────────────────────────────────────────────────────
    if args.mine and ctx:
        print(f"\n{'─' * 58}")
        print(f"  {C}[MINING]{RST} Ejecutando expansion_session_miner...")
        result = run_miner(rec_path, session_date, max_bars=args.bars)
        if result:
            ep    = result.get("ep_score", "?")
            stype = result.get("session_type", "?")
            rec   = result.get("recommendation", "?")
            color = G if str(ep).isdigit() and int(ep) >= 50 else Y
            print(f"  ep_score     : {color}{ep}{RST}")
            print(f"  session_type : {stype}")
            print(f"  Recomendación: {rec}")
        else:
            print(f"  {Y}[MINER]{RST} Sin resultado automático.")
            _print_manual_mine_hint(rec_path, session_date)
    elif args.mine and not ctx:
        print(f"\n  {Y}[MINER]{RST} Skipped — contexto histórico faltante.")
        _print_manual_mine_hint(rec_path, session_date)
    else:
        print()
        _print_manual_mine_hint(rec_path, session_date)

    print(f"{'═' * 58}\n")


def _print_manual_mine_hint(rec_path: str, session_date: str):
    basename = os.path.basename(rec_path)
    print(f"  {DIM}Para analizar manualmente:{RST}")
    print(f"  {DIM}1. Agregá a KNOWN_DATES en expansion_session_miner.py:{RST}")
    print(f"  {DIM}     \"{basename}\": \"{session_date}\",{RST}")
    print(f"  {DIM}2. Corré: PYTHONUTF8=1 python expansion_session_miner.py{RST}")


if __name__ == "__main__":
    main()
