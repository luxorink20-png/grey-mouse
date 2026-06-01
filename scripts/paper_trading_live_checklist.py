"""
scripts/paper_trading_live_checklist.py
Checklist completo Paper Trading -> Live Trading.

Evalua 15 criterios (6 basicos + 9 avanzados) para determinar si el sistema
esta listo para pasar a Live Trading Fase 1.

USO:
    python scripts/paper_trading_live_checklist.py
    python scripts/paper_trading_live_checklist.py --date 2026-06-15
    python scripts/paper_trading_live_checklist.py --start 2026-06-01 --date 2026-06-15
"""

import sys
import os
import csv
import argparse
from datetime import datetime, date, timedelta
from math import isfinite
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

CORE_DIR    = Path(__file__).parent.parent
LOGS_DIR    = CORE_DIR / "logs"
REPORTS_DIR = CORE_DIR / "reports" / "paper_trading"

# Zones inside Value Area (VA80) vs outside (FA = Fuera del Area)
VA80_ZONES = {"IN_VALUE_AREA", "AT_POC", "AT_VAH", "AT_VAL"}
FA_ZONES   = {"ABOVE_VAH", "BELOW_VAL"}

# Live-trading go/no-go thresholds
THRESHOLDS = {
    "pf_min":            2.5,
    "pnl_min":           0.0,
    "trades_per_week":  10,
    "maxdd_max":        10.0,   # pts (~2% of NQ/ES micro)
    "wr_min":           45.0,   # %
    "exp_min":           0.0,   # pts/trade
    "long_pf_min":       2.0,
    "short_pf_min":      4.0,
    "slip_avg_max":      1.0,   # ticks/leg
    "slip_max_max":      2.0,   # ticks/leg
    "max_consec_losses": 3,
    "va80_pf_min":       2.5,
    "fa_pf_min":         2.0,
    "min_subsample":     3,     # min trades before a sub-PF is evaluated
}

OK   = "[ OK ]"
FAIL = "[FAIL]"
WARN = "[WARN]"
NA   = "[ N/A]"


# ---------------------------------------------------------------------------
# I/O
# ---------------------------------------------------------------------------

def _read_date(d: date) -> list[dict]:
    fname = LOGS_DIR / f"gibbz_trades_{d.strftime('%Y-%m-%d')}.csv"
    if not fname.exists():
        return []
    rows = []
    try:
        with open(fname, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                result = row.get("result", "")
                if result not in ("WIN", "LOSS", "BREAKEVEN", "TIMEOUT"):
                    continue
                try:
                    rows.append({
                        "date":      str(d),
                        "direction": row.get("direction", "").strip().upper(),
                        "result":    result,
                        "pnl_pts":   float(row.get("pnl_pts", "0") or "0"),
                        "zone":      row.get("zone", "").strip().upper(),
                        "rr":        float(row.get("rr", "0") or "0"),
                    })
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    return rows


def read_all_trades(start: date, end: date) -> list[dict]:
    trades: list[dict] = []
    d = start
    while d <= end:
        trades.extend(_read_date(d))
        d += timedelta(days=1)
    return trades


# ---------------------------------------------------------------------------
# Metric helpers
# ---------------------------------------------------------------------------

def _pf(subset: list[dict]) -> float | None:
    """Profit factor for a subset. None if subset is empty."""
    if not subset:
        return None
    wins   = [t for t in subset if t["result"] == "WIN"]
    losses = [t for t in subset if t["result"] == "LOSS"]
    sw = sum(t["pnl_pts"] for t in wins)
    sl = sum(t["pnl_pts"] for t in losses)
    if sl == 0:
        return float("inf") if sw > 0 else None
    return abs(sw / sl)


def _fmt(v: float | None) -> str:
    if v is None:
        return "N/A"
    if v == float("inf"):
        return "inf"
    return f"{v:.2f}"


def compute_metrics(trades: list[dict]) -> dict:
    if not trades:
        return {}

    wins   = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    n, nw, nl = len(trades), len(wins), len(losses)

    wr  = 100.0 * nw / n
    sw  = sum(t["pnl_pts"] for t in wins)
    sl  = sum(t["pnl_pts"] for t in losses)
    pf  = abs(sw / sl) if sl != 0 else (float("inf") if sw > 0 else 0.0)
    aw  = sw / max(nw, 1)
    al  = sl / max(nl, 1)
    exp = wr / 100 * aw + (1 - wr / 100) * al
    pnl = sw + sl

    # MaxDD (running peak drawdown)
    peak = max_dd = cum = 0.0
    for t in trades:
        cum += t["pnl_pts"]
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd

    # Max consecutive losses
    max_cl = cur_cl = 0
    for t in trades:
        if t["result"] == "LOSS":
            cur_cl += 1
            max_cl = max(max_cl, cur_cl)
        else:
            cur_cl = 0

    # Direction split
    longs  = [t for t in trades if t["direction"] == "LONG"]
    shorts = [t for t in trades if t["direction"] == "SHORT"]

    # Zone split
    va80 = [t for t in trades if t["zone"] in VA80_ZONES]
    fa   = [t for t in trades if t["zone"] in FA_ZONES]

    return {
        "n": n, "wins": nw, "losses": nl,
        "wr":      round(wr, 1),
        "pf":      round(pf, 2) if isfinite(pf) else float("inf"),
        "exp":     round(exp, 2),
        "pnl":     round(pnl, 2),
        "max_dd":  round(max_dd, 2),
        "max_cl":  max_cl,
        # Direction
        "n_long":  len(longs),
        "n_short": len(shorts),
        "long_pf": _pf(longs),
        "short_pf": _pf(shorts),
        # Zone
        "n_va80":  len(va80),
        "n_fa":    len(fa),
        "va80_pf": _pf(va80),
        "fa_pf":   _pf(fa),
    }


# ---------------------------------------------------------------------------
# Checklist builder
# ---------------------------------------------------------------------------

def _item(status: str, label: str, value: str, target: str, note: str = "") -> dict:
    return {"status": status, "label": label,
            "value": value, "target": target, "note": note}


def build_checklist(m: dict, weeks_elapsed: float) -> list[dict]:
    if not m:
        return [_item(FAIL, "Sin datos de trades en el periodo", "0", "> 0")]

    T = THRESHOLDS
    items: list[dict] = []

    # ── BASICOS ──────────────────────────────────────────────────────────────

    # 1. PF >= 2.5
    pf_ok = (m["pf"] >= T["pf_min"]) if m["pf"] != float("inf") else True
    items.append(_item(
        OK if pf_ok else FAIL,
        "PF global >= 2.5",
        _fmt(m["pf"]), f">= {T['pf_min']}",
    ))

    # 2. PnL > 0 pts
    items.append(_item(
        OK if m["pnl"] > T["pnl_min"] else FAIL,
        "PnL total > 0 pts",
        f"{m['pnl']:+.2f} pts", "> 0 pts",
    ))

    # 3. N trades >= 10/semana
    tpw = m["n"] / max(weeks_elapsed, 1)
    items.append(_item(
        OK if tpw >= T["trades_per_week"] else FAIL,
        f"Trades >= {T['trades_per_week']}/semana",
        f"{tpw:.1f}/sem", f">= {T['trades_per_week']}/sem",
    ))

    # 4. MaxDD < 10 pts
    items.append(_item(
        OK if m["max_dd"] < T["maxdd_max"] else FAIL,
        "MaxDD < 10 pts  (~2%)",
        f"{m['max_dd']:.2f} pts", f"< {T['maxdd_max']} pts",
    ))

    # 5. WR >= 45%
    items.append(_item(
        OK if m["wr"] >= T["wr_min"] else FAIL,
        "WR >= 45%",
        f"{m['wr']:.1f}%", f">= {T['wr_min']:.0f}%",
    ))

    # 6. Expectancy > 0 pts/trade
    items.append(_item(
        OK if m["exp"] > T["exp_min"] else FAIL,
        "Expectancy > 0 pts/trade",
        f"{m['exp']:+.2f} pts/trade", "> 0",
    ))

    # ── AVANZADOS — DIRECCION ─────────────────────────────────────────────────

    min_n = T["min_subsample"]

    # 7. LONG PF >= 2.0
    if m["n_long"] >= min_n and m["long_pf"] is not None:
        ok = (m["long_pf"] >= T["long_pf_min"]) if m["long_pf"] != float("inf") else True
        items.append(_item(
            OK if ok else FAIL,
            f"LONG PF >= 2.0  (n={m['n_long']})",
            _fmt(m["long_pf"]), f">= {T['long_pf_min']}",
        ))
    else:
        items.append(_item(
            WARN,
            f"LONG PF >= 2.0  (n={m['n_long']})",
            _fmt(m["long_pf"]), f">= {T['long_pf_min']}",
            f"Necesita >= {min_n} trades LONG para evaluar",
        ))

    # 8. SHORT PF >= 4.0
    if m["n_short"] >= min_n and m["short_pf"] is not None:
        ok = (m["short_pf"] >= T["short_pf_min"]) if m["short_pf"] != float("inf") else True
        items.append(_item(
            OK if ok else FAIL,
            f"SHORT PF >= 4.0  (n={m['n_short']})",
            _fmt(m["short_pf"]), f">= {T['short_pf_min']}",
        ))
    else:
        items.append(_item(
            WARN,
            f"SHORT PF >= 4.0  (n={m['n_short']})",
            _fmt(m["short_pf"]), f">= {T['short_pf_min']}",
            f"Necesita >= {min_n} trades SHORT para evaluar",
        ))

    # 9. SHORT PF > LONG PF
    if m["n_long"] >= min_n and m["n_short"] >= min_n and \
       m["long_pf"] is not None and m["short_pf"] is not None:
        lpf = m["long_pf"] if m["long_pf"] != float("inf") else 999.0
        spf = m["short_pf"] if m["short_pf"] != float("inf") else 999.0
        ok  = spf > lpf
        items.append(_item(
            OK if ok else FAIL,
            "SHORT PF > LONG PF",
            f"SHORT={_fmt(m['short_pf'])} vs LONG={_fmt(m['long_pf'])}",
            "SHORT > LONG",
        ))
    else:
        items.append(_item(
            WARN,
            "SHORT PF > LONG PF",
            f"SHORT={_fmt(m['short_pf'])} vs LONG={_fmt(m['long_pf'])}",
            "SHORT > LONG",
            "Insuf. trades en una o ambas direcciones",
        ))

    # ── AVANZADOS — SLIPPAGE (N/A — no columna en CSV) ───────────────────────

    # 10. Slippage promedio <= 1 tick/leg
    items.append(_item(
        NA,
        "Slippage promedio <= 1 tick/leg",
        "N/A", f"<= {T['slip_avg_max']} ticks",
        "Requiere columna 'slippage_ticks' en gibbz_trades CSV",
    ))

    # 11. Slippage maximo < 2 ticks/leg
    items.append(_item(
        NA,
        "Slippage maximo < 2 ticks/leg",
        "N/A", f"< {T['slip_max_max']} ticks",
        "Requiere columna 'slippage_ticks' en gibbz_trades CSV",
    ))

    # ── AVANZADOS — RACHAS ────────────────────────────────────────────────────

    # 12. Max perdidas consecutivas <= 3
    items.append(_item(
        OK if m["max_cl"] <= T["max_consec_losses"] else FAIL,
        "Max perdidas consecutivas <= 3",
        str(m["max_cl"]), f"<= {T['max_consec_losses']}",
    ))

    # ── AVANZADOS — ZONA ──────────────────────────────────────────────────────

    # 13. VA80 PF >= 2.5
    if m["n_va80"] >= min_n and m["va80_pf"] is not None:
        ok = (m["va80_pf"] >= T["va80_pf_min"]) if m["va80_pf"] != float("inf") else True
        items.append(_item(
            OK if ok else FAIL,
            f"VA80 PF >= 2.5  (n={m['n_va80']})",
            _fmt(m["va80_pf"]), f">= {T['va80_pf_min']}",
        ))
    else:
        items.append(_item(
            WARN,
            f"VA80 PF >= 2.5  (n={m['n_va80']})",
            _fmt(m["va80_pf"]), f">= {T['va80_pf_min']}",
            f"Necesita >= {min_n} trades en zona VA80",
        ))

    # 14. FA PF >= 2.0  (FA = Fuera del Area: ABOVE_VAH, BELOW_VAL)
    if m["n_fa"] >= min_n and m["fa_pf"] is not None:
        ok = (m["fa_pf"] >= T["fa_pf_min"]) if m["fa_pf"] != float("inf") else True
        items.append(_item(
            OK if ok else FAIL,
            f"FA PF >= 2.0  (n={m['n_fa']})",
            _fmt(m["fa_pf"]), f">= {T['fa_pf_min']}",
        ))
    else:
        items.append(_item(
            WARN,
            f"FA PF >= 2.0  (n={m['n_fa']})",
            _fmt(m["fa_pf"]), f">= {T['fa_pf_min']}",
            f"Necesita >= {min_n} trades en zona FA (fuera de area)",
        ))

    # 15. VA80 PF > FA PF
    if m["n_va80"] >= min_n and m["n_fa"] >= min_n and \
       m["va80_pf"] is not None and m["fa_pf"] is not None:
        v = m["va80_pf"] if m["va80_pf"] != float("inf") else 999.0
        f_ = m["fa_pf"]  if m["fa_pf"]  != float("inf") else 999.0
        items.append(_item(
            OK if v > f_ else FAIL,
            "VA80 PF > FA PF",
            f"VA80={_fmt(m['va80_pf'])} vs FA={_fmt(m['fa_pf'])}",
            "VA80 > FA",
        ))
    else:
        items.append(_item(
            WARN,
            "VA80 PF > FA PF",
            f"VA80={_fmt(m['va80_pf'])} vs FA={_fmt(m['fa_pf'])}",
            "VA80 > FA",
            "Insuf. trades en una o ambas zonas",
        ))

    return items


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_SEP = "  " + "-" * 74


def _print_section(title: str, items: list[dict]) -> None:
    print(f"\n  {title}")
    print(_SEP)
    w_label = 48
    w_value = 20
    for it in items:
        note = f"  <- {it['note']}" if it["note"] else ""
        print(f"  {it['status']}  {it['label']:<{w_label}}  "
              f"{it['value']:<{w_value}}  {it['target']}{note}")


def print_checklist(start: date, end: date, trades: list[dict]) -> None:
    m = compute_metrics(trades)
    days_elapsed  = max((end - start).days + 1, 1)
    weeks_elapsed = days_elapsed / 7

    items = build_checklist(m, weeks_elapsed)

    # Header
    print()
    print("=" * 76)
    print("  GIBBZ — Paper Trading -> Live Trading   CHECKLIST COMPLETO")
    print(f"  Periodo : {start}  ->  {end}  ({weeks_elapsed:.1f} semanas)")
    print(f"  Trades  : {m.get('n', 0)}  "
          f"({m.get('wins', 0)}W / {m.get('losses', 0)}L)  "
          f"|  WR={m.get('wr', 0):.1f}%  PF={_fmt(m.get('pf'))}  "
          f"Exp={m.get('exp', 0):+.2f}  PnL={m.get('pnl', 0):+.2f} pts")
    print("=" * 76)

    SECTIONS = [
        ("BASICOS (6 criterios)",                  items[0:6]),
        ("AVANZADOS — Direccion (3 criterios)",     items[6:9]),
        ("AVANZADOS — Slippage (2 criterios)",      items[9:11]),
        ("AVANZADOS — Rachas (1 criterio)",         items[11:12]),
        ("AVANZADOS — Zona (3 criterios)",          items[12:15]),
    ]
    for title, subset in SECTIONS:
        _print_section(title, subset)

    # Summary counts
    n_ok   = sum(1 for it in items if it["status"] == OK)
    n_fail = sum(1 for it in items if it["status"] == FAIL)
    n_warn = sum(1 for it in items if it["status"] == WARN)
    n_na   = sum(1 for it in items if it["status"] == NA)
    total  = len(items)

    print()
    print("=" * 76)
    print(f"  RESUMEN:  OK={n_ok}  FAIL={n_fail}  WARN={n_warn}  N/A={n_na}  "
          f"(de {total} criterios)")
    print("=" * 76)

    # Verdict
    n_trades   = m.get("n", 0)
    two_weeks  = weeks_elapsed >= 2.0
    enough_data = n_trades >= 20

    if n_fail == 0 and n_warn == 0 and two_weeks and enough_data:
        verdict = "GO  — TODOS LOS CRITERIOS CUMPLIDOS"
        detail  = "Sistema listo para Live Fase 1 (1 contrato, 50% size)."
    elif n_fail == 0 and two_weeks and enough_data:
        verdict = "GO CONDICIONAL"
        detail  = (f"{n_warn} criterio(s) WARN por datos insuficientes. "
                   "Continuar 1 semana adicional para confirmar.")
    elif n_fail == 0 and (not two_weeks or not enough_data):
        verdict = "EN PROGRESO — PERIODO INSUFICIENTE"
        detail  = (f"{weeks_elapsed:.1f} sem / {n_trades} trades acumulados. "
                   "Necesitas >= 2 semanas y >= 20 trades.")
    elif n_trades < 10:
        verdict = "EN PROGRESO — DATOS INSUFICIENTES"
        detail  = f"Solo {n_trades} trades. Continua acumulando sesiones."
    else:
        verdict = "NO-GO  — CRITERIOS PENDIENTES"
        detail  = (f"{n_fail} criterio(s) FAIL. "
                   "No avanzar a live hasta resolver todos los FAILs.")

    print(f"  VEREDICTO: {verdict}")
    print(f"  {detail}")

    if n_na > 0:
        print()
        print(f"  NOTA N/A: {n_na} criterio(s) de slippage no evaluables.")
        print("  Para activarlos: agregar columna 'slippage_ticks' en feedback_engine.py")
        print("  (diferencia entry_price_real vs entry_price_intendido en ticks).")

    print()

    # Save to file
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out = REPORTS_DIR / f"live_checklist_{end.strftime('%Y-%m-%d')}.txt"
    try:
        with open(out, "w", encoding="utf-8") as rf:
            rf.write(f"GIBBZ Paper Trading -> Live Trading Checklist\n")
            rf.write(f"Periodo: {start} -> {end}  ({weeks_elapsed:.1f} semanas)\n")
            rf.write(f"Trades: {n_trades}  WR={m.get('wr',0):.1f}%  "
                     f"PF={_fmt(m.get('pf'))}  Exp={m.get('exp',0):+.2f}\n\n")
            for it in items:
                note = f"  <- {it['note']}" if it["note"] else ""
                rf.write(f"{it['status']}  {it['label']:<48}  "
                         f"{it['value']:<20}  {it['target']}{note}\n")
            rf.write(f"\nVEREDICTO: {verdict}\n{detail}\n")
            if n_na > 0:
                rf.write("\nNOTA N/A: slippage requiere columna 'slippage_ticks' en CSV.\n")
        print(f"  Checklist guardado: {out}")
    except Exception as e:
        print(f"  (No se pudo guardar: {e})")
    print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Checklist Paper Trading -> Live Trading GIBBZ"
    )
    parser.add_argument("--date",  default=None,
                        help="Fecha fin YYYY-MM-DD (default: hoy)")
    parser.add_argument("--start", default=None,
                        help="Fecha inicio YYYY-MM-DD (default: config start_date)")
    args = parser.parse_args()

    end_date = (datetime.strptime(args.date, "%Y-%m-%d").date()
                if args.date else date.today())

    if args.start:
        start_date = datetime.strptime(args.start, "%Y-%m-%d").date()
    else:
        start_date = _config_start_date(end_date)

    trades = read_all_trades(start_date, end_date)
    print_checklist(start_date, end_date, trades)


def _config_start_date(fallback_end: date) -> date:
    try:
        import yaml
        cfg_file = CORE_DIR / "config" / "paper_trading_config.yaml"
        if cfg_file.exists():
            with open(cfg_file, encoding="utf-8") as f:
                cfg = yaml.safe_load(f) or {}
            sd = cfg.get("paper_trading", {}).get("start_date")
            if sd:
                return datetime.strptime(str(sd), "%Y-%m-%d").date()
    except Exception:
        pass
    return fallback_end - timedelta(days=30)


if __name__ == "__main__":
    main()
