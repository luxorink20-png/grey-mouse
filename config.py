# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — config.py
#  Central configuration with environment-variable overrides.
#
#  Override any value at runtime:
#    $env:GIBBZ_UDP_PORT = "9998"
#    $env:GIBBZ_ENABLE_VOICE = "0"
#    $env:GIBBZ_USE_REAL_FEED = "0"
#
#  Engine-internal thresholds (not overridable here — edit in-class):
#    EventEngine    → event_engine.py  (THRESHOLD_INTENTO, WARMUP_BARS …)
#    RiskEngine     → risk_engine.py   (MIN_RR, MAX_RISK_PTS, SIZING_TABLE)
#    Validator      → validator.py     (MIN_SCORE_TO_TRADE, MIN_BASE_SCORE)
#    ConfluenceEng  → confluence_engine.py (SCORE_BANDS, CONFLUENCE_MATRIX)
# ╚══════════════════════════════════════════════════════════════════╝

import os

def _bool(key: str, default: bool) -> bool:
    val = os.environ.get(key)
    if val is None:
        return default
    return val.strip().lower() not in ("0", "false", "no", "off")

def _int(key: str, default: int) -> int:
    val = os.environ.get(key)
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default

def _str(key: str, default: str) -> str:
    return os.environ.get(key, default)


# ── Feature flags ─────────────────────────────────────────────────────
ENABLE_LOGGING   = _bool("GIBBZ_ENABLE_LOGGING",   True)
OVERRIDE_SESSION = _bool("GIBBZ_OVERRIDE_SESSION",  True)
USE_REAL_FEED    = _bool("GIBBZ_USE_REAL_FEED",     True)
ENABLE_VOICE     = _bool("GIBBZ_ENABLE_VOICE",      True)

# ── UDP connection ────────────────────────────────────────────────────
UDP_HOST = _str("GIBBZ_UDP_HOST", "127.0.0.1")
UDP_PORT = _int("GIBBZ_UDP_PORT", 9999)
