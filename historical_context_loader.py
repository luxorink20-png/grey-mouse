"""
GIBBZ Historical Context Loader
Carga automáticamente los niveles institucionales correctos
para una fecha de replay específica.

Flujo:
  replay_feed.py detecta fecha del .jsonl
      ↓
  HistoricalContextLoader.load(date)
      ↓
  Busca historical_context/YYYY-MM-DD.json
      ↓
  Si existe → retorna niveles correctos
  Si no existe → aborta con warning crítico
"""

import json
import os
from datetime import datetime, date
from dataclasses import dataclass
from typing import Optional


CONTEXT_DIR = "historical_context"


@dataclass
class SessionContext:
    date:               str
    # Volume Profile
    vah:                float
    poc:                float
    val:                float
    # SpotGamma
    call_wall:          float
    put_wall:           float
    zero_gamma:         float
    volatility_trigger: float
    hpz:                float
    # Session
    prev_high:          float
    prev_low:           float
    prev_close:         float
    open_price:         float
    onh:                float
    onl:                float
    ibh:                float
    ibl:                float
    # Extra gamma levels
    combo_1:            float = 0.0
    combo_2:            float = 0.0
    large_gamma_1:      float = 0.0
    large_gamma_2:      float = 0.0
    large_gamma_3:      float = 0.0
    large_gamma_4:      float = 0.0

    def to_dict(self) -> dict:
        return self.__dict__

    def print_summary(self):
        print("─── HISTORICAL CONTEXT: " + self.date + " ──────────────")
        print("  VAH=" + str(self.vah) +
              "  POC=" + str(self.poc) +
              "  VAL=" + str(self.val))
        print("  Call Wall=" + str(self.call_wall) +
              "  Put Wall=" + str(self.put_wall))
        print("  Zero Gamma=" + str(self.zero_gamma) +
              "  Vol Trigger=" + str(self.volatility_trigger))
        print("  HPZ=" + str(self.hpz) +
              "  ONH=" + str(self.onh) +
              "  ONL=" + str(self.onl))
        print("  PDH=" + str(self.prev_high) +
              "  PDL=" + str(self.prev_low))
        print("────────────────────────────────────────────")


class HistoricalContextLoader:

    def __init__(self, context_dir: str = CONTEXT_DIR):
        self.context_dir = context_dir
        os.makedirs(context_dir, exist_ok=True)

    # ── API PÚBLICA ────────────────────────────────────────────────

    def load(self, replay_date: str) -> SessionContext:
        """
        Carga el contexto histórico para una fecha.
        replay_date: 'YYYY-MM-DD'
        Lanza RuntimeError si el archivo no existe.
        """
        self._validate_date_format(replay_date)
        path = self._get_path(replay_date)

        if not os.path.exists(path):
            self._abort_missing(replay_date, path)

        with open(path, encoding="utf-8") as f:
            raw = json.load(f)

        self._validate_integrity(raw, replay_date)
        ctx = self._parse(raw)
        ctx.print_summary()
        return ctx

    def load_from_file(self, jsonl_path: str) -> SessionContext:
        """
        Detecta la fecha del archivo .jsonl y carga el contexto.
        El archivo debe tener formato: YYYY-MM-DD_HHMM.jsonl
        """
        filename  = os.path.basename(jsonl_path)
        date_part = filename[:10]   # 'YYYY-MM-DD'
        try:
            datetime.strptime(date_part, "%Y-%m-%d")
        except ValueError:
            raise RuntimeError(
                "\n╔══ CONTEXT ERROR ══════════════════════════╗"
                "\n  No se puede detectar fecha del archivo:"
                "\n  " + jsonl_path +
                "\n  Formato esperado: YYYY-MM-DD_HHMM.jsonl"
                "\n╚═══════════════════════════════════════════╝"
            )
        print("  Replay date detectado : " + date_part)
        return self.load(date_part)

    def save(self, ctx: SessionContext):
        """Guarda un contexto histórico en disco."""
        path = self._get_path(ctx.date)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._to_file_format(ctx), f, indent=2)
        print("  Context guardado: " + path)

    def exists(self, replay_date: str) -> bool:
        return os.path.exists(self._get_path(replay_date))

    def list_available(self):
        files = sorted([
            f.replace(".json", "")
            for f in os.listdir(self.context_dir)
            if f.endswith(".json")
        ])
        print("  Contextos históricos disponibles:")
        for d in files:
            print("    → " + d)
        return files

    # ── INTERNOS ──────────────────────────────────────────────────

    def _get_path(self, d: str) -> str:
        return os.path.join(self.context_dir, d + ".json")

    def _validate_date_format(self, d: str):
        try:
            datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            raise RuntimeError("Fecha inválida: " + d + " (esperado YYYY-MM-DD)")

    def _validate_integrity(self, raw: dict, expected_date: str):
        file_date = raw.get("date", "")
        if file_date != expected_date:
            raise RuntimeError(
                "\n╔══ INTEGRITY ERROR ════════════════════════╗"
                "\n  Fecha del replay    : " + expected_date +
                "\n  Fecha del contexto  : " + file_date +
                "\n  ¡MISMATCH CRÍTICO! Abortando replay."
                "\n╚═══════════════════════════════════════════╝"
            )

    def _abort_missing(self, replay_date: str, path: str):
        raise RuntimeError(
            "\n╔══ MISSING HISTORICAL CONTEXT ═════════════╗"
            "\n  Replay date  : " + replay_date +
            "\n  Archivo      : " + path +
            "\n"
            "\n  ¡REPLAY ABORTADO!"
            "\n  Crear el archivo con los niveles históricos"
            "\n  correctos para esta fecha antes de continuar."
            "\n"
            "\n  Usar como template:"
            "\n  python create_context.py " + replay_date +
            "\n╚═══════════════════════════════════════════╝"
        )

    def _parse(self, raw: dict) -> SessionContext:
        vp = raw.get("volume_profile", {})
        sg = raw.get("spotgamma",      {})
        ss = raw.get("session",        {})
        return SessionContext(
            date               = raw["date"],
            vah                = float(vp.get("vah",                0)),
            poc                = float(vp.get("poc",                0)),
            val                = float(vp.get("val",                0)),
            call_wall          = float(sg.get("call_wall",          0)),
            put_wall           = float(sg.get("put_wall",           0)),
            zero_gamma         = float(sg.get("zero_gamma",         0)),
            volatility_trigger = float(sg.get("volatility_trigger", 0)),
            hpz                = float(sg.get("hpz",                0)),
            combo_1            = float(sg.get("combo_1",            0)),
            combo_2            = float(sg.get("combo_2",            0)),
            large_gamma_1      = float(sg.get("large_gamma_1",      0)),
            large_gamma_2      = float(sg.get("large_gamma_2",      0)),
            large_gamma_3      = float(sg.get("large_gamma_3",      0)),
            large_gamma_4      = float(sg.get("large_gamma_4",      0)),
            prev_high          = float(ss.get("prev_high",          0)),
            prev_low           = float(ss.get("prev_low",           0)),
            prev_close         = float(ss.get("prev_close",         0)),
            open_price         = float(ss.get("open_price",         0)),
            onh                = float(ss.get("onh",                0)),
            onl                = float(ss.get("onl",                0)),
            ibh                = float(ss.get("ibh",                0)),
            ibl                = float(ss.get("ibl",                0)),
        )

    def _to_file_format(self, ctx: SessionContext) -> dict:
        return {
            "date": ctx.date,
            "volume_profile": {
                "vah": ctx.vah,
                "poc": ctx.poc,
                "val": ctx.val,
            },
            "spotgamma": {
                "call_wall":          ctx.call_wall,
                "put_wall":           ctx.put_wall,
                "zero_gamma":         ctx.zero_gamma,
                "volatility_trigger": ctx.volatility_trigger,
                "hpz":                ctx.hpz,
                "combo_1":            ctx.combo_1,
                "combo_2":            ctx.combo_2,
                "large_gamma_1":      ctx.large_gamma_1,
                "large_gamma_2":      ctx.large_gamma_2,
                "large_gamma_3":      ctx.large_gamma_3,
                "large_gamma_4":      ctx.large_gamma_4,
            },
            "session": {
                "prev_high":   ctx.prev_high,
                "prev_low":    ctx.prev_low,
                "prev_close":  ctx.prev_close,
                "open_price":  ctx.open_price,
                "onh":         ctx.onh,
                "onl":         ctx.onl,
                "ibh":         ctx.ibh,
                "ibl":         ctx.ibl,
            }
        }