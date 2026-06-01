# Dry Run Final — Informe Ejecutivo
**GIBBZ #MESM6 — Validacion Real de Mejoras 1+2**  
Fecha: 2026-05-31  
Autor: GIBBZ Engineering  
Estado: **COMPLETADO — VEREDICTO: DISCARD**

---

## 1. Resumen Ejecutivo

| Item | Detalle |
|------|---------|
| Fecha ejecucion | 2026-05-31 |
| Tipo | Dry Run con backtest REAL (no proyecciones) |
| Sesiones | 43 sesiones historicas (#MESM6) |
| Branches creados | `improvement-1`, `improvement-1-plus-2` |
| Backtests ejecutados | SI — run_backtest_with_filter.py (43 sesiones) |
| Bootstrap ejecutado | SI — 100 runs (improvement-1-plus-2) |
| Merge a master | NO |
| Veredicto final | **DISCARD** |

---

## 2. Estado Baseline (master actual)

Sistema actual CON ContextFilter activo:

| Metrica | Valor |
|---------|-------|
| Profit Factor | **2.91** |
| Max Drawdown | **12.00 pts** |
| Trades totales | **32** |
| Trades/sesion (43) | **0.74** |
| Win Rate | **53.1%** |
| Expectancy | **+6.70 pts/trade** |
| PnL total | +214.25 pts |
| Sesiones elegibles | 19/43 (24 VOL_RELEASE filtradas) |

---

## 3. Mejora 1 — Reducir Thresholds ContextFilter

### 3.1 Implementacion (branch: improvement-1)

Archivos modificados: `context_filter.py`, `scripts/run_backtest_with_filter.py`

Cambios aplicados:
- `_ATR_RATIO_THRESHOLD`: 1.5 → 2.0 (filtro VOL_RELEASE dinamico mas laxo)
- `_VOLUME_RATIO_THRESHOLD`: 2.0 → 2.5 (idem)
- Check de actividad eliminado (de 3 checks a 2 checks en `_check_vol_release_dynamic`)
- `enable_destructive_regime` default: `True` → `False`
- `session_maxdd_threshold`: 30 → 40 pts

### 3.2 Resultado Real vs. Proyeccion

| Metrica | Proyeccion dry_run | Real backtest | Diferencia |
|---------|-------------------|--------------|-----------|
| Profit Factor | 2.71 | **2.91** | N/A (sin cambio) |
| Max Drawdown | 15.00 pts | **12.00 pts** | N/A (sin cambio) |
| Trades | 42 | **32** | -24% vs proyeccion |
| Trades/sesion | 0.97 | **0.74** | -24% vs proyeccion |
| Win Rate | 50.4% | **53.1%** | N/A (sin cambio) |
| Expectancy | +5.5 pts | **+6.70 pts** | N/A (sin cambio) |

### 3.3 Hallazgo Critico

**Mejora 1 no produce ningun cambio en el backtest.**

Razon: `context_filter.py` tiene dos niveles de filtrado:
1. **Nivel sesion** (`is_session_filtered`): bloquea sesiones completas clasificadas como `VOL_RELEASE`. Este es el filtro dominante — filtra 24 de 43 sesiones.
2. **Nivel barra** (`should_skip`): ejecuta checks ATR/volumen barra a barra. **No se llama en el backtest** — solo se usa en el live engine (`engine.py`).

Los cambios de Mejora 1 (thresholds ATR, volumen, actividad, regime) solo afectan el filtro de nivel barra, el cual no se ejecuta en el backtest. Por lo tanto, el resultado del backtest es identico al baseline.

**Las proyecciones del dry_run estaban basadas en una suposicion incorrecta sobre como funciona el filtro en el backtest.**

---

## 4. Mejora 1+2 — Agregar Setups Pullback + Breakout

### 4.1 Implementacion (branch: improvement-1-plus-2)

Archivos creados:
- `setup_pullback.py` — `PullbackDetector`: detecta pullbacks en tendencias (clase con estado, patron FA/VA80)
- `setup_breakout.py` — `BreakoutDetector`: detecta breakouts de consolidacion con spike de volumen

Archivos modificados:
- `gibbz_setup_router.py` — prioridades 4 (PULLBACK_SETUP) y 5 (BREAKOUT_SETUP) agregadas
- `full_backtest.py` — instancia y llama `PullbackDetector` y `BreakoutDetector` por barra

### 4.2 Resultado Real — Intento 1 (thresholds iniciales)

Parametros: `trend_bars=4, delta_confirm=100, range_max=10 pts, vol_spike=1.8x, cooldown=3 barras`

| Metrica | Proyeccion | Real | Estado |
|---------|-----------|------|--------|
| Profit Factor | 2.54 | **1.63** | FAIL (necesita >=2.5) |
| Max Drawdown | 18.00 pts | **15.88 pts** | PASS (<= 20) |
| Trades | 58 | **141** | FAIL (demasiados) |
| Win Rate | 48.4% | **62.4%** | PASS (>= 45%) |
| Expectancy | +5.80 pts | **+1.83 pts** | FAIL (< 5.0) |

**Diagnostico**: Los detectors en los thresholds iniciales generan 109 trades adicionales (de 32 a 141). Estos son trades de baja calidad: expectancy de +0.40 pts/trade (vs +6.70 de los setups originales). La adicion de volumen de trades diluye el edge fuertemente.

### 4.3 Resultado Real — Intento 2 (thresholds endurecidos)

Parametros: `trend_bars=6, delta_confirm=250, range_max=6 pts, vol_spike=2.5x, cooldown=12 barras`

| Metrica | Proyeccion | Real | Estado |
|---------|-----------|------|--------|
| Profit Factor | 2.54 | **2.47** | FAIL (necesita >=2.5, faltan 0.03) |
| Max Drawdown | 18.00 pts | **34.00 pts** | FAIL (>20 pts — 2.83x el limite) |
| Trades | 58 | **35** | PASS (<= 60) |
| Win Rate | 48.4% | **51.4%** | PASS (>= 45%) |
| Expectancy | +5.80 pts | **+5.63 pts** | PASS (>= 5.0) |

**Diagnostico**: Con thresholds endurecidos, solo se agregan 3 trades nuevos (32→35). PF muy cerca del threshold (2.47 vs 2.50 necesario), pero MaxDD salta de 12 a 34 pts porque los 3 nuevos trades ocurren en sesiones donde hay drawdown compuesto.

---

## 5. Validacion Bootstrap — Improvement-1-plus-2 (Intento 2)

Bootstrap: 100 runs × 43 sesiones con reemplazo, seed=42

| Criterio | Requerido | Obtenido | Estado |
|---------|-----------|---------|--------|
| PF mediano | >= 2.3 | **2.73** | PASS |
| PF p5% | >= 2.0 | **1.16** | FAIL |
| Exp p5% | > 0 | **+0.84** | PASS |
| MaxDD p95% | <= 30 pts | **78.10 pts** | FAIL |
| Runs PF > 1.0 | >= 90% | **97.0%** | PASS |

**Interpretacion**: Alta varianza del PF (std=1.14) indica que el edge es inestable y dependiente de sesiones especificas. MaxDD p95%=78 pts es inaceptable para capital real. El edge no es estadisticamente robusto con los nuevos setups.

---

## 6. Validacion de Proyecciones

| Proyeccion | Esperado | Real (mejor intento) | Dentro del 10%? |
|-----------|---------|---------------------|----------------|
| PF | 2.54 | 2.47 | SI (dif=2.8%) |
| MaxDD | 18 pts | 34 pts | NO (dif=+89%) |
| Trades | 58 | 35 | NO (dif=-40%) |
| WR | 48.4% | 51.4% | SI |
| Expectancy | +5.80 | +5.63 | SI (dif=2.9%) |

**Solo 3 de 5 metricas dentro de tolerancia (60%).** Umbral minimo: 80%. 

**Raiz del problema en las proyecciones:**
- El dry_run asumio +31% trades de Mejora 1, pero Mejora 1 no cambia el backtest
- El dry_run asumio trades de calidad similar al baseline, pero los nuevos setups generan trades de menor calidad
- MaxDD fue subestimado por 89% porque los nuevos setups coinciden con sesiones de drawdown existente

---

## 7. Comparacion Final: Baseline vs. Mejor Resultado

| Metrica | Baseline (master) | Imp-1+2 Tightened | Cambio |
|---------|------------------|------------------|--------|
| Profit Factor | 2.91 | 2.47 | **-15%** |
| Max Drawdown | 12.00 pts | 34.00 pts | **+183%** |
| Trades | 32 | 35 | +9% |
| Trades/sesion | 0.74 | 0.81 | +9% |
| Win Rate | 53.1% | 51.4% | -3% |
| Expectancy | +6.70 pts | +5.63 pts | -16% |
| PnL total | +214.25 pts | +197.00 pts | -8% |
| Recovery Factor | 17.85 | 5.79 | **-68%** |

Los nuevos setups DEGRADAN el sistema: PF baja 15%, MaxDD casi se triplica, Recovery Factor colapsa de 17.85 a 5.79.

---

## 8. Criterios de Aceptacion — Resultado Final

| Criterio | Umbral | Imp-1 | Imp-1+2 | Estado |
|---------|--------|-------|---------|--------|
| Profit Factor | >= 2.5 | 2.91 | 2.47 | FAIL |
| Max Drawdown | <= 20 pts | 12.00 | 34.00 | FAIL |
| Trades/sesion | >= 1.0 | 0.74 | 0.81 | FAIL |
| Win Rate | >= 45% | 53.1% | 51.4% | PASS |
| Expectancy | >= +5.0 pts | +6.70 | +5.63 | PASS |
| Bootstrap PF p5% | >= 2.0 | N/A | 1.16 | FAIL |
| Counterfactual edge | Real | N/A | No (alta varianza) | FAIL |
| Proyecciones validadas | >= 80% | 0% | 60% | FAIL |

---

## 9. Veredicto Final

```
╔═══════════════════════════════════════════════════════════╗
║                    VEREDICTO: DISCARD                     ║
╠═══════════════════════════════════════════════════════════╣
║  improvement-1:     NO MERGE — sin efecto en backtest     ║
║  improvement-1+2:   NO MERGE — PF < 2.5, MaxDD > 20 pts  ║
╠═══════════════════════════════════════════════════════════╣
║  Razon principal:                                         ║
║  Los setups Pullback + Breakout diluyen el edge del       ║
║  sistema. La fortaleza de GIBBZ reside en la extrema      ║
║  selectividad de VA80+FA con filtro VOL_RELEASE, que      ║
║  produce PF=2.91 con alta consistencia. Agregar setups   ║
║  adicionales destruye esa selectividad.                   ║
╠═══════════════════════════════════════════════════════════╣
║  Accion recomendada:                                      ║
║  Mantener sistema actual (PF=2.91, 32 trades).           ║
║  Proceder a paper trading segun protocolo establecido.    ║
║  El problema real no es falta de trades — es que el       ║
║  edge esta concentrado en solo 6 sesiones de 43.          ║
║  Solucion: mejorar clasificacion de sesiones elegibles,   ║
║  no agregar setups genericos de menor calidad.            ║
╚═══════════════════════════════════════════════════════════╝
```

---

## 10. Lecciones Aprendidas

1. **Bar-level vs session-level filtering**: Los cambios en thresholds de ContextFilter solo afectan el live engine. El backtest usa exclusivamente `is_session_filtered()`. Cualquier mejora al filtro debe operar a nivel sesion para tener efecto en el backtest.

2. **Dilution of edge**: GIBBZ tiene un edge altamente concentrado y selectivo (PF=2.91 con solo 32 trades). Agregar setups adicionales sistematicamente diluye la calidad porque los nuevos setups tienen menor selectividad inherente.

3. **MaxDD amplification**: Los nuevos trades no son independientes temporalmente — cuando ocurren en las mismas sesiones que los trades originales fallidos, amplifican el drawdown en lugar de distribuirlo.

4. **Projection accuracy**: Las proyecciones del dry_run sobreestimaron el trade count de Mejora 1 en 100% y subestimaron el MaxDD de Mejora 1+2 en 89%. Las proyecciones sin backtest real son orientativas, no confiables para decision de deployment.

5. **Session concentration is the real problem**: El edge real esta en 6 de 43 sesiones (14%). La solucion no es agregar setups, sino identificar que condiciones de mercado producen sesiones con edge y enfocar el sistema en detectarlas mejor.

---

## 11. Proximos Pasos Recomendados

| Prioridad | Accion |
|---------|--------|
| INMEDIATO | Proceder a paper trading con sistema actual (PF=2.91) |
| CORTO PLAZO | Analizar las 6 sesiones con edge — que las hace diferentes |
| MEDIO PLAZO | Mejorar clasificacion de tipos de sesion para capturar mas sesiones elegibles |
| LARGO PLAZO | Investigar integracion de informacion contextual (regimen de mercado, nivel de volatilidad VIX) |

---

*Generado automaticamente por el pipeline de backtest GIBBZ — 2026-05-31*
