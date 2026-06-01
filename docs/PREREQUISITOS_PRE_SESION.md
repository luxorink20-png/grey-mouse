# GIBBZ — Prerequisitos Pre-Sesion

> Checklist completo antes de iniciar Paper Trading o Live Trading.  
> Ultimo update: 2026-06-01

---

## TL;DR — Comando unico de inicio

```powershell
python scripts/start_paper_trading.py
```

Esto automaticamente: carga contexto → muestra niveles → lanza engine.py.

---

## 1. Contexto de Niveles (PDH/PDL/ONH/ONL/VAH/VAL)

### Estado actual

| Nivel | Fuente automatica | Precision | Latencia |
|-------|-------------------|-----------|---------|
| PDH / PDL | yfinance MES=F (RTH) | ~99% | <5s |
| PDH / PDL | GibbzBridge.cs + Rithmic | 100% | <1ms |
| ONH / ONL | GibbzBridge.cs + Rithmic | 100% | <1ms |
| ONH / ONL | Manual (ATAS Globex chart) | 100% | ~1 min |
| VAH / VAL / POC | Manual (ATAS Volume Profile) | 100% | ~1 min |

### Flujo automatico (con ATAS corriendo)

1. Iniciar ATAS con GibbzBridge indicador activo
2. GibbzBridge.cs escribe `%USERPROFILE%\gibbz_context_levels.json` automaticamente
3. `context_fetcher.py` lee ese archivo → PDH/PDL/ONH/ONL 100% precisos de Rithmic
4. Solo necesitas ingresar VAH/VAL/POC (del Volume Profile en ATAS)

### Flujo sin ATAS (solo Python)

1. PDH/PDL: yfinance los obtiene automaticamente (MES=F, OHLC RTH)
2. ONH/ONL + VAH/VAL/POC: ingresar manualmente con `update_context.py`

```powershell
python scripts/update_context.py    # interactivo, ~1-2 minutos
python scripts/update_context.py --auto   # no-interactivo, solo auto-fetch PDH/PDL
python scripts/update_context.py --show   # mostrar niveles actuales
```

### Donde obtener los niveles manualmente

| Nivel | Donde en ATAS |
|-------|---------------|
| PDH / PDL | Chart > Daily timeframe > Previous day candle High/Low |
| ONH / ONL | Chart > Overnight Range indicator, o Globex session |
| VAH / VAL | Volume Profile indicator > 70% Value Area |
| POC | Volume Profile indicator > Point of Control |

---

## 2. Broker y Conexion

| Elemento | Estado | Configuracion |
|----------|--------|---------------|
| ATAS Paper Account | Conectar antes de operar | ATAS Settings > Paper Trading |
| ATAS Live Account | Solo para Live Phase 1+ | ATAS Settings > Live Account |
| Rithmic credentials | Ya en keyring | `keyring.get_password("gibbz", "rithmic_user")` |
| GibbzBridge indicator | Cargar en ATAS | Indicators > Add > GibbzBridge, Port=9999 |
| UDP listener | Automatico | engine.py abre socket 127.0.0.1:9999 |

---

## 3. Archivos Necesarios

| Archivo | Auto-generado | Descripcion |
|---------|--------------|-------------|
| `levels.json` | NO — manual o via `update_context.py` | Niveles del dia |
| `logs/gibbz_trades_YYYY-MM-DD.csv` | SI — engine.py al primer trade | Log de trades |
| `logs/gibbz.log` | SI — al iniciar engine.py | Log de sesion |
| `reports/paper_trading/` | SI — al correr checklist | Reportes |
| `%USERPROFILE%\gibbz_context_levels.json` | SI — GibbzBridge.cs | Contexto Rithmic |

---

## 4. Checklist Pre-Sesion

```
[ ] 1. ATAS corriendo con GibbzBridge indicador activo (opcional, pero da Rithmic data)
[ ] 2. Contexto actualizado para hoy:
        python scripts/start_paper_trading.py --show-context
        Si fecha != hoy → python scripts/update_context.py
[ ] 3. levels.json fecha == hoy (verificado en paso 2)
[ ] 4. UDP port 9999 libre (no otro proceso usando el puerto)
[ ] 5. Lanzar:
        python scripts/start_paper_trading.py
```

---

## 5. Requisitos de Sistema

| Requisito | Version | Verificar |
|-----------|---------|-----------|
| Python | 3.10+ | `python --version` |
| Dependencias | ver requirements.txt | `pip install -r requirements.txt` |
| yfinance | 1.4.1+ | Auto-instalado con requirements.txt |
| ATAS | Cualquier version con GibbzBridge | Windows only |
| Rithmic | Via ATAS (ya integrado) | Sin configuracion adicional |

```powershell
pip install -r requirements.txt   # instalar todo
pytest                            # verificar 166/166 tests
```

---

## 6. Automatizacion — Nivel de Precision por Fuente

### Fuentes disponibles para PDH/PDL

| Fuente | Precision | Latencia | Requiere |
|--------|-----------|---------|---------|
| **Rithmic via GibbzBridge.cs** | 100% (exacto CME) | <1ms | ATAS activo |
| **yfinance MES=F** | ~99% (RTH, 2 ticks max) | <5s | Internet |
| Manual TradingView | ~95% | 5 min | Manual |

### Fuentes disponibles para VAH/VAL/POC

| Fuente | Precision | Estado |
|--------|-----------|--------|
| ATAS Volume Profile | 100% (volumen real Rithmic) | Manual (1 min) |
| Rithmic via Python | N/A | Rithmic no tiene Python API |
| TradingView indicators | 85% | No recomendado |

### Porque no hay Python Rithmic API directa

Rithmic provee SDKs en C++ y C# solamente. ATAS (C#) se conecta a Rithmic internamente.
El puente GibbzBridge.cs es la interfaz entre Rithmic y Python:

```
Rithmic (exchange data)
    │ C++ API
    ▼
ATAS Platform (C#)
    │ ATAS Indicator API
    ▼
GibbzBridge.cs (C# indicator)
    │ UDP :9999 (bar data)
    │ gibbz_context_levels.json (context levels) ← NEW
    ▼
Python (context_fetcher.py + engine.py)
```

---

## 7. Comandos de Referencia Rapida

```powershell
# Inicio rapido (automatico)
python scripts/start_paper_trading.py

# Solo actualizar contexto
python scripts/update_context.py

# Solo ver contexto actual
python scripts/update_context.py --show
python scripts/start_paper_trading.py --show-context

# Checklist Paper → Live
python scripts/paper_trading_live_checklist.py

# Reporte diario
python scripts/daily_paper_trading_report.py
python scripts/daily_paper_trading_report.py --cumulative

# Tests
pytest                                          # 166/166
pytest --cov=. --cov-report=term-missing       # con coverage
```
