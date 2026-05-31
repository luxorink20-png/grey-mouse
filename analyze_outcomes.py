"""
GIBBZ V3 — Outcome Cross-Session Analyzer
Analiza todos los JSONs en outcomes/ y genera reporte estadístico.

USO:
  python analyze_outcomes.py
"""

import json
import os
import glob

files = sorted(glob.glob('outcomes/*_observation.json'))
print(f'\nSessions encontradas: {len(files)}')

if not files:
    print('No hay archivos en outcomes/ — corre los replays con --save-outcomes primero.')
    exit()

print()
hdr = f"{'DATE':<12} {'BARS':>5} {'ET%':>6} {'A':>3} {'GTAL_V':>7} {'MICRO_OR':>9} {'STARV':>7} {'EDGE':>7} {'REG_Q':>7}"
print(hdr)
print('-' * 75)

total_et = total_a = total_gtal = total_micro = total_bars = 0
sessions = []

for f in files:
    d = json.load(open(f, encoding='utf-8'))
    date   = d['session_date']
    bars   = d['total_bars']
    et     = d['efficient_trend_bars']
    a_s    = d['a_setup_bars']
    gv     = d['gtal_valid_bars']
    mo     = d['micro_or_bars']
    starv  = d['signal_starvation_score']
    edge   = d['edge_opportunity_score']
    regq   = d['regime_quality_score']
    et_pct = round(et / max(bars, 1) * 100, 1)

    total_et    += et
    total_a     += a_s
    total_gtal  += gv
    total_micro += mo
    total_bars  += bars
    sessions.append(d)

    marker = ' <- ET!' if et > 0 else ''
    print(f"{date:<12} {bars:>5} {et_pct:>5.1f}% {a_s:>3} {gv:>7} {mo:>9} "
          f"{starv:>5.0f}/100 {edge:>5.1f}/100 {regq:>5.1f}/100{marker}")

print('-' * 75)
et_pct_total = round(total_et / max(total_bars, 1) * 100, 1)
print(f"{'TOTAL':<12} {total_bars:>5} {et_pct_total:>5.1f}% "
      f"{total_a:>3} {total_gtal:>7} {total_micro:>9}")

# ── STARVATION BREAKDOWN ─────────────────────────────────────────
print()
print('=' * 75)
print('STARVATION BREAKDOWN (por qué no ejecutaron setups detectados):')
print('-' * 75)
total_conf = sum(d['conf_block_count']      for d in sessions)
total_cont = sum(d['cont_block_count']      for d in sessions)
total_hb   = sum(d['hb_block_count']        for d in sessions)
total_val  = sum(d['validator_block_count'] for d in sessions)
total_rej  = sum(d['gtal_rejection_count']  for d in sessions)
total_or   = sum(d['over_rejection_bars']   for d in sessions)

print(f"  conf_block      (conf < 65 con GTAL=VALID):  {total_conf}")
print(f"  cont_block      (cont < 72):                  {total_cont}")
print(f"  hb_block        (HB=True):                    {total_hb}")
print(f"  validator_block (score bajo):                 {total_val}")
print(f"  gtal_rejection  (OPP!=NONE pero INVALID):     {total_rej}")
print(f"  over_rejection  (filter=OVER_REJECTION bars): {total_or}")

# ── REGIME DISTRIBUTION CONSOLIDADA ──────────────────────────────
print()
print('=' * 75)
print('REGIME DISTRIBUTION CONSOLIDADA:')
print('-' * 75)
regime_total = {}
for d in sessions:
    for regime, count in d['regime_distribution'].items():
        regime_total[regime] = regime_total.get(regime, 0) + count

for regime, count in sorted(regime_total.items(), key=lambda x: -x[1]):
    pct = round(count / max(total_bars, 1) * 100, 1)
    bar_vis = '█' * int(pct / 2)
    print(f"  {regime:<20} {count:>5} bars  ({pct:>5.1f}%)  {bar_vis}")

# ── SCORES PROMEDIO ───────────────────────────────────────────────
print()
print('=' * 75)
print('SCORES PROMEDIO DEL DATASET:')
print('-' * 75)
avg_starv = sum(d['signal_starvation_score'] for d in sessions) / len(sessions)
avg_edge  = sum(d['edge_opportunity_score']  for d in sessions) / len(sessions)
avg_regq  = sum(d['regime_quality_score']    for d in sessions) / len(sessions)
avg_ets   = sum(d['avg_ets']                 for d in sessions) / len(sessions)
avg_conf  = sum(d['avg_conf']                for d in sessions) / len(sessions)
max_ets   = max(d['max_ets']                 for d in sessions)
max_conf  = max(d['max_conf']                for d in sessions)
max_rt    = max(d['max_rt']                  for d in sessions)

print(f"  signal_starvation_score  avg: {avg_starv:.1f}/100  (alto = escasez severa)")
print(f"  edge_opportunity_score   avg: {avg_edge:.1f}/100  (alto = oportunidad real)")
print(f"  regime_quality_score     avg: {avg_regq:.1f}/100  (alto = régimen favorable)")
print(f"  ETS  avg: {avg_ets:.1f}   max dataset: {max_ets}")
print(f"  conf avg: {avg_conf:.1f}   max dataset: {max_conf}")
print(f"  RT   max dataset: {max_rt}")

# ── NOTABLE EVENTS ────────────────────────────────────────────────
print()
print('=' * 75)
print('NOTABLE EVENTS (ETS>=65 o GTAL=VALID o MICRO_OR):')
print('-' * 75)
notable_count = 0
for d in sessions:
    for nb in d.get('notable_bars', []):
        if nb['ets'] >= 65 or nb['ev'] == 'VALID' or nb['micro_or']:
            notable_count += 1
            micro_tag = ' [MICRO_OR]' if nb['micro_or'] else ''
            print(f"  {d['session_date']} Bar{nb['bar']:4d} | "
                  f"{nb['env']:<16} "
                  f"ETS={nb['ets']:3d} conf={nb['conf']:3d} "
                  f"opp={nb['opp']} ev={nb['ev']:<8}"
                  f"{micro_tag} -> {nb['block_reason']}")

if notable_count == 0:
    print('  (ninguno — ETS nunca superó 65 en todo el dataset)')

# ── DIAGNÓSTICO INSTITUCIONAL ─────────────────────────────────────
print()
print('=' * 75)
print('DIAGNOSTICO INSTITUCIONAL:')
print('-' * 75)

et_rate = total_et / max(total_bars, 1)
a_rate  = total_a  / max(total_bars, 1)
gv_rate = total_gtal / max(total_bars, 1)

if et_rate < 0.02:
    print('  [CRITICAL] EFFICIENT_TREND < 2% del tiempo — edge RTH extremadamente raro')
elif et_rate < 0.05:
    print('  [WARNING]  EFFICIENT_TREND < 5% del tiempo — edge RTH poco frecuente')
else:
    print('  [OK]       EFFICIENT_TREND presente en cantidad aceptable')

if total_a == 0:
    print('  [CRITICAL] 0 A-setups en todo el dataset — sin oportunidades institucionales')
elif total_a < 5:
    print(f'  [WARNING]  Solo {total_a} A-setups en {len(sessions)} sesiones — muy escaso')
else:
    print(f'  [OK]       {total_a} A-setups detectados')

if total_gtal == 0:
    print('  [INFO]     GTAL_VALID = 0 — ninguna señal superó todos los filtros')
else:
    print(f'  [OK]       {total_gtal} barras GTAL=VALID detectadas')

if total_hb > total_rej * 0.5:
    print(f'  [INFO]     HB={total_hb} domina rechazos — el mercado produce muchos spikes')

if avg_starv > 70:
    print(f'  [CONCLUSION] Signal starvation SEVERA ({avg_starv:.0f}/100)')
    print('               El sistema es correcto pero el dataset no tiene edge RTH.')
    print('               El edge vive en overnight / expansion sessions.')
elif avg_starv > 50:
    print(f'  [CONCLUSION] Signal starvation MODERADA ({avg_starv:.0f}/100)')
    print('               Hay oportunidades ocasionales pero infrecuentes.')
else:
    print(f'  [CONCLUSION] Signal starvation BAJA ({avg_starv:.0f}/100)')
    print('               El dataset tiene condiciones favorables.')

print()
print(f'  Dataset total: {total_bars} barras / {len(sessions)} sesiones')
print(f'  Bars per session avg: {total_bars // len(sessions)}')
print()