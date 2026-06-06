# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — concentration_monitor.py
#  Setup Concentration & Degradation Monitor v1.0
#
#  PURPOSE:
#  Tracks per-setup trade outcomes (VA80, FA, CONFLUENCE, etc.)
#  and fires degradation alerts when a setup's rolling PF collapses.
#
#  PIPELINE POSITION:
#  feedback_engine (closed_trade) → [CONCENTRATION MONITOR] → voice_engine
# ╚══════════════════════════════════════════════════════════════════╝

from __future__ import annotations
from dataclasses import dataclass, field
from collections import deque
from log_config import get_logger

_log = get_logger("concentration_monitor")

# Setups with PnL concentration risk (audited: VA80 = 38% PnL, 17 trades)
HIGH_CONCENTRATION_SETUPS = {"VA80", "FA"}


@dataclass
class DegradationAlert:
    setup_type:    str
    rolling_pf:    float   # PF over the last `window` trades for this setup
    total_pf:      float   # all-time PF for this setup
    trades_total:  int
    trades_window: int
    consecutive_losses: int
    message:       str


@dataclass
class _SetupStats:
    setup_type: str
    _history:   deque = field(default_factory=lambda: deque(maxlen=200))
    _last_alert_at: int = 0  # total trade index when last alert fired

    def register(self, pnl_pts: float, win: bool) -> None:
        self._history.append((pnl_pts, win))

    @property
    def total(self) -> int:
        return len(self._history)

    def rolling_pf(self, window: int) -> float:
        recent = list(self._history)[-window:]
        gross_win  = sum(p for p, w in recent if w)
        gross_loss = abs(sum(p for p, w in recent if not w))
        if gross_loss == 0:
            return float("inf") if gross_win > 0 else 1.0
        return round(gross_win / gross_loss, 2)

    def total_pf(self) -> float:
        gross_win  = sum(p for p, w in self._history if w)
        gross_loss = abs(sum(p for p, w in self._history if not w))
        if gross_loss == 0:
            return float("inf") if gross_win > 0 else 1.0
        return round(gross_win / gross_loss, 2)

    def consecutive_losses(self) -> int:
        count = 0
        for _, win in reversed(list(self._history)):
            if not win:
                count += 1
            else:
                break
        return count


class ConcentrationMonitor:
    """
    Tracks per-setup trade outcomes and emits degradation alerts.

    Usage:
        monitor = ConcentrationMonitor()

        # When a trade opens:
        monitor.set_pending(setup_type)

        # When a trade closes:
        alert = monitor.register_close(pnl_pts=tr.pnl_pts, win=(tr.result == "WIN"))
        if alert:
            voice.say(alert.message)
    """

    def __init__(
        self,
        min_trades:     int   = 5,   # minimum trades before degradation can fire
        pf_floor:       float = 1.0, # PF below this = degradation
        window:         int   = 20,  # rolling window size for PF calculation
        cooldown_trades: int  = 10,  # trades between successive alerts for same setup
    ):
        self._min_trades      = min_trades
        self._pf_floor        = pf_floor
        self._window          = window
        self._cooldown_trades = cooldown_trades
        self._stats:          dict[str, _SetupStats] = {}
        self._pending_setup:  str  = "CONFLUENCE"
        self._total_closed:   int  = 0

    def set_pending(self, setup_type: str) -> None:
        """Tag the next trade-close with this setup type. Call when trade opens."""
        self._pending_setup = setup_type or "CONFLUENCE"

    def register_close(
        self,
        pnl_pts: float,
        win:     bool,
    ) -> DegradationAlert | None:
        """
        Register a closed trade against the pending setup type.
        Returns DegradationAlert if degradation detected, else None.
        """
        setup = self._pending_setup
        self._pending_setup = "CONFLUENCE"
        self._total_closed += 1

        if setup not in self._stats:
            self._stats[setup] = _SetupStats(setup_type=setup)

        stats = self._stats[setup]
        stats.register(pnl_pts, win)

        return self._check_degradation(stats)

    def _check_degradation(self, stats: _SetupStats) -> DegradationAlert | None:
        if stats.total < self._min_trades:
            return None

        rpf = stats.rolling_pf(self._window)
        if rpf >= self._pf_floor:
            return None

        # Cooldown: don't alert more than once per cooldown_trades for this setup
        trades_since_last = stats.total - stats._last_alert_at
        if stats._last_alert_at > 0 and trades_since_last < self._cooldown_trades:
            return None

        stats._last_alert_at = stats.total
        tpf = stats.total_pf()
        closs = stats.consecutive_losses()
        window_trades = min(stats.total, self._window)

        severity = "CRITICAL" if setup_in_high_concentration(stats.setup_type) else "WARNING"
        msg = (
            f"{severity}: {stats.setup_type} degrading — "
            f"PF {rpf:.2f} last {window_trades} trades "
            f"(all-time {tpf:.2f}, {closs} consecutive losses)"
        )
        _log.warning(msg)

        return DegradationAlert(
            setup_type        = stats.setup_type,
            rolling_pf        = rpf,
            total_pf          = tpf,
            trades_total      = stats.total,
            trades_window     = window_trades,
            consecutive_losses = closs,
            message           = msg,
        )

    def get_summary(self) -> dict[str, dict]:
        """Return per-setup stats dict for dashboard display."""
        out = {}
        for stype, stats in self._stats.items():
            out[stype] = {
                "trades":       stats.total,
                "pf_all":       stats.total_pf(),
                "pf_rolling":   stats.rolling_pf(self._window),
                "consec_loss":  stats.consecutive_losses(),
                "high_conc":    setup_in_high_concentration(stype),
            }
        return out


def setup_in_high_concentration(setup_type: str) -> bool:
    """True if setup_type is in the known high-concentration group."""
    for name in HIGH_CONCENTRATION_SETUPS:
        if name in setup_type:
            return True
    return False
