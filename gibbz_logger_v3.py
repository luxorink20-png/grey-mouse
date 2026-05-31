# gibbz_logger_v3.py
# Extended Logger V3 — ETS vs CONF comparison per bar

from dataclasses import dataclass, field
from typing import List
import json


@dataclass
class BarLog:
    bar:        int
    price:      float
    env:        str
    eff:        int
    trap:       int
    sc:         int
    ets:        int
    ets_class:  str
    conf_sc:    int
    cont:       int
    t_score:    int
    t_grade:    str
    edge_str:   int
    decay_rate: str
    opp_grade:  str
    validated:  bool
    val_reason: str


class GIBBZLoggerV3:
    """
    Logger extendido V3.
    Registra ETS vs CONF por barra para análisis de timing.
    """

    def __init__(self):
        self._logs: List[BarLog] = []

    def log(self, bar_count: int, raw_data: dict,
            env_r, conf_r, cont_r, etil_r, timing_r,
            decay_r, opp_r, analysis, validation) -> None:

        log = BarLog(
            bar        = bar_count,
            price      = raw_data.get("price", 0),
            env        = getattr(env_r,    "environment",            "?"),
            eff        = getattr(env_r,    "directional_efficiency", 0),
            trap       = getattr(env_r,    "trap_density",           0),
            sc         = getattr(analysis, "score",                  0),
            ets        = getattr(etil_r,   "ets_score",              0),
            ets_class  = getattr(etil_r,   "classification",         "NOISE"),
            conf_sc    = getattr(conf_r,   "confirmation_score",     0),
            cont       = getattr(cont_r,   "continuation_probability", 0),
            t_score    = getattr(timing_r, "entry_timing_score",     0),
            t_grade    = getattr(timing_r, "timing_grade",           "?"),
            edge_str   = getattr(decay_r,  "edge_strength",          0),
            decay_rate = getattr(decay_r,  "decay_rate",             "?"),
            opp_grade  = getattr(opp_r,    "grade",                  "NONE"),
            validated  = getattr(validation, "validated",            False),
            val_reason = getattr(validation, "reason",               ""),
        )
        self._logs.append(log)

    def print_bar(self, log: BarLog,
                  G: str, Y: str, R: str, W: str, RST: str) -> None:
        """Output extendido por barra para replay_debug_v3."""

        # Color por opp grade
        opp_c = G if log.opp_grade == "A" else \
                Y if log.opp_grade == "B" else \
                W if log.opp_grade == "C" else R

        print(
            f"  Bar {log.bar:4d} | "
            f"P={log.price:8.2f} | "
            f"env={log.env:<16} "
            f"eff={log.eff:3d} trap={log.trap:3d} | "
            f"sc={log.sc:3d} conf={log.conf_sc:3d} cont={log.cont:3d} | "
            f"ETS={log.ets:3d}[{log.ets_class:<11}] | "
            f"T={log.t_score:3d}[{log.t_grade:<10}] | "
            f"edge={log.edge_str:3d}[{log.decay_rate:<8}] | "
            f"{opp_c}OPP={log.opp_grade}{RST}"
        )
        if log.validated:
            print(f"         {G}✅ TRADE VALID{RST}")
        elif log.val_reason and log.sc > 0:
            print(f"         {Y}↳ {log.val_reason}{RST}")

    def summary(self) -> dict:
        """Resumen de timing para análisis post-sesión."""
        if not self._logs:
            return {}

        a_setups = [l for l in self._logs if l.opp_grade == "A"]
        b_setups = [l for l in self._logs if l.opp_grade == "B"]
        c_setups = [l for l in self._logs if l.opp_grade == "C"]
        trades   = [l for l in self._logs if l.validated]
        ets_bars = [l for l in self._logs if l.ets >= 65]

        return {
            "total_bars":   len(self._logs),
            "a_setups":     len(a_setups),
            "b_setups":     len(b_setups),
            "c_setups":     len(c_setups),
            "trades_valid": len(trades),
            "ets_active_bars": len(ets_bars),
            "first_ets_bar":   ets_bars[0].bar if ets_bars else 0,
            "first_trade_bar": trades[0].bar   if trades   else 0,
        }