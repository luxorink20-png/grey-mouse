# GIBBZ Context Filter

## Proposito

Filtrar contextos de mercado no rentables **sin modificar la logica de setups existente** (VA80_SETUP, FA_SETUP, INSTITUTIONAL_GRADE, etc.).

El filtro actua como una capa previa al trade: si el contexto es destructivo, el setup se omite. Si el contexto es favorable, el pipeline continua sin cambios.

---

## Problema que resuelve

Datos de `edge_contribution_audit.py` (n=106 trades, 21 sesiones):

| Contexto | PF interno | WR | PnL neto | % del MaxDD | Veredicto |
|---|---|---|---|---|---|
| VOL_RELEASE | 1.17 | 32.4% | +62.75 | **87.5%** | RENTABLE INEFICIENTE |
| Sesiones >=7 trades | 1.39 | 36.1% | +138.75 | 56.4% | RENTABLE INEFICIENTE |
| Marzo 2026 | 0.44 | 14.3% | -75.25 | 41.0% | DESTRUCTIVO NETO |
| Mediodia ET | 0.42 | 14.3% | -28.00 | 25.1% | DESTRUCTIVO NETO |
| Apertura ET | 0.92 | 28.6% | -3.00 | 12.5% | DESTRUCTIVO MENOR |

VOL_RELEASE: 69.8% del volumen, 87.5% del MaxDD, pero solo 22.7% del PnL.

---

## Expected Outcomes (sin VOL_RELEASE — counterfactual auditado)

| Metrica | Baseline | Sin VOL_RELEASE | Delta |
|---|---|---|---|
| PF | 1.56 | **2.91** | +87% |
| Expectancy | +2.61 pts | **+6.70 pts** | +157% |
| MaxDD | 95.75 pts | **12.00 pts** | -87% |
| Recovery Factor | 2.89 | **17.85** | +518% |
| PnL total | +277 pts | +214 pts | -22% (aceptable) |
| Trades | 106 | 32 | -70% volumen |

**La perdida de PnL absoluto (-22%) esta justificada por la reduccion de riesgo (-87% DD) y la mejora de calidad (+157% Expectancy).**

---

## Filtros implementados

### 1. VOL_RELEASE Filter

**Backtest**: si `session_type == "VOL_RELEASE"` → omitir toda la sesion.

**Live (deteccion dinamica)**: requiere TODOS los criterios simultaneos:
- Hora 13:00–15:00 ET (franja mediodia)
- ATR actual > 1.5x media rolling (20 barras)
- Volumen actual > 2.0x media rolling
- Actividad (trades/barra) > 3.0x media rolling

Los umbrales son definiciones objetivas de mercado (no optimizados).

**Confianza estadistica**: 78% FUERTE (n=74, 15 sesiones).

### 2. Destructive Regime Filter

Detecta cuando el edge ha desaparecido en el contexto actual:
- WR de los ultimos 10 trades < 25%
- PF de los ultimos 10 trades < 0.80
- Minimo 5 trades registrados

Basado en: Marzo 2026 (PF=0.44) y Mediodia ET (PF=0.42).

### 3. Session Kill Switch

Detiene operaciones cuando el drawdown de la sesion supera 30 pts.

- MaxDD baseline: 95.75 pts
- MaxDD objetivo post-filtro: ≤ 20 pts
- Umbral conservador: 30 pts

---

## Integracion

### Live (engine.py)

```python
# Ya integrado. Los cambios son:
# 1. _context_filter = ContextFilter() — instancia al inicio de sesion
# 2. _context_filter.update_bar(raw) — cada barra
# 3. _context_filter.reset_session() — al cambiar de sesion
# 4. if risk_result.approved: skip, reason = _context_filter.should_skip(raw)
# 5. _context_filter.register_trade(pnl, win) — al cerrar trade
```

### Backtest (full_backtest.py)

```python
from context_filter import ContextFilter
from full_backtest import run_session, run_backtest

cf = ContextFilter(enable_vol_release=True)

# run_session ahora acepta context_filter y session_type opcionales
bars = run_session(date, recording, max_bars, target_cap,
                   context_filter=cf, session_type="VOL_RELEASE")
# → retorna [] si session_type esta en la lista de filtrados
```

---

## Como usar en produccion

```python
from context_filter import ContextFilter

# Configuracion por defecto (todos los filtros activos)
cf = ContextFilter()

# O con configuracion especifica
cf = ContextFilter(
    enable_vol_release=True,
    enable_destructive_regime=True,
    enable_session_kill_switch=True,
    session_maxdd_threshold=30.0,
)

# En el loop principal
cf.update_bar(raw_bar)
skip, reason = cf.should_skip(raw_bar)
if skip:
    logger.info("SKIP: %s", reason)
    continue

# Despues de cerrar un trade
cf.register_trade(pnl=trade.pnl, win=(trade.result == "WIN"))

# Al iniciar nueva sesion
cf.reset_session()
```

---

## Como desactivar (rollback)

### Via instanciacion directa

```python
# Todos los filtros OFF = comportamiento identico al baseline
cf = ContextFilter(
    enable_vol_release=False,
    enable_destructive_regime=False,
    enable_session_kill_switch=False,
)
```

### Via YAML

```yaml
# config/context_filter_config.yaml
context_filter:
  filters:
    vol_release:
      enabled: false
    destructive_regime:
      enabled: false
    session_kill_switch:
      enabled: false
```

---

## Validacion

### Tests unitarios

```powershell
pytest tests/unit/test_context_filter.py -v
# Esperado: 26/26 passed
```

### Backtest con filtro

```powershell
python scripts/run_backtest_with_filter.py
# Esperado: PF >= 2.5, MaxDD <= 20, Trades <= 50
```

### Validacion rapida

```powershell
python scripts/validate_context_filter.py
# Esperado: 4/4 tests PASSED
```

---

## Principios de diseno

1. No modificar logica de setups existente (VA80, FA, INSTITUTIONAL_GRADE)
2. No optimizar parametros — umbrales = definiciones objetivas de mercado
3. Todos los filtros son reversibles (toggle on/off)
4. Logging via `log_config.get_logger()`, no print()
5. No eliminar trades — omitir ANTES de entrar
6. Funciona independiente en backtest (metadata) y live (dinamico)

---

## Referencias

- Evidencia cuantitativa: `edge_contribution_audit.py`
- Analisis contrafactual: `counterfactual_edge_audit.py`
- Investigacion de fallos: `failure_investigation.py`
