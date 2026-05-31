"""
GIBBZ — extract_context.py  v1.0
Genera historical_context JSON a partir de screenshots usando Claude Vision.

MODOS:
  python extract_context.py --watch              vigila screenshots/ indefinidamente
  python extract_context.py --file img.png       procesa un screenshot puntual
  python extract_context.py --file img.png --date 2026-05-12   fecha explícita

NAMING CONVENTION (modo watch):
  El nombre del archivo puede incluir la fecha de sesión:
    2026-05-12.png          → fecha: 2026-05-12
    context_2026-05-12.png  → fecha: 2026-05-12
    screenshot_2026-05-12_...png → fecha: 2026-05-12
  Si no hay fecha en el nombre → usa la fecha de hoy como fallback.

API KEY:
  Leer de $env:ANTHROPIC_API_KEY o de un archivo .env en el proyecto.

SALIDA:
  historical_context/YYYY-MM-DD.json  (sobrescribe si ya existe, confirma primero)
"""

import anthropic
import argparse
import base64
import hashlib
import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path


# ── Config ────────────────────────────────────────────────────────────
SCREENSHOTS_DIR  = "screenshots"
CONTEXT_DIR      = "historical_context"
WATCH_INTERVAL   = 2.0          # segundos entre polls
MODEL            = "claude-sonnet-4-6"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif"}

G   = "\033[92m"
R   = "\033[91m"
Y   = "\033[93m"
C   = "\033[96m"
B   = "\033[1m"
DIM = "\033[2m"
RST = "\033[0m"


# ── API key ───────────────────────────────────────────────────────────

def get_api_key() -> str:
    # 1. Environment variable
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if key:
        return key

    # 2. .env file in project root (keep .env out of version control via .gitignore)
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line.startswith("ANTHROPIC_API_KEY"):
                parts = line.split("=", 1)
                if len(parts) == 2:
                    key = parts[1].strip().strip('"').strip("'")
                    if key:
                        return key

    raise RuntimeError(
        "ANTHROPIC_API_KEY not found. "
        "Set it via the environment variable $env:ANTHROPIC_API_KEY "
        "or add ANTHROPIC_API_KEY=<key> to a .env file in the project root."
    )


# ── Date detection from filename ──────────────────────────────────────

def date_from_filename(path: str) -> str | None:
    name = Path(path).stem
    m = re.search(r"(\d{4}[-_]\d{2}[-_]\d{2})", name)
    if m:
        raw = m.group(1).replace("_", "-")
        try:
            datetime.strptime(raw, "%Y-%m-%d")
            return raw
        except ValueError:
            pass
    return None


# ── Claude Vision prompt ──────────────────────────────────────────────

EXTRACTION_PROMPT = """
You are analyzing a trading platform screenshot for the GIBBZ algorithmic trading system.
The platform is ATAS (Advanced Trading Analytical Software) for futures (ES/NQ).

Extract trading levels from the screenshot and return ONLY a valid JSON object — no explanation, no markdown, no code fences.

Look for these levels (they may appear as labeled horizontal lines, a sidebar panel, or a levels list):

VOLUME PROFILE (look for VAH/POC/VAL labels, or a volume histogram on the right side):
  vah = Value Area High
  poc = Point of Control (highest volume price)
  val = Value Area Low

SESSION LEVELS (labeled lines or a pre-market panel):
  prev_high  = Previous day high (PDH)
  prev_low   = Previous day low (PDL)
  prev_close = Previous day close (PDC)
  onh        = Overnight session high
  onl        = Overnight session low
  open_price = Today's RTH opening price (09:30 ET / 08:30 CT)
  ibh        = Initial Balance High (first 60-min high)
  ibl        = Initial Balance Low  (first 60-min low)

SPOTGAMMA LEVELS (may appear as colored horizontal lines with SpotGamma labels):
  call_wall          = Call Wall
  put_wall           = Put Wall
  zero_gamma         = Zero Gamma / Gamma Flip
  volatility_trigger = Volatility Trigger
  hpz                = High Positive Gamma Zone
  combo_1            = First combo strike  (0.0 if not visible)
  combo_2            = Second combo strike (0.0 if not visible)
  large_gamma_1 through large_gamma_4 = Large gamma strikes (0.0 if not visible)

Rules:
- Use 0.0 for any value not visible or not present in the screenshot
- All prices must be positive numbers (e.g. 5500.25, 19850.0)
- vah > poc > val must hold if Volume Profile is visible
- Return ONLY the JSON object below, nothing else:

{
  "volume_profile": {
    "vah": 0.0,
    "poc": 0.0,
    "val": 0.0
  },
  "session": {
    "prev_high":  0.0,
    "prev_low":   0.0,
    "prev_close": 0.0,
    "onh":        0.0,
    "onl":        0.0,
    "open_price": 0.0,
    "ibh":        0.0,
    "ibl":        0.0
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
  }
}
"""


# ── Extract levels via Claude Vision ─────────────────────────────────

def extract_levels(client: anthropic.Anthropic, image_path: str) -> dict:
    path = Path(image_path)
    ext  = path.suffix.lower()

    media_types = {
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif":  "image/gif",
    }
    media_type = media_types.get(ext, "image/png")

    raw_bytes   = path.read_bytes()
    b64_data    = base64.standard_b64encode(raw_bytes).decode("utf-8")

    message = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type":       "base64",
                            "media_type": media_type,
                            "data":       b64_data,
                        },
                    },
                    {
                        "type": "text",
                        "text": EXTRACTION_PROMPT,
                    },
                ],
            }
        ],
    )

    raw_text = message.content[0].text.strip()

    # Strip markdown fences if model ignored the instruction
    raw_text = re.sub(r"^```[a-z]*\n?", "", raw_text)
    raw_text = re.sub(r"\n?```$",       "", raw_text)
    raw_text = raw_text.strip()

    return json.loads(raw_text)


# ── Validate extracted context ────────────────────────────────────────

def validate_context(ctx: dict, session_date: str) -> list[str]:
    warnings = []
    vp  = ctx.get("volume_profile", {})
    ses = ctx.get("session", {})

    vah, poc, val = vp.get("vah", 0), vp.get("poc", 0), vp.get("val", 0)

    if vah > 0 and poc > 0 and val > 0:
        if not (vah > poc > val):
            warnings.append(f"VAH({vah}) > POC({poc}) > VAL({val}) — orden incorrecto")
        if (vah - val) > 500:
            warnings.append(f"VA range muy amplio: {vah-val} pts")
    else:
        warnings.append("Volume Profile incompleto (vah/poc/val = 0)")

    op = ses.get("open_price", 0)
    if op > 0 and vah > 0:
        distance = abs(op - poc)
        if distance > 200:
            warnings.append(f"Open ({op}) muy lejos del POC ({poc}): {distance} pts")

    ph = ses.get("prev_high", 0)
    pl = ses.get("prev_low",  0)
    if ph > 0 and pl > 0 and ph <= pl:
        warnings.append(f"PDH({ph}) <= PDL({pl}) — incorrecto")

    return warnings


# ── Build final context JSON ──────────────────────────────────────────

def build_context(session_date: str, levels: dict) -> dict:
    vp  = levels.get("volume_profile", {})
    ses = levels.get("session",        {})
    sg  = levels.get("spotgamma",      {})

    def f(d, k):
        return float(d.get(k) or 0.0)

    return {
        "date": session_date,
        "volume_profile": {
            "vah": f(vp, "vah"),
            "poc": f(vp, "poc"),
            "val": f(vp, "val"),
        },
        "session": {
            "prev_high":  f(ses, "prev_high"),
            "prev_low":   f(ses, "prev_low"),
            "prev_close": f(ses, "prev_close"),
            "onh":        f(ses, "onh"),
            "onl":        f(ses, "onl"),
            "open_price": f(ses, "open_price"),
            "ibh":        f(ses, "ibh"),
            "ibl":        f(ses, "ibl"),
        },
        "spotgamma": {
            "call_wall":          f(sg, "call_wall"),
            "put_wall":           f(sg, "put_wall"),
            "zero_gamma":         f(sg, "zero_gamma"),
            "volatility_trigger": f(sg, "volatility_trigger"),
            "hpz":                f(sg, "hpz"),
            "combo_1":            f(sg, "combo_1"),
            "combo_2":            f(sg, "combo_2"),
            "large_gamma_1":      f(sg, "large_gamma_1"),
            "large_gamma_2":      f(sg, "large_gamma_2"),
            "large_gamma_3":      f(sg, "large_gamma_3"),
            "large_gamma_4":      f(sg, "large_gamma_4"),
        },
    }


# ── Save context ──────────────────────────────────────────────────────

def save_context(ctx: dict, session_date: str, overwrite: bool = False) -> str:
    os.makedirs(CONTEXT_DIR, exist_ok=True)
    out_path = os.path.join(CONTEXT_DIR, f"{session_date}.json")

    if os.path.exists(out_path) and not overwrite:
        print(f"  {Y}[EXISTE]{RST} {out_path}")
        ans = input("  ¿Sobrescribir? [s/N] ").strip().lower()
        if ans not in ("s", "si", "sí", "y", "yes"):
            print(f"  {DIM}Cancelado.{RST}")
            return ""

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(ctx, fh, indent=2)

    return out_path


# ── Process one screenshot ────────────────────────────────────────────

def process_file(client: anthropic.Anthropic,
                 image_path: str,
                 session_date: str | None = None,
                 overwrite: bool = False) -> bool:

    if not session_date:
        session_date = date_from_filename(image_path)
    if not session_date:
        session_date = date.today().isoformat()
        print(f"  {Y}[FECHA]{RST} No detectada en nombre — usando hoy: {session_date}")

    print(f"\n  {C}[VISION]{RST} {Path(image_path).name} → {session_date}")

    try:
        levels = extract_levels(client, image_path)
    except json.JSONDecodeError as e:
        print(f"  {R}[ERROR]{RST} Claude no devolvió JSON válido: {e}")
        return False
    except Exception as e:
        print(f"  {R}[ERROR]{RST} Extracción fallida: {e}")
        return False

    ctx      = build_context(session_date, levels)
    warnings = validate_context(ctx, session_date)

    # Print extracted values
    vp  = ctx["volume_profile"]
    ses = ctx["session"]
    sg  = ctx["spotgamma"]
    print(f"  {G}[EXTRAÍDO]{RST}")
    print(f"    VAH={vp['vah']}  POC={vp['poc']}  VAL={vp['val']}")
    print(f"    PDH={ses['prev_high']}  PDL={ses['prev_low']}  PDC={ses['prev_close']}")
    print(f"    ONH={ses['onh']}  ONL={ses['onl']}  Open={ses['open_price']}")
    print(f"    IBH={ses['ibh']}  IBL={ses['ibl']}")
    print(f"    CallWall={sg['call_wall']}  PutWall={sg['put_wall']}")
    print(f"    ZeroGamma={sg['zero_gamma']}  VolTrigger={sg['volatility_trigger']}  HPZ={sg['hpz']}")

    if warnings:
        for w in warnings:
            print(f"  {Y}[WARN]{RST} {w}")

    out = save_context(ctx, session_date, overwrite=overwrite)
    if out:
        print(f"  {G}[GUARDADO]{RST} {out}")
        return True
    return False


# ── Watch mode ────────────────────────────────────────────────────────

def watch_mode(client: anthropic.Anthropic, overwrite: bool = False):
    watch_dir = Path(SCREENSHOTS_DIR)
    watch_dir.mkdir(exist_ok=True)

    print(f"\n{B}{'═'*56}{RST}")
    print(f"{B}  GIBBZ Context Extractor — WATCH MODE{RST}")
    print(f"{B}{'═'*56}{RST}")
    print(f"  Vigilando: {watch_dir.resolve()}")
    print(f"  Salida:    {CONTEXT_DIR}/")
    print(f"  Modelo:    {MODEL}")
    print(f"  Interval:  {WATCH_INTERVAL}s")
    print(f"  {DIM}Ctrl+C para detener{RST}\n")
    print(f"  Esperando screenshots...")
    print(f"  {DIM}Naming: YYYY-MM-DD.png o context_YYYY-MM-DD.png{RST}\n")

    seen_hashes: set[str] = set()

    # Pre-populate with existing files so we don't re-process them on start
    for f in watch_dir.iterdir():
        if f.suffix.lower() in IMAGE_EXTENSIONS:
            h = _file_hash(f)
            seen_hashes.add(h)

    try:
        while True:
            for f in sorted(watch_dir.iterdir()):
                if f.suffix.lower() not in IMAGE_EXTENSIONS:
                    continue
                h = _file_hash(f)
                if h in seen_hashes:
                    continue
                seen_hashes.add(h)
                print(f"\n  {G}[NUEVO]{RST} {f.name}")
                process_file(client, str(f), overwrite=overwrite)

            time.sleep(WATCH_INTERVAL)

    except KeyboardInterrupt:
        print(f"\n  {Y}[STOP]{RST} Watch mode detenido.")


def _file_hash(path: Path) -> str:
    h = hashlib.md5()
    h.update(path.read_bytes())
    return h.hexdigest()


# ── Main ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="GIBBZ — Extrae historical_context desde screenshots via Claude Vision",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Ejemplos:
  python extract_context.py --watch
  python extract_context.py --file screenshots/2026-05-12.png
  python extract_context.py --file img.png --date 2026-05-12
  python extract_context.py --file img.png --date 2026-05-12 --overwrite
""")
    parser.add_argument("--watch",     action="store_true",
                        help=f"Vigilar {SCREENSHOTS_DIR}/ en tiempo real")
    parser.add_argument("--file",      type=str, default=None,
                        help="Procesar un screenshot específico")
    parser.add_argument("--date",      type=str, default=None,
                        help="Fecha de sesión YYYY-MM-DD (override del nombre del archivo)")
    parser.add_argument("--overwrite", action="store_true",
                        help="Sobrescribir JSON existente sin preguntar")
    args = parser.parse_args()

    if not args.watch and not args.file:
        parser.print_help()
        sys.exit(0)

    # API key
    api_key = get_api_key()
    if not api_key:
        print(f"\n{R}[ERROR]{RST} ANTHROPIC_API_KEY no encontrada.")
        print(f"  Opciones:")
        print(f"  1. Variable de entorno:  $env:ANTHROPIC_API_KEY = 'sk-ant-...'")
        print(f"  2. Archivo .env en el proyecto: ANTHROPIC_API_KEY=sk-ant-...")
        sys.exit(1)

    client = anthropic.Anthropic(api_key=api_key)

    if args.watch:
        watch_mode(client, overwrite=args.overwrite)
    elif args.file:
        if not os.path.exists(args.file):
            print(f"{R}[ERROR]{RST} Archivo no encontrado: {args.file}")
            sys.exit(1)
        ok = process_file(client, args.file,
                          session_date=args.date,
                          overwrite=args.overwrite)
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
