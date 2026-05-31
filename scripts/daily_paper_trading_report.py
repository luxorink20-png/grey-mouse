"""
scripts/daily_paper_trading_report.py
Reporte diario de paper trading.

Lee logs/gibbz_trades_YYYY-MM-DD.csv (generados por engine.py + feedback_engine.py)
y computa metricas del dia + acumuladas desde start_date.

Ejecutar al final de cada sesion (16:00 ET) o cuando se quiera revisar.

USO:
    python scripts/daily_paper_trading_report.py
    python scripts/daily_paper_trading_report.py --date 2026-06-05
    python scripts/daily_paper_trading_report.py --cumulative   # todas las fechas
"""

import sys
import os
import csv
import argparse
from datetime import datetime, date, timedelta
from pathlib import Path
from collections import defaultdict

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

try:
    import yaml
    _YAML_OK = True
except ImportError:
    _YAML_OK = False

CORE_DIR    = Path(__file__).parent.parent
LOGS_DIR    = CORE_DIR / "logs"
REPORTS_DIR = CORE_DIR / "reports" / "paper_trading"
CONFIG_FILE = CORE_DIR / "config" / "paper_trading_config.yaml"


# ── Config ─────────────────────────────────────────────────────────────────

def load_config() -> dict:
    defaults = {
        "paper_trading": {
            "start_date": date.today().strftime("%Y-%m-%d"),
            "success_criteria": {
                "min_profit_factor":    2.5,
                "max_drawdown_pts":     20.0,
                "min_win_rate":         0.45,
                "min_cumulative_trades":20,
                "min_trades_per_week":  5,
                "max_pf_degradation_pct": 40.0,
            },
            "failure_criteria": {
                "pf_below_threshold":   2.0,
                "pf_consecutive_days":  3,
                "maxdd_critical_pts":   30.0,
                "wr_below_threshold":   0.35,
                "zero_trades_consecutive_days": 3,
            },
            "alerts": {
                "warn_maxdd_pts":       20.0,
                "warn_pf_degradation":  2.3,
                "warn_wr":              0.40,
                "warn_daily_trades_high": 10,
            },
            "logging": {
                "trades_csv_pattern":   "logs/gibbz_trades_{date}.csv",
                "context_filter_log":   "logs/gibbz.log",
            }
        }
    }
    if not CONFIG_FILE.exists() or not _YAML_OK:
        return defaults
    import yaml
    with open(CONFIG_FILE, encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    # Deep merge defaults with loaded
    if "paper_trading" in loaded:
        for k, v in defaults["paper_trading"].items():
            if k not in loaded["paper_trading"]:
                loaded["paper_trading"][k] = v
            elif isinstance(v, dict):
                for sk, sv in v.items():
                    loaded["paper_trading"][k].setdefault(sk, sv)
    else:
        loaded.update(defaults)
    return loaded


# ── Trades reader ───────────────────────────────────────────────────────────

def read_trades_for_date(target_date: date) -> list:
    fname = LOGS_DIR / f"gibbz_trades_{target_date.strftime('%Y-%m-%d')}.csv"
    if not fname.exists():
        return []
    trades = []
    try:
        with open(fname, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                result = row.get("result", "")
                if result not in ("WIN", "LOSS", "BREAKEVEN", "TIMEOUT"):
                    continue
                try:
                    trades.append({
                        "date":             str(target_date),
                        "trade_id":         row.get("trade_id", ""),
                        "open_time":        row.get("open_time", ""),
                        "direction":        row.get("direction", ""),
                        "result":           result,
                        "pnl_pts":          float(row.get("pnl_pts", "0") or "0"),
                        "entry_price":      float(row.get("entry_price", "0") or "0"),
                        "exit_price":       float(row.get("exit_price", "0") or "0"),
                        "confluence_score": int(row.get("confluence_score", "0") or "0"),
                        "zone":             row.get("zone", ""),
                        "rr":               float(row.get("rr", "0") or "0"),
                        "session":          row.get("session", ""),
                    })
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    return trades


def count_cf_skips_for_date(log_file: Path, target_date: date) -> int:
    if not log_file.exists():
        return 0
    ds = target_date.strftime("%Y-%m-%d")
    count = 0
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            for line in f:
                if ds in line and "CONTEXT SKIP" in line:
                    count += 1
    except Exception:
        pass
    return count


def compute_metrics(trades: list) -> dict:
    if not trades:
        return {"n": 0, "wins": 0, "losses": 0, "wr": 0.0,
                "pf": 0.0, "exp": 0.0, "pnl": 0.0, "max_dd": 0.0,
                "avg_win": 0.0, "avg_loss": 0.0}
    wins   = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    n, nw, nl = len(trades), len(wins), len(losses)
    wr   = 100.0 * nw / n
    sw   = sum(t["pnl_pts"] for t in wins)
    sl   = sum(t["pnl_pts"] for t in losses)
    pf   = abs(sw / sl) if sl != 0 else float("inf")
    aw   = round(sw / max(nw, 1), 2)
    al   = round(sl / max(nl, 1), 2)
    exp_ = round(wr / 100 * aw + (1 - wr / 100) * al, 2)
    tot  = round(sw + sl, 2)
    cum  = peak = max_dd = 0.0
    for t in trades:
        cum += t["pnl_pts"]
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return {"n": n, "wins": nw, "losses": nl, "wr": round(wr, 1),
            "pf": round(pf, 2), "exp": exp_, "pnl": tot,
            "max_dd": round(max_dd, 2), "avg_win": aw, "avg_loss": al}


def _pf(pf): return f"{pf:.2f}" if pf != float("inf") else "inf"
def _sep(n=70): print("  " + "-" * n)


# ── Alert evaluation ────────────────────────────────────────────────────────

def evaluate_alerts(m: dict, cfg: dict) -> list:
    al  = cfg["paper_trading"]["alerts"]
    sc  = cfg["paper_trading"]["success_criteria"]
    fc  = cfg["paper_trading"]["failure_criteria"]
    msgs = []
    if m["n"] == 0:
        msgs.append(("[WARN]", "0 trades hoy — el edge no se activo"))
    else:
        if m["pf"] != float("inf") and m["pf"] < fc["pf_below_threshold"]:
            msgs.append(("[CRITICO]", f"PF={_pf(m['pf'])} < {fc['pf_below_threshold']} — failure threshold"))
        elif m["pf"] != float("inf") and m["pf"] < al["warn_pf_degradation"]:
            msgs.append(("[WARN]", f"PF={_pf(m['pf'])} < {al['warn_pf_degradation']}"))
        if m["max_dd"] > fc["maxdd_critical_pts"]:
            msgs.append(("[CRITICO]", f"DD={m['max_dd']:.2f} pts > {fc['maxdd_critical_pts']} — detener sesion"))
        elif m["max_dd"] > al["warn_maxdd_pts"]:
            msgs.append(("[WARN]", f"DD={m['max_dd']:.2f} pts > {al['warn_maxdd_pts']}"))
        if m["wr"] / 100 < al["warn_wr"]:
            msgs.append(("[WARN]", f"WR={m['wr']:.1f}% < {al['warn_wr']:.0%}"))
        if m["n"] > al["warn_daily_trades_high"]:
            msgs.append(("[WARN]", f"{m['n']} trades hoy > {al['warn_daily_trades_high']} — actividad alta"))
    if not msgs:
        msgs.append(("[OK]", "Sin alertas — todas las metricas dentro de rango"))
    return msgs


def evaluate_success(m_cum: dict, cfg: dict,
                     days_elapsed: int, cf_skip_rate: float) -> list:
    sc = cfg["paper_trading"]["success_criteria"]
    checks = []
    baseline_pf = 2.91

    def chk(label, ok, got, expected):
        checks.append(("[OK]  " if ok else "[FAIL]", label, str(got), str(expected)))

    if m_cum["n"] >= 5:
        chk("PF acumulado >= 2.5",        m_cum["pf"] >= sc["min_profit_factor"],
            _pf(m_cum["pf"]),             f">= {sc['min_profit_factor']}")
        chk("MaxDD acumulado <= 20 pts",   m_cum["max_dd"] <= sc["max_drawdown_pts"],
            f"{m_cum['max_dd']:.2f}",     f"<= {sc['max_drawdown_pts']}")
        chk("WR acumulado >= 45%",         m_cum["wr"] / 100 >= sc["min_win_rate"],
            f"{m_cum['wr']:.1f}%",        f">= {sc['min_win_rate']:.0%}")
        # PF degradation vs baseline
        if m_cum["pf"] != float("inf"):
            deg = (baseline_pf - m_cum["pf"]) / baseline_pf * 100
            max_deg = sc["max_pf_degradation_pct"]
            chk(f"Degradacion PF <= {max_deg:.0f}% vs baseline",
                deg <= max_deg, f"{deg:.1f}%", f"<= {max_deg}%")
    else:
        checks.append(("(--)", "Esperando mas trades para evaluar criterios",
                       str(m_cum["n"]), f">= 5"))

    chk(f"Trades acumulados >= {sc['min_cumulative_trades']}",
        m_cum["n"] >= sc["min_cumulative_trades"],
        str(m_cum["n"]),  f">= {sc['min_cumulative_trades']}")

    return checks


# ── Report ──────────────────────────────────────────────────────────────────

def print_report(target_date: date, cfg: dict, cumulative: bool = False) -> None:
    pt = cfg["paper_trading"]
    cfg_start  = datetime.strptime(pt.get("start_date", str(date.today())), "%Y-%m-%d").date()
    # If querying a date before config start_date, use that date as the range start
    start_date = min(cfg_start, target_date)
    log_file   = CORE_DIR / pt["logging"].get("context_filter_log", "logs/gibbz.log")

    # Trades del dia objetivo
    trades_today = read_trades_for_date(target_date)
    skips_today  = count_cf_skips_for_date(log_file, target_date)
    m_today      = compute_metrics(trades_today)

    # Trades acumulados (desde start_date)
    all_trades: list = []
    daily_data: list = []
    d = start_date
    while d <= target_date:
        dt = read_trades_for_date(d)
        sk = count_cf_skips_for_date(log_file, d)
        dm = compute_metrics(dt)
        if dm["n"] > 0 or d == target_date:
            daily_data.append({"date": str(d), "metrics": dm, "skips": sk})
        all_trades.extend(dt)
        d += timedelta(days=1)
    m_cum = compute_metrics(all_trades)
    days_elapsed = (target_date - start_date).days + 1
    weeks_elapsed = max(days_elapsed / 7, 1)

    # CF skip rate
    total_skips  = sum(r["skips"] for r in daily_data)
    total_signals = m_cum["n"] + total_skips
    skip_rate = total_skips / max(total_signals, 1)

    print()
    print("=" * 72)
    print(f"  GIBBZ Paper Trading — Reporte {target_date.strftime('%Y-%m-%d')}")
    print(f"  Inicio: {start_date}  |  Semana {weeks_elapsed:.1f}")
    print("=" * 72)

    # ── Hoy ─────────────────────────────────────────────────────────────
    print()
    print(f"  HOY ({target_date.strftime('%Y-%m-%d')})")
    _sep()
    if m_today["n"] == 0:
        print(f"  Sin trades hoy  |  CF Skips: {skips_today}")
    else:
        print(f"  Trades:     {m_today['n']}  ({m_today['wins']}W / {m_today['losses']}L)")
        print(f"  WR:         {m_today['wr']:.1f}%")
        print(f"  PF:         {_pf(m_today['pf'])}")
        print(f"  Exp:        {m_today['exp']:+.2f} pts/trade")
        print(f"  PnL:        {m_today['pnl']:+.2f} pts")
        print(f"  MaxDD:      {m_today['max_dd']:.2f} pts")
        print(f"  CF Skips:   {skips_today}")

    # ── Acumulado ────────────────────────────────────────────────────────
    print()
    print(f"  ACUMULADO ({start_date} → {target_date})")
    _sep()
    if m_cum["n"] == 0:
        print("  Sin trades en el periodo")
    else:
        print(f"  Trades:     {m_cum['n']}  ({m_cum['wins']}W / {m_cum['losses']}L)  "
              f"~{m_cum['n']/weeks_elapsed:.1f}/semana")
        print(f"  WR:         {m_cum['wr']:.1f}%")
        print(f"  PF:         {_pf(m_cum['pf'])}  (baseline: 2.91)")
        print(f"  Exp:        {m_cum['exp']:+.2f} pts/trade  (baseline: +6.70)")
        print(f"  PnL:        {m_cum['pnl']:+.2f} pts")
        print(f"  MaxDD:      {m_cum['max_dd']:.2f} pts  (maximo: 30 pts)")
        print(f"  CF Skips:   {total_skips}  (skip rate: {skip_rate:.1%})")

    # ── Historial diario (si --cumulative) ───────────────────────────────
    if cumulative and daily_data:
        print()
        print("  HISTORIAL DIARIO")
        _sep()
        print(f"  {'Fecha':<12}  {'N':>3}  {'WR':>6}  {'PF':>6}  "
              f"{'Exp':>7}  {'PnL':>8}  {'DD':>6}  {'Skips':>6}")
        _sep(60)
        for r in daily_data:
            dm = r["metrics"]
            if dm["n"] == 0:
                print(f"  {r['date']:<12}  {'0':>3}  {'--':>6}  {'--':>6}  "
                      f"{'--':>7}  {'--':>8}  {'--':>6}  {r['skips']:>6}")
            else:
                print(f"  {r['date']:<12}  {dm['n']:>3}  {dm['wr']:>5.1f}%  "
                      f"{_pf(dm['pf']):>6}  {dm['exp']:>+7.2f}  "
                      f"{dm['pnl']:>+8.2f}  {dm['max_dd']:>6.2f}  {r['skips']:>6}")

    # ── Alertas hoy ───────────────────────────────────────────────────────
    print()
    print("  ALERTAS")
    _sep()
    for icon, msg in evaluate_alerts(m_today, cfg):
        print(f"  {icon}  {msg}")

    # ── Criterios de exito ────────────────────────────────────────────────
    print()
    print("  CRITERIOS DE EXITO (acumulado)")
    _sep()
    print(f"  {'Check':<42}  {'Estado':>7}  {'Valor':<12}  {'Objetivo'}")
    _sep()
    for icon, label, got, expected in evaluate_success(m_cum, cfg, days_elapsed, skip_rate):
        print(f"  {icon}  {label:<42}  {got:<12}  {expected}")

    # ── Veredicto ─────────────────────────────────────────────────────────
    print()
    print("=" * 72)
    all_ok = all(
        icon.strip() == "[OK]"
        for icon, _, _, _ in evaluate_success(m_cum, cfg, days_elapsed, skip_rate)
        if icon.strip() not in ("(--)",)
    )
    weeks_ok = weeks_elapsed >= 2
    trades_ok = m_cum["n"] >= cfg["paper_trading"]["success_criteria"]["min_cumulative_trades"]

    if all_ok and weeks_ok and trades_ok:
        print("  VEREDICTO: GO CONDICIONAL")
        print("  Criterios de exito cumplidos. Evaluar avance a Live Phase 1.")
        print("  (Recomendado: 1 semana adicional para confirmar estabilidad)")
    elif m_cum["n"] < 5:
        print("  VEREDICTO: EN PROGRESO")
        print("  Datos insuficientes — continuar acumulando sesiones.")
    else:
        print("  VEREDICTO: CONTINUAR MONITOREANDO")
        print("  Revisar criterios marcados [FAIL] antes de avanzar.")
    print()

    # ── Guardar reporte ───────────────────────────────────────────────────
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_file = REPORTS_DIR / f"daily_report_{target_date.strftime('%Y-%m-%d')}.txt"
    import io, contextlib
    buf = io.StringIO()
    # Write same output to file (simple approach)
    try:
        with open(report_file, "w", encoding="utf-8") as rf:
            rf.write(f"GIBBZ Paper Trading — Reporte {target_date}\n")
            rf.write(f"Generado: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            rf.write(f"HOY ({target_date}): n={m_today['n']} WR={m_today['wr']:.1f}% "
                     f"PF={_pf(m_today['pf'])} Exp={m_today['exp']:+.2f} "
                     f"PnL={m_today['pnl']:+.2f} DD={m_today['max_dd']:.2f}\n\n")
            rf.write(f"ACUMULADO: n={m_cum['n']} WR={m_cum['wr']:.1f}% "
                     f"PF={_pf(m_cum['pf'])} Exp={m_cum['exp']:+.2f} "
                     f"PnL={m_cum['pnl']:+.2f} DD={m_cum['max_dd']:.2f}\n")
        print(f"  Reporte guardado: {report_file}")
    except Exception as e:
        print(f"  (No se pudo guardar reporte: {e})")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Reporte diario de paper trading GIBBZ"
    )
    parser.add_argument("--date",       type=str, default=None,
                        help="Fecha en formato YYYY-MM-DD (default: hoy)")
    parser.add_argument("--cumulative", action="store_true",
                        help="Mostrar historial diario completo")
    args = parser.parse_args()

    if args.date:
        target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    else:
        target_date = date.today()

    cfg = load_config()
    print_report(target_date, cfg, cumulative=args.cumulative)


if __name__ == "__main__":
    main()
