"""
router_backtest.py — backtest bar-a-bar usando señales del setup_router

Lee la salida de replay_debug_v3 (ya generada en tmp_sN.txt) y simula:
  - Entrada: primera barra de cada setup nuevo (transición de tipo)
  - Stop:    entry ± stp_pts (del router)
  - Target:  entry ± min(tgt_pts, TARGET_CAP) (del router)
  - 1 trade activo a la vez
  - Close-to-close: si el close de una barra cruza stop/target → exit

uso: python router_backtest.py tmp_s1.txt "2026-03-11" [tmp_s2.txt "label2" ...]
"""

import re
import sys
from dataclasses import dataclass, field
from collections import defaultdict

TARGET_CAP = 20.0   # pts máximo de target
SKIP_TYPES = {"NO_SETUP", "INSTITUTIONAL_GRADE"}   # no entramos en estos

bar_re   = re.compile(r"Bar\s+(\d+)\s+\|\s+P=\s*([\d.]+)")
setup_re = re.compile(
    r"\[SETUP:([\w_]+)\s+(LONG|SHORT|NEUTRAL)\s+conf=(\d+)"
    r"\s+stp=([\d.]+)\s+tgt=([\d.]+)\]"
)


@dataclass
class BarData:
    bar:   int
    price: float
    stype: str   = "NO_SETUP"
    sdir:  str   = "NEUTRAL"
    sconf: int   = 0
    sstp:  float = 0.0
    stgt:  float = 0.0


@dataclass
class Trade:
    entry_bar:   int
    entry_price: float
    stype:       str
    direction:   str
    stop_level:  float
    tgt_level:   float
    raw_tgt:     float   # router target before cap
    exit_bar:    int   = 0
    exit_price:  float = 0.0
    result:      str   = ""   # WIN / LOSS / OPEN
    pnl:         float = 0.0


def parse_bars(path: str) -> list[BarData]:
    bars: list[BarData] = []
    cur: BarData | None = None
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            bm = bar_re.search(line)
            if bm:
                cur = BarData(bar=int(bm.group(1)), price=float(bm.group(2)))
                bars.append(cur)
            sm = setup_re.search(line)
            if sm and cur is not None and cur.bar == (bars[-1].bar if bars else -1):
                cur.stype = sm.group(1)
                cur.sdir  = sm.group(2)
                cur.sconf = int(sm.group(3))
                cur.sstp  = float(sm.group(4))
                cur.stgt  = float(sm.group(5))
    return bars


def run_backtest(bars: list[BarData], label: str) -> list[Trade]:
    trades: list[Trade] = []
    active: Trade | None = None
    prev_type = "NO_SETUP"

    for i, b in enumerate(bars):
        # ── Chequeo de exit si hay trade activo ───────────────────
        if active is not None:
            if active.direction == "LONG":
                if b.price <= active.stop_level:
                    active.exit_bar   = b.bar
                    active.exit_price = active.stop_level
                    active.pnl        = round(active.stop_level - active.entry_price, 2)
                    active.result     = "LOSS"
                    active = None
                elif b.price >= active.tgt_level:
                    active.exit_bar   = b.bar
                    active.exit_price = active.tgt_level
                    active.pnl        = round(active.tgt_level - active.entry_price, 2)
                    active.result     = "WIN"
                    active = None
            else:  # SHORT
                if b.price >= active.stop_level:
                    active.exit_bar   = b.bar
                    active.exit_price = active.stop_level
                    active.pnl        = round(active.entry_price - active.stop_level, 2)
                    active.result     = "LOSS"
                    active = None
                elif b.price <= active.tgt_level:
                    active.exit_bar   = b.bar
                    active.exit_price = active.tgt_level
                    active.pnl        = round(active.entry_price - active.tgt_level, 2)
                    active.result     = "WIN"
                    active = None

        # ── Chequeo de nueva entrada ───────────────────────────────
        if (active is None
                and b.stype not in SKIP_TYPES
                and b.stype != "NO_SETUP"
                and b.stype != prev_type
                and b.sdir in ("LONG", "SHORT")
                and b.sstp > 0):

            capped_tgt = min(b.stgt, TARGET_CAP)
            if capped_tgt <= 0:
                prev_type = b.stype
                continue

            if b.sdir == "LONG":
                stop_lvl = round(b.price - b.sstp, 2)
                tgt_lvl  = round(b.price + capped_tgt, 2)
            else:
                stop_lvl = round(b.price + b.sstp, 2)
                tgt_lvl  = round(b.price - capped_tgt, 2)

            t = Trade(
                entry_bar   = b.bar,
                entry_price = b.price,
                stype       = b.stype,
                direction   = b.sdir,
                stop_level  = stop_lvl,
                tgt_level   = tgt_lvl,
                raw_tgt     = b.stgt,
            )
            trades.append(t)
            active = t

        prev_type = b.stype

    # Trades que llegan al final sin cerrar
    if active is not None:
        last_price = bars[-1].price if bars else active.entry_price
        active.exit_bar   = bars[-1].bar if bars else active.entry_bar
        active.exit_price = last_price
        if active.direction == "LONG":
            active.pnl = round(last_price - active.entry_price, 2)
        else:
            active.pnl = round(active.entry_price - last_price, 2)
        active.result = "WIN" if active.pnl > 0 else "LOSS"

    return trades


def report(trades: list[Trade], label: str):
    print(f"\n{'='*72}")
    print(f"  BACKTEST: {label}   ({len(trades)} trades)")
    print(f"{'='*72}")

    if not trades:
        print("  Sin trades.\n")
        return

    by_type: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_type[t.stype].append(t)

    total_pnl = sum(t.pnl for t in trades)
    wins      = sum(1 for t in trades if t.result == "WIN")
    wr        = 100 * wins / len(trades)
    avg_win   = sum(t.pnl for t in trades if t.result=="WIN") / max(wins,1)
    losses    = len(trades) - wins
    avg_loss  = sum(t.pnl for t in trades if t.result=="LOSS") / max(losses,1)
    expectancy= round(wr/100 * avg_win + (1-wr/100) * avg_loss, 2)

    print(f"\n  GLOBAL:  trades={len(trades)}  WR={wr:.1f}%  "
          f"Expectancy={expectancy:+.2f}pts  PnL={total_pnl:+.2f}pts")
    print(f"           avg_win={avg_win:+.2f}  avg_loss={avg_loss:+.2f}\n")

    priority = ["ORB_SETUP","FA_SETUP","VA80_SETUP","VWAP_SETUP",
                "GAP_SETUP","POC_SETUP","BOUNCE_SETUP"]
    print(f"  {'SETUP TYPE':<22} {'N':>3}  {'WR':>6}  {'Exp':>7}  {'PnL':>8}  trades")
    print(f"  {'-'*68}")
    for p in priority:
        ts = by_type.get(p, [])
        if not ts:
            continue
        w = sum(1 for t in ts if t.result=="WIN")
        l = len(ts) - w
        wr_t = 100*w/len(ts)
        aw = sum(t.pnl for t in ts if t.result=="WIN") / max(w,1)
        al = sum(t.pnl for t in ts if t.result=="LOSS") / max(l,1)
        ex = round(wr_t/100*aw + (1-wr_t/100)*al, 2)
        pnl_t = round(sum(t.pnl for t in ts), 2)
        detail = "  ".join(
            f"[B{t.entry_bar} P={t.entry_price:.1f} {t.direction} "
            f"stp={t.stop_level:.1f} tgt={t.tgt_level:.1f} "
            f"{'WIN' if t.result=='WIN' else 'LOSS'} {t.pnl:+.2f}]"
            for t in ts
        )
        print(f"  {p:<22} {len(ts):>3}  {wr_t:>5.1f}%  {ex:>+7.2f}  {pnl_t:>+8.2f}  {detail}")

    print(f"\n  DETALLE CRONOLOGICO:")
    print(f"  {'Bar':>5} {'Entry':>8} {'Dir':>5} {'Stype':<22} "
          f"{'Stop':>8} {'Target':>8} {'ExBar':>6} {'ExPx':>8} {'Res':>5} {'PnL':>7}")
    for t in trades:
        cap = "*" if t.raw_tgt > TARGET_CAP else " "
        print(f"  {t.entry_bar:>5} {t.entry_price:>8.2f} {t.direction:>5} {t.stype:<22}"
              f" {t.stop_level:>8.2f} {t.tgt_level:>8.2f}{cap}"
              f" {t.exit_bar:>6} {t.exit_price:>8.2f} {t.result:>5} {t.pnl:>+7.2f}")

    return total_pnl


if __name__ == "__main__":
    args = sys.argv[1:]
    if len(args) % 2 != 0 or not args:
        print("uso: python router_backtest.py file1.txt label1 [file2.txt label2 ...]")
        sys.exit(1)

    pairs = [(args[i], args[i+1]) for i in range(0, len(args), 2)]
    grand_total = 0.0
    all_trades: list[Trade] = []

    for path, label in pairs:
        bars   = parse_bars(path)
        trades = run_backtest(bars, label)
        pnl    = report(trades, label)
        if pnl:
            grand_total += pnl
        all_trades.extend(trades)

    if len(pairs) > 1:
        w = sum(1 for t in all_trades if t.result == "WIN")
        wr = 100*w/len(all_trades) if all_trades else 0
        aw = sum(t.pnl for t in all_trades if t.result=="WIN") / max(w,1)
        ls = len(all_trades)-w
        al = sum(t.pnl for t in all_trades if t.result=="LOSS") / max(ls,1)
        ex = round(wr/100*aw + (1-wr/100)*al, 2)
        print(f"\n{'='*72}")
        print(f"  GLOBAL 3 SESIONES:  trades={len(all_trades)}  WR={wr:.1f}%  "
              f"Expectancy={ex:+.2f}pts  PnL total={grand_total:+.2f}pts")
        print(f"{'='*72}\n")
