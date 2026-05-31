"""
scripts/dry_run_final_proyecciones.py
Dry Run Final — Proyecciones Simuladas de Mejoras — GIBBZ #MESM6

Proposito:
Proyectar el impacto de 4 mejoras propuestas sin ejecutar backtest real
ni modificar codigo existente. Las proyecciones usan los datos confirmados
(PF=2.91, 32 trades, 43 sesiones, Exp=+6.70) y estimaciones conservadoras
basadas en evidencia forense acumulada.

Metodo:
  - No se modifica ningun archivo de produccion
  - Los porcentajes de cambio son estimaciones basadas en la estructura
    del dataset y el comportamiento del filtro/router conocidos
  - Las proyecciones son CONSERVADORAS (se asume degradacion por ruido
    al agregar volumen de trades)

Salida:
  - Reporte por consola con tabla comparativa
  - Archivo reports/dry_run_proyecciones.md con analisis completo

USO:
    python scripts/dry_run_final_proyecciones.py
"""

import sys
import os
from dataclasses import dataclass
from pathlib import Path
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

REPORTS_DIR = Path(__file__).parent.parent / "reports"


# ── Datos confirmados por backtest ─────────────────────────────────────────

@dataclass(frozen=True)
class Baseline:
    pf:                   float = 2.91
    exp_pts:              float = 6.70
    max_dd:               float = 12.00
    wr:                   float = 0.531
    n_trades:             int   = 32
    n_sessions:           int   = 43
    sessions_with_trades: int   = 6
    pnl_pts:              float = 214.25


# ── Escenarios de mejora ───────────────────────────────────────────────────

@dataclass(frozen=True)
class Mejora:
    nombre:           str
    descripcion:      str
    # Cambios porcentuales MARGINALES (aplicados incrementalmente)
    delta_trades_pct: float  # +% sobre el estado previo
    delta_pf_pct:     float  # +% sobre el estado previo (puede ser negativo)
    delta_dd_pct:     float  # +% sobre el estado previo (puede ser negativo si mejora)
    delta_wr_pct:     float  # +% sobre el estado previo


MEJORAS = [
    Mejora(
        nombre="Mejora 1: Reducir thresholds ContextFilter",
        descripcion=(
            "VOL_RELEASE filtro principal conservado. "
            "Destructive regime + kill switch relajados: "
            "WR threshold 25%→20%, DD threshold 30→40pts. "
            "Permite mas trades en sesiones borderline."
        ),
        delta_trades_pct=+31.0,   # 32 → ~42 trades
        delta_pf_pct    =-7.0,    # 2.91 → ~2.71 (mas ruido, menos precision)
        delta_dd_pct    =+25.0,   # 12 → ~15 pts (mas trades = mas DD)
        delta_wr_pct    =-5.0,    # 53.1% → ~50.4%
    ),
    Mejora(
        nombre="Mejora 2: Agregar 2 setups (Pullback + Breakout)",
        descripcion=(
            "Pullback to VA boundary + Breakout ORB 10min. "
            "Ambos ya tienen detectores parciales en el router. "
            "Setups adicionales introducen varianza pero aumentan "
            "cobertura de senales en sesiones elegibles."
        ),
        delta_trades_pct=+38.0,   # ~42 → ~58 trades
        delta_pf_pct    =-6.0,    # ~2.71 → ~2.55
        delta_dd_pct    =+20.0,   # ~15 → ~18 pts
        delta_wr_pct    =-4.0,    # ~50.4% → ~48.4%
    ),
    Mejora(
        nombre="Mejora 3: Expandir session types (WATCH_LATE + LATE_EXPANSION)",
        descripcion=(
            "WATCH sessions actuales generan PF=4.15 en 2 de 10 sesiones. "
            "Las 8 WATCH sin trades podrian activarse si se relajan los "
            "criterios del session_classifier. Riesgo: senales de menor calidad."
        ),
        delta_trades_pct=+15.0,   # ~58 → ~67 trades
        delta_pf_pct    =-4.0,    # ~2.55 → ~2.45
        delta_dd_pct    =+12.0,   # ~18 → ~20.2 pts
        delta_wr_pct    =-2.0,    # ~48.4% → ~47.4%
    ),
    Mejora(
        nombre="Mejora 4: Expandir horario (09:30-11:30 ET → 09:30-13:00 ET)",
        descripcion=(
            "Agregar franja 11:30-13:00 ET (NY_POWER_HOUR). "
            "Franja mediodia 13:00-15:00 ya esta FILTRADA por ContextFilter. "
            "La extension moderada al mediodia temprano agrega algunas senales "
            "pero esta cerca de la franja destructiva."
        ),
        delta_trades_pct=+7.0,    # ~67 → ~72 trades
        delta_pf_pct    =-2.0,    # ~2.45 → ~2.40
        delta_dd_pct    =+9.0,    # ~20.2 → ~22.0 pts
        delta_wr_pct    =-1.0,    # ~47.4% → ~46.9%
    ),
]


# ── Calculo de escenarios combinados ──────────────────────────────────────

def apply_mejoras(baseline: Baseline, n_mejoras: int) -> dict:
    """Aplica las primeras n_mejoras de MEJORAS de forma acumulativa."""
    trades  = float(baseline.n_trades)
    pf      = baseline.pf
    dd      = baseline.max_dd
    wr      = baseline.wr

    for m in MEJORAS[:n_mejoras]:
        trades *= 1.0 + m.delta_trades_pct / 100.0
        pf     *= 1.0 + m.delta_pf_pct     / 100.0
        dd     *= 1.0 + m.delta_dd_pct     / 100.0
        wr     *= 1.0 + m.delta_wr_pct     / 100.0

    # Expectancy como funcion del PF (aprox: conservar la proporcion ganancias)
    exp = baseline.exp_pts * (pf / baseline.pf)

    return {
        "n_mejoras":          n_mejoras,
        "trades":             round(trades, 1),
        "trades_per_session": round(trades / baseline.n_sessions, 2),
        "pf":                 round(pf, 2),
        "exp":                round(exp, 2),
        "max_dd":             round(dd, 2),
        "wr":                 round(wr, 3),
        "pnl":                round(trades * exp, 1),
        # Dinero por sesion (MES micro = $5/pto, ES full = $50/pto)
        "money_micro":        round((trades / baseline.n_sessions) * exp * 5, 2),
        "money_full":         round((trades / baseline.n_sessions) * exp * 50, 2),
        # Efficiency score = trades × PF (proxy de calidad × volumen)
        "efficiency":         round(trades * pf, 1),
    }


# ── Criterios de evaluacion ────────────────────────────────────────────────

def evaluate(m: dict) -> dict:
    pf_ok     = m["pf"]                 >= 2.50
    dd_ok     = m["max_dd"]             <= 20.0
    trades_ok = m["trades_per_session"] >= 1.00
    wr_ok     = m["wr"]                 >= 0.45
    all_ok    = pf_ok and dd_ok and trades_ok and wr_ok
    marginal  = m["pf"] >= 2.30 and dd_ok

    return {
        "pf_ok":     pf_ok,
        "dd_ok":     dd_ok,
        "trades_ok": trades_ok,
        "wr_ok":     wr_ok,
        "all_ok":    all_ok,
        "marginal":  marginal,
        "verdict": (
            "IMPLEMENTAR"     if all_ok   else
            "MARGINAL"        if marginal else
            "NO_IMPLEMENTAR"
        ),
    }


def icon(ok: bool) -> str:
    return "OK  " if ok else "FAIL"


def _pf(pf): return f"{pf:.2f}" if pf != float("inf") else "inf"
def _sep(n=72): print("  " + "-" * n)


# ── Impresion por consola ──────────────────────────────────────────────────

def print_report(baseline: Baseline) -> None:
    scenarios = {
        "BASELINE":    {"n_mejoras": 0, **{k: v for k, v in [
            ("trades",          float(baseline.n_trades)),
            ("trades_per_session", round(baseline.n_trades / baseline.n_sessions, 2)),
            ("pf",              baseline.pf),
            ("exp",             baseline.exp_pts),
            ("max_dd",          baseline.max_dd),
            ("wr",              baseline.wr),
            ("pnl",             baseline.pnl_pts),
            ("money_micro",     round(baseline.n_trades / baseline.n_sessions * baseline.exp_pts * 5, 2)),
            ("money_full",      round(baseline.n_trades / baseline.n_sessions * baseline.exp_pts * 50, 2)),
            ("efficiency",      round(baseline.n_trades * baseline.pf, 1)),
        ]}},
    }
    for i in range(1, 5):
        m = apply_mejoras(baseline, i)
        key = "M1" if i == 1 else ("M1+2" if i == 2 else ("M1+2+3" if i == 3 else "TODAS"))
        scenarios[key] = m

    print()
    print("=" * 72)
    print("  GIBBZ Dry Run Final — Proyecciones Simuladas")
    print("  (Sin modificar codigo. Sin ejecutar backtest.)")
    print("=" * 72)

    # ── Tabla comparativa ─────────────────────────────────────────────────
    print()
    print("  PROYECCIONES COMPARATIVAS")
    _sep()
    header = f"  {'Metrica':<22}  {'BASELINE':>10}  {'M1':>8}  {'M1+2':>8}  {'M1+2+3':>8}  {'TODAS':>8}"
    print(header)
    _sep()

    def row(label, key, fmt=".2f", suffix=""):
        vals = [f"{scenarios[s][key]:{fmt}}{suffix}" for s in scenarios]
        print(f"  {label:<22}  {vals[0]:>10}  {vals[1]:>8}  {vals[2]:>8}  {vals[3]:>8}  {vals[4]:>8}")

    row("Trades totales",    "trades",          ".1f")
    row("Trades/sesion",     "trades_per_session",".2f")
    row("Profit Factor",     "pf",              ".2f")
    row("Expectancy (pts)",  "exp",             "+.2f")
    row("MaxDD (pts)",       "max_dd",          ".2f")
    row("Win Rate",          "wr",              ".1%")
    row("PnL total (pts)",   "pnl",             "+.1f")
    row("$/sesion (micro)",  "money_micro",     "+.2f",  " USD")
    row("$/sesion (full)",   "money_full",      "+.2f",  " USD")
    row("Efficiency Score",  "efficiency",      ".1f")

    # ── Evaluacion por criterios ───────────────────────────────────────────
    print()
    print("  CRITERIOS DE ACEPTACION (PF>=2.5  MaxDD<=20  Trades/s>=1.0  WR>=45%)")
    _sep()
    print(f"  {'Criterio':<22}  {'BASELINE':>10}  {'M1':>8}  {'M1+2':>8}  {'M1+2+3':>8}  {'TODAS':>8}")
    _sep()

    evals = {k: evaluate(v) for k, v in scenarios.items()}

    def crit_row(label, key):
        vals = [f"[{icon(evals[s][key])}]" for s in scenarios]
        print(f"  {label:<22}  {vals[0]:>10}  {vals[1]:>8}  {vals[2]:>8}  {vals[3]:>8}  {vals[4]:>8}")

    crit_row("PF >= 2.50",          "pf_ok")
    crit_row("MaxDD <= 20 pts",      "dd_ok")
    crit_row("Trades/sesion >= 1.0", "trades_ok")
    crit_row("Win Rate >= 45%",      "wr_ok")

    print()
    verdicts = [evals[s]["verdict"] for s in scenarios]
    print(f"  VEREDICTO: " + "  ".join(
        f"{s}={v}" for s, v in zip(scenarios.keys(), verdicts)
    ))

    # ── Analisis de cada mejora incremental ───────────────────────────────
    print()
    print("  ANALISIS INCREMENTAL")
    _sep()
    prev_money_micro = scenarios["BASELINE"]["money_micro"]
    for i, m_obj in enumerate(MEJORAS, 1):
        key = ["M1", "M1+2", "M1+2+3", "TODAS"][i - 1]
        m   = scenarios[key]
        ev  = evals[key]
        delta_money = m["money_micro"] - prev_money_micro
        print(f"  [{i}] {m_obj.nombre[:50]}")
        print(f"      Trades: {scenarios[['BASELINE','M1','M1+2','M1+2+3','TODAS'][i-1]]['trades']:.0f}"
              f" → {m['trades']:.0f} (+{m_obj.delta_trades_pct:.0f}%)  "
              f"PF: {scenarios[['BASELINE','M1','M1+2','M1+2+3','TODAS'][i-1]]['pf']:.2f}"
              f" → {m['pf']:.2f} ({m_obj.delta_pf_pct:+.0f}%)  "
              f"$/sesion: {prev_money_micro:+.2f} → {m['money_micro']:+.2f} "
              f"(delta: {delta_money:+.2f})")
        print(f"      Veredicto: [{ev['verdict']}]  "
              f"Todos criterios: {'[OK]' if ev['all_ok'] else '[FAIL]'}")
        prev_money_micro = m["money_micro"]
        print()

    # ── Veredicto final ───────────────────────────────────────────────────
    print("=" * 72)
    print("  VEREDICTO FINAL")
    _sep()

    m12  = scenarios["M1+2"]
    ev12 = evals["M1+2"]
    m_base = scenarios["BASELINE"]

    delta_money_micro = m12["money_micro"] - m_base["money_micro"]
    delta_money_full  = m12["money_full"]  - m_base["money_full"]
    delta_pct         = round((m12["money_micro"] / m_base["money_micro"] - 1) * 100, 1)

    print()
    if ev12["all_ok"]:
        print("  RECOMENDACION: IMPLEMENTAR MEJORA 1 + 2 ANTES DE PAPER TRADING")
        print()
        print(f"  Por que:")
        print(f"    PF: {m_base['pf']:.2f} -> {m12['pf']:.2f}  (degradacion controlada, >{2.5:.1f} = aceptable)")
        print(f"    MaxDD: {m_base['max_dd']:.2f} -> {m12['max_dd']:.2f} pts  (dentro de limite 20 pts)")
        print(f"    Trades/sesion: {m_base['trades_per_session']:.2f} -> {m12['trades_per_session']:.2f}  (+{((m12['trades_per_session']/m_base['trades_per_session'])-1)*100:.0f}%)")
        print(f"    $/sesion micro:  {m_base['money_micro']:+.2f} -> {m12['money_micro']:+.2f} USD "
              f"({delta_pct:+.1f}%,  delta: {delta_money_micro:+.2f})")
        print(f"    $/sesion full:   {m_base['money_full']:+.2f} -> {m12['money_full']:+.2f} USD "
              f"(delta: {delta_money_full:+.2f})")
        print(f"    Efficiency Score: {m_base['efficiency']:.1f} -> {m12['efficiency']:.1f} "
              f"(+{((m12['efficiency']/m_base['efficiency'])-1)*100:.0f}%)")
        print()
        print("  Mejoras 3 y 4 (session types + horarios): NO implementar aun.")
        print(f"  PF proyectado con todas: {scenarios['TODAS']['pf']:.2f} (<2.5, criterio falla)")
        print(f"  MaxDD con todas: {scenarios['TODAS']['max_dd']:.2f} pts (>20, criterio falla)")
        print()
        print("  HOJA DE RUTA:")
        print("    [1] Implementar Mejora 1 (relajar 2 filtros en context_filter.py) — 2-4 hrs")
        print("    [2] Implementar Mejora 2 (Pullback + Breakout en gibbz_setup_router.py) — 15-20 hrs")
        print("    [3] Backtest de validacion con 43 sesiones (scripts/run_backtest_with_filter.py)")
        print("    [4] Criterio: PF >=2.5, MaxDD <=20 pts — si pasa -> paper trading")
        print("    [5] Mejoras 3+4: evaluar despues de 2 semanas paper trading exitoso")
    else:
        print("  RECOMENDACION: PROCEDER A PAPER TRADING SIN MODIFICACIONES")
        print()
        print(f"  Por que: Mejora 1+2 no cumple todos los criterios.")
        print(f"  Veredicto M1+2: {ev12['verdict']}")
        print(f"  El edge actual (PF={m_base['pf']:.2f}, Exp=+{m_base['exp_pts']:.2f}) es solido.")
        print("  Modificar sin validacion empirica introduce riesgo innecesario.")

    print()
    print("  ADVERTENCIA CRITICA:")
    print("  Estas son PROYECCIONES SIMULADAS, no resultados de backtest real.")
    print("  Los porcentajes de cambio son estimaciones conservadoras.")
    print("  Implementar Mejora 1+2 REQUIERE backtest de validacion con 43 sesiones")
    print("  antes de usar en paper trading (scripts/run_backtest_with_filter.py).")
    print()
    print("=" * 72)


# ── Guardar reporte en markdown ────────────────────────────────────────────

def save_markdown_report(baseline: Baseline) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filepath = REPORTS_DIR / "dry_run_proyecciones.md"

    b0 = {
        "trades": float(baseline.n_trades),
        "trades_per_session": round(baseline.n_trades / baseline.n_sessions, 2),
        "pf": baseline.pf, "exp": baseline.exp_pts,
        "max_dd": baseline.max_dd, "wr": baseline.wr, "pnl": baseline.pnl_pts,
        "money_micro": round(baseline.n_trades / baseline.n_sessions * baseline.exp_pts * 5, 2),
        "money_full":  round(baseline.n_trades / baseline.n_sessions * baseline.exp_pts * 50, 2),
        "efficiency":  round(baseline.n_trades * baseline.pf, 1),
    }
    m1   = apply_mejoras(baseline, 1)
    m12  = apply_mejoras(baseline, 2)
    m123 = apply_mejoras(baseline, 3)
    mall = apply_mejoras(baseline, 4)
    ev12 = evaluate(m12)
    ev1  = evaluate(m1)

    def ok(v): return "SI" if v else "NO"
    def chk(v): return "OK" if v else "FAIL"

    lines = [
        f"# GIBBZ Dry Run Final — Proyecciones Simuladas",
        f"",
        f"**Fecha:** {datetime.now().strftime('%Y-%m-%d %H:%M')}  ",
        f"**Tipo:** Simulacion matematica (sin backtest real, sin modificacion de codigo)",
        f"",
        f"---",
        f"",
        f"## Baseline Confirmado",
        f"",
        f"| Metrica | Valor |",
        f"|---|---|",
        f"| Profit Factor | {b0['pf']:.2f} |",
        f"| Expectancy | +{b0['exp']:.2f} pts/trade |",
        f"| Max Drawdown | {b0['max_dd']:.2f} pts |",
        f"| Win Rate | {b0['wr']:.1%} |",
        f"| Trades totales | {int(b0['trades'])} en {baseline.n_sessions} sesiones |",
        f"| Trades/sesion | {b0['trades_per_session']:.2f} |",
        f"| Sesiones con trades | {baseline.sessions_with_trades} de {baseline.n_sessions} (14%) |",
        f"| $/sesion (micro MES) | +{b0['money_micro']:.2f} USD |",
        f"| $/sesion (full ES) | +{b0['money_full']:.2f} USD |",
        f"",
        f"**Problema principal:** Edge muy concentrado — 0.74 trades/sesion, solo 14% de sesiones activas.",
        f"",
        f"---",
        f"",
        f"## Mejoras Evaluadas",
        f"",
        f"| # | Mejora | Trades +% | PF delta | DD delta |",
        f"|---|---|---|---|---|",
    ]
    for i, m in enumerate(MEJORAS, 1):
        lines.append(f"| {i} | {m.nombre} | +{m.delta_trades_pct:.0f}% | {m.delta_pf_pct:+.0f}% | {m.delta_dd_pct:+.0f}% |")

    lines += [
        f"",
        f"---",
        f"",
        f"## Proyecciones Comparativas",
        f"",
        f"| Metrica | Baseline | M1 | M1+2 | M1+2+3 | TODAS |",
        f"|---|---|---|---|---|---|",
        f"| Trades/sesion | {b0['trades_per_session']:.2f} | {m1['trades_per_session']:.2f} | {m12['trades_per_session']:.2f} | {m123['trades_per_session']:.2f} | {mall['trades_per_session']:.2f} |",
        f"| Profit Factor | {b0['pf']:.2f} | {m1['pf']:.2f} | {m12['pf']:.2f} | {m123['pf']:.2f} | {mall['pf']:.2f} |",
        f"| Max Drawdown (pts) | {b0['max_dd']:.2f} | {m1['max_dd']:.2f} | {m12['max_dd']:.2f} | {m123['max_dd']:.2f} | {mall['max_dd']:.2f} |",
        f"| Win Rate | {b0['wr']:.1%} | {m1['wr']:.1%} | {m12['wr']:.1%} | {m123['wr']:.1%} | {mall['wr']:.1%} |",
        f"| $/sesion micro | +{b0['money_micro']:.2f} | +{m1['money_micro']:.2f} | +{m12['money_micro']:.2f} | +{m123['money_micro']:.2f} | +{mall['money_micro']:.2f} |",
        f"| $/sesion full | +{b0['money_full']:.2f} | +{m1['money_full']:.2f} | +{m12['money_full']:.2f} | +{m123['money_full']:.2f} | +{mall['money_full']:.2f} |",
        f"| Efficiency Score | {b0['efficiency']:.1f} | {m1['efficiency']:.1f} | {m12['efficiency']:.1f} | {m123['efficiency']:.1f} | {mall['efficiency']:.1f} |",
        f"",
        f"## Criterios de Aceptacion",
        f"",
        f"| Criterio | Umbral | M1 | M1+2 | TODAS |",
        f"|---|---|---|---|---|",
        f"| PF | >=2.50 | {chk(evaluate(m1)['pf_ok'])} | {chk(ev12['pf_ok'])} | {chk(evaluate(mall)['pf_ok'])} |",
        f"| MaxDD | <=20 pts | {chk(evaluate(m1)['dd_ok'])} | {chk(ev12['dd_ok'])} | {chk(evaluate(mall)['dd_ok'])} |",
        f"| Trades/sesion | >=1.0 | {chk(evaluate(m1)['trades_ok'])} | {chk(ev12['trades_ok'])} | {chk(evaluate(mall)['trades_ok'])} |",
        f"| Win Rate | >=45% | {chk(evaluate(m1)['wr_ok'])} | {chk(ev12['wr_ok'])} | {chk(evaluate(mall)['wr_ok'])} |",
        f"| **VEREDICTO** | | **{evaluate(m1)['verdict']}** | **{ev12['verdict']}** | **{evaluate(mall)['verdict']}** |",
        f"",
        f"---",
        f"",
        f"## Veredicto Final",
        f"",
    ]

    delta_money_pct = round((m12['money_micro'] / b0['money_micro'] - 1) * 100, 1)
    if ev12["all_ok"]:
        lines += [
            f"**RECOMENDACION: IMPLEMENTAR MEJORA 1 + 2 ANTES DE PAPER TRADING**",
            f"",
            f"- PF proyectado: {m12['pf']:.2f} (>=2.5, aceptable)",
            f"- MaxDD proyectado: {m12['max_dd']:.2f} pts (<=20, controlado)",
            f"- Trades/sesion: {m12['trades_per_session']:.2f} (+{((m12['trades_per_session']/b0['trades_per_session'])-1)*100:.0f}%)",
            f"- $/sesion micro: +{b0['money_micro']:.2f} -> +{m12['money_micro']:.2f} ({delta_money_pct:+.1f}%)",
            f"- $/sesion full:  +{b0['money_full']:.2f} -> +{m12['money_full']:.2f}",
            f"",
            f"**Mejoras 3 y 4: NO implementar ahora**",
            f"- PF con todas: {mall['pf']:.2f} (<2.5, criterio falla)",
            f"- MaxDD con todas: {mall['max_dd']:.2f} pts (>20, criterio falla)",
            f"",
            f"**Hoja de ruta:**",
            f"1. Implementar Mejora 1 (context_filter.py: relajar 2 filtros) — 2-4 hrs",
            f"2. Implementar Mejora 2 (gibbz_setup_router.py: Pullback + Breakout) — 15-20 hrs",
            f"3. Backtest de validacion con 43 sesiones — 2-4 hrs",
            f"4. Si PF>=2.5 y MaxDD<=20 -> Paper Trading",
            f"5. Mejoras 3+4: evaluar despues de paper trading exitoso",
        ]
    else:
        lines += [
            f"**RECOMENDACION: PROCEDER A PAPER TRADING SIN MODIFICACIONES**",
            f"",
            f"Mejora 1+2 no cumple todos los criterios. El edge actual es solido.",
        ]

    lines += [
        f"",
        f"---",
        f"",
        f"*Proyecciones simuladas. No constituyen resultados de backtest real.*  ",
        f"*Implementar requiere backtest de validacion con scripts/run_backtest_with_filter.py*",
    ]

    filepath.write_text("\n".join(lines), encoding="utf-8")
    return filepath


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    baseline = Baseline()
    print_report(baseline)
    md_path = save_markdown_report(baseline)
    print(f"  Reporte guardado: {md_path}")
    print()


if __name__ == "__main__":
    main()
