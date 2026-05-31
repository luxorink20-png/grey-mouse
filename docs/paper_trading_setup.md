# GIBBZ Paper Trading — Setup y Protocolo

## Estado Actual del Sistema

| Metrica | Valor |
|---|---|
| Edge (backtest con ContextFilter) | PF=2.91, Exp=+6.70, MaxDD=12.00 pts |
| Sesiones analizadas | 43 total (24 VOL_RELEASE filtradas) |
| Sesiones con trades | 6 de 19 elegibles |
| Go-Live Readiness Score | 90/100 — VEREDICTO: GO |
| Bootstrap Treadmill | PF mediano=2.86, 96% runs PF>1 |

---

## Que es Paper Trading en GIBBZ

Paper trading en GIBBZ NO requiere codigo nuevo. El sistema ya funciona en modo "paper" por diseño:

- `engine.py` procesa señales de ATAS via UDP
- `ContextFilter` gatea entradas (VOL_RELEASE, destructive regime, kill switch)
- `FeedbackEngine` registra trades hipoteticos (sin enviar órdenes reales a ningun broker)
- `logs/gibbz_trades_YYYY-MM-DD.csv` contiene el registro completo

**No hay "live trading" en el sentido de ordenes reales** hasta que se conecte una API de broker. El sistema actual es 100% paper trading por default.

### Las tres etapas de validación

| Etapa | Descripcion | Estado |
|---|---|---|
| Backtest | Edge en datos historicos | COMPLETO (PF=2.91) |
| Paper trading | Edge en datos reales en tiempo real | PENDIENTE (2-4 semanas) |
| Live Phase 1 | Edge con dinero real (50% size) | BLOQUEADO hasta paper OK |

---

## Protocolo de Ejecución

### Paso 1: Verificar configuracion

```powershell
python scripts/run_paper_trading.py --check
```

Verifica:
- ContextFilter importable y correctamente configurado
- config/paper_trading_config.yaml existe
- logs/ accesible
- engine.py presente

### Paso 2: Ejecutar trading (terminal A)

```powershell
python engine.py
```

Prerequisitos:
- ATAS activo con #MESM6
- GibbzBridge.cs corriendo en ATAS (UDP :9999)
- `USE_REAL_FEED=true` en config.py (o `$env:GIBBZ_USE_REAL_FEED=1`)

### Paso 3: Monitorear en tiempo real (terminal B, opcional)

```powershell
python scripts/run_paper_trading.py --watch 30
```

Polling cada 30 segundos. Muestra:
- Trades del dia en tiempo real
- Alertas si PF o DD se deterioran
- Count de Context Filter skips

### Paso 4: Reporte diario (al cierre, 16:00 ET)

```powershell
python scripts/daily_paper_trading_report.py
python scripts/daily_paper_trading_report.py --cumulative  # historial completo
```

---

## Criterios de Exito (para avanzar a Live Phase 1)

Evaluar despues de **2 semanas minimas** con **>= 20 trades acumulados**:

| Criterio | Objetivo | Critico |
|---|---|---|
| Profit Factor | >= 2.5 | < 2.0 por 3 dias |
| Max Drawdown | <= 20 pts | > 30 pts |
| Win Rate | >= 45% | < 35% por 1 semana |
| Trades totales | >= 20 | 0 por 3 dias consecutivos |
| Degradacion vs backtest | PF no cae > 40% | PF < 2.0 |

### Interpretacion de degradacion aceptable

El backtest tiene PF=2.91 sobre datos con sesgo de seleccion (43 sesiones sobre 4 dias de precio real). En paper trading con mercado real, una degradacion del 20-30% es esperada y aceptable:

- PF backtest: 2.91
- PF paper trading minimo aceptable: 2.50 (−14%)
- PF paper trading warning: 2.30 (−21%)
- PF paper trading failure: 2.00 (−31%)

---

## Calendarios Esperados

### Sesiones a operar

De los 43 tipos de sesion analizados, las elegibles (sin VOL_RELEASE) son:
- **EARLY_EXPANSION**: PF=5.00, Exp=+10.67 — prioridad alta
- **OPENING_DRIVE**: PF=5.00, Exp=+10.67 — prioridad alta
- **WATCH**: PF=4.15, Exp=+8.66 — prioridad media
- **EXPANSION**: PF=1.29, Exp=+1.42 — prioridad baja
- **ROTATIONAL**: PF=0.0, 0 trades — evitar

### Frecuencia esperada

Con el ContextFilter activo, se espera:
- 3-8 trades por sesion activa
- 2-4 sesiones activas por semana (en sesiones EARLY_EXPANSION/OPENING_DRIVE/WATCH)
- Skip rate >= 70% en sesiones VOL_RELEASE (24 de 43 = 56% del tiempo)

---

## Archivos del Sistema

| Archivo | Proposito |
|---|---|
| `engine.py` | Motor de trading principal — ejecutar esto |
| `context_filter.py` | Filtro de contexto (VOL_RELEASE, regime, kill switch) |
| `config/paper_trading_config.yaml` | Criterios de exito/fallo configurados |
| `scripts/run_paper_trading.py` | Pre-flight + monitor live |
| `scripts/daily_paper_trading_report.py` | Reporte diario con metricas acumuladas |
| `logs/gibbz_trades_YYYY-MM-DD.csv` | Registro de trades (generado por engine.py) |
| `logs/gibbz.log` | Log del sistema incluyendo CONTEXT SKIP events |
| `reports/paper_trading/` | Reportes diarios generados |

---

## Protocolo de Fallo

Si se activan los criterios de fallo:

1. Detener `engine.py` (Ctrl+C)
2. Ejecutar reporte completo: `python scripts/daily_paper_trading_report.py --cumulative`
3. Ejecutar auditoria de fallos: `python failure_investigation.py`
4. NO avanzar a live trading
5. Revisar si cambio el regimen de mercado (nuevo periodo destructivo como Marzo 2026)

---

## Transicion a Live Phase 1

Solo despues de 2-4 semanas exitosas:

```yaml
# config/paper_trading_config.yaml
live_trading_phase1:
  enabled: true  # cambiar false → true
  capital_allocation: 0.15
  position_size_multiplier: 0.5
  max_daily_loss_pts: 15.0
  max_session_dd_pts: 25.0
```

Live Phase 1 = 15% del capital total, 0.5x del size normal, 4 semanas.
