# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — session_filter.py
#  Session & Killzone Time Filter
#
#  All times in CR (Costa Rica, UTC-6)
#  NY = CR + 2h (summer EDT) / CR + 1h (winter EST)
#
#  Usage:
#      sf = SessionFilter()
#      if sf.is_active_session():
#          # process confluence
# ╔══════════════════════════════════════════════════════════════════╝

from datetime import datetime, time as dtime
from dataclasses import dataclass
from typing import Optional


# ══════════════════════════════════════════════════════════════════
#  SESSION DEFINITIONS (all in CR time UTC-6)
# ══════════════════════════════════════════════════════════════════

@dataclass
class SessionWindow:
    name:       str
    start:      dtime
    end:        dtime
    tradeable:  bool    # True = signals valid, False = dead zone
    color:      str     # for dashboard display


SESSION_WINDOWS = [

    # ── NY PRE-MARKET ──────────────────────────────────────────────
    SessionWindow(
        name      = "NY_PREMARKET",
        start     = dtime(6, 30),
        end       = dtime(7, 30),
        tradeable = False,
        color     = "DIM"
    ),

    # ── NY OPEN — PRIMARY KILLZONE ─────────────────────────────────
    # NY 08:30–10:00 = CR 06:30–08:00 (summer)
    # Highest volume, institutional flow, best signals
    SessionWindow(
        name      = "NY_OPEN_KILLZONE",
        start     = dtime(7, 30),
        end       = dtime(9, 0),
        tradeable = True,
        color     = "GREEN"
    ),

    # ── NY MORNING — HIGH QUALITY ─────────────────────────────────
    # NY 10:00–11:30 = CR 08:00–09:30 (summer)
    SessionWindow(
        name      = "NY_MORNING",
        start     = dtime(9, 0),
        end       = dtime(10, 30),
        tradeable = True,
        color     = "YELLOW"
    ),

    # ── LUNCH DEAD ZONE ───────────────────────────────────────────
    # NY 11:30–13:00 = CR 09:30–11:00
    # Low volume, choppy, avoid
    SessionWindow(
        name      = "LUNCH_DEAD_ZONE",
        start     = dtime(10, 30),
        end       = dtime(12, 0),
        tradeable = False,
        color     = "RED"
    ),

    # ── NY AFTERNOON — POWER HOUR ─────────────────────────────────
    # NY 13:00–14:00 = CR 11:00–12:00
    SessionWindow(
        name      = "NY_POWER_HOUR",
        start     = dtime(12, 0),
        end       = dtime(13, 30),
        tradeable = True,
        color     = "YELLOW"
    ),

    # ── NY CLOSE ─────────────────────────────────────────────────
    # NY 14:00–16:00 = CR 12:00–14:00
    SessionWindow(
        name      = "NY_CLOSE",
        start     = dtime(13, 30),
        end       = dtime(14, 0),
        tradeable = False,
        color     = "DIM"
    ),
]


# ══════════════════════════════════════════════════════════════════
#  SESSION FILTER
# ══════════════════════════════════════════════════════════════════

class SessionFilter:
    """
    Time-based session filter for GIBBZ SMC COP.

    Returns whether the current time is inside a tradeable window.
    All comparisons done in local system time (should be CR / UTC-6).

    If override_always_active=True, bypasses all time checks.
    Useful for testing or when running outside market hours.
    """

    def __init__(self, override_always_active: bool = False):
        self._override = override_always_active
        self._windows  = SESSION_WINDOWS

    def is_active_session(self) -> bool:
        """
        Returns True if current time is in a tradeable window.
        Returns False during dead zones (lunch, pre-market, after close).
        """
        if self._override:
            return True

        current = self._get_current_window()
        if current is None:
            return False
        return current.tradeable

    def get_session_name(self) -> str:
        """Returns the name of the current session window."""
        if self._override:
            return "OVERRIDE_ACTIVE"

        current = self._get_current_window()
        if current is None:
            return "OUT_OF_SESSION"
        return current.name

    def get_session_color(self) -> str:
        """Returns display color for dashboard."""
        current = self._get_current_window()
        if current is None:
            return "DIM"
        return current.color

    def get_all_windows(self) -> list:
        """Returns all defined session windows."""
        return self._windows

    def time_to_next_session(self) -> Optional[str]:
        """
        Returns time remaining until next tradeable session.
        Returns None if already in a tradeable session.
        """
        if self.is_active_session():
            return None

        now = datetime.now().time()
        for window in self._windows:
            if window.tradeable and window.start > now:
                h = window.start.hour   - now.hour
                m = window.start.minute - now.minute
                if m < 0:
                    h -= 1
                    m += 60
                return f"{h:02d}:{m:02d} until {window.name}"

        return "tomorrow"

    def _get_current_window(self) -> Optional[SessionWindow]:
        """Finds which SessionWindow contains the current time."""
        now = datetime.now().time()
        for window in self._windows:
            if window.start <= now < window.end:
                return window
        return None