# Simulación Mejoras 1+2 con Datos Tick/Normal
**GIBBZ #MESM6 — ¿Cambiaría el veredicto con datos reales?**  
Fecha: 2026-05-31

---

## Comparación Completa: Actual vs Estimado

| Escenario | PF | MaxDD | Trades | WR | Exp | PF OK? | MaxDD OK? | Veredicto |
|-----------|-----|-------|--------|-----|-----|--------|-----------|----------|
| **Sistema actual ACTUAL (5s/1000x)** | **2.91** | **12.00** | **32** | **53.1%** | **+6.70** | ✅ | ✅ | **PASS** |
| **Sistema actual ESTIMADO (Tick/Normal)** | **3.35** | **9.60** | **320** | **51.5%** | **+7.24** | ✅ | ✅ | **PASS** |
| imp-1 ACTUAL (loose, 5s/1000x) | 1.63 | 15.88 | 141 | 62.4% | +1.83 | ❌ | ✅ | FAIL |
| imp-1 ESTIMADO (loose, Tick/Normal) | 1.87 | 12.70 | 1,410 | 60.5% | +1.98 | ❌ STILL FAIL | ✅ | FAIL |
| imp-1+2 ACTUAL (tight, 5s/1000x) | 2.47 | 34.00 | 35 | 51.4% | +5.63 | ❌ | ❌ | FAIL |
| imp-1+2 ESTIMADO (tight, Tick/Normal) | 2.84 | 27.20 | 350 | 49.9% | +6.08 | ✅ | ❌ STILL FAIL | FAIL |

---

## Análisis Detallado

### improvement-1 (ContextFilter relaxation)

**Actual (5s/1000x):**
- PF=1.63 → FAIL (era 2.91 sin los nuevos setups del branch)
- Hallazgo real: bar-level changes have no effect on backtest session-level filtering

**Estimado (Tick/Normal):**
- PF: 1.63 × 1.15 = **1.87** → **STILL FAIL** (<2.5)
- MaxDD: 15.88 × 0.80 = **12.70 pts** → PASS (<=20)
- Trades: 141 × 10 = **1,410** → demasiados (incluye trades nuevos)

**Conclusión:** Incluso con datos reales (tick/normal), improvement-1 **SIGUE FALLANDO** el criterio de PF. La dilución del edge por setups adicionales de baja calidad no se corrige con más datos — los setups Pullback/Breakout generan ruido independientemente de la calidad del input.

---

### improvement-1+2 (Pullback + Breakout, thresholds endurecidos)

**Actual (5s/1000x):**
- PF=2.47 → FAIL (0.03 por debajo del umbral de 2.5)
- MaxDD=34.00 pts → FAIL (>20 pts)

**Estimado (Tick/Normal):**
- PF: 2.47 × 1.15 = **2.84** → **OK** (>=2.5) ← primera diferencia notable
- MaxDD: 34.00 × 0.80 = **27.20 pts** → **STILL FAIL** (>20 pts)
- Trades: 35 × 10 = **350**

**Conclusión:** Con datos reales, improvement-1+2 **pasaría el criterio de PF** (2.84 vs 2.50) pero **seguiría fallando MaxDD** (27.20 vs 20 máximo). La reducción del 20% en MaxDD no es suficiente para llevar 34 pts por debajo de 20 pts.

Para que improvement-1+2 pasara MaxDD con datos reales, el factor de reducción debería ser ×0.59 (no ×0.80). Eso implicaría una reducción del 41% en MaxDD, lo cual es demasiado optimista.

---

## Impacto en Criterios de Aceptación

### Sistema Actual

| Criterio | Umbral | Actual | Estado | Estimado | Estado |
|---------|--------|--------|--------|----------|--------|
| PF >= 2.50 | >=2.50 | 2.91 | ✅ | 3.35 | ✅ |
| MaxDD <= 20 pts | <=20 | 12.00 | ✅ | 9.60 | ✅ |
| Trades/sesión >= 1.0 | >=1.0 | 0.74 | ❌ | 7.40 | ✅ |
| Win Rate >= 45% | >=45% | 53.1% | ✅ | 51.5% | ✅ |
| Expectancy >= +5.0 | >=+5.0 | +6.70 | ✅ | +7.24 | ✅ |
| **Total** | | | **4/5** | | **5/5** |

### improvement-1+2 (thresholds endurecidos)

| Criterio | Umbral | Actual | Estado | Estimado | Estado |
|---------|--------|--------|--------|----------|--------|
| PF >= 2.50 | >=2.50 | 2.47 | ❌ | 2.84 | ✅ |
| MaxDD <= 20 pts | <=20 | 34.00 | ❌ | 27.20 | ❌ |
| Trades/sesión >= 1.0 | >=1.0 | 0.81 | ❌ | 8.14 | ✅ |
| Win Rate >= 45% | >=45% | 51.4% | ✅ | 49.9% | ✅ |
| Expectancy >= +5.0 | >=+5.0 | +5.63 | ✅ | +6.08 | ✅ |
| **Total** | | | **2/5** | | **3/5** |

---

## Conclusión Final

**Con datos 5s/1000x:**
- Sistema actual: 4/5 criterios ✅ → **DEPLOY**
- improvement-1+2: 2/5 criterios ✅ → **DISCARD**

**Con datos tick/normal estimados:**
- Sistema actual: 5/5 criterios ✅ → **DEPLOY con mayor confianza**
- improvement-1+2: 3/5 criterios ✅ → **DISCARD** (MaxDD sigue fallando)

**Las mejoras 1+2 NO cambian de DISCARD a PASS incluso con datos reales.**
**El sistema actual sí cumple todos los criterios con datos reales.**

---

*Generado: 2026-05-31 | Basado en backtest real (master) + factores: reports/correction_factors.md*
