"""
shadow_stats.py — WR y expectancy de shadow trades acumulados por setup_type

Lee gibbz_trades_*.csv y gibbz_session_*.csv del directorio logs/.
Joinea por timestamp para asignar setup_type a cada trade.
Reporta WR, expectancy y PnL global y por setup_type.

Uso:
    python -X utf8 shadow_stats.py
    python -X utf8 shadow_stats.py --date 2026-04-29
    python -X utf8 shadow_stats.py --days 7
"""

import csv
import os
import sys
import argparse
from pathlib import Path
from datetime import datetime, timedelta
from collections import defaultdict

LOGS_DIR   = Path(__file__).parent / "logs"
NO_SETUP   = "NO_SETUP"
SKIP_TYPES = {NO_SETUP, "INSTITUTIONAL_GRADE", ""}


def load_trades(date_str: str) -> list[dict]:
    path = LOGS_DIR / f"gibbz_trades_{date_str}.csv"
    if not path.exists():
        return []
    trades = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if row.get("result", "") in ("PENDING", ""):
                continue
            row["_date"] = date_str
            trades.append(row)
    return trades


def load_session_setups(date_str: str) -> list[tuple[str, str]]:
    """Returns list of (HH:MM:SS, setup_type) for bars where setup fired."""
    path = LOGS_DIR / f"gibbz_session_{date_str}.csv"
    if not path.exists():
        return []
    setups = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            stype = row.get("setup_type", "")
            if stype in SKIP_TYPES:
                continue
            ts = row.get("timestamp", "")
            if len(ts) >= 19:
                setups.append((ts[11:19], stype))  # extract HH:MM:SS
    return setups


def nearest_setup(trade_time: str, setups: list[tuple[str, str]],
                  window_secs: int = 30) -> str:
    """Find closest setup_type within window_secs of trade open_time."""
    if not setups:
        return NO_SETUP

    def to_secs(t: str) -> int:
        try:
            h, m, s = t.split(":")
            return int(h) * 3600 + int(m) * 60 + int(s)
        except Exception:
            return -9999

    t0 = to_secs(trade_time)
    best_stype = NO_SETUP
    best_diff  = window_secs + 1

    for ts, stype in setups:
        diff = abs(to_secs(ts) - t0)
        if diff < best_diff:
            best_diff  = diff
            best_stype = stype

    return best_stype if best_diff <= window_secs else NO_SETUP


def compute_stats(trades: list[dict]) -> dict:
    wins    = [t for t in trades if t.get("result") == "WIN"]
    losses  = [t for t in trades if t.get("result") == "LOSS"]
    timeouts= [t for t in trades if t.get("result") == "TIMEOUT"]

    n   = len(trades)
    if n == 0:
        return {}

    pnl_list = [float(t.get("pnl_pts", 0)) for t in trades]
    total_pnl = round(sum(pnl_list), 2)
    wr        = round(100 * len(wins) / n, 1)
    avg_win   = round(sum(float(t["pnl_pts"]) for t in wins)  / max(len(wins), 1), 2)
    avg_loss  = round(sum(float(t["pnl_pts"]) for t in losses)/ max(len(losses), 1), 2)
    exp       = round(wr/100 * avg_win + (1 - wr/100) * avg_loss, 2)
    rr_list   = [float(t.get("rr", 0)) for t in trades if t.get("rr")]
    avg_rr    = round(sum(rr_list) / max(len(rr_list), 1), 2)

    return {
        "n":        n,
        "wins":     len(wins),
        "losses":   len(losses),
        "timeouts": len(timeouts),
        "wr":       wr,
        "avg_win":  avg_win,
        "avg_loss": avg_loss,
        "exp":      exp,
        "pnl":      total_pnl,
        "avg_rr":   avg_rr,
    }


def print_stats(label: str, s: dict, width: int = 22):
    if not s:
        return
    print(f"  {label:<{width}} N={s['n']:>3}  WR={s['wr']:>5.1f}%  "
          f"Exp={s['exp']:>+6.2f}  PnL={s['pnl']:>+8.2f}  "
          f"W={s['avg_win']:>+7.2f}  L={s['avg_loss']:>+7.2f}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--date",  help="Filtrar a una fecha YYYY-MM-DD")
    parser.add_argument("--days",  type=int, default=0,
                        help="Ultimos N dias (default: todos)")
    args = parser.parse_args()

    # Collect dates to process
    all_trade_files = sorted(LOGS_DIR.glob("gibbz_trades_*.csv"))
    if not all_trade_files:
        sys.exit("No hay archivos gibbz_trades_*.csv en logs/")

    dates = [f.stem.replace("gibbz_trades_", "") for f in all_trade_files]

    if args.date:
        dates = [d for d in dates if d == args.date]
    elif args.days > 0:
        cutoff = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
        dates  = [d for d in dates if d >= cutoff]

    if not dates:
        sys.exit("No hay datos para el filtro indicado.")

    all_trades: list[dict] = []
    by_type: dict[str, list[dict]] = defaultdict(list)
    by_date: dict[str, list[dict]] = defaultdict(list)

    for date_str in dates:
        trades = load_trades(date_str)
        if not trades:
            continue

        setups = load_session_setups(date_str)
        has_setup_col = any(
            "setup_type" in row for row in trades
        )

        for trade in trades:
            # If trade CSV already has setup_type (future), use it directly
            stype = trade.get("setup_type", "")
            if not stype or stype in SKIP_TYPES:
                # Join via timestamp
                stype = nearest_setup(trade.get("open_time", ""), setups)
            trade["_setup_type"] = stype if stype not in SKIP_TYPES else "UNKNOWN"

            all_trades.append(trade)
            by_type[trade["_setup_type"]].append(trade)
            by_date[date_str].append(trade)

    if not all_trades:
        print("No hay trades cerrados en el rango seleccionado.")
        return

    print(f"\n{'='*80}")
    print(f"  SHADOW STATS — {len(dates)} sesiones — {len(all_trades)} trades")
    print(f"{'='*80}")

    # Global
    gs = compute_stats(all_trades)
    print(f"\n  GLOBAL:")
    print_stats("ALL SETUPS", gs)

    # By setup type
    known_order = ["FA_SETUP", "VA80_SETUP", "INSTITUTIONAL_GRADE", "UNKNOWN"]
    ordered_types = [t for t in known_order if t in by_type]
    other_types   = [t for t in by_type if t not in known_order]
    all_types     = ordered_types + sorted(other_types)

    if len(all_types) > 1 or (len(all_types) == 1 and all_types[0] != "UNKNOWN"):
        print(f"\n  POR SETUP TYPE:")
        print(f"  {'':22} {'N':>3}  {'WR':>6}  {'Exp':>7}  {'PnL':>9}  {'AvgW':>8}  {'AvgL':>8}")
        print(f"  {'-'*72}")
        for stype in all_types:
            s = compute_stats(by_type[stype])
            if s:
                print_stats(stype, s)

    # By date
    print(f"\n  POR SESION:")
    print(f"  {'Date':<12} {'N':>3}  {'WR':>6}  {'Exp':>7}  {'PnL':>9}  Setup types activos")
    print(f"  {'-'*70}")
    for date_str in sorted(by_date.keys()):
        trades_day = by_date[date_str]
        s = compute_stats(trades_day)
        if not s:
            continue
        types_seen = sorted({t["_setup_type"] for t in trades_day
                             if t["_setup_type"] not in ("UNKNOWN", "")})
        types_str  = ", ".join(types_seen) if types_seen else "—"
        print(f"  {date_str:<12} {s['n']:>3}  {s['wr']:>5.1f}%  "
              f"{s['exp']:>+6.2f}  {s['pnl']:>+8.2f}  {types_str}")

    print(f"\n{'='*80}\n")


if __name__ == "__main__":
    main()
