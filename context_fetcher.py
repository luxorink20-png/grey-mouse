"""
context_fetcher.py
Loads, validates, and refreshes session context levels for GIBBZ engine.

Context hierarchy (highest precision first):
  1. gibbz_context_levels.json  — written by GibbzBridge.cs at ATAS startup
                                   (real Rithmic data: PDH/PDL/ONH/ONL)
  2. levels.json                 — manually updated (or populated by this module)
  3. yfinance fallback           — auto-fetches PDH/PDL for today if both above are stale

VAH/VAL/POC always come from levels.json (volume profile requires ATAS computation;
not automatable without the ATAS C# volume profile API).

Usage:
    ctx = load_context()           # auto-resolves best available source
    ctx = load_context(strict=True) # raises ContextStaleError if any field stale
    print_context_summary(ctx)
    save_context(ctx)              # writes back to levels.json with today's date
"""

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

try:
    import yfinance as yf
    _YF_AVAILABLE = True
except ImportError:
    _YF_AVAILABLE = False

CORE_DIR          = Path(__file__).parent
LEVELS_JSON       = CORE_DIR / "levels.json"
# GibbzBridge.cs writes this file when ATAS is running (real Rithmic data)
BRIDGE_CONTEXT    = Path(os.path.expanduser("~")) / "gibbz_context_levels.json"
# Tick size for #MES micro futures
TICK_SIZE         = 0.25
# yfinance ticker for Micro E-Mini S&P 500 (continuous front month)
YF_TICKER         = "MES=F"


class ContextStaleError(RuntimeError):
    """Raised when context data is stale and cannot be auto-refreshed."""
    pass


class ContextValidationError(RuntimeError):
    """Raised when context data has structural issues."""
    pass


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_context(strict: bool = False) -> dict:
    """
    Load context from the best available source.

    Resolution order:
      1. gibbz_context_levels.json (ATAS/Rithmic) — if file exists and is today
      2. levels.json — always read for VAH/VAL/POC/spotgamma
      3. yfinance — auto-refresh PDH/PDL if levels.json date != today

    strict=True raises ContextStaleError if any required field is stale.
    strict=False logs warnings but continues.
    """
    ctx = _read_levels_json()
    today_str = date.today().isoformat()

    # Layer 1: Try to get PDH/PDL/ONH/ONL/VAH/VAL/POC from GibbzBridge.cs output
    bridge_data = _read_bridge_context()
    if bridge_data and bridge_data.get("date") == today_str:
        ctx["session"]["prev_high"]  = bridge_data.get("pdh", ctx["session"]["prev_high"])
        ctx["session"]["prev_low"]   = bridge_data.get("pdl", ctx["session"]["prev_low"])
        onh = bridge_data.get("onh")
        onl = bridge_data.get("onl")
        if onh is not None: ctx["session"]["onh"] = float(onh)
        if onl is not None: ctx["session"]["onl"] = float(onl)
        ctx["_source_pdh_pdl"] = "rithmic_atas"
        ctx["_source_onh_onl"] = "rithmic_atas"

        # VAH/VAL/POC: present only when ATAS is on a Footprint/Cluster chart
        b_vah = bridge_data.get("vah")
        b_val = bridge_data.get("val")
        b_poc = bridge_data.get("poc")
        if b_vah is not None and b_val is not None and b_poc is not None:
            ctx["volume_profile"]["VAH"] = float(b_vah)
            ctx["volume_profile"]["VAL"] = float(b_val)
            ctx["volume_profile"]["POC"] = float(b_poc)
            ctx["_source_vah_val"]       = "rithmic_atas"
        else:
            ctx["_source_vah_val"] = "levels_json"
    else:
        # Layer 2: Check if levels.json is fresh for today
        levels_date = ctx.get("_date", "")
        if levels_date != today_str:
            # Layer 3: Auto-refresh PDH/PDL from yfinance
            pdh, pdl = _fetch_pdh_pdl_yfinance()
            if pdh and pdl:
                ctx["session"]["prev_high"] = pdh
                ctx["session"]["prev_low"]  = pdl
                ctx["_source_pdh_pdl"]      = "yfinance"
            elif strict:
                raise ContextStaleError(
                    f"levels.json date='{levels_date}' != today '{today_str}' "
                    f"and yfinance could not fetch PDH/PDL. "
                    f"Run: python scripts/update_context.py"
                )

            # ONH/ONL cannot be auto-fetched without Rithmic (yfinance has no overnight data)
            ctx["_source_onh_onl"] = "levels_json_stale"
            if strict:
                raise ContextStaleError(
                    f"ONH/ONL data is stale (levels.json date='{levels_date}'). "
                    f"Run: python scripts/update_context.py\n"
                    f"Or start ATAS first (GibbzBridge writes gibbz_context_levels.json)."
                )
        else:
            ctx["_source_pdh_pdl"] = "levels_json"
            ctx["_source_onh_onl"] = "levels_json"

    ctx["_source_vah_val"] = "levels_json"
    ctx["_date"]           = today_str

    _validate_structure(ctx)
    return ctx


def save_context(ctx: dict, path: Path = LEVELS_JSON) -> None:
    """Write context back to levels.json with today's date."""
    payload = {
        "_date": date.today().isoformat(),
        "volume_profile": ctx.get("volume_profile", {}),
        "spotgamma":      ctx.get("spotgamma", {}),
        "session":        ctx.get("session", {}),
    }
    # Preserve any extra top-level keys that aren't internal metadata
    for k, v in ctx.items():
        if not k.startswith("_") and k not in payload:
            payload[k] = v
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def print_context_summary(ctx: dict) -> None:
    """Print a formatted context summary to stdout."""
    today = ctx.get("_date", "?")
    src_pdh = ctx.get("_source_pdh_pdl", "levels_json")
    src_onh = ctx.get("_source_onh_onl", "levels_json")
    src_vah = ctx.get("_source_vah_val", "levels_json")

    vp  = ctx.get("volume_profile", {})
    ses = ctx.get("session", {})
    sg  = ctx.get("spotgamma", {})

    print()
    print("  CONTEXT LEVELS  —  " + today)
    print("  " + "-" * 50)
    print(f"  PDH  : {ses.get('prev_high', '?'):<10}  PDL  : {ses.get('prev_low', '?')}  [{src_pdh}]")
    print(f"  ONH  : {ses.get('onh', '?'):<10}  ONL  : {ses.get('onl', '?')}  [{src_onh}]")
    print(f"  VAH  : {vp.get('VAH', '?'):<10}  VAL  : {vp.get('VAL', '?')}  [{src_vah}]")
    print(f"  POC  : {vp.get('POC', '?')}")
    print(f"  IBH  : {ses.get('ibh', '?'):<10}  IBL  : {ses.get('ibl', '?')}")
    print(f"  OPEN : {ses.get('open_price', '?')}")
    print(f"  HPZ  : {sg.get('hpz', '?')}")
    print()


# ---------------------------------------------------------------------------
# Source readers
# ---------------------------------------------------------------------------

def _read_levels_json(path: Path = LEVELS_JSON) -> dict:
    if not path.exists():
        raise ContextValidationError(
            f"levels.json not found at {path}. "
            "Create it with: python scripts/update_context.py"
        )
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data


def _read_bridge_context(path: Path = BRIDGE_CONTEXT) -> Optional[dict]:
    """Read context_levels.json written by GibbzBridge.cs (real Rithmic data)."""
    if not path.exists():
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _fetch_pdh_pdl_yfinance() -> tuple[Optional[float], Optional[float]]:
    """
    Fetch previous business day High/Low from Yahoo Finance.
    Returns (pdh, pdl) or (None, None) on failure.

    Note: yfinance gives RTH OHLC (~9:30-16:00 ET).
    For the full CME session (incl. Globex), start ATAS to get Rithmic data via GibbzBridge.cs.
    """
    if not _YF_AVAILABLE:
        return None, None
    try:
        ticker = yf.Ticker(YF_TICKER)
        hist = ticker.history(period="5d", interval="1d")
        if hist.empty or len(hist) < 2:
            return None, None
        # Take the most recent COMPLETED day (not today's partial bar)
        prev = hist.iloc[-2] if date.today().isoformat() == str(hist.index[-1].date()) else hist.iloc[-1]
        pdh = round(float(prev["High"]), 2)
        pdl = round(float(prev["Low"]),  2)
        return pdh, pdl
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _validate_structure(ctx: dict) -> None:
    required = {
        "volume_profile": ["VAH", "POC", "VAL"],
        "session":        ["prev_high", "prev_low", "onh", "onl"],
    }
    missing = []
    for section, keys in required.items():
        for k in keys:
            v = ctx.get(section, {}).get(k, 0)
            if not v:
                missing.append(f"{section}.{k}")
    if missing:
        raise ContextValidationError(
            f"Context missing or zero for: {missing}. "
            "Run: python scripts/update_context.py"
        )
    vap = ctx.get("volume_profile", {})
    if vap.get("VAL", 0) >= vap.get("POC", 0) >= vap.get("VAH", 1):
        raise ContextValidationError(
            f"VAL={vap['VAL']} >= POC={vap['POC']} >= VAH={vap['VAH']} — levels inverted."
        )


if __name__ == "__main__":
    ctx = load_context()
    print_context_summary(ctx)
