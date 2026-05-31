# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — test_simulado.py
#  Simulador completo del pipeline con datos de MESM6
#
#  Replica condiciones reales:
#  - Precio ~7387 (ABOVE_VAH, sesión de hoy)
#  - VAH=7326, POC=7135, VAL=6826
#  - Escenarios: rango, breakout, agotamiento, squeeze, trampa
#
#  USO: python test_simulado.py
#  Reproduce voz real de Jorge para cada escenario.
# ╚══════════════════════════════════════════════════════════════════╝

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Imports del sistema ───────────────────────────────────────────
from event_engine          import EventEngine
from confluence_engine     import ConfluenceEngine
from validator             import Validator
from intent_engine         import IntentEngine
from risk_engine           import RiskEngine
from levels                import create_levels
from session_filter        import SessionFilter
from voice_engine          import VoiceEngine
from microstructure_engine import MicrostructureEngine

# ── Colores terminal ──────────────────────────────────────────────
GRN  = "\033[92m"
RED  = "\033[91m"
YLW  = "\033[93m"
BLU  = "\033[94m"
CYN  = "\033[96m"
RST  = "\033[0m"
BOLD = "\033[1m"

# ── Niveles MESM6 actuales ────────────────────────────────────────
VAH = 7326.0
POC = 7135.0
VAL = 6826.0

# ══════════════════════════════════════════════════════════════════
#  INICIALIZAR SISTEMA COMPLETO
# ══════════════════════════════════════════════════════════════════

print(f"\n{BOLD}{BLU}{'═'*55}{RST}")
print(f"{BOLD}{BLU}  GIBBZ SMC COP — SIMULADOR MESM6{RST}")
print(f"{BOLD}{BLU}  VAH={VAH}  POC={POC}  VAL={VAL}{RST}")
print(f"{BOLD}{BLU}{'═'*55}{RST}\n")

engine         = EventEngine(window=10)
confluence_eng = ConfluenceEngine(history_size=10)
validator      = Validator(tick=0.25, min_liq_ticks=4)
intent_eng     = IntentEngine(buffer_size=15, tick=0.25)
risk_eng       = RiskEngine(tick=0.25)
levels         = create_levels(vah=VAH, poc=POC, val=VAL, proximity=2.0)
micro_eng      = MicrostructureEngine(window=20)
voice          = VoiceEngine(enabled=True)

print("Iniciando voz de Jorge...")
voice.start()
time.sleep(1)


# ══════════════════════════════════════════════════════════════════
#  FUNCIÓN PRINCIPAL — procesar una vela
# ══════════════════════════════════════════════════════════════════

def procesar_vela(raw: dict, label: str = "") -> dict:
    """Pasa una vela por el pipeline completo y retorna todos los resultados."""
    from validator import ValidationResult

    result       = engine.process(raw)
    context      = levels.get_context(raw["price"])
    analysis     = confluence_eng.evaluate(result, context)
    micro_result = micro_eng.analyze(result, context, analysis, raw)

    validation   = validator.validate(analysis, result, raw)
    narrative    = intent_eng.analyze(result, context, analysis, validation)
    risk_result  = risk_eng.analyze(
        price         = raw["price"],
        confluence    = analysis,
        validation    = validation,
        intent        = narrative,
        level_context = context,
    )

    return {
        "raw":        raw,
        "result":     result,
        "context":    context,
        "analysis":   analysis,
        "micro":      micro_result,
        "validation": validation,
        "narrative":  narrative,
        "risk":       risk_result,
        "label":      label,
    }


def mostrar(d: dict) -> None:
    """Muestra resumen del tick en terminal."""
    r   = d["result"]
    a   = d["analysis"]
    val = d["validation"]
    nar = d["narrative"]
    rsk = d["risk"]
    ctx = d["context"]
    mic = d["micro"]
    raw = d["raw"]

    status_val = f"{GRN}✓ PASS{RST}" if val.validated else f"{RED}✗ REJECTED{RST}"
    status_rsk = f"{GRN}✓ TRADE{RST}" if rsk.approved else f"{RED}✗ NO TRADE{RST}"

    print(f"\n{BOLD}{CYN}  [{d['label']}]{RST}")
    print(f"  Precio      : {raw['price']}  |  Zona: {ctx.zone}")
    print(f"  Evento      : {r['event']} ({r['confidence']}%)")
    print(f"  Score       : {a.score}/100  [{a.classification}]  Bias: {a.bias}")
    print(f"  Validator   : {status_val}  ({val.reason[:60]})")
    print(f"  Narrativa   : {nar.narrative} ({nar.conviction}%)")
    print(f"  Risk        : {status_rsk}  ({rsk.reason[:60]})")
    if mic.active:
        brk = f" BREAKOUT={mic.breakout}" if mic.breakout else " COMPRESIÓN"
        print(f"  Micro       : {YLW}ACTIVO{RST}{brk}  conf={mic.confidence}%")


def tick_voz(d: dict) -> None:
    """Dispara la voz para este tick."""
    voice.on_tick(
        price        = d["raw"]["price"],
        result       = d["result"],
        context      = d["context"],
        analysis     = d["analysis"],
        validation   = d["validation"],
        narrative    = d["narrative"],
        risk_result  = d["risk"],
        micro_result = d["micro"],
    )


def separador(titulo: str) -> None:
    print(f"\n{BOLD}{YLW}{'─'*55}{RST}")
    print(f"{BOLD}{YLW}  ESCENARIO: {titulo}{RST}")
    print(f"{BOLD}{YLW}{'─'*55}{RST}")


def esperar(s: float = 6.0) -> None:
    time.sleep(s)


# ══════════════════════════════════════════════════════════════════
#  WARMUP — 5 velas para llenar buffers
# ══════════════════════════════════════════════════════════════════

print(f"\n{BLU}Calentando buffers (5 velas)...{RST}")
warmup_prices = [7382.0, 7383.5, 7384.0, 7383.0, 7384.5]
for p in warmup_prices:
    procesar_vela({
        "price": p, "high": p+0.5, "low": p-0.5,
        "ask_volume": 400, "bid_volume": 380,
        "volume": 780, "delta": 20, "open": p-0.25,
        "close": p, "trades": 40, "timestamp": time.time(),
    })
print(f"{GRN}Buffers listos.{RST}")
time.sleep(1)


# ══════════════════════════════════════════════════════════════════
#  ESCENARIO 1 — RANGO PURO (lo que pasó hoy)
#  Precio en ABOVE_VAH, acumulación repetida, score debe ser BAJO
# ══════════════════════════════════════════════════════════════════

separador("1. RANGO PURO — ABOVE_VAH (condición de hoy)")

for i in range(4):
    precio = 7387.0 + (0.25 if i % 2 == 0 else -0.25)
    d = procesar_vela({
        "price": precio, "high": precio+0.5, "low": precio-0.5,
        "ask_volume": 260, "bid_volume": 270,
        "volume": 530, "delta": -10, "open": precio+0.25,
        "close": precio, "trades": 35, "timestamp": time.time(),
    }, f"RANGO vela {i+1}")
    mostrar(d)

print(f"\n{YLW}→ Score esperado: BAJO (25-45). Sin voz o rango muerto.{RST}")
tick_voz(d)
esperar(5)


# ══════════════════════════════════════════════════════════════════
#  ESCENARIO 2 — MICRO RANGO + BREAKOUT
#  5 velas en compresión → ruptura alcista
# ══════════════════════════════════════════════════════════════════

separador("2. MICRO RANGO + BREAKOUT ALCISTA")

# Compresión: 5 velas en rango ≤ 1.5 puntos
base = 7385.0
print(f"\n{BLU}Construyendo compresión (5 velas)...{RST}")
for i in range(5):
    precio = base + (0.25 if i % 2 == 0 else 0.0)
    d = procesar_vela({
        "price": precio, "high": base+0.75, "low": base-0.75,
        "ask_volume": 350, "bid_volume": 310,
        "volume": 660, "delta": 40, "open": precio-0.25,
        "close": precio, "trades": 45, "timestamp": time.time(),
    }, f"COMPRESIÓN vela {i+1}")
    mostrar(d)

tick_voz(d)
esperar(3)

# Breakout
print(f"\n{GRN}→ BREAKOUT{RST}")
d_break = procesar_vela({
    "price": 7389.5, "high": 7390.5, "low": 7387.0,
    "ask_volume": 850, "bid_volume": 200,
    "volume": 1050, "delta": 650, "open": 7385.5,
    "close": 7389.5, "trades": 95, "timestamp": time.time(),
}, "BREAKOUT UP")
mostrar(d_break)
tick_voz(d_break)
esperar(7)


# ══════════════════════════════════════════════════════════════════
#  ESCENARIO 3 — AGOTAMIENTO EN ABOVE_VAH
#  Score alto esperado (88+), voz debe sonar
# ══════════════════════════════════════════════════════════════════

separador("3. AGOTAMIENTO — ABOVE_VAH")

# Primero un INTENTO
d_int = procesar_vela({
    "price": 7392.0, "high": 7393.5, "low": 7389.5,
    "ask_volume": 700, "bid_volume": 250,
    "volume": 950, "delta": 450, "open": 7389.0,
    "close": 7392.0, "trades": 80, "timestamp": time.time(),
}, "INTENTO alcista")
mostrar(d_int)
esperar(1)

# Luego AGOTAMIENTO — precio regresa con delta negativo
d_agot = procesar_vela({
    "price": 7388.0, "high": 7392.5, "low": 7387.5,
    "ask_volume": 180, "bid_volume": 680,
    "volume": 860, "delta": -500, "open": 7392.0,
    "close": 7388.0, "trades": 75, "timestamp": time.time(),
}, "AGOTAMIENTO BEARISH")
mostrar(d_agot)

print(f"\n{YLW}→ Score esperado: 85-92. Voz: agotamiento bajista sobre VAH.{RST}")
tick_voz(d_agot)
esperar(8)


# ══════════════════════════════════════════════════════════════════
#  ESCENARIO 4 — SQUEEZE EN AT_POC
#  Compresión en el POC (7135) → narrativa SQUEEZE
# ══════════════════════════════════════════════════════════════════

separador("4. SQUEEZE EN AT_POC (7135)")

# Warmup cerca del POC
for p in [7136.0, 7135.5, 7134.5, 7135.0, 7134.75]:
    procesar_vela({
        "price": p, "high": p+0.5, "low": p-0.5,
        "ask_volume": 420, "bid_volume": 400,
        "volume": 820, "delta": 20, "open": p+0.25,
        "close": p, "trades": 55, "timestamp": time.time(),
    })

# Acumulación sostenida en POC con volumen creciente
for i in range(5):
    precio = 7135.0 + (0.25 if i % 2 == 0 else -0.25)
    d_sq = procesar_vela({
        "price": precio, "high": precio+0.5, "low": precio-0.5,
        "ask_volume": 500 + i*50, "bid_volume": 480 + i*40,
        "volume": 980 + i*90, "delta": 20+i*10,
        "open": precio-0.25, "close": precio,
        "trades": 60+i*5, "timestamp": time.time(),
    }, f"ACUMULACIÓN POC vela {i+1}")
    mostrar(d_sq)

print(f"\n{YLW}→ Si detecta SQUEEZE: voz dice 'Eskuís. Estás en el Point of Control.'{RST}")
tick_voz(d_sq)
esperar(7)


# ══════════════════════════════════════════════════════════════════
#  ESCENARIO 5 — TRAMPA EN AT_VAH
#  Spike alcista que regresa → TRAP_DETECTION debe activarse
# ══════════════════════════════════════════════════════════════════

separador("5. TRAMPA / INDUCCIÓN EN AT_VAH")

# Precio cerca de VAH
for p in [7324.0, 7325.0, 7325.5, 7325.0]:
    procesar_vela({
        "price": p, "high": p+0.5, "low": p-0.5,
        "ask_volume": 300, "bid_volume": 290,
        "volume": 590, "delta": 10, "open": p-0.25,
        "close": p, "trades": 40, "timestamp": time.time(),
    })

# Spike alcista fuerte (stop hunt)
d_spike = procesar_vela({
    "price": 7328.5, "high": 7330.0, "low": 7325.0,
    "ask_volume": 900, "bid_volume": 150,
    "volume": 1050, "delta": 750, "open": 7325.0,
    "close": 7328.5, "trades": 90, "timestamp": time.time(),
}, "SPIKE alcista VAH")
mostrar(d_spike)
esperar(1)

# Reversal inmediato (trampa confirmada)
d_trap = procesar_vela({
    "price": 7323.0, "high": 7328.5, "low": 7322.5,
    "ask_volume": 150, "bid_volume": 750,
    "volume": 900, "delta": -600, "open": 7328.0,
    "close": 7323.0, "trades": 85, "timestamp": time.time(),
}, "REVERSAL — trampa confirmada")
mostrar(d_trap)

print(f"\n{YLW}→ Validator debe detectar TRAP. Voz: 'Trampa. Estás en el Value Area High.'{RST}")
tick_voz(d_trap)
esperar(8)


# ══════════════════════════════════════════════════════════════════
#  ESCENARIO 6 — SETUP REAL: AGOTAMIENTO + AT_VAL
#  El mejor setup del sistema (score ~87)
# ══════════════════════════════════════════════════════════════════

separador("6. SETUP IDEAL: AGOTAMIENTO + AT_VAL (6826)")

# Warmup cerca del VAL
for p in [6828.0, 6827.0, 6826.5, 6827.5, 6826.0]:
    procesar_vela({
        "price": p, "high": p+0.5, "low": p-0.5,
        "ask_volume": 380, "bid_volume": 360,
        "volume": 740, "delta": 20, "open": p+0.25,
        "close": p, "trades": 50, "timestamp": time.time(),
    })

# INTENTO bajista (llegando al VAL)
d_int2 = procesar_vela({
    "price": 6822.0, "high": 6826.5, "low": 6821.5,
    "ask_volume": 220, "bid_volume": 720,
    "volume": 940, "delta": -500, "open": 6826.5,
    "close": 6822.0, "trades": 80, "timestamp": time.time(),
}, "INTENTO bajista al VAL")
mostrar(d_int2)
esperar(1)

# AGOTAMIENTO — precio regresa alcista con delta comprador
d_best = procesar_vela({
    "price": 6828.5, "high": 6829.0, "low": 6821.5,
    "ask_volume": 780, "bid_volume": 180,
    "volume": 960, "delta": 600, "open": 6822.0,
    "close": 6828.5, "trades": 88, "timestamp": time.time(),
}, "AGOTAMIENTO BULLISH en AT_VAL ← MEJOR SETUP")
mostrar(d_best)

print(f"\n{GRN}→ Score esperado: 85-90+. Posible TRADE APROBADO.{RST}")
print(f"{GRN}→ Voz: 'Alta calidad / Agotamiento alcista. Estás en el Value Area Low.'{RST}")
tick_voz(d_best)
esperar(10)


# ══════════════════════════════════════════════════════════════════
#  ESCENARIO 7 — FALLO + RANGO MUERTO
#  Verifica alerta de dead zone
# ══════════════════════════════════════════════════════════════════

separador("7. RANGO MUERTO — dead zone")

# 6 velas sin movimiento
base2 = 7386.0
for i in range(6):
    precio = base2 + (0.0 if i % 2 == 0 else 0.25)
    d_dz = procesar_vela({
        "price": precio, "high": precio+0.25, "low": precio-0.25,
        "ask_volume": 150, "bid_volume": 145,
        "volume": 295, "delta": 5, "open": precio,
        "close": precio, "trades": 20, "timestamp": time.time(),
    }, f"DEAD ZONE vela {i+1}")

mostrar(d_dz)
print(f"\n{YLW}→ Voz esperada: 'Mercado en rango. Baja volatilidad. No operar.'{RST}")
tick_voz(d_dz)
esperar(6)


# ══════════════════════════════════════════════════════════════════
#  RESUMEN FINAL
# ══════════════════════════════════════════════════════════════════

print(f"\n{BOLD}{BLU}{'═'*55}{RST}")
print(f"{BOLD}{BLU}  SIMULACIÓN COMPLETADA{RST}")
print(f"{BOLD}{BLU}{'═'*55}{RST}")
print(f"\n  Escenarios probados:")
print(f"  1. Rango puro ABOVE_VAH     → score bajo, sin voz")
print(f"  2. Micro rango + Breakout   → voz breakout inmediata")
print(f"  3. Agotamiento ABOVE_VAH    → voz agotamiento bearish")
print(f"  4. Squeeze AT_POC           → voz eskuís")
print(f"  5. Trampa AT_VAH            → voz trampa / no operar")
print(f"  6. Agotamiento AT_VAL       → mejor setup, voz completa")
print(f"  7. Dead zone                → voz rango muerto")

print(f"\n  {GRN}Validator stats:{RST}")
vs = validator.stats
print(f"  Pass rate: {vs['pass_rate']}%  "
      f"({vs['validated']}/{vs['total']} validados)")

print(f"\n{BOLD}¿Qué verificar?{RST}")
print(f"  - ¿Sonaron las alertas correctas en cada escenario?")
print(f"  - ¿El escenario 1 NO generó voz (rango puro)?")
print(f"  - ¿El escenario 6 generó la voz más completa?")
print(f"  - ¿El pass rate es mayor a 0%?")
print(f"  - ¿Los scores del escenario 1 son bajos (<50)?")
print()

voice.stop()