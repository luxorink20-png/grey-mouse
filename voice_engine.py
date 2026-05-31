# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — voice_engine.py
#  Institutional Voice Preemption System v4.1
#
#  BACKEND: playsound (MP3 nativo) → winsound fallback
#
#  INTERRUPCIÓN:
#  threading.Event (_stop_event) señala al thread que pare.
#  Cola con prioridad — evento urgente salta la fila.
#
#  PRIORIDADES:
#  100 = trade_approved/closed   → interrumpe todo
#   95 = micro_target1/runner    → interrumpe todo
#   90 = micro_breakout          → interrumpe todo
#   80 = squeeze                 → interrumpe si > actual
#   75 = induction               → interrumpe si > actual
#   40 = high_quality            → espera
#   30 = hpz                     → espera
#
#  MODOS DE VOZ:
#  ultra → 2-4 palabras (breakout, targets)
#  short → mensaje conciso
#  long  → análisis completo
# ╚══════════════════════════════════════════════════════════════════╝

import threading
import queue
import time
import asyncio
import os
import sys
from typing import Optional
from log_config import get_logger

_log = get_logger("voice_engine")


# ══════════════════════════════════════════════════════════════════
#  PRIORIDADES
# ══════════════════════════════════════════════════════════════════

INTERRUPT_PRIORITY = {
    "trade_approved":    100,
    "trade_closed":       95,
    "micro_target1":      95,
    "micro_runner":       95,
    "micro_breakout":     90,
    "squeeze_fallo":      82,
    "squeeze":            80,
    "induction":          75,
    "institutional":      70,
    "agotamiento":        60,
    "intento":            50,
    "setup_signal":       45,
    "high_quality":       40,
    "hpz":                30,
    "micro_compression":  25,
    "session_start":      20,
    "session_end":        20,
    "dead_zone":          10,
    "no_intent":          10,
    "status":              5,
}

INTERRUPT_THRESHOLD = 75


# ══════════════════════════════════════════════════════════════════
#  MENSAJE CON PRIORIDAD
# ══════════════════════════════════════════════════════════════════

class PriorityMessage:
    def __init__(self, text: str, priority: int, event_type: str = ""):
        self.text       = text
        self.priority   = priority
        self.event_type = event_type
        self.timestamp  = time.time()

    def __lt__(self, other):
        return self.priority > other.priority


# ══════════════════════════════════════════════════════════════════
#  TTS ENGINE v4.1
# ══════════════════════════════════════════════════════════════════

class TTSEngine:

    VOICE   = "es-MX-JorgeNeural"
    TMP_DIR = os.path.dirname(os.path.abspath(__file__))

    def __init__(self):
        self._queue            = queue.PriorityQueue()
        self._thread           = None
        self._running          = False
        self._ready            = False
        self._counter          = 0
        self._current_priority = 0
        self._stop_event       = threading.Event()
        self._speak_lock       = threading.Lock()
        self._backend          = "none"
        self._edge_ready       = False
        self._check()

    def _check(self) -> None:
        try:
            import edge_tts
            self._edge_ready = True
        except ImportError:
            print("[VOICE] edge_tts no disponible")
            self._ready = False
            return

        # Backend 1: playsound — reproduce MP3 directamente ✅
        try:
            from playsound import playsound
            self._backend = "playsound"
            self._ready   = True
            return
        except ImportError:
            pass

        # Backend 2: winsound — solo WAV, fallback limitado
        if sys.platform == "win32":
            try:
                import winsound
                self._backend = "winsound"
                self._ready   = True
                return
            except ImportError:
                pass

        print("[VOICE] Sin backend de audio disponible")
        self._ready = False

    def start(self) -> None:
        if not self._ready:
            return
        self._running = True
        self._thread  = threading.Thread(
            target = self._loop,
            daemon = True,
            name   = "GibbzTTS"
        )
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        self._stop_current_audio()

    def say(self, text: str, priority: int = 0,
            event_type: str = "") -> None:
        if not self._ready:
            return

        msg = PriorityMessage(text, priority, event_type)

        if priority >= INTERRUPT_THRESHOLD:
            if priority > self._current_priority:
                self._stop_current_audio()
                self._clear_queue_below(priority)

        self._queue.put(msg)

    def say_blocking(self, text: str) -> None:
        if not self._ready:
            return
        tmp = self._tmp_path()
        try:
            self._generate_tts(text, tmp)
            self._play_file(tmp)
        except Exception as e:
            print("[VOICE ERROR] " + str(e))
        finally:
            self._cleanup(tmp)

    # ──────────────────────────────────────────────────────────────
    #  INTERRUPCIÓN
    # ──────────────────────────────────────────────────────────────

    def _stop_current_audio(self) -> None:
        self._stop_event.set()
        self._current_priority = 0

    def _clear_queue_below(self, min_priority: int) -> None:
        kept = []
        try:
            while True:
                msg = self._queue.get_nowait()
                if msg.priority >= min_priority:
                    kept.append(msg)
        except queue.Empty:
            pass
        for msg in kept:
            self._queue.put(msg)

    # ──────────────────────────────────────────────────────────────
    #  LOOP PRINCIPAL
    # ──────────────────────────────────────────────────────────────

    def _loop(self) -> None:
        while self._running:
            try:
                msg = self._queue.get(timeout=0.3)
                self._stop_event.clear()
                self._current_priority = msg.priority
                self._speak_sync(msg.text)
                self._current_priority = 0
            except queue.Empty:
                continue
            except Exception as e:
                _log.error("loop error: %s", e)
                self._current_priority = 0

    def _speak_sync(self, text: str) -> None:
        tmp = self._tmp_path()
        try:
            self._generate_tts(text, tmp)
            if not self._stop_event.is_set():
                self._play_file(tmp)
        except Exception as e:
            print("[VOICE ERROR] " + str(e))
        finally:
            self._cleanup(tmp)

    # ──────────────────────────────────────────────────────────────
    #  GENERACIÓN TTS
    # ──────────────────────────────────────────────────────────────

    def _generate_tts(self, text: str, path: str) -> None:
        import edge_tts
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        async def _gen():
            tts = edge_tts.Communicate(text, voice=self.VOICE)
            await tts.save(path)
        loop.run_until_complete(_gen())
        loop.close()

    # ──────────────────────────────────────────────────────────────
    #  REPRODUCCIÓN
    # ──────────────────────────────────────────────────────────────

    def _play_file(self, path: str) -> None:
        if not os.path.exists(path):
            return
        if self._stop_event.is_set():
            return

        if self._backend == "playsound":
            self._play_playsound(path)
        elif self._backend == "winsound":
            self._play_winsound(path)

    def _play_playsound(self, path: str) -> None:
        try:
            from playsound import playsound
            playsound(path)
        except Exception as e:
            print(f"[VOICE playsound] {e}")

    def _play_winsound(self, path: str) -> None:
        """Fallback — winsound solo reproduce WAV."""
        try:
            import winsound
            flags    = winsound.SND_FILENAME | winsound.SND_ASYNC
            winsound.PlaySound(path, flags)

            try:
                file_size      = os.path.getsize(path)
                estimated_secs = max(1.0, file_size / 8000)
            except Exception:
                estimated_secs = 5.0

            deadline = time.time() + estimated_secs + 1.0
            while time.time() < deadline:
                if self._stop_event.is_set():
                    winsound.PlaySound(None, winsound.SND_PURGE)
                    return
                time.sleep(0.05)

            winsound.PlaySound(None, winsound.SND_PURGE)
        except Exception as e:
            print(f"[VOICE winsound] {e}")

    # ──────────────────────────────────────────────────────────────
    #  HELPERS
    # ──────────────────────────────────────────────────────────────

    def _tmp_path(self) -> str:
        self._counter += 1
        return os.path.join(
            self.TMP_DIR,
            f"_gibbz_{self._counter}.mp3"
        )

    @staticmethod
    def _cleanup(path: str) -> None:
        try:
            if path and os.path.exists(path):
                os.remove(path)
        except Exception:
            pass

    @property
    def is_speaking(self) -> bool:
        return self._current_priority > 0

    @property
    def current_priority(self) -> int:
        return self._current_priority


# ══════════════════════════════════════════════════════════════════
#  VOICE ENGINE v4.1
# ══════════════════════════════════════════════════════════════════

class VoiceEngine:

    MOVE_THRESHOLD = 1.5

    COOLDOWNS = {
        "trade_approved":    0,
        "trade_closed":      0,
        "hpz":              30,
        "high_quality":     20,
        "institutional":     0,
        "squeeze":          15,
        "squeeze_fallo":    10,
        "induction":        15,
        "session_start":     0,
        "session_end":       0,
        "intento":          10,
        "agotamiento":      10,
        "dead_zone":        30,
        "no_intent":        20,
        "status":            0,
        "setup_signal":     30,
        "micro_compression":20,
        "micro_breakout":    0,
        "micro_target1":     0,
        "micro_runner":      0,
    }

    VOICE_MODE = {
        "trade_approved":    "long",
        "trade_closed":      "short",
        "micro_breakout":    "ultra",
        "micro_target1":     "ultra",
        "micro_runner":      "ultra",
        "squeeze":           "short",
        "squeeze_fallo":     "short",
        "induction":         "short",
        "institutional":     "long",
        "agotamiento":       "short",
        "intento":           "short",
        "high_quality":      "long",
        "hpz":               "short",
        "micro_compression": "short",
        "dead_zone":         "short",
        "no_intent":         "short",
        "setup_signal":      "short",
        "session_start":     "short",
        "session_end":       "short",
        "status":            "long",
    }

    def __init__(self, enabled: bool = True):
        self.enabled             = enabled
        self._tts                = TTSEngine()
        self._last_spoken: dict  = {}
        self._muted              = False
        self._tick_count         = 0
        self._last_squeeze_dir   = None
        self._squeeze_bars       = 0
        self._micro_active       = False
        self._micro_target1      = 0.0
        self._micro_runner       = 0.0
        self._micro_direction    = None
        self._target1_alerted    = False
        self._runner_alerted     = False

    def start(self) -> None:
        if not self.enabled:
            return
        self._tts.start()
        self._tts.say_blocking(
            "Hola May, te saluda tu asistente de operaciones, Guibs. "
            "Sistema institucional listo."
        )

    def stop(self) -> None:
        self._tts.stop()

    def mute(self) -> None:
        self._muted = True

    def unmute(self) -> None:
        self._muted = False
        self._emit("status", "Voz activada.")

    # ──────────────────────────────────────────────────────────────
    #  MAIN TICK HANDLER
    # ──────────────────────────────────────────────────────────────

    def on_tick(self, price, result, context, analysis,
                validation, narrative, risk_result,
                micro_result=None) -> None:
        if not self.enabled or self._muted:
            return

        self._tick_count += 1

        event          = result.get("event",      "NONE") if result else "NONE"
        confidence     = result.get("confidence", 0)      if result else 0
        ctx            = result.get("context",    {})     if result else {}
        delta          = ctx.get("delta",      0)
        price_move     = ctx.get("price_move", 0)
        absorption     = ctx.get("absorption", False)
        dead_zone      = ctx.get("dead_zone",  False)

        zone           = getattr(context,    "zone",           "")
        hpz            = getattr(context,    "high_prob_zone", False)
        score          = getattr(analysis,   "score",          0)
        classification = getattr(analysis,   "classification", "")
        bias           = getattr(analysis,   "bias",           "NEUTRAL")
        narr           = getattr(narrative,  "narrative",      "UNCLEAR")
        conviction     = getattr(narrative,  "conviction",     0)
        validated      = getattr(validation, "validated",      False)
        filters_passed = getattr(validation, "filters_passed", [])
        risk_approved  = getattr(risk_result,"approved",       False)
        direction      = getattr(risk_result,"direction",      "")
        stop           = getattr(risk_result,"stop",           0)
        size           = getattr(risk_result,"position_size",  0)
        rr             = getattr(risk_result,"risk_reward",    0)

        zone_str = self._zone_to_speech(zone)
        bias_str = self._bias_to_speech(bias)

        # ── P1: TRADE APROBADO ────────────────────────────────────
        if risk_approved and direction != "NONE":
            dir_str  = "largo" if direction == "LONG" else "corto"
            long_msg = (
                f"Trade aprobado. {dir_str}. "
                f"Tamaño {size} porciento. "
                f"Stop en {stop}. "
                f"Ratio uno a {rr}."
            )
            self._emit_if_ready("trade_approved", long_msg)
            return

        # ── P2: INSTITUTIONAL GRADE ───────────────────────────────
        if classification == "INSTITUTIONAL GRADE" and validated:
            long_msg  = (
                f"Setup institucional. {zone_str}. "
                f"Sesgo {bias_str}. Score {score}. Máxima atención."
            )
            short_msg = f"Institucional. {zone_str}. {bias_str}."
            self._emit_if_ready("institutional", long_msg, short_msg)
            return

        # ── P3: MICROESTRUCTURA ───────────────────────────────────
        if micro_result is not None:
            micro_active   = getattr(micro_result, "active",             False)
            micro_breakout = getattr(micro_result, "breakout",           None)
            micro_compress = getattr(micro_result, "compression_active", False)
            micro_conf     = getattr(micro_result, "confidence",         0)
            micro_t1       = getattr(micro_result, "target1",            0.0)
            micro_runner   = getattr(micro_result, "runner",             0.0)
            micro_rsize    = getattr(micro_result, "range_size",         0.0)
            micro_bars     = getattr(micro_result, "bars_in_range",      0)

            if micro_active and micro_breakout is not None and micro_conf >= 60:
                dir_break  = "al alza" if micro_breakout == "UP" else "a la baja"
                ultra_msg  = "Breakout. Expansión en curso."
                short_msg  = f"Breakout {dir_break}. Objetivo {round(micro_t1, 2)}."
                self._micro_active    = True
                self._micro_target1   = micro_t1
                self._micro_runner    = micro_runner
                self._micro_direction = micro_breakout
                self._target1_alerted = False
                self._runner_alerted  = False
                self._emit("micro_breakout", ultra_msg, short_msg)
                return

            if micro_active and micro_compress and micro_conf >= 55:
                short_msg = (
                    f"Micro rango. {zone_str}. "
                    f"{micro_bars} velas. Posible expansión."
                )
                long_msg = (
                    f"Micro rango detectado. {zone_str}. "
                    f"Rango de {round(micro_rsize, 2)} puntos. "
                    f"{micro_bars} velas en compresión. "
                    f"Posible expansión inminente."
                )
                self._emit_if_ready("micro_compression", long_msg, short_msg)

            if self._micro_active and self._micro_target1 > 0:
                self._check_micro_targets(price)

        # ── P4: ESQUÍS ────────────────────────────────────────────
        if narr == "SQUEEZE" and conviction >= 70:
            quien, squeeze_dir = self._evaluar_fuerza(delta, price_move, absorption)
            self._last_squeeze_dir = squeeze_dir
            self._squeeze_bars     = 1
            short_msg = f"Eskuís. {zone_str}. {quien}."
            long_msg  = (
                f"Alerta. Eskuís detectado. {zone_str}. "
                f"Ruptura inminente. {quien}."
            )
            self._emit_if_ready("squeeze", long_msg, short_msg)
            return

        # ── P5: FALLO DE ESQUÍS ───────────────────────────────────
        if self._last_squeeze_dir is not None:
            self._squeeze_bars += 1
            fallo = self._detectar_fallo_squeeze(
                delta, price_move, absorption, self._last_squeeze_dir
            )
            if fallo:
                dir_fallida = "compradores" if self._last_squeeze_dir == "BUY" else "vendedores"
                self._last_squeeze_dir = None
                self._squeeze_bars     = 0
                short_msg = f"Fallo. {dir_fallida} rechazados. Reversal."
                long_msg  = (
                    f"Fallo de eskuís. Los {dir_fallida} no pudieron mover el precio. "
                    f"{zone_str}. Posible reversal."
                )
                self._emit_if_ready("squeeze_fallo", long_msg, short_msg)
                return
            if self._squeeze_bars > 5:
                self._last_squeeze_dir = None
                self._squeeze_bars     = 0

        # ── P6: TRAMPA ────────────────────────────────────────────
        if narr == "INDUCTION" and conviction >= 70:
            short_msg = f"Trampa. {zone_str}. No operar."
            long_msg  = (
                f"Trampa institucional detectada. {zone_str}. "
                f"Retail siendo inducido. No operar."
            )
            self._emit_if_ready("induction", long_msg, short_msg)
            return

        # ── P7: HPZ ───────────────────────────────────────────────
        if hpz and score >= 60:
            short_msg = f"Alta probabilidad. {zone_str}. {bias_str}."
            long_msg  = (
                f"Zona de alta probabilidad activa. {zone_str}. "
                f"Sesgo {bias_str}. Score {score}."
            )
            self._emit_if_ready("hpz", long_msg, short_msg)
            return

        # ── P8: ALTA CALIDAD VALIDADA ─────────────────────────────
        if classification == "HIGH QUALITY" and validated:
            short_msg = f"Alta calidad. {zone_str}. {bias_str}."
            long_msg  = (
                f"Alta calidad. {zone_str}. "
                f"Sesgo {bias_str}. Score {score}. "
                f"Filtros pasados: {len(filters_passed)} de 4."
            )
            self._emit_if_ready("high_quality", long_msg, short_msg)
            return

        # ── P9: AGOTAMIENTO ───────────────────────────────────────
        if event == "AGOTAMIENTO" and confidence >= 70:
            dir_agot  = "alcista" if price_move > 0 else "bajista"
            short_msg = f"Agotamiento {dir_agot}. {zone_str}."
            long_msg  = (
                f"Agotamiento {dir_agot}. {zone_str}. "
                f"Momentum se detiene. Posible reversal."
            )
            self._emit_if_ready("agotamiento", long_msg, short_msg)
            return

        # ── P10: INTENTO EN ZONA CLAVE ────────────────────────────
        if event == "INTENTO" and confidence >= 75 and zone in (
            "AT_VAL", "AT_VAH", "AT_POC", "BELOW_VAL", "ABOVE_VAH"
        ):
            dir_str   = "alcista" if delta > 0 else "bajista"
            short_msg = f"Intento {dir_str}. {zone_str}."
            long_msg  = (
                f"Intento {dir_str}. {zone_str}. "
                f"Confirmación pendiente."
            )
            self._emit_if_ready("intento", long_msg, short_msg)
            return

        # ── P11: RANGO MUERTO ─────────────────────────────────────
        if dead_zone:
            self._emit_if_ready(
                "dead_zone",
                "Mercado en rango. Baja volatilidad. No operar.",
                "Rango muerto. No operar."
            )
            return

        # ── P12: SIN INTENCIÓN ────────────────────────────────────
        if (narr == "UNCLEAR"
                and conviction < 40
                and not validated
                and score < 40):
            self._emit_if_ready(
                "no_intent",
                "Sin intención institucional. Evitar operar.",
                "Sin intención. Esperar."
            )
            return

    # ──────────────────────────────────────────────────────────────
    #  TRACKING MICRO TARGETS
    # ──────────────────────────────────────────────────────────────

    def _check_micro_targets(self, price: float) -> None:
        if not self._micro_active:
            return
        direction = self._micro_direction

        if not self._target1_alerted and self._micro_target1 > 0:
            hit = (
                (direction == "UP"   and price >= self._micro_target1) or
                (direction == "DOWN" and price <= self._micro_target1)
            )
            if hit:
                self._target1_alerted = True
                self._emit(
                    "micro_target1",
                    f"Objetivo uno. {round(self._micro_target1, 2)}. Parciales.",
                )
                return

        if self._target1_alerted and not self._runner_alerted and self._micro_runner > 0:
            hit = (
                (direction == "UP"   and price >= self._micro_runner) or
                (direction == "DOWN" and price <= self._micro_runner)
            )
            if hit:
                self._runner_alerted = True
                self._micro_active   = False
                self._emit(
                    "micro_runner",
                    "Runner activo. Extensión confirmada.",
                )

    # ──────────────────────────────────────────────────────────────
    #  TRADE / SESSION
    # ──────────────────────────────────────────────────────────────

    def on_trade_closed(self, trade) -> None:
        if not self.enabled or self._muted:
            return
        result    = getattr(trade, "result",   "UNKNOWN")
        pnl       = getattr(trade, "pnl_pts",  0.0)
        direction = getattr(trade, "direction", "")
        dir_str   = "largo" if direction == "LONG" else "corto"
        if result == "WIN":
            self._emit("trade_closed",
                f"Ganador. {dir_str}. Más {round(pnl, 2)} puntos.")
        elif result == "LOSS":
            self._emit("trade_closed",
                f"Stop. {dir_str}. Menos {round(abs(pnl), 2)} puntos.")
        elif result == "TIMEOUT":
            self._emit("trade_closed", "Trade cerrado por tiempo.")

    def on_session_start(self, session_name: str) -> None:
        if not self.enabled:
            return
        name = session_name.replace("_", " ").lower()
        self._emit("session_start", f"Sesión {name} iniciada.")

    def on_session_end(self) -> None:
        if not self.enabled:
            return
        self._emit("session_end", "Sesión cerrada. Zona muerta.")

    def on_setup_signal(self, setup_result, env_name: str = "ROTATIONAL") -> None:
        """Shadow router alert. Fires at most once per cooldown when a setup is active."""
        if not self.enabled or self._muted:
            return
        stype = getattr(setup_result, "signal_type", "NO_SETUP")
        sdir  = getattr(setup_result, "direction",   "NEUTRAL")
        sconf = getattr(setup_result, "confidence",  0)
        if stype in ("NO_SETUP", "INSTITUTIONAL_GRADE"):
            return
        dir_str = "largo" if sdir == "LONG" else "corto"
        if stype == "VA80_SETUP":
            long_msg  = f"Setup ochenta por ciento. {dir_str}. Confianza {sconf}."
            short_msg = f"Ochenta. {dir_str}."
        elif stype == "FA_SETUP":
            long_msg  = f"Failed Auction {dir_str}. Confianza {sconf}."
            short_msg = f"Failed Auction. {dir_str}."
        else:
            long_msg  = f"Setup {stype}. {dir_str}."
            short_msg = long_msg
        self._emit_if_ready("setup_signal", long_msg, short_msg)

    def speak_status(self, price, analysis, context, feedback) -> None:
        zone     = getattr(context,  "zone",   "desconocida")
        score    = getattr(analysis, "score",  0)
        wins     = getattr(feedback, "wins",   0)
        losses   = getattr(feedback, "losses", 0)
        zone_str = self._zone_to_speech(zone)
        self._emit(
            "status",
            f"Precio {round(price, 2)}. {zone_str}. "
            f"Score {score}. {wins} ganados, {losses} perdidos."
        )

    # ──────────────────────────────────────────────────────────────
    #  EMIT
    # ──────────────────────────────────────────────────────────────

    def _emit(self, event_type: str,
              long_text: str,
              short_text: str = "") -> None:
        if not self.enabled or self._muted:
            return
        priority = INTERRUPT_PRIORITY.get(event_type, 0)
        mode     = self.VOICE_MODE.get(event_type, "short")
        text     = self._select_text(mode, long_text, short_text)
        self._tts.say(text, priority=priority, event_type=event_type)

    def _emit_if_ready(self, event_type: str,
                       long_text: str,
                       short_text: str = "") -> None:
        if not self.enabled or self._muted:
            return
        cooldown = self.COOLDOWNS.get(event_type, 15)
        now      = time.time()
        last     = self._last_spoken.get(event_type, 0)
        if now - last < cooldown:
            return
        self._last_spoken[event_type] = now
        self._emit(event_type, long_text, short_text)

    def _select_text(self, mode: str,
                     long_text: str,
                     short_text: str) -> str:
        if mode in ("ultra", "short"):
            return short_text if short_text else long_text
        return long_text

    # ──────────────────────────────────────────────────────────────
    #  MAPPINGS
    # ──────────────────────────────────────────────────────────────

    def _zone_to_speech(self, zone: str) -> str:
        mapping = {
            "ABOVE_VAH":     "Estás por encima del Value Area High",
            "AT_VAH":        "Estás en el Value Area High",
            "IN_VALUE_AREA": "Estás dentro del Value Area",
            "AT_POC":        "Estás en el Point of Control",
            "AT_VAL":        "Estás en el Value Area Low",
            "BELOW_VAL":     "Estás por debajo del Value Area Low",
            "UNKNOWN":       "Zona desconocida",
        }
        return mapping.get(zone, zone.replace("_", " ").lower())

    def _bias_to_speech(self, bias: str) -> str:
        return {
            "BULLISH": "alcista",
            "BEARISH": "bajista",
            "NEUTRAL": "neutral",
        }.get(bias, "neutral")

    # ──────────────────────────────────────────────────────────────
    #  FUERZA EN SQUEEZE
    # ──────────────────────────────────────────────────────────────

    def _evaluar_fuerza(self, delta: float, price_move: float,
                        absorption: bool) -> tuple:
        precio_quieto       = abs(price_move) <= self.MOVE_THRESHOLD
        confirma_compras    = delta > 0 and price_move >  self.MOVE_THRESHOLD
        confirma_ventas     = delta < 0 and price_move < -self.MOVE_THRESHOLD
        rechaza_compradores = delta > 0 and price_move < -self.MOVE_THRESHOLD
        rechaza_vendedores  = delta < 0 and price_move >  self.MOVE_THRESHOLD

        if absorption or precio_quieto:
            if delta > 0:
                return ("compradores presionando, precio absorbido", "BUY")
            elif delta < 0:
                return ("vendedores presionando, precio absorbido", "SELL")
            else:
                return ("fuerza neutral", None)

        if confirma_compras:    return ("compradores ganando", "BUY")
        if confirma_ventas:     return ("vendedores ganando", "SELL")
        if rechaza_compradores: return ("compradores rechazados", "SELL")
        if rechaza_vendedores:  return ("vendedores rechazados", "BUY")
        return ("fuerza neutral", None)

    def _detectar_fallo_squeeze(self, delta: float, price_move: float,
                                absorption: bool, last_dir: str) -> bool:
        if last_dir == "BUY":
            return (delta < -100
                    or price_move < -self.MOVE_THRESHOLD
                    or (absorption and price_move <= 0))
        if last_dir == "SELL":
            return (delta > 100
                    or price_move > self.MOVE_THRESHOLD
                    or (absorption and price_move >= 0))
        return False