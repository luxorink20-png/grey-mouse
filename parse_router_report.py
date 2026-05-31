"""parse_router_report.py — analiza salida de replay_debug_v3 y reporta señales del router"""
import re, sys
from collections import defaultdict

def parse(path, label):
    bar_re   = re.compile(r"Bar\s+(\d+)\s+\|\s+P=\s*([\d.]+)")
    setup_re = re.compile(r"\[SETUP:([\w_]+)\s+(LONG|SHORT|NEUTRAL)\s+conf=(\d+)\s+stp=([\d.]+)\s+tgt=([\d.]+)\]")
    total_bars_re = re.compile(r"Bars:\s*(\d+)")

    counts     = defaultdict(int)
    signals    = []
    cur_bar    = None
    cur_price  = None
    total_bars = 0

    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            tm = total_bars_re.search(line)
            if tm:
                total_bars = int(tm.group(1))
            bm = bar_re.search(line)
            if bm:
                cur_bar   = int(bm.group(1))
                cur_price = float(bm.group(2))
            sm = setup_re.search(line)
            if sm and cur_bar is not None:
                stype = sm.group(1)
                sdir  = sm.group(2)
                sconf = int(sm.group(3))
                sstp  = float(sm.group(4))
                stgt  = float(sm.group(5))
                counts[stype] += 1
                signals.append((cur_bar, cur_price, stype, sdir, sconf, sstp, stgt))

    no_setup_bars = total_bars - len(signals)

    print(f"\n{'='*72}")
    print(f"  {label}")
    print(f"  Total barras: {total_bars}  |  Con señal: {len(signals)}  |  NO_SETUP: {no_setup_bars}")
    print(f"{'='*72}")

    priority = ["INSTITUTIONAL_GRADE","ORB_SETUP","FA_SETUP","VA80_SETUP",
                "VWAP_SETUP","GAP_SETUP","POC_SETUP","BOUNCE_SETUP"]
    print("\n  CONTEO POR SETUP TYPE:")
    for p in priority:
        n = counts.get(p, 0)
        if n:
            pct = 100*n/total_bars if total_bars else 0
            bar = "#" * int(pct / 2)
            print(f"    {p:<22} {n:4d} bars  {pct:5.1f}%  {bar}")
    if no_setup_bars > 0:
        pct = 100*no_setup_bars/total_bars if total_bars else 0
        bar = "." * int(pct / 2)
        print(f"    {'NO_SETUP':<22} {no_setup_bars:4d} bars  {pct:5.1f}%  {bar}")

    ig = [s for s in signals if s[2] == "INSTITUTIONAL_GRADE"]
    print(f"\n  INSTITUTIONAL_GRADE: {'SI — ' + str(len(ig)) + ' barras' if ig else 'NO aparecio'}")
    if ig:
        for b,p,st,sd,sc,ss,st2 in ig[:10]:
            print(f"    Bar {b:4d}  P={p:.2f}  {sd}  conf={sc}  stp={ss:.1f}  tgt={st2:.1f}")

    top5 = sorted(signals, key=lambda x: -x[4])[:5]
    print(f"\n  TOP-5 POR CONFIANZA:")
    for b,p,st,sd,sc,ss,st2 in top5:
        print(f"    Bar {b:4d}  P={p:.2f}  {st:<22} {sd:<5}  conf={sc}  stp={ss:.1f}  tgt={st2:.1f}")

    prev_type = None
    transitions = []
    for b,p,st,sd,sc,ss,st2 in signals:
        if st != prev_type:
            transitions.append((b,p,st,sd,sc))
            prev_type = st
    print(f"\n  TRANSICIONES DE SETUP (primeras 12):")
    for b,p,st,sd,sc in transitions[:12]:
        print(f"    Bar {b:4d}  P={p:.2f}  -> {st} {sd}  conf={sc}")

if __name__ == "__main__":
    path  = sys.argv[1]
    label = sys.argv[2] if len(sys.argv) > 2 else path
    parse(path, label)
