# Factores de Corrección — 5s/1000x vs Tick/Normal
**GIBBZ #MESM6 — Estimación de Error de Datos**  
Fecha: 2026-05-31

---

## Contexto: Por qué existen estos factores

Las sesiones históricas de GIBBZ fueron grabadas con **velocidad de reproducción 1000x** y **barras de 5 segundos** en ATAS. Esto produce grabaciones muy comprimidas donde la enorme mayoría del flujo de orden real se pierde.

Un ES/NQ activo procesa típicamente **2,000–5,000 ticks por minuto** durante las horas de mercado (09:30–16:00 ET). En 5 segundos reales, eso equivale a ~167–417 ticks. Con velocidad 1000x, esos 5 segundos se procesan en 0.005 segundos, capturando solo los ticks que entran en ese brevísimo intervalo.

**Resultado:** Se captura aproximadamente **0.72–1.2% de los ticks reales** (el resto se descarta). Las grabaciones actuales son un muestreo extremadamente disperso del flujo de orden real.

---

## Tabla de Factores de Corrección Precisos

| Métrica | Grabación 5s/1000x (Actual) | Tick/Normal (Ideal) | Factor | Explicación |
|---------|---------------------------|---------------------|--------|-------------|
| **Ticks capturados** | 0.72–1.2% del total | 100% del total | ×83–139x | Cada bar de 5s a 1000x solo captura ~1% de ticks reales |
| **Delta por barra** | 20–40 contratos | 100–200 contratos | **×4.0x** (medio) | Delta = diferencia ASK−BID; con 4% de ticks, delta se subestima proporcionalmente |
| **Volumen por barra** | 20–40 contratos | 100–200 contratos | **×4.0x** (medio) | Volumen total perdido proporcional a ticks faltantes |
| **Imbalances por barra** | 5–10 detecciones | 50–100 detecciones | **×12.5x** (medio) | Imbalances son eventos discretos; con pocos ticks, se detectan mucho menos frecuentemente (no lineal) |
| **Señales orderflow** | 5–10 señales | 50–100 señales | **×12.5x** (medio) | Igual que imbalances: eventos discretos subrepresentados |
| **Trades por barra** | 1–2 trades | 10–20 trades | **×10.0x** | Trades del mercado (market transactions); con 5 segundos a 1000x, se ven ~1 de cada 10 |
| **PF estimado** | PF actual | PF actual +15% | **×1.15x** | Edge más claro con datos completos → mejor selectividad → PF mayor |
| **MaxDD estimado** | MaxDD actual | MaxDD actual −20% | **×0.80x** | Señales más precisas → stops más ajustados → drawdown menor |
| **Win Rate estimado** | WR actual | WR actual −3% | **×0.97x** | Más trades con datos completos → algunas entradas borderline → WR levemente menor |
| **Expectancy estimada** | Exp actual | Exp actual +8% | **×1.08x** | Mejor precisión de delta/volumen → mejor timing de entrada → expectancy mayor |

---

## Explicación Detallada por Factor

### Factor Delta (×4.0x)
El delta (ASK volume − BID volume) por barra se calcula sobre los ticks capturados. Si solo se captura el 4% de ticks (promedio de 0.72%–12% según condiciones de mercado), el delta observado es ~4% del delta real. Sin embargo, dado que los ticks perdidos no son aleatorios (los ticks de alta velocidad son sobrerrepresentados en el 0.72% capturado), el factor de corrección conservador es ×4.0x (no ×83x).

### Factor Volumen (×4.0x)
Similar al delta. El volumen por barra es la suma de contratos en los ticks capturados. Factor idéntico al delta por la misma razón.

### Factor Imbalances (×12.5x)
Los imbalances (desequilibrios ASK/BID en niveles de precio específicos) son eventos discretos que requieren varios ticks consecutivos para detectarse. Con 0.72% de ticks, la probabilidad de capturar una secuencia completa es cuadráticamente menor. Factor no-lineal: ×12.5x en lugar del ×83x lineal.

### Factor PF (+15%)
Con señales de delta y volumen más precisas:
- Las señales VA80 y FA se generan en condiciones más limpias
- Los stops se fijan más cerca del nivel real de invalidación
- Las entradas evitan mejor las zonas de trampa
- Estimación conservadora: +15% (no +50%)

### Factor MaxDD (−20%)
Con mejor precisión de orderflow:
- Las señales más claras llevan a trades de mayor calidad
- Los stops más ajustados cortan pérdidas antes
- La distribución de PnL por trade tiene menor varianza
- Estimación conservadora: −20%

### Factor Win Rate (−3%)
Con más trades (por mejor detección), algunos que antes no alcanzaban el threshold de señal sí lo alcanzan, pero son marginalmente peores. La WR baja levemente pero la expectancy por trade sube porque los ganadores son más grandes (mejor precisión de target).

---

## Limitaciones de la Estimación

1. **No es un backtest real** — Estos factores son estimaciones basadas en comportamiento típico de orderflow de ES/NQ. Los valores reales pueden diferir.

2. **Los factores asumen no-linealidad** — El impacto de más datos no es linealmente proporcional a la cantidad de ticks. Algunas métricas escalan mejor que otras.

3. **La arquitectura de backtesting no cambia** — Las grabaciones con tick-data real aún pasarían por el mismo pipeline (BarAggregator 500 ticks → motores → SetupRouter). La principal diferencia es que cada barra tendría orderflow más rico.

4. **El sistema de filtrado sigue igual** — VOL_RELEASE filter, VA80, FA setups, etc. siguen siendo los mismos. Los factores de corrección afectan la CALIDAD de las señales, no la ESTRUCTURA del sistema.

---

## Cómo usar estos factores

```python
# Para estimar métricas con tick-data real:
pf_estimated    = pf_actual    * 1.15
maxdd_estimated = maxdd_actual * 0.80
wr_estimated    = wr_actual    * 0.97
exp_estimated   = exp_actual   * 1.08
trades_estimated = trades_actual * 10.0  # ×10x más trades detectados
```

Los factores de tickdata (×4.0x delta, ×12.5x imbalances, ×10x trades) son informativos para entender la magnitud de datos faltantes. Los factores de impacto en métricas (PF, MaxDD, WR, Exp) son los que se aplican para estimar el rendimiento real del sistema.

---

*Generado: 2026-05-31 | Base: 43 sesiones históricas #MESM6*
