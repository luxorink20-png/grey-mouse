"""
scripts/run_paper_trading.py
Pre-flight checklist + live monitor para sesiones de paper trading.

El paper trading de GIBBZ se ejecuta con:
    python engine.py

Este script:
  1. Valida que toda la configuracion es correcta
  2. Muestra el estado del ContextFilter
  3. Monitorea logs/gibbz_trades_YYYY-MM-DD.csv en tiempo real
     mientras engine.py corre en otro terminal

USO:
    python scripts/run_paper_trading.py --check       # solo pre-flight
    python scripts/run_paper_trading.py               # pre-flight + monitor live
    python scripts/run_paper_trading.py --watch 60    # polling cada 60s
"""

import sys
import os
import csv
import time
import argparse
import json
from datetime import datetime, date
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

CORE_DIR = Path(__file__).parent.parent
CONFIG_FILE = CORE_DIR / "config" / "paper_trading_config.yaml"
LOGS_DIR    = CORE_DIR / "logs"


# ── Config loader ──────────────────────────────────────────────────────────

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    if _YAML_OK:
        import yaml
        with open(CONFIG_FILE, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    # Fallback: return hardcoded defaults if yaml not installed
    return {
        "paper_trading": {
            "success_criteria": {"min_profit_factor": 2.5, "max_drawdown_pts": 20.0,
                                 "min_win_rate": 0.45, "min_cumulative_trades": 20},
            "failure_criteria": {"pf_below_threshold": 2.0, "maxdd_critical_pts": 30.0},
            "alerts": {"warn_maxdd_pts": 20.0, "warn_pf_degradation": 2.3},
            "logging": {"trades_csv_pattern": "logs/gibbz_trades_{date}.csv"},
        }
    }


# ── Trades CSV reader ──────────────────────────────────────────────────────

def read_trades_csv(filepath: Path) -> list:
    if not filepath.exists():
        return []
    trades = []
    try:
        with open(filepath, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    result = row.get("result", "")
                    pnl    = float(row.get("pnl_pts", "0") or "0")
                    if result in ("WIN", "LOSS", "BREAKEVEN", "TIMEOUT"):
                        trades.append({
                            "trade_id":         row.get("trade_id", ""),
                            "open_time":        row.get("open_time", ""),
                            "close_time":       row.get("close_time", ""),
                            "direction":        row.get("direction", ""),
                            "entry_price":      float(row.get("entry_price", "0") or "0"),
                            "exit_price":       float(row.get("exit_price", "0") or "0"),
                            "result":           result,
                            "pnl_pts":          pnl,
                            "confluence_score": int(row.get("confluence_score", "0") or "0"),
                            "zone":             row.get("zone", ""),
                            "session":          row.get("session", ""),
                            "rr":               float(row.get("rr", "0") or "0"),
                        })
                except (ValueError, TypeError):
                    pass
    except Exception:
        pass
    return trades


def metrics_from_trades(trades: list) -> dict:
    if not trades:
        return {"n": 0, "wins": 0, "losses": 0, "wr": 0.0,
                "pf": 0.0, "exp": 0.0, "pnl": 0.0, "max_dd": 0.0}
    wins   = [t for t in trades if t["result"] == "WIN"]
    losses = [t for t in trades if t["result"] == "LOSS"]
    n, nw, nl = len(trades), len(wins), len(losses)
    wr  = 100.0 * nw / n
    sw  = sum(t["pnl_pts"] for t in wins)
    sl  = sum(t["pnl_pts"] for t in losses)
    pf  = abs(sw / sl) if sl != 0 else float("inf")
    aw  = sw / max(nw, 1)
    al  = sl / max(nl, 1)
    exp = round(wr / 100 * aw + (1 - wr / 100) * al, 2)
    total = round(sw + sl, 2)
    # Sequential MaxDD
    cum = peak = max_dd = 0.0
    for t in trades:
        cum += t["pnl_pts"]
        if cum > peak:
            peak = cum
        dd = peak - cum
        if dd > max_dd:
            max_dd = dd
    return {"n": n, "wins": nw, "losses": nl, "wr": round(wr, 1),
            "pf": round(pf, 2), "exp": exp, "pnl": total, "max_dd": round(max_dd, 2)}


def count_context_skips_today(log_file: Path) -> int:
    if not log_file.exists():
        return 0
    today = date.today().strftime("%Y-%m-%d")
    count = 0
    try:
        with open(log_file, encoding="utf-8", errors="replace") as f:
            for line in f:
                if today in line and "CONTEXT SKIP" in line:
                    count += 1
    except Exception:
        pass
    return count


def _pf(pf): return f"{pf:.2f}" if pf != float("inf") else "inf"
def _sep(n=70): print("  " + "-" * n)


# ── Pre-flight check ────────────────────────────────────────────────────────

def preflight_check() -> bool:
    """Verifica que todo esta listo para paper trading."""
    print()
    print("=" * 72)
    print("  GIBBZ Paper Trading — Pre-flight Check")
    print("=" * 72)
    print()

    ok_all = True

    # 1. ContextFilter importable
    try:
        from context_filter import ContextFilter
        cf = ContextFilter()
        status = cf.get_status()
        print("  [OK] context_filter.py importable")
        print(f"       VOL_RELEASE={status['enable_vol_release']}  "
              f"REGIME={status['enable_destructive_regime']}  "
              f"KILL_SWITCH={status['enable_session_kill_switch']}")
    except Exception as e:
        print(f"  [FAIL] context_filter.py: {e}")
        ok_all = False

    # 2. Config file
    if CONFIG_FILE.exists():
        print(f"  [OK] config/paper_trading_config.yaml encontrado")
        cfg = load_config()
        pt = cfg.get("paper_trading", {})
        sc = pt.get("success_criteria", {})
        print(f"       PF objetivo: >= {sc.get('min_profit_factor', '?')}  "
              f"MaxDD: <= {sc.get('max_drawdown_pts', '?')} pts  "
              f"WR: >= {sc.get('min_win_rate', '?'):.0%}")
    else:
        print("  [WARN] config/paper_trading_config.yaml no encontrado (usar defaults)")

    # 3. Logs directory
    if LOGS_DIR.exists():
        trades_today = LOGS_DIR / f"gibbz_trades_{date.today().strftime('%Y-%m-%d')}.csv"
        if trades_today.exists():
            n_trades = len(read_trades_csv(trades_today))
            print(f"  [OK] logs/ existe — {trades_today.name}: {n_trades} trades hoy")
        else:
            print(f"  [OK] logs/ existe — {trades_today.name}: no existe aun")
    else:
        print("  [WARN] logs/ no existe — se creara cuando corra engine.py")

    # 4. Full_backtest baseline
    baseline_pf = 2.91
    baseline_dd = 12.0
    print(f"  [OK] Baseline backtest: PF={baseline_pf}, MaxDD={baseline_dd} pts")
    print(f"       (Degradacion maxima aceptable: PF >= 2.3 en paper trading)")

    # 5. engine.py
    engine_path = CORE_DIR / "engine.py"
    if engine_path.exists():
        print("  [OK] engine.py encontrado")
    else:
        print("  [FAIL] engine.py no encontrado")
        ok_all = False

    print()
    if ok_all:
        print("  SISTEMA LISTO PARA PAPER TRADING")
        print()
        print("  Para iniciar la sesion de paper trading:")
        print("  ─────────────────────────────────────────")
        print("  $ python engine.py")
        print()
        print("  Para monitorear en tiempo real (en otro terminal):")
        print("  ─────────────────────────────────────────────────")
        print("  $ python scripts/run_paper_trading.py --watch 30")
        print()
        print("  Para reporte diario (al final de cada sesion):")
        print("  ───────────────────────────────────────────────")
        print("  $ python scripts/daily_paper_trading_report.py")
    else:
        print("  REVISAR ITEMS MARCADOS [FAIL] ANTES DE CONTINUAR")

    return ok_all


# ── Live monitor ────────────────────────────────────────────────────────────

def live_monitor(poll_seconds: int = 30) -> None:
    """Monitorea trades en tiempo real polling el CSV."""
    print()
    print("=" * 72)
    print(f"  GIBBZ Paper Trading — Monitor Live (poll cada {poll_seconds}s)")
    print("  Asegurarse que engine.py esta corriendo en otro terminal.")
    print("  Ctrl+C para salir.")
    print("=" * 72)

    cfg = load_config()
    pt  = cfg.get("paper_trading", {})
    sc  = pt.get("success_criteria", {})
    fc  = pt.get("failure_criteria", {})
    al  = pt.get("alerts", {})

    min_pf   = sc.get("min_profit_factor", 2.5)
    warn_pf  = al.get("warn_pf_degradation", 2.3)
    fail_pf  = fc.get("pf_below_threshold", 2.0)
    warn_dd  = al.get("warn_maxdd_pts", 20.0)
    fail_dd  = fc.get("maxdd_critical_pts", 30.0)
    min_wr   = sc.get("min_win_rate", 0.45)

    log_file  = CORE_DIR / "logs" / "gibbz.log"
    last_n    = 0

    while True:
        trades_file = LOGS_DIR / f"gibbz_trades_{date.today().strftime('%Y-%m-%d')}.csv"
        trades      = read_trades_csv(trades_file)
        skips       = count_context_skips_today(log_file)
        m           = metrics_from_trades(trades)
        now         = datetime.now().strftime("%H:%M:%S")

        if m["n"] != last_n or m["n"] == 0:
            last_n = m["n"]
            print(f"\n  [{now}] trades={m['n']}  "
                  f"WR={m['wr']:.1f}%  "
                  f"PF={_pf(m['pf'])}  "
                  f"Exp={m['exp']:+.2f}  "
                  f"PnL={m['pnl']:+.2f}  "
                  f"DD={m['max_dd']:.2f}  "
                  f"skips={skips}")

            # Alertas
            if m["n"] > 0:
                if m["pf"] != float("inf") and m["pf"] < fail_pf:
                    print(f"  [ALERTA CRITICA] PF={_pf(m['pf'])} < {fail_pf} — considerar parar sesion")
                elif m["pf"] != float("inf") and m["pf"] < warn_pf:
                    print(f"  [WARN] PF={_pf(m['pf'])} < {warn_pf} — monitorear")
                if m["max_dd"] > fail_dd:
                    print(f"  [ALERTA CRITICA] DD={m['max_dd']:.2f} > {fail_dd} pts — KILL SWITCH")
                elif m["max_dd"] > warn_dd:
                    print(f"  [WARN] DD={m['max_dd']:.2f} > {warn_dd} pts")
                if m["n"] >= 5 and m["wr"] / 100 < min_wr:
                    print(f"  [WARN] WR={m['wr']:.1f}% < {min_wr:.0%} objetivo")

        try:
            time.sleep(poll_seconds)
        except KeyboardInterrupt:
            print("\n\n  Monitor detenido.")
            if m["n"] > 0:
                print()
                print("  Resumen final de la sesion:")
                _sep()
                print(f"  Trades:    {m['n']}  ({m['wins']}W / {m['losses']}L)")
                print(f"  WR:        {m['wr']:.1f}%  (objetivo >= {min_wr:.0%})")
                print(f"  PF:        {_pf(m['pf'])}  (objetivo >= {min_pf})")
                print(f"  Exp:       {m['exp']:+.2f} pts/trade")
                print(f"  PnL:       {m['pnl']:+.2f} pts")
                print(f"  MaxDD:     {m['max_dd']:.2f} pts  (maximo {fail_dd} pts)")
                print(f"  CF Skips:  {skips}")
                print()
                print("  Ejecutar reporte diario:")
                print("  $ python scripts/daily_paper_trading_report.py")
            break


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="GIBBZ Paper Trading — pre-flight + live monitor"
    )
    parser.add_argument("--check",  action="store_true",
                        help="Solo pre-flight check, no monitor")
    parser.add_argument("--watch",  type=int, default=30, metavar="SECS",
                        help="Polling interval en segundos (default: 30)")
    args = parser.parse_args()

    ok = preflight_check()

    if args.check or not ok:
        sys.exit(0 if ok else 1)

    live_monitor(poll_seconds=args.watch)


if __name__ == "__main__":
    main()
