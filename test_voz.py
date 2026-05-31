# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — test_voz.py
#  Prueba secuencial de todas las alertas del sistema
#  Corre: python test_voz.py
# ╚══════════════════════════════════════════════════════════════════╝

import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from voice_engine import VoiceEngine

# ── Objetos mock para simular el sistema ──────────────────────────

class MockResult:
    def __init__(self, event="ACUMULACIÓN", confidence=80,
                 delta=200, price_move=2.5, absorption=False,
                 momentum=0.5, dead_zone=False):
        self._data = {
            "event":      event,
            "confidence": confidence,
            "context": {
                "delta":      delta,
                "price_move": price_move,
                "absorption": absorption,
                "momentum":   momentum,
                "dead_zone":  dead_zone,
                "volume":     1000,
            }
        }
    def get(self, key, default=None):
        return self._data.get(key, default)

class MockContext:
    def __init__(self, zone="AT_VAH", hpz=False, bias="BEARISH"):
        self.zone           = zone
        self.high_prob_zone = hpz
        self.reaction_bias  = bias
        self.nearest_price  = 7326.0
        self.nearest_level  = "VAH"

class MockAnalysis:
    def __init__(self, score=85, classification="HIGH QUALITY", bias="BEARISH"):
        self.score          = score
        self.classification = classification
        self.bias           = bias

class MockNarrative:
    def __init__(self, narrative="UNCLEAR", conviction=0):
        self.narrative    = narrative
        self.conviction   = conviction
        self.trapped_side = "NONE"

class MockValidation:
    def __init__(self, validated=True, filters_passed=None):
        self.validated      = validated
        self.filters_passed = filters_passed or ["GAMMA","EXPANSION","LIQUIDITY","TRAP"]
        self.filters_failed = []
        self.adjusted_score = 85

class MockRisk:
    def __init__(self, approved=False, direction="NONE",
                 stop=0, size=0, rr=0):
        self.approved      = approved
        self.direction     = direction
        self.stop          = stop
        self.position_size = size
        self.risk_reward   = rr

class MockTrade:
    def __init__(self, result="WIN", pnl=8.5, direction="LONG"):
        self.result    = result
        self.pnl_pts   = pnl
        self.direction = direction


# ── Helpers ───────────────────────────────────────────────────────

def esperar(s=4):
    time.sleep(s)

def sep(titulo):
    print(f"\n{'─'*50}")
    print(f"  🔊 {titulo}")
    print('─'*50)


# ── MAIN ──────────────────────────────────────────────────────────

def run_tests():
    print("\n╔══════════════════════════════════════╗")
    print("  GIBBZ — PRUEBA DE ALERTAS DE VOZ")
    print("╚══════════════════════════════════════╝\n")

    voice = VoiceEngine(enabled=True)
    voice._tts.start()

    sep("SALUDO INICIAL")
    voice._tts.say_blocking(
        "Hola May, te saluda tu asistente de operaciones, Guibs. "
        "Sistema institucional listo."
    )
    esperar(2)

    sep("1. TRADE APROBADO — LARGO")
    voice.on_tick(
        price=7326.0,
        result=MockResult(delta=300, price_move=4.0),
        context=MockContext(zone="AT_VAH", bias="BULLISH"),
        analysis=MockAnalysis(score=88, classification="HIGH QUALITY", bias="BULLISH"),
        narrative=MockNarrative(narrative="SQUEEZE", conviction=85),
        validation=MockValidation(validated=True),
        risk_result=MockRisk(approved=True, direction="LONG",
                             stop=7318.0, size=1.0, rr=2.5),
    )
    esperar(7)

    sep("2. SETUP INSTITUCIONAL GRADE")
    voice._last_spoken.clear()
    voice.on_tick(
        price=7135.0,
        result=MockResult(delta=450, price_move=5.0),
        context=MockContext(zone="AT_POC", bias="BULLISH"),
        analysis=MockAnalysis(score=92, classification="INSTITUTIONAL GRADE", bias="BULLISH"),
        narrative=MockNarrative(narrative="ACCUMULATION", conviction=88),
        validation=MockValidation(validated=True),
        risk_result=MockRisk(),
    )
    esperar(8)

    sep("3. ESQUÍS — COMPRADORES GANANDO")
    voice._last_spoken.clear()
    voice._last_squeeze_dir = None
    voice.on_tick(
        price=7320.0,
        result=MockResult(delta=350, price_move=3.5, absorption=False),
        context=MockContext(zone="IN_VALUE_AREA", bias="BULLISH"),
        analysis=MockAnalysis(score=78, classification="HIGH QUALITY"),
        narrative=MockNarrative(narrative="SQUEEZE", conviction=80),
        validation=MockValidation(validated=False),
        risk_result=MockRisk(),
    )
    esperar(8)

    sep("4. ESQUÍS — ABSORBIDO / DOJI")
    voice._last_spoken.clear()
    voice._last_squeeze_dir = None
    voice.on_tick(
        price=7320.0,
        result=MockResult(delta=400, price_move=0.5, absorption=True),
        context=MockContext(zone="AT_VAH", bias="BEARISH"),
        analysis=MockAnalysis(score=75, classification="HIGH QUALITY"),
        narrative=MockNarrative(narrative="SQUEEZE", conviction=82),
        validation=MockValidation(validated=False),
        risk_result=MockRisk(),
    )
    esperar(8)

    sep("5. ESQUÍS — VENDEDORES GANANDO")
    voice._last_spoken.clear()
    voice._last_squeeze_dir = None
    voice.on_tick(
        price=7310.0,
        result=MockResult(delta=-380, price_move=-4.0, absorption=False),
        context=MockContext(zone="ABOVE_VAH", bias="BEARISH"),
        analysis=MockAnalysis(score=80, classification="HIGH QUALITY", bias="BEARISH"),
        narrative=MockNarrative(narrative="SQUEEZE", conviction=85),
        validation=MockValidation(validated=False),
        risk_result=MockRisk(),
    )
    esperar(8)

    sep("6. FALLO DE ESQUÍS — COMPRADORES NO PUDIERON")
    voice._last_spoken.clear()
    voice._last_squeeze_dir = "BUY"
    voice._squeeze_bars     = 2
    voice.on_tick(
        price=7318.0,
        result=MockResult(delta=-250, price_move=-3.0, absorption=False),
        context=MockContext(zone="AT_VAH", bias="BEARISH"),
        analysis=MockAnalysis(score=70, classification="MEDIUM QUALITY", bias="BEARISH"),
        narrative=MockNarrative(narrative="INDUCTION", conviction=75),
        validation=MockValidation(validated=False),
        risk_result=MockRisk(),
    )
    esperar(8)

    sep("7. TRAMPA INSTITUCIONAL")
    voice._last_spoken.clear()
    voice._last_squeeze_dir = None
    voice.on_tick(
        price=7340.0,
        result=MockResult(delta=-200, price_move=3.5),
        context=MockContext(zone="ABOVE_VAH", bias="BEARISH"),
        analysis=MockAnalysis(score=72, classification="HIGH QUALITY", bias="BEARISH"),
        narrative=MockNarrative(narrative="INDUCTION", conviction=78),
        validation=MockValidation(validated=False),
        risk_result=MockRisk(),
    )
    esperar(8)

    sep("8. ZONA DE ALTA PROBABILIDAD HPZ")
    voice._last_spoken.clear()
    voice.on_tick(
        price=7135.0,
        result=MockResult(delta=180, price_move=1.0),
        context=MockContext(zone="AT_POC", hpz=True, bias="BULLISH"),
        analysis=MockAnalysis(score=68, classification="HIGH QUALITY", bias="BULLISH"),
        narrative=MockNarrative(narrative="ACCUMULATION", conviction=60),
        validation=MockValidation(validated=False),
        risk_result=MockRisk(),
    )
    esperar(8)

    sep("9. ALTA CALIDAD VALIDADA")
    voice._last_spoken.clear()
    voice.on_tick(
        price=7326.0,
        result=MockResult(delta=-300, price_move=-4.5),
        context=MockContext(zone="AT_VAH", bias="BEARISH"),
        analysis=MockAnalysis(score=82, classification="HIGH QUALITY", bias="BEARISH"),
        narrative=MockNarrative(narrative="DISTRIBUTION", conviction=70),
        validation=MockValidation(validated=True),
        risk_result=MockRisk(),
    )
    esperar(8)

    sep("10. AGOTAMIENTO BAJISTA")
    voice._last_spoken.clear()
    voice.on_tick(
        price=7345.0,
        result=MockResult(event="AGOTAMIENTO", confidence=82,
                          delta=-150, price_move=-3.5),
        context=MockContext(zone="ABOVE_VAH", bias="BEARISH"),
        analysis=MockAnalysis(score=65, classification="MEDIUM QUALITY", bias="BEARISH"),
        narrative=MockNarrative(narrative="REBALANCE", conviction=65),
        validation=MockValidation(validated=False),
        risk_result=MockRisk(),
    )
    esperar(8)

    sep("11. INTENTO ALCISTA EN AT_VAL")
    voice._last_spoken.clear()
    voice.on_tick(
        price=6826.0,
        result=MockResult(event="INTENTO", confidence=80,
                          delta=280, price_move=4.0),
        context=MockContext(zone="AT_VAL", bias="BULLISH"),
        analysis=MockAnalysis(score=70, classification="HIGH QUALITY", bias="BULLISH"),
        narrative=MockNarrative(narrative="ACCUMULATION", conviction=65),
        validation=MockValidation(validated=False),
        risk_result=MockRisk(),
    )
    esperar(8)

    sep("12. RANGO MUERTO — MERCADO NO OPERABLE")
    voice._last_spoken.clear()
    voice.on_tick(
        price=7320.0,
        result=MockResult(event="ACUMULACIÓN", confidence=40,
                          delta=20, price_move=0.25, dead_zone=True),
        context=MockContext(zone="IN_VALUE_AREA", bias="NEUTRAL"),
        analysis=MockAnalysis(score=30, classification="LOW QUALITY", bias="NEUTRAL"),
        narrative=MockNarrative(narrative="UNCLEAR", conviction=20),
        validation=MockValidation(validated=False, filters_passed=[]),
        risk_result=MockRisk(),
    )
    esperar(7)

    sep("13. SIN INTENCIÓN INSTITUCIONAL")
    voice._last_spoken.clear()
    voice.on_tick(
        price=7315.0,
        result=MockResult(event="ACUMULACIÓN", confidence=30,
                          delta=10, price_move=0.5, dead_zone=False),
        context=MockContext(zone="IN_VALUE_AREA", bias="NEUTRAL"),
        analysis=MockAnalysis(score=28, classification="LOW QUALITY", bias="NEUTRAL"),
        narrative=MockNarrative(narrative="UNCLEAR", conviction=25),
        validation=MockValidation(validated=False, filters_passed=[]),
        risk_result=MockRisk(),
    )
    esperar(7)

    sep("14. TRADE GANADOR")
    voice.on_trade_closed(MockTrade(result="WIN", pnl=10.0, direction="LONG"))
    esperar(6)

    sep("15. STOP ALCANZADO")
    voice.on_trade_closed(MockTrade(result="LOSS", pnl=-4.0, direction="SHORT"))
    esperar(6)

    sep("16. SESIÓN NY OPEN INICIADA")
    voice.on_session_start("NY_OPEN_KILLZONE")
    esperar(5)

    sep("17. SESIÓN CERRADA")
    voice.on_session_end()
    esperar(5)

    print("\n╔══════════════════════════════════════╗")
    print("  PRUEBA COMPLETADA — 17 alertas")
    print("╚══════════════════════════════════════╝\n")

    voice.stop()


if __name__ == "__main__":
    run_tests()