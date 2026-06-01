# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC — setup_pullback.py
#  Pullback-to-trend detector.
#
#  Logic:
#    1. Establish a trend: last TREND_BARS bars have monotonically
#       rising OR falling closes.
#    2. Pullback bar: the bar immediately after the trend moves
#       counter-trend (close reverses direction).
#    3. Delta confirmation: the counter-trend bar's delta must
#       confirm the ORIGINAL trend direction (buyers on LONG dip,
#       sellers on SHORT rally) — i.e. smart money absorbing the
#       pullback.
#    4. Stop is placed below the trend's lowest low (LONG) or above
#       the trend's highest high (SHORT), plus a small buffer.
#    5. Target is the trend extreme extended by a fraction of the
#       swing range (capped by run_backtest at 20 pts).
#
#  Integration: instantiate once per session in full_backtest.run_session().
#  Call update(bar) every bar after the bar dict is built.
# ╚══════════════════════════════════════════════════════════════════╝

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

_TREND_BARS     = 6     # consecutive monotonic bars required for trend (tightened)
_DELTA_CONFIRM  = 250   # minimum |delta| on pullback bar to confirm (tightened)
_MIN_SWING_PTS  = 8.0   # minimum trend swing to be worth trading (tightened)
_STOP_BUFFER    = 2.0   # pts beyond swing extreme for stop
_TGT_EXTENSION  = 0.50  # extend target beyond swing extreme by this * swing_range


@dataclass
class PullbackResult:
    signal:    str    # PULLBACK_LONG | PULLBACK_SHORT | NONE
    state:     str    # diagnostic
    stop_pts:  float  # suggested stop distance in points
    target_pts: float  # suggested target distance in points


class PullbackDetector:
    """
    Stateless-per-bar but stateful across bars within a session.
    Reset by creating a new instance at session start.
    """

    def __init__(
        self,
        trend_bars:    int   = _TREND_BARS,
        delta_confirm: int   = _DELTA_CONFIRM,
        min_swing:     float = _MIN_SWING_PTS,
        stop_buffer:   float = _STOP_BUFFER,
        tgt_extension: float = _TGT_EXTENSION,
    ) -> None:
        self._trend_bars    = trend_bars
        self._delta_confirm = delta_confirm
        self._min_swing     = min_swing
        self._stop_buffer   = stop_buffer
        self._tgt_extension = tgt_extension
        # deque stores (close, high, low, delta) tuples
        self._history: deque[tuple[float, float, float, float]] = deque(
            maxlen=trend_bars + 2
        )
        self._cooldown = 0   # bars to wait after firing before firing again (reset to 12 after signal)

    def update(self, bar: dict) -> PullbackResult:
        price  = float(bar.get("price", 0.0))
        high   = float(bar.get("high", price))
        low    = float(bar.get("low",  price))
        delta  = float(bar.get("delta", 0.0))

        self._history.append((price, high, low, delta))

        if self._cooldown > 0:
            self._cooldown -= 1
            return PullbackResult("NONE", "COOLDOWN", 0.0, 0.0)

        if len(self._history) < self._trend_bars + 1:
            return PullbackResult("NONE", "INSUFFICIENT_DATA", 0.0, 0.0)

        hist    = list(self._history)
        trend   = hist[:-1]   # last _trend_bars bars (the trend)
        current = hist[-1]    # bar we're evaluating now

        closes = [b[0] for b in trend]

        # Uptrend: each close strictly higher than the previous
        if all(closes[i] > closes[i - 1] for i in range(1, len(closes))):
            swing_high = max(b[1] for b in trend)
            swing_low  = min(b[2] for b in trend)
            swing_range = swing_high - swing_low

            if swing_range < self._min_swing:
                return PullbackResult("NONE", "SWING_TOO_SMALL", 0.0, 0.0)

            cur_close = current[0]
            cur_delta = current[3]

            # Pullback: current close is lower than the last trend bar's close
            # AND delta is positive (buyers absorbing the dip)
            if cur_close < closes[-1] and cur_delta >= self._delta_confirm:
                stop_pts = round(
                    (cur_close - swing_low) + self._stop_buffer, 2
                )
                tgt_pts = round(
                    (swing_high - cur_close) + swing_range * self._tgt_extension,
                    2,
                )
                if stop_pts > 0 and tgt_pts > 0:
                    self._cooldown = 12
                    return PullbackResult(
                        "PULLBACK_LONG", "UPTREND_PULLBACK",
                        stop_pts, tgt_pts,
                    )

        # Downtrend: each close strictly lower than the previous
        elif all(closes[i] < closes[i - 1] for i in range(1, len(closes))):
            swing_high = max(b[1] for b in trend)
            swing_low  = min(b[2] for b in trend)
            swing_range = swing_high - swing_low

            if swing_range < self._min_swing:
                return PullbackResult("NONE", "SWING_TOO_SMALL", 0.0, 0.0)

            cur_close = current[0]
            cur_delta = current[3]

            # Pullback: current close is higher than the last trend bar's close
            # AND delta is negative (sellers absorbing the bounce)
            if cur_close > closes[-1] and cur_delta <= -self._delta_confirm:
                stop_pts = round(
                    (swing_high - cur_close) + self._stop_buffer, 2
                )
                tgt_pts = round(
                    (cur_close - swing_low) + swing_range * self._tgt_extension,
                    2,
                )
                if stop_pts > 0 and tgt_pts > 0:
                    self._cooldown = 12
                    return PullbackResult(
                        "PULLBACK_SHORT", "DOWNTREND_PULLBACK",
                        stop_pts, tgt_pts,
                    )

        return PullbackResult("NONE", "NO_PATTERN", 0.0, 0.0)
