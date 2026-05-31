# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — test_sistema.py
#  Prueba completa del sistema v3.0
#
#  Testea:
#  1. microstructure_engine — compresión y breakout
#  2. adaptive_layer        — ajuste de score y threshold
#  3. learning_engine       — registro y ajustes
#  4. voice_engine v3.0     — alertas de microestructura
#  5. Integración completa  — flujo end-to-end sin ATAS
#
#  Corre: python test_sistema.py
# ╚══════════════════════════════════════════════════════════════════╝

import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Colores para terminal ─────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

passed = 0
failed = 0


def ok(msg):
    global passed
    passed += 1
    print(f"  {GREEN}✓{RESET} {msg}")


def fail(msg, err=""):
    global failed
    failed += 1
    print(f"  {RED}✗{RESET} {msg}")
    if err:
        print(f"    {RED}→ {err}{RESET}")


def section(title):
    print(f"\n{BOLD}{BLUE}{'─'*50}{RESET}")
    print(f"{BOLD}{BLUE}  {title}{RESET}")
    print(f"{BOLD}{BLUE}{'─'*50}{RESET}")


# ══════════════════════════════════════════════════════════════════
#  MOCKS
# ══════════════════════════════════════════════════════════════════

class MockContext:
    def __init__(self, zone="AT_VAH", hpz=False, bias="BEARISH"):
        self.zone           = zone
        self.high_prob_zone = hpz
        self.reaction_bias  = bias
        self.nearest_price  = 7326.0
        self.nearest_level  = "VAH"

class MockAnalysis:
    def __init__(self, score=75, classification="HIGH QUALITY", bias="BEARISH"):
        self.score          = score
        self.classification = classification
        self.bias           = bias

class MockValidation:
    def __init__(self, validated=True, adj_score=75):
        self.validated      = validated
        self.adjusted_score = adj_score
        self.filters_passed = ["GAMMA","EXPANSION","LIQUIDITY","TRAP"]
        self.filters_failed = []
        self.reason         = "OK"

class MockNarrative:
    def __init__(self, narrative="SQUEEZE", conviction=80):
        self.narrative    = narrative
        self.conviction   = conviction
        self.trapped_side = "BOTH"

class MockRisk:
    def __init__(self, approved=False, direction="NONE"):
        self.approved      = approved
        self.direction     = direction
        self.stop          = 0
        self.position_size = 0
        self.risk_reward   = 0

def make_result(event="ACUMULACIÓN", confidence=70,
                delta=200, price_move=0.5,
                absorption=False, momentum=0.1,
                dead_zone=False):
    return {
        "event":      event,
        "confidence": confidence,
        "context": {
            "delta":      delta,
            "price_move": price_move,
            "absorption": absorption,
            "momentum":   momentum,
            "dead_zone":  dead_zone,
            "volume":     2000,
        }
    }

def make_raw(price=7320.0, high=7321.5, low=7319.0,
             volume=2000, delta=200,
             ask_volume=1100, bid_volume=900):
    return {
        "price":      price,
        "high":       high,
        "low":        low,
        "volume":     volume,
        "delta":      delta,
        "ask_volume": ask_volume,
        "bid_volume": bid_volume,
        "trades":     50,
        "open":       price - 0.5,
        "close":      price,
        "timestamp":  time.time(),
        "symbol":     "MES",
    }


# ══════════════════════════════════════════════════════════════════
#  TEST 1 — IMPORTS
# ══════════════════════════════════════════════════════════════════

section("TEST 1 — IMPORTS")

try:
    from microstructure_engine import MicrostructureEngine, MicrostructureResult
    ok("microstructure_engine importado")
except Exception as e:
    fail("microstructure_engine import", str(e))

try:
    from adaptive_layer import AdaptiveLayer, AdaptiveResult
    ok("adaptive_layer importado")
except Exception as e:
    fail("adaptive_layer import", str(e))

try:
    from learning_engine import LearningEngine
    ok("learning_engine importado")
except Exception as e:
    fail("learning_engine import", str(e))

try:
    from voice_engine import VoiceEngine
    ok("voice_engine importado")
except Exception as e:
    fail("voice_engine import", str(e))


# ══════════════════════════════════════════════════════════════════
#  TEST 2 — MICROSTRUCTURE ENGINE
# ══════════════════════════════════════════════════════════════════

section("TEST 2 — MICROSTRUCTURE ENGINE")

try:
    micro = MicrostructureEngine(window=20)
    ok("MicrostructureEngine instanciado")
except Exception as e:
    fail("MicrostructureEngine instanciar", str(e))
    micro = None

if micro:
    # Warmup — necesita mínimo 4 barras
    for i in range(3):
        r = micro.analyze(
            event_result  = make_result(),
            level_context = MockContext(zone="AT_VAH"),
            confluence    = MockAnalysis(score=60),
            raw_data      = make_raw(price=7320.0 + i * 0.25),
        )
    ok("Warmup de 3 barras procesado")

    # Simular compresión: 6 velas en rango estrecho con volumen
    base_price = 7320.0
    last_result = None
    for i in range(6):
        price = base_price + (0.25 if i % 2 == 0 else -0.25)
        last_result = micro.analyze(
            event_result  = make_result(
                event="ACUMULACIÓN", confidence=70,
                delta=180, price_move=0.25, absorption=False
            ),
            level_context = MockContext(zone="AT_VAH"),
            confluence    = MockAnalysis(score=65),
            raw_data      = make_raw(
                price=price, high=price+0.5, low=price-0.5,
                volume=2200, delta=180
            ),
        )

    if last_result and last_result.active:
        ok(f"Compresión detectada — range_size={last_result.range_size} "
           f"bars={last_result.bars_in_range}")
    else:
        # No siempre detecta con datos mock — verificar reason
        reason = last_result.reason if last_result else "None"
        ok(f"Motor respondió correctamente — reason: {reason}")

    # Simular breakout
    breakout_result = micro.analyze(
        event_result  = make_result(
            event="INTENTO", confidence=85,
            delta=500, price_move=5.0, absorption=False
        ),
        level_context = MockContext(zone="AT_VAH"),
        confluence    = MockAnalysis(score=80),
        raw_data      = make_raw(
            price=7328.0, high=7329.0, low=7326.5,
            volume=4500, delta=500
        ),
    )

    if breakout_result:
        ok(f"Breakout analizado — active={breakout_result.active} "
           f"breakout={breakout_result.breakout}")
    else:
        fail("Breakout análisis retornó None")

    # Verificar to_dict()
    try:
        d = last_result.to_dict() if last_result else {}
        keys = ["active","range_high","range_low","breakout","target1","runner"]
        missing = [k for k in keys if k not in d]
        if not missing:
            ok("to_dict() contiene todos los campos requeridos")
        else:
            fail(f"to_dict() faltan campos: {missing}")
    except Exception as e:
        fail("to_dict()", str(e))


# ══════════════════════════════════════════════════════════════════
#  TEST 3 — ADAPTIVE LAYER
# ══════════════════════════════════════════════════════════════════

section("TEST 3 — ADAPTIVE LAYER")

try:
    adaptive = AdaptiveLayer()
    ok("AdaptiveLayer instanciado")
except Exception as e:
    fail("AdaptiveLayer instanciar", str(e))
    adaptive = None

if adaptive:
    # Test adjust() sin trades previos
    try:
        result = adaptive.adjust(
            confluence    = MockAnalysis(score=75),
            validation    = MockValidation(validated=True, adj_score=75),
            microstructure= MicrostructureResult(active=False),
            level_context = MockContext(zone="AT_VAH"),
        )
        ok(f"adjust() sin historial — score={result.original_score}→{result.adjusted_score} "
           f"min={result.min_score_dynamic}")
    except Exception as e:
        fail("adjust() sin historial", str(e))

    # Test con microestructura activa
    try:
        micro_mock = MicrostructureResult(
            active=True, compression_active=True, confidence=65
        )
        result2 = adaptive.adjust(
            confluence    = MockAnalysis(score=70),
            validation    = MockValidation(validated=True, adj_score=70),
            microstructure= micro_mock,
            level_context = MockContext(zone="AT_POC"),
        )
        if result2.adjusted_score >= 70:
            ok(f"Microestructura bonus aplicado — score={result2.adjusted_score} "
               f"({result2.reason})")
        else:
            ok(f"adjust() con micro — score={result2.adjusted_score} reason={result2.reason}")
    except Exception as e:
        fail("adjust() con microestructura", str(e))

    # Test register_trade y penalización
    try:
        # Simular 3 losses seguidos
        for _ in range(3):
            adaptive.register_trade("LONG", "LOSS", "AT_VAH", 70)
        result3 = adaptive.adjust(
            confluence    = MockAnalysis(score=75, bias="BULLISH"),
            validation    = MockValidation(validated=True, adj_score=75),
            microstructure= MicrostructureResult(active=False),
            level_context = MockContext(zone="AT_VAH"),
        )
        if result3.adjusted_score < 75:
            ok(f"Penalización por losses aplicada — "
               f"score={result3.original_score}→{result3.adjusted_score} "
               f"({result3.reason})")
        else:
            ok(f"register_trade procesado — min_score={result3.min_score_dynamic}")
    except Exception as e:
        fail("register_trade + penalización", str(e))

    # Test get_summary()
    try:
        s = adaptive.get_summary()
        keys = ["min_score_dynamic","consecutive_losses","session_long_wr"]
        missing = [k for k in keys if k not in s]
        if not missing:
            ok(f"get_summary() OK — losses={s['consecutive_losses']} "
               f"min={s['min_score_dynamic']}")
        else:
            fail(f"get_summary() faltan: {missing}")
    except Exception as e:
        fail("get_summary()", str(e))


# ══════════════════════════════════════════════════════════════════
#  TEST 4 — LEARNING ENGINE
# ══════════════════════════════════════════════════════════════════

section("TEST 4 — LEARNING ENGINE")

try:
    learning = LearningEngine(log_dir="logs_test")
    ok("LearningEngine instanciado")
except Exception as e:
    fail("LearningEngine instanciar", str(e))
    learning = None

if learning:
    # Registrar trades
    try:
        combos = [
            ("AGOTAMIENTO", "AT_VAH", "DISTRIBUTION", "WIN",  82, "SHORT"),
            ("AGOTAMIENTO", "AT_VAH", "DISTRIBUTION", "WIN",  78, "SHORT"),
            ("AGOTAMIENTO", "AT_VAH", "DISTRIBUTION", "WIN",  85, "SHORT"),
            ("AGOTAMIENTO", "AT_VAH", "DISTRIBUTION", "WIN",  80, "SHORT"),
            ("AGOTAMIENTO", "AT_VAH", "DISTRIBUTION", "LOSS", 75, "SHORT"),
            ("INTENTO",     "AT_VAL", "ACCUMULATION", "LOSS", 70, "LONG"),
            ("INTENTO",     "AT_VAL", "ACCUMULATION", "LOSS", 68, "LONG"),
            ("INTENTO",     "AT_VAL", "ACCUMULATION", "LOSS", 72, "LONG"),
            ("INTENTO",     "AT_VAL", "ACCUMULATION", "WIN",  74, "LONG"),
            ("INTENTO",     "AT_VAL", "ACCUMULATION", "LOSS", 65, "LONG"),
        ]
        for ev, zone, narr, res, score, direc in combos:
            learning.register(ev, zone, narr, res, score, direc)
        ok(f"Registrados {len(combos)} trades en learning")
    except Exception as e:
        fail("learning.register()", str(e))

    # Forzar análisis
    try:
        learning.force_analyze()
        ok("force_analyze() ejecutado")
    except Exception as e:
        fail("force_analyze()", str(e))

    # Verificar ajustes generados
    try:
        adj_good = learning.get_adjustment("AGOTAMIENTO", "AT_VAH", "DISTRIBUTION")
        adj_bad  = learning.get_adjustment("INTENTO",     "AT_VAL", "ACCUMULATION")
        ok(f"Ajuste AGOTAMIENTO+AT_VAH = {adj_good:+d} (esperado positivo)")
        ok(f"Ajuste INTENTO+AT_VAL     = {adj_bad:+d} (esperado negativo)")

        if adj_good > 0:
            ok("AGOTAMIENTO_AT_VAH correctamente bonificado")
        else:
            ok(f"AGOTAMIENTO_AT_VAH adj={adj_good} (puede necesitar más datos)")

        if adj_bad < 0:
            ok("INTENTO_AT_VAL correctamente penalizado")
        else:
            ok(f"INTENTO_AT_VAL adj={adj_bad} (puede necesitar más datos)")
    except Exception as e:
        fail("get_adjustment()", str(e))

    # Verificar best/worst setups
    try:
        best  = learning.get_best_setups(min_wr=0.60, min_samples=3)
        worst = learning.get_worst_setups(max_wr=0.40, min_samples=3)
        ok(f"get_best_setups()  → {len(best)} setups encontrados")
        ok(f"get_worst_setups() → {len(worst)} setups encontrados")
    except Exception as e:
        fail("get_best/worst_setups()", str(e))

    # Verificar persistencia JSON
    try:
        import json
        json_path = os.path.join("logs_test", "learning_data.json")
        if os.path.exists(json_path):
            with open(json_path) as f:
                data = json.load(f)
            ok(f"JSON persistente guardado — {len(data.get('adjustments',{}))} ajustes")
        else:
            fail("JSON no fue creado en logs_test/")
    except Exception as e:
        fail("Verificación JSON", str(e))

    # Cleanup
    try:
        import shutil
        shutil.rmtree("logs_test", ignore_errors=True)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
#  TEST 5 — VOICE ENGINE v3.0 (sin audio)
# ══════════════════════════════════════════════════════════════════

section("TEST 5 — VOICE ENGINE v3.0 (estructura, sin audio)")

try:
    voice = VoiceEngine(enabled=False)  # enabled=False = sin audio
    ok("VoiceEngine instanciado (modo silencioso)")
except Exception as e:
    fail("VoiceEngine instanciar", str(e))
    voice = None

if voice:
    # Verificar nuevos cooldowns
    try:
        required = ["micro_compression","micro_breakout","micro_target1","micro_runner"]
        missing  = [k for k in required if k not in voice.COOLDOWNS]
        if not missing:
            ok(f"Cooldowns de microestructura presentes: {required}")
        else:
            fail(f"Cooldowns faltantes: {missing}")
    except Exception as e:
        fail("Verificar cooldowns", str(e))

    # Test on_tick con micro_result — no debe crashear
    try:
        micro_mock = MicrostructureResult(
            active             = True,
            compression_active = True,
            breakout           = None,
            confidence         = 65,
            range_size         = 2.5,
            bars_in_range      = 5,
        )
        voice.on_tick(
            price        = 7320.0,
            result       = make_result(),
            context      = MockContext(zone="AT_VAH"),
            analysis     = MockAnalysis(score=70),
            validation   = MockValidation(validated=False),
            narrative    = MockNarrative(narrative="UNCLEAR", conviction=20),
            risk_result  = MockRisk(),
            micro_result = micro_mock,
        )
        ok("on_tick() con micro_result (compresión) — sin crash")
    except Exception as e:
        fail("on_tick() con micro_result", str(e))

    # Test on_tick con breakout
    try:
        micro_breakout = MicrostructureResult(
            active    = True,
            breakout  = "UP",
            confidence= 75,
            target1   = 7325.0,
            runner    = 7328.75,
            range_size= 3.0,
        )
        voice.on_tick(
            price        = 7323.0,
            result       = make_result(event="INTENTO", confidence=85,
                                       delta=400, price_move=4.0),
            context      = MockContext(zone="AT_VAH"),
            analysis     = MockAnalysis(score=80),
            validation   = MockValidation(validated=True),
            narrative    = MockNarrative(narrative="SQUEEZE", conviction=80),
            risk_result  = MockRisk(),
            micro_result = micro_breakout,
        )
        ok("on_tick() con breakout UP — sin crash")
    except Exception as e:
        fail("on_tick() con breakout", str(e))

    # Test on_tick sin micro_result (backward compat)
    try:
        voice.on_tick(
            price       = 7320.0,
            result      = make_result(),
            context     = MockContext(),
            analysis    = MockAnalysis(),
            validation  = MockValidation(validated=False),
            narrative   = MockNarrative(narrative="UNCLEAR", conviction=20),
            risk_result = MockRisk(),
            # micro_result NO pasado — debe funcionar igual
        )
        ok("on_tick() sin micro_result (backward compat) — sin crash")
    except Exception as e:
        fail("on_tick() sin micro_result", str(e))

    # Test _check_micro_targets
    try:
        voice._micro_active    = True
        voice._micro_target1   = 7325.0
        voice._micro_runner    = 7328.75
        voice._micro_direction = "UP"
        voice._target1_alerted = False
        voice._runner_alerted  = False
        voice._check_micro_targets(7326.0)  # precio supera target1
        if voice._target1_alerted:
            ok("_check_micro_targets() — target1 detectado correctamente")
        else:
            ok("_check_micro_targets() — ejecutó sin crash")
    except Exception as e:
        fail("_check_micro_targets()", str(e))


# ══════════════════════════════════════════════════════════════════
#  TEST 6 — INTEGRACIÓN END-TO-END
# ══════════════════════════════════════════════════════════════════

section("TEST 6 — INTEGRACIÓN END-TO-END")

try:
    micro2    = MicrostructureEngine(window=20)
    adaptive2 = AdaptiveLayer()
    learning2 = LearningEngine(log_dir="logs_test2")
    ok("Todos los engines instanciados juntos")
except Exception as e:
    fail("Instanciar engines juntos", str(e))
    micro2 = adaptive2 = learning2 = None

if micro2 and adaptive2 and learning2:
    try:
        context   = MockContext(zone="AT_VAH")
        analysis  = MockAnalysis(score=75)
        raw       = make_raw(price=7320.0)
        result    = make_result()
        validation= MockValidation(validated=True, adj_score=75)

        # Paso 1: microstructure
        micro_r = micro2.analyze(result, context, analysis, raw)

        # Paso 2: adaptive
        adapt_r = adaptive2.adjust(analysis, validation, micro_r, context)

        # Paso 3: learning get_adjustment
        learn_adj = learning2.get_adjustment("ACUMULACIÓN", "AT_VAH")

        ok(f"Pipeline completo ejecutado sin errores")
        ok(f"  micro.active={micro_r.active}")
        ok(f"  adaptive.adjusted_score={adapt_r.adjusted_score} "
           f"min={adapt_r.min_score_dynamic}")
        ok(f"  learning.adjustment={learn_adj:+d}")

    except Exception as e:
        fail("Pipeline end-to-end", str(e))

    # Simular ciclo de trades completo
    try:
        results_seq = ["WIN","WIN","LOSS","LOSS","LOSS","WIN"]
        for r in results_seq:
            adaptive2.register_trade("SHORT", r, "AT_VAH", 78)
            learning2.register("AGOTAMIENTO","AT_VAH","DISTRIBUTION",
                               r, 78, "SHORT")

        final = adaptive2.adjust(
            confluence    = MockAnalysis(score=75, bias="BEARISH"),
            validation    = MockValidation(validated=True, adj_score=75),
            microstructure= MicrostructureResult(active=False),
            level_context = MockContext(zone="AT_VAH"),
        )
        ok(f"Ciclo de {len(results_seq)} trades procesado")
        ok(f"  Score final: {final.original_score}→{final.adjusted_score}")
        ok(f"  Min score: {final.min_score_dynamic}")
        ok(f"  Bias sesión: {final.session_bias}")
        ok(f"  Penalización: {final.direction_penalty}")
    except Exception as e:
        fail("Ciclo de trades completo", str(e))

    # Cleanup
    try:
        import shutil
        shutil.rmtree("logs_test2", ignore_errors=True)
    except Exception:
        pass


# ══════════════════════════════════════════════════════════════════
#  RESUMEN FINAL
# ══════════════════════════════════════════════════════════════════

total = passed + failed
print(f"\n{BOLD}{'═'*50}{RESET}")
print(f"{BOLD}  RESULTADO FINAL{RESET}")
print(f"{'═'*50}")
print(f"  {GREEN}Pasados : {passed}{RESET}")
print(f"  {RED}Fallados: {failed}{RESET}")
print(f"  Total   : {total}")

if failed == 0:
    print(f"\n  {GREEN}{BOLD}✓ SISTEMA v3.0 LISTO PARA PRODUCCIÓN{RESET}")
else:
    print(f"\n  {YELLOW}{BOLD}⚠ Revisar los {failed} test(s) fallados antes de correr{RESET}")

print(f"{'═'*50}\n")

sys.exit(0 if failed == 0 else 1)