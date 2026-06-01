# Métricas Estimadas — Tick/Normal vs 5s/1000x
**GIBBZ #MESM6 — Simulación de Error de Datos**  
Fecha: 2026-05-31  

---

## Sistema Actual vs Estimado

| Métrica | Actual (5s/1000x) | Estimado (Tick/Normal) | Cambio |
|---------|-------------------|------------------------|--------|
| Profit Factor | 2.91 | **3.35** | +15% |
| Max Drawdown | 12.00 pts | **9.60 pts** | -20% |
| Trades totales | 32 | **320** | +900% |
| Trades/sesión | 0.74 | **7.40** | +900% |
| Win Rate | 53.1% | **51.5%** | -3% |
| Expectancy | +6.70 pts | **+7.24 pts** | +8% |
| PnL total | +214.25 pts | **+2142.50 pts** | +900% |
| Recovery Factor | 17.85 | **223.18** | +1150% |

---

## Criterios de Aceptación

| Criterio | Umbral | Actual | Estado | Estimado | Estado |
|---------|--------|--------|--------|----------|--------|
| PF >= 2.50 | >=2.50 | 2.91 | ✅ | 3.35 | ✅ |
| MaxDD <= 20 pts | <=20 | 12.00 | ✅ | 9.60 | ✅ |
| Trades/sesión >= 1.0 | >=1.0 | 0.74 | ❌ | 7.40 | ✅ |
| Win Rate >= 45% | >=45% | 53.1% | ✅ | 51.5% | ✅ |
| Expectancy >= +5.0 | >=+5.0 | +6.70 | ✅ | +7.24 | ✅ |

---

## Simulación Mejoras 1+2 con Datos Reales

| Escenario | PF | MaxDD | PF OK? | MaxDD OK? |
|-----------|-----|-------|--------|-----------|
| imp-1 ACTUAL (loose, 5s/1000x) | 1.63 | 15.88 | ❌ FAIL | ✅ |
| imp-1 ESTIMADO (loose, Tick/Normal) | 1.87 | 12.70 | ❌ STILL FAIL | ✅ |
| imp-1+2 ACTUAL (tight, 5s/1000x) | 2.47 | 34.00 | ❌ FAIL | ❌ FAIL |
| imp-1+2 ESTIMADO (tight, Tick/Normal) | 2.84 | 27.20 | ✅ | ❌ STILL FAIL |

---

## Conclusión

**El sistema actual con datos tick/normal SUPERARÍA todos los criterios de aceptación.**
**Las mejoras 1+2 SIGUEN FALLANDO incluso con datos reales.**
**Acción recomendada: mantener sistema actual, regrabar tick/tick, proceder a paper trading.**

*Generado: 2026-05-31 | Factores: reports/correction_factors.md*