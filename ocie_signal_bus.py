# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ OCIE — ocie_signal_bus.py
#  Signal Bus v1.0
#
#  Centraliza todas las señales del pipeline y las emite:
#  → A ATAS via UDP (para visualización)
#  → Al archivo de log (para analytics)
#  → Al pipeline interno (para decisiones)
#
#  DISEÑO:
#  - Python calcula todo
#  - ATAS recibe JSON y solo renderiza
#  - El chart NUNCA piensa
# ╚══════════════════════════════════════════════════════════════════╝

import json
import socket
import time
import os
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from collections import deque


# ══════════════════════════════════════════════════════════════════
#  SIGNAL SCHEMA
# ══════════════════════════════════════════════════════════════════

@dataclass
class OcieSignal:
    """
    Señal estructurada unificada.
    Este es el contrato entre Python y ATAS.
    """
    # Identidad
    signal_id:    str
    timestamp:    float
    price:        float

    # Tipo de señal
    signal_type:  str   # ENTRY | IMBALANCE | REGIME | POC | LEVEL | INFO

    # Dirección y fuerza
    direction:    str   # LONG | SHORT | NEUTRAL
    strength:     int   # 0-100

    # Contexto institucional
    regime:       str   # session regime
    breakout:     str   # REAL | EXPLOSIVE | WEAK | MODERATE
    environment:  str   # EFFICIENT_TREND | ROTATIONAL | etc.

    # Niveles para visualización
    entry:        float = 0.0
    stop:         float = 0.0
    target:       float = 0.0
    zone_top:     float = 0.0
    zone_bottom:  float = 0.0

    # Labels para el chart
    label:        str   = ""
    sublabel:     str   = ""
    color:        str   = "#64FF00"   # verde institucional

    # Confluence breakdown
    score:        int   = 0
    dp:           int   = 0
    acc:          int   = 0
    eff:          float = 0.0
    cont_prob:    int   = 0
    poc_score:    int   = 0
    absorption:   int   = 0

    # Flags
    is_entry:         bool = False
    is_imbalance:     bool = False
    is_regime_change: bool = False
    is_poc_event:     bool = False

    def to_dict(self) -> dict:
        return {
            "id":          self.signal_id,
            "ts":          self.timestamp,
            "price":       self.price,
            "type":        self.signal_type,
            "direction":   self.direction,
            "strength":    self.strength,
            "regime":      self.regime,
            "breakout":    self.breakout,
            "environment": self.environment,
            "entry":       self.entry,
            "stop":        self.stop,
            "target":      self.target,
            "zone_top":    self.zone_top,
            "zone_bot":    self.zone_bottom,
            "label":       self.label,
            "sublabel":    self.sublabel,
            "color":       self.color,
            "score":       self.score,
            "dp":          self.dp,
            "acc":         self.acc,
            "eff":         self.eff,
            "cont":        self.cont_prob,
            "poc":         self.poc_score,
            "absorb":      self.absorption,
            "is_entry":    self.is_entry,
            "is_imb":      self.is_imbalance,
            "is_regime":   self.is_regime_change,
            "is_poc":      self.is_poc_event,
        }

    def to_json(self) -> bytes:
        return json.dumps(self.to_dict()).encode("utf-8")


# ══════════════════════════════════════════════════════════════════
#  SIGNAL BUS
# ══════════════════════════════════════════════════════════════════

class OcieSignalBus:
    """
    Bus central de señales OCIE.

    Recibe señales de todos los módulos del pipeline y las distribuye:
    1. UDP → ATAS (visualización)
    2. Log file → analytics
    3. Internal history → para decisiones del engine

    ATAS recibe JSON y renderiza marcadores/zonas en el chart.
    Python mantiene el estado — el chart solo muestra.
    """

    # Puerto separado del feed (9999) para no mezclar canales
    ATAS_SIGNAL_HOST = "127.0.0.1"
    ATAS_SIGNAL_PORT = 9998

    def __init__(self, log_dir: str = "logs",
                 send_to_atas: bool = True,
                 log_signals:  bool = True):
        self._send_to_atas = send_to_atas
        self._log_signals  = log_signals
        self._signal_count = 0

        # Socket UDP para enviar a ATAS
        self._sock = None
        if send_to_atas:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # Log file
        self._log_path = None
        if log_signals:
            os.makedirs(log_dir, exist_ok=True)
            ts = time.strftime("%Y-%m-%d_%H%M")
            self._log_path = os.path.join(log_dir, f"ocie_signals_{ts}.jsonl")
            self._log_file = open(self._log_path, "w", encoding="utf-8")

        # Historial interno (últimas 200 señales)
        self._history: deque = deque(maxlen=200)
        self._last_regime    = ""
        self._last_entry_ts  = 0.0

    # ──────────────────────────────────────────────────────────────
    #  EMIT — punto de entrada principal
    # ──────────────────────────────────────────────────────────────

    def emit(self, signal: OcieSignal):
        """Distribuye la señal a todos los destinos."""
        self._signal_count += 1
        self._history.append(signal)

        payload = signal.to_json()

        # → ATAS via UDP
        if self._send_to_atas and self._sock:
            try:
                self._sock.sendto(
                    payload,
                    (self.ATAS_SIGNAL_HOST, self.ATAS_SIGNAL_PORT)
                )
            except Exception:
                pass

        # → Log file
        if self._log_signals and self._log_path:
            try:
                self._log_file.write(payload.decode("utf-8") + "\n")
                self._log_file.flush()
            except Exception:
                pass

    # ──────────────────────────────────────────────────────────────
    #  SIGNAL BUILDERS — constructores por tipo
    # ──────────────────────────────────────────────────────────────

    def emit_entry(self, price: float, direction: str,
                   entry: float, stop: float, target: float,
                   score: int, regime: str, breakout: str,
                   environment: str, dp: int, acc: int,
                   eff: float, cont_prob: int, poc_score: int) -> OcieSignal:
        """Señal de entrada institucional."""
        label = f"{'▲ LONG' if direction == 'LONG' else '▼ SHORT'} | {breakout}"
        sublabel = f"Score:{score} | {regime}"
        color = "#00FF88" if direction == "LONG" else "#FF4444"

        s = OcieSignal(
            signal_id    = f"ENTRY_{self._signal_count:05d}",
            timestamp    = time.time(),
            price        = price,
            signal_type  = "ENTRY",
            direction    = direction,
            strength     = score,
            regime       = regime,
            breakout     = breakout,
            environment  = environment,
            entry        = entry,
            stop         = stop,
            target       = target,
            label        = label,
            sublabel     = sublabel,
            color        = color,
            score        = score,
            dp           = dp,
            acc          = acc,
            eff          = eff,
            cont_prob    = cont_prob,
            poc_score    = poc_score,
            is_entry     = True,
        )
        self.emit(s)
        self._last_entry_ts = s.timestamp
        return s

    def emit_imbalance(self, zone_top: float, zone_bottom: float,
                       direction: str, strength: int,
                       price: float, absorbed: bool = False) -> OcieSignal:
        """Señal de Stacked Imbalance zona."""
        label = f"{'BUY' if direction == 'BUY' else 'SELL'} IMB ×{strength}"
        color = "#64FF00" if direction == "BUY" else "#FF6464"
        if absorbed:
            label += " [ABSORBED]"
            color  = "#888888"

        s = OcieSignal(
            signal_id    = f"IMB_{self._signal_count:05d}",
            timestamp    = time.time(),
            price        = price,
            signal_type  = "IMBALANCE",
            direction    = direction,
            strength     = min(strength * 10, 100),
            regime       = "",
            breakout     = "",
            environment  = "",
            zone_top     = zone_top,
            zone_bottom  = zone_bottom,
            label        = label,
            color        = color,
            is_imbalance = True,
        )
        self.emit(s)
        return s

    def emit_regime_change(self, price: float,
                            new_regime: str, confidence: int,
                            environment: str) -> OcieSignal:
        """Señal de cambio de régimen."""
        if new_regime == self._last_regime:
            return None
        self._last_regime = new_regime

        colors = {
            "TREND_DAY":      "#00DDFF",
            "SHORT_COVERING": "#FF9900",
            "BALANCED_DAY":   "#AAAAAA",
            "EXPANSION_DAY":  "#FF4400",
            "LIQUIDATION":    "#FF0000",
        }
        color = colors.get(new_regime, "#FFFFFF")

        s = OcieSignal(
            signal_id         = f"REGIME_{self._signal_count:05d}",
            timestamp         = time.time(),
            price             = price,
            signal_type       = "REGIME",
            direction         = "NEUTRAL",
            strength          = confidence,
            regime            = new_regime,
            breakout          = "",
            environment       = environment,
            label             = f"◈ {new_regime}",
            sublabel          = f"conf={confidence}%",
            color             = color,
            is_regime_change  = True,
        )
        self.emit(s)
        return s

    def emit_poc_event(self, price: float,
                       poc_state: str, absorption: int,
                       trap: bool, auction_fail: bool) -> OcieSignal:
        """Señal de evento POC (absorción, trampa, fallo de subasta)."""
        if not (absorption >= 60 or trap or auction_fail):
            return None

        label = f"POC: {poc_state}"
        if trap:           label += " [TRAP]"
        if auction_fail:   label += " [FAIL]"
        if absorption >= 70: label += " [ABSORB]"
        color = "#FF6600" if (trap or auction_fail) else "#FFAA00"

        s = OcieSignal(
            signal_id    = f"POC_{self._signal_count:05d}",
            timestamp    = time.time(),
            price        = price,
            signal_type  = "POC",
            direction    = "NEUTRAL",
            strength     = absorption,
            regime       = "",
            breakout     = "",
            environment  = "",
            label        = label,
            color        = color,
            absorption   = absorption,
            is_poc_event = True,
        )
        self.emit(s)
        return s

    # ──────────────────────────────────────────────────────────────
    #  UTILS
    # ──────────────────────────────────────────────────────────────

    def get_recent_signals(self, signal_type: str = "",
                            n: int = 10) -> list:
        signals = list(self._history)
        if signal_type:
            signals = [s for s in signals
                       if s.signal_type == signal_type]
        return signals[-n:]

    def close(self):
        if self._sock:
            try: self._sock.close()
            except: pass
        if self._log_signals and hasattr(self, "_log_file"):
            try: self._log_file.close()
            except: pass

    @property
    def total_emitted(self) -> int:
        return self._signal_count