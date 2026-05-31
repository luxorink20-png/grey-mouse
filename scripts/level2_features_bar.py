"""
scripts/level2_features_bar.py
Level 2 Proxy Features derivadas de datos de barra (tick agregados).

Limitacion honesta: NO tenemos order book real. Los features son PROXIES
derivados de ask_volume/bid_volume/delta/volume/range por barra.
Correlation con Level 2 real: alta para imbalances, moderada para icebergs,
baja para liquidity walls individuales.

Cada tick en las grabaciones de GIBBZ ya contiene:
  price, open, high, low, close, volume, ask_volume, bid_volume, delta, trades

Los features implementados replican logica de ATAS con los datos disponibles.
"""

from __future__ import annotations

import json
import sys
import os
import math
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ocie_stacked_imbalance import StackedImbalanceEngine, BarSnapshot, ImbalanceZone
from bar_aggregator import BarAggregator

# ── Thresholds (definiciones objetivas, NO optimizadas) ────────────────────
_VOLUME_SPIKE_RATIO    = 2.5   # volumen > 2.5x media → anomalo
_RANGE_COMPRESS_RATIO  = 0.40  # rango < 0.40x media → comprimido
_ABSORPTION_THRESHOLD  = 0.60  # |delta / volume| < 0.60 con precio moviendo = absorcion
_IMBALANCE_RATIO_MIN   = 3.0   # ask/bid o bid/ask >= 3.0 → imbalance significativo
_ROLLING_WINDOW        = 20    # barras para rolling stats
_PROXIMITY_TICKS       = 8     # ticks de proximidad para zona de imbalance


@dataclass
class BarL2Features:
    """Features de Level 2 para una barra especifica."""
    bar_idx:            int
    price:              float
    volume:             float
    delta:              float
    range_pts:          float

    # Rolling context
    vol_ratio:          float   # volume / rolling_avg_volume
    range_ratio:        float   # range / rolling_avg_range
    imbalance_ratio:    float   # max(ask/bid, bid/ask)
    imbalance_dir:      str     # "BUY" | "SELL" | "NEUTRAL"

    # Proxy detections
    is_iceberg_proxy:   bool    # high volume, compressed range
    is_absorption:      bool    # delta contradicts price direction
    is_strong_imbalance:bool    # ask_vol or bid_vol >> other

    def __repr__(self) -> str:
        flags = []
        if self.is_iceberg_proxy:   flags.append("ICEBERG")
        if self.is_absorption:      flags.append("ABSORB")
        if self.is_strong_imbalance: flags.append(f"IMBAL({self.imbalance_dir})")
        return (f"BarL2[{self.bar_idx}] price={self.price} "
                f"vol_r={self.vol_ratio:.2f} range_r={self.range_ratio:.2f} "
                f"flags={flags}")


def extract_raw_bars(recording_path: Path, max_bars: int = 4000) -> list[dict]:
    """
    Extrae barras de 500 ticks de un archivo JSONL de grabacion.
    Independiente del pipeline completo de run_session().
    Cada barra tiene: bar, price, open, high, low, volume, ask_volume, bid_volume, delta.
    """
    agg = BarAggregator(mode="TICK", ticks=500)
    bars = []
    bar_count = 0
    try:
        with open(recording_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if bar_count >= max_bars:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    tick = json.loads(line)
                except Exception:
                    continue
                bar = agg.process(tick)
                if bar is None:
                    continue
                bar_count += 1
                bars.append({
                    "bar":        bar_count,
                    "price":      float(bar.get("price",      0)),
                    "open":       float(bar.get("open",       bar.get("price", 0))),
                    "high":       float(bar.get("high",       bar.get("price", 0))),
                    "low":        float(bar.get("low",        bar.get("price", 0))),
                    "close":      float(bar.get("close",      bar.get("price", 0))),
                    "volume":     float(bar.get("volume",     0)),
                    "ask_volume": float(bar.get("ask_volume", 0)),
                    "bid_volume": float(bar.get("bid_volume", 0)),
                    "delta":      float(bar.get("delta",      0)),
                    "trades":     int(bar.get("trades",       0)),
                })
    except Exception:
        pass
    return bars


def compute_l2_features(raw_bars: list[dict]) -> dict[int, BarL2Features]:
    """
    Computa features de Level 2 para cada barra de la sesion.

    Args:
        raw_bars: lista de dicts con bar, price, volume, delta, ask_volume, bid_volume, high, low

    Returns:
        dict bar_idx → BarL2Features
    """
    vol_deque   = deque(maxlen=_ROLLING_WINDOW)
    range_deque = deque(maxlen=_ROLLING_WINDOW)

    # Initialize stacked imbalance engine
    si_engine = StackedImbalanceEngine(
        min_ratio=_IMBALANCE_RATIO_MIN,
        min_stack=3,
    )

    features: dict[int, BarL2Features] = {}

    for raw in raw_bars:
        bar_idx = raw["bar"]
        volume  = raw["volume"]
        delta   = raw["delta"]
        ask_vol = raw["ask_volume"]
        bid_vol = raw["bid_volume"]
        high    = raw["high"]
        low     = raw["low"]
        price   = raw["price"]
        open_p  = raw["open"]

        bar_range = max(high - low, 0.001)

        # Rolling stats
        avg_vol   = sum(vol_deque)   / len(vol_deque)   if vol_deque   else (volume or 1.0)
        avg_range = sum(range_deque) / len(range_deque) if range_deque else (bar_range or 1.0)

        vol_ratio   = volume    / max(avg_vol,   0.001)
        range_ratio = bar_range / max(avg_range, 0.001)

        # Imbalance ratio
        if bid_vol > 0 and ask_vol > 0:
            imb_ratio = max(ask_vol / bid_vol, bid_vol / ask_vol)
            imb_dir   = "BUY" if ask_vol >= bid_vol else "SELL"
        elif ask_vol > 0:
            imb_ratio = _IMBALANCE_RATIO_MIN * 2
            imb_dir   = "BUY"
        elif bid_vol > 0:
            imb_ratio = _IMBALANCE_RATIO_MIN * 2
            imb_dir   = "SELL"
        else:
            imb_ratio = 1.0
            imb_dir   = "NEUTRAL"

        # Iceberg proxy: large volume + compressed range
        is_iceberg = (
            vol_ratio   >= _VOLUME_SPIKE_RATIO and
            range_ratio <= _RANGE_COMPRESS_RATIO and
            len(vol_deque) >= 5  # need warming period
        )

        # Absorption: delta contradicts price direction
        price_went_up   = price > open_p + 0.25
        price_went_down = price < open_p - 0.25
        delta_pct = delta / max(volume, 0.001)
        is_absorption = (
            (price_went_up   and delta_pct < -_ABSORPTION_THRESHOLD) or
            (price_went_down and delta_pct >  _ABSORPTION_THRESHOLD)
        )

        # Strong imbalance
        is_strong_imbalance = imb_ratio >= _IMBALANCE_RATIO_MIN

        features[bar_idx] = BarL2Features(
            bar_idx            = bar_idx,
            price              = price,
            volume             = volume,
            delta              = delta,
            range_pts          = bar_range,
            vol_ratio          = round(vol_ratio,   2),
            range_ratio        = round(range_ratio, 2),
            imbalance_ratio    = round(imb_ratio,   2),
            imbalance_dir      = imb_dir,
            is_iceberg_proxy   = is_iceberg,
            is_absorption      = is_absorption,
            is_strong_imbalance= is_strong_imbalance,
        )

        # Update stacked imbalance engine with BarSnapshot
        snapshot = BarSnapshot(
            bar_index  = bar_idx,
            open       = open_p,
            high       = high,
            low        = low,
            close      = price,
            volume     = volume,
            ask_volume = ask_vol,
            bid_volume = bid_vol,
            delta      = delta,
        )
        si_engine.update_bar(snapshot)

        vol_deque.append(volume)
        range_deque.append(bar_range)

    # Annotate stacked imbalance zones onto the features
    # (zones from the engine are stored internally; expose active zones)
    try:
        for zone in si_engine.active_zones:
            # Find bars near the zone midpoint
            for bar_idx, feat in features.items():
                if abs(feat.price - zone.midpoint) <= _PROXIMITY_TICKS * 0.25:
                    # Update imbalance direction from institutional zone
                    if zone.direction == "BUY" and feat.imbalance_dir != "BUY":
                        feat.imbalance_dir = "BUY"
                    elif zone.direction == "SELL" and feat.imbalance_dir != "SELL":
                        feat.imbalance_dir = "SELL"
    except AttributeError:
        pass  # si_engine may not expose active_zones directly

    return features


def l2_filter_decision(
    trade_direction: str,
    entry_bar: int,
    features: dict[int, BarL2Features],
    window: int = 5,
) -> tuple[bool, str]:
    """
    Decide si saltar un trade basado en features de Level 2.

    Filosofia de filtro (conservadora):
    - SKIP solo si hay evidencia CONVERGENTE de multiples proxies opuestos
    - NO skip si solo un proxy dispara (demasiado ruido con n pequeno)
    - NO skip si features apoyan la direccion del trade

    Args:
        trade_direction: "LONG" o "SHORT"
        entry_bar: numero de barra de entrada del trade
        features: dict bar_idx → BarL2Features para la sesion
        window: barras antes/despues del entry a analizar

    Returns:
        (should_skip, reason_str)
    """
    opposing_signals = 0
    supporting_signals = 0
    reasons = []

    for bar_offset in range(-window, 2):
        bar_idx = entry_bar + bar_offset
        feat = features.get(bar_idx)
        if feat is None:
            continue

        if trade_direction == "LONG":
            # Para un LONG, queremos BUY imbalance y absorcion de vendedores
            if feat.is_iceberg_proxy and feat.imbalance_dir == "SELL":
                opposing_signals += 1
                reasons.append(f"iceberg SELL en bar {bar_idx} ({feat.vol_ratio:.1f}x vol)")
            if feat.is_absorption and feat.delta < 0:
                # Precio subio pero delta negativo = sellers absorbiendo
                opposing_signals += 1
                reasons.append(f"absorcion vendedora en bar {bar_idx}")
            if feat.is_strong_imbalance and feat.imbalance_dir == "BUY":
                supporting_signals += 1
            if feat.is_absorption and feat.delta > 0:
                supporting_signals += 1

        else:  # SHORT
            # Para un SHORT, queremos SELL imbalance y absorcion de compradores
            if feat.is_iceberg_proxy and feat.imbalance_dir == "BUY":
                opposing_signals += 1
                reasons.append(f"iceberg BUY en bar {bar_idx} ({feat.vol_ratio:.1f}x vol)")
            if feat.is_absorption and feat.delta > 0:
                # Precio bajo pero delta positivo = buyers absorbiendo
                opposing_signals += 1
                reasons.append(f"absorcion compradora en bar {bar_idx}")
            if feat.is_strong_imbalance and feat.imbalance_dir == "SELL":
                supporting_signals += 1
            if feat.is_absorption and feat.delta < 0:
                supporting_signals += 1

    # Conservador: skip solo si hay 2+ signals opuestos convergentes SIN signals de apoyo
    should_skip = (opposing_signals >= 2 and supporting_signals == 0)
    reason = ("; ".join(reasons) if should_skip else "")
    return should_skip, reason


def session_l2_summary(features: dict[int, BarL2Features]) -> dict:
    """Resumen de actividad L2 en una sesion."""
    n = len(features)
    if n == 0:
        return {"n_bars": 0}
    icebergs   = sum(1 for f in features.values() if f.is_iceberg_proxy)
    absorptions= sum(1 for f in features.values() if f.is_absorption)
    imbalances = sum(1 for f in features.values() if f.is_strong_imbalance)
    buy_imb    = sum(1 for f in features.values() if f.is_strong_imbalance and f.imbalance_dir == "BUY")
    sell_imb   = sum(1 for f in features.values() if f.is_strong_imbalance and f.imbalance_dir == "SELL")
    return {
        "n_bars":         n,
        "icebergs":       icebergs,
        "icebergs_pct":   round(100 * icebergs / n, 1),
        "absorptions":    absorptions,
        "absorptions_pct":round(100 * absorptions / n, 1),
        "imbalances":     imbalances,
        "buy_imbalances": buy_imb,
        "sell_imbalances":sell_imb,
    }
