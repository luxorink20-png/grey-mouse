"""
scripts/validate_context_filter.py — Validacion rapida del ContextFilter.
Ejecutar despues de implementar para confirmar comportamiento correcto.

Uso:
    python scripts/validate_context_filter.py
"""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from datetime import datetime, timezone, timedelta
from context_filter import ContextFilter

_ET = timezone(timedelta(hours=-4))

PASS = "[PASS]"
FAIL = "[FAIL]"


def _bar(price=7200.0, high=None, low=None, volume=500.0, trades=50):
    h = high if high is not None else price + 2.0
    l = low  if low  is not None else price - 2.0
    return {
        "price": float(price), "open": float(price),
        "high": float(h), "low": float(l), "close": float(price),
        "volume": float(volume), "ask_volume": float(volume * 0.55),
        "bid_volume": float(volume * 0.45), "delta": float(volume * 0.1),
        "trades": int(trades),
    }


def _ts(hour: int) -> datetime:
    return datetime(2026, 5, 31, hour, 0, tzinfo=_ET)


def _warm(cf, n=15, base_vol=500.0, base_atr=4.0):
    for _ in range(n):
        cf.update_bar(_bar(volume=base_vol,
                          high=7200.0 + base_atr / 2,
                          low=7200.0  - base_atr / 2,
                          trades=50))


passed = failed = 0

def check(label: str, condition: bool, expected: bool, detail: str = ""):
    global passed, failed
    ok = condition == expected
    icon = PASS if ok else FAIL
    tag = f"(esperado={expected}, got={condition})"
    print(f"  {icon}  {label}  {tag}  {detail}")
    if ok:
        passed += 1
    else:
        failed += 1


print()
print("=" * 72)
print("  GIBBZ ContextFilter — Validacion Rapida")
print("=" * 72)

# ── Test 1: VOL_RELEASE detectado (modo dinamico) ──────────────────────────
print()
print("[Test 1] VOL_RELEASE — deteccion dinamica")
cf = ContextFilter(enable_vol_release=True)
_warm(cf)
hot_bar = _bar(high=7210.0, low=7190.0, volume=2000.0, trades=300)
skip, reason = cf.should_skip(hot_bar, ts_et=_ts(14))
check("vol_release skip=True bajo condiciones completas", skip, True, reason[:60])

# ── Test 2: Mercado normal (mediodia pero sin condiciones) ─────────────────
print()
print("[Test 2] Mercado normal en mediodia — NO debe skippearse")
cf2 = ContextFilter(enable_vol_release=True)
_warm(cf2)
normal_bar = _bar(volume=510.0, high=7202.0, low=7198.0, trades=52)
skip2, _ = cf2.should_skip(normal_bar, ts_et=_ts(14))
check("mercado normal en mediodia skip=False", skip2, False)

# ── Test 3: Filtro OFF — nunca skippea ─────────────────────────────────────
print()
print("[Test 3] Filtro VOL_RELEASE OFF — siempre permite")
cf3 = ContextFilter(enable_vol_release=False)
_warm(cf3)
skip3, _ = cf3.should_skip(hot_bar, ts_et=_ts(14))
check("filtro OFF skip=False aunque condiciones completas", skip3, False)

# ── Test 4: Session kill switch ─────────────────────────────────────────────
print()
print("[Test 4] Session kill switch — DD > umbral")
cf4 = ContextFilter(enable_vol_release=False,
                    enable_destructive_regime=False,
                    enable_session_kill_switch=True,
                    session_maxdd_threshold=30.0)
cf4.register_trade(10.0, True)    # peak = 10
cf4.register_trade(-50.0, False)  # cum = -40, DD = 50 > 30
skip4, reason4 = cf4.should_skip(_bar())
check("kill switch skip=True con DD>30", skip4, True, reason4[:60])

# ── Test 5: kill switch reset ──────────────────────────────────────────────
print()
print("[Test 5] Kill switch se resetea con reset_session()")
cf4.reset_session()
skip5, _ = cf4.should_skip(_bar())
check("despues de reset_session kill switch skip=False", skip5, False)

# ── Test 6: Regimen destructivo ────────────────────────────────────────────
print()
print("[Test 6] Regimen destructivo — WR<25%, PF<0.8")
cf6 = ContextFilter(enable_vol_release=False,
                    enable_destructive_regime=True,
                    enable_session_kill_switch=False)
for _ in range(9):
    cf6.register_trade(-10.0, False)
cf6.register_trade(5.0, True)   # 1/10 = 10% WR, PF = 5/90 = 0.056
skip6, reason6 = cf6.should_skip(_bar())
check("regimen destructivo skip=True", skip6, True, reason6[:60])

# ── Test 7: is_session_filtered (backtest) ────────────────────────────────
print()
print("[Test 7] is_session_filtered — uso en backtest")
cf7 = ContextFilter(enable_vol_release=True)
check("VOL_RELEASE session filtrada=True",
      cf7.is_session_filtered("VOL_RELEASE"), True)
check("EXPANSION session filtrada=False",
      cf7.is_session_filtered("EXPANSION"), False)
check("OPENING_DRIVE session filtrada=False",
      cf7.is_session_filtered("OPENING_DRIVE"), False)

# ── Test 8: datos vacios no crashean ──────────────────────────────────────
print()
print("[Test 8] Robustez — datos ausentes no crashean")
cf8 = ContextFilter()
try:
    cf8.update_bar({})
    skip8, _ = cf8.should_skip({})
    check("bar vacio skip=False (no crash)", skip8, False)
except Exception as e:
    check(f"bar vacio sin excepcion (CRASH: {e})", False, True)

# ── Resumen ────────────────────────────────────────────────────────────────
print()
print("=" * 72)
total = passed + failed
if failed == 0:
    print(f"  ALL TESTS PASSED ({passed}/{total})")
    print()
    print("  Expected backtest outcomes (scripts/run_backtest_with_filter.py):")
    print("    PF:         1.56 -> ~2.91  (+87%)")
    print("    MaxDD:      95.75 -> ~12.00  (-87%)")
    print("    Expectancy: +2.61 -> ~+6.70  (+157%)")
    print("    Trades:     106 -> ~32  (-70% volumen)")
    print()
    print("  Siguiente paso: python scripts/run_backtest_with_filter.py")
else:
    print(f"  {failed}/{total} TESTS FAILED -- revisar antes de continuar")
print("=" * 72)
print()

sys.exit(0 if failed == 0 else 1)
