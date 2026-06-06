# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — feedback_engine.py
#  Institutional Feedback & Learning Loop v1.0
#
#  PURPOSE:
#  Tracks trade outcomes, measures system accuracy,
#  and detects patterns in winning vs losing setups.
#
#  PIPELINE POSITION:
#  risk_engine → [FEEDBACK ENGINE] → stats_engine / logger
#
#  KEY CONCEPTS:
#  - Every APPROVED risk setup is tracked as a "pending trade"
#  - Each tick checks if price hit stop, target_1, or target_2
#  - Results are tagged and stored for stats analysis
#  - No ML — pure deterministic outcome detection
# ╔══════════════════════════════════════════════════════════════════╝

import csv
import os
from dataclasses import dataclass, field
from datetime import datetime
from collections import deque
from typing import Optional
from log_config import get_logger

_log = get_logger("feedback_engine")


# ══════════════════════════════════════════════════════════════════
#  TRADE RECORD
# ══════════════════════════════════════════════════════════════════

@dataclass
class TradeRecord:
    """Represents one approved setup from entry to resolution."""

    # Identity
    trade_id:       int
    open_time:      str
    close_time:     str   = ""

    # Entry context
    entry_price:    float = 0.0
    direction:      str   = "NONE"
    position_size:  float = 0.0
    stop:           float = 0.0
    target_1:       float = 0.0
    target_2:       float = 0.0
    risk_reward:    float = 0.0
    risk_pts:       float = 0.0

    # Confluence context at entry
    confluence_score: int  = 0
    zone:             str  = ""
    event:            str  = ""
    narrative:        str  = ""
    intent_conviction: int = 0
    session:          str  = ""

    # Signal price (bar price when trade was approved)
    signal_price:   float = 0.0
    # Multiplier applied to base contract size (0.25x–2.0x). See RiskResult.size_unit.
    size_unit:      str   = "multiplier"
    # Setup type from SetupRouter (e.g. VA80_LONG, FA_SHORT, CONFLUENCE, NO_SETUP)
    setup_type:     str   = "CONFLUENCE"

    # Outcome
    result:         str   = "PENDING"  # PENDING/WIN/LOSS/BREAKEVEN/TIMEOUT/CANCELLED
    exit_price:     float = 0.0
    pnl_pts:        float = 0.0
    hit_target_1:   bool  = False
    hit_target_2:   bool  = False
    hit_stop:       bool  = False
    was_trap:       bool  = False
    bars_held:      int   = 0
    follow_through: bool  = False   # reached T1 before stop
    slippage_ticks: float = 0.0     # abs(entry_price - signal_price) / tick


@dataclass
class FeedbackSummary:
    """Rolling accuracy metrics for dashboard display."""
    total_trades:    int   = 0
    wins:            int   = 0
    losses:          int   = 0
    breakevens:      int   = 0
    timeouts:        int   = 0
    win_rate:        float = 0.0
    avg_score_wins:  float = 0.0
    avg_score_loss:  float = 0.0
    traps_detected:  int   = 0
    follow_through_rate: float = 0.0
    best_zone:       str   = ""
    best_narrative:  str   = ""


# ══════════════════════════════════════════════════════════════════
#  FEEDBACK ENGINE
# ══════════════════════════════════════════════════════════════════

class FeedbackEngine:
    """
    GIBBZ Institutional Feedback & Learning Loop v1.0

    Tracks every APPROVED risk setup from open to close.
    Detects outcomes automatically by monitoring price vs levels.

    Key features:
    - Auto-detects WIN (hit T1), LOSS (hit stop), TIMEOUT (max bars)
    - Trap detection: fast reversal after entry
    - Follow-through: did price reach T1 before stop?
    - Writes results to CSV for stats analysis
    - Zero ML — pure price-level comparison

    Usage:
        fb = FeedbackEngine()

        # When risk approves:
        fb.open_trade(risk_result, analysis, narrative, session_name)

        # Every tick:
        fb.update(current_price)

        # Get current summary:
        summary = fb.get_summary()
    """

    MAX_BARS_HELD   = 30    # auto-close after N bars (timeout)
    TRAP_BARS       = 3     # bars to detect fast reversal = trap
    # 4 ticks = 1.0 pt: accounts for ES/NQ spread (1-2 ticks) + slippage without
    # premature breakeven exits that mis-classify potential winners.
    BREAKEVEN_TICKS = 4

    def __init__(self,
                 log_dir:          str   = "logs",
                 enabled:          bool  = True,
                 tick:             float = 0.25,
                 breakeven_ticks:  int   = 4):
        self._log_dir         = log_dir
        self._enabled         = enabled
        self._tick            = tick
        self._breakeven_ticks = breakeven_ticks
        self._counter         = 0
        self._pending:   Optional[TradeRecord] = None
        self._history:   deque = deque(maxlen=100)
        self._filepath   = ""
        self._initialized = False

        # Running accumulators for summary
        self._wins        = 0
        self._losses      = 0
        self._breakevens  = 0
        self._timeouts    = 0
        self._traps       = 0
        self._follow_thru = 0
        self._score_wins  = []
        self._score_loss  = []
        self._zone_wins:  dict = {}
        self._narr_wins:  dict = {}

    # ──────────────────────────────────────────────────────────────
    #  OPEN TRADE
    # ──────────────────────────────────────────────────────────────

    def open_trade(self,
                   risk_result,
                   analysis,
                   narrative,
                   session_name:  str   = "",
                   signal_price:  float = 0.0,
                   setup_type:    str   = "CONFLUENCE") -> None:
        """
        Register a new approved setup for tracking.
        Only one pending trade at a time — new one replaces old.
        signal_price: bar price when the signal fired (for slippage calc).
        setup_type: SetupRouter signal_type label (e.g. VA80_LONG, FA_SHORT).
        """
        if not risk_result.approved:
            return

        # If existing pending trade, resolve it before opening new one.
        # Preserve the correct outcome — do not blindly TIMEOUT a trade that
        # already reached target_1 or was stopped out.
        if self._pending is not None:
            tr = self._pending
            tr.close_time = datetime.now().strftime("%H:%M:%S")
            # Guard: entry_price=0 means update() was never called (signal fired but
            # no tick arrived). PnL is undefined — cancel rather than record 0.0 noise.
            if tr.entry_price == 0.0:
                tr.result = "CANCELLED"
                _log.warning(
                    "force-close trade #%d with entry_price=0.0 — "
                    "classified as CANCELLED (no tick received after open)",
                    tr.trade_id
                )
                self._close_trade(tr)
            elif tr.hit_target_1 or tr.follow_through:
                tr.result     = "WIN"
                tr.exit_price = tr.target_1
                tr.pnl_pts    = abs(tr.target_1 - tr.entry_price)
                self._close_trade(tr)
            elif tr.hit_stop:
                tr.result     = "LOSS"
                tr.exit_price = tr.stop
                tr.pnl_pts    = -(tr.risk_pts)
                self._close_trade(tr)
            else:
                tr.result = "TIMEOUT"
                self._close_trade(tr)

        self._counter += 1

        self._pending = TradeRecord(
            trade_id          = self._counter,
            open_time         = datetime.now().strftime("%H:%M:%S"),
            entry_price       = 0.0,  # set on first update
            signal_price      = signal_price,
            direction         = risk_result.direction,
            position_size     = risk_result.position_size,
            size_unit         = getattr(risk_result, "size_unit", "multiplier"),
            stop              = risk_result.stop,
            target_1          = risk_result.target_1,
            target_2          = risk_result.target_2,
            risk_reward       = risk_result.risk_reward,
            risk_pts          = risk_result.risk_pts,
            confluence_score  = getattr(analysis,  "score",        0),
            zone              = getattr(analysis,  "zone",         ""),
            event             = getattr(analysis,  "event",        ""),
            narrative         = getattr(narrative, "narrative",    ""),
            intent_conviction = getattr(narrative, "conviction",   0),
            session           = session_name,
            setup_type        = setup_type or "CONFLUENCE",
        )

    # ──────────────────────────────────────────────────────────────
    #  UPDATE — called every tick
    # ──────────────────────────────────────────────────────────────

    def update(self, price: float) -> Optional[TradeRecord]:
        """
        Check if pending trade hit stop, T1, T2, or timeout.
        Returns closed TradeRecord if trade resolved, else None.
        """
        if self._pending is None:
            return None

        tr = self._pending

        # Set entry price on first update; compute slippage vs signal price
        if tr.entry_price == 0.0:
            tr.entry_price = price
            if tr.signal_price > 0.0:
                tr.slippage_ticks = round(
                    abs(price - tr.signal_price) / self._tick, 2
                )
            return None

        tr.bars_held += 1

        # ── STOP HIT ───────────────────────────────────────────────
        stop_hit = (
            (tr.direction == "LONG"  and price <= tr.stop) or
            (tr.direction == "SHORT" and price >= tr.stop)
        )
        if stop_hit:
            tr.hit_stop   = True
            tr.exit_price = tr.stop
            tr.pnl_pts    = -(tr.risk_pts)
            tr.result     = "LOSS"
            tr.close_time = datetime.now().strftime("%H:%M:%S")

            # Trap check: loss within TRAP_BARS = fast reversal = trap
            if tr.bars_held <= self.TRAP_BARS:
                tr.was_trap = True

            return self._close_trade(tr)

        # ── TARGET 1 HIT ───────────────────────────────────────────
        t1_hit = (
            (tr.direction == "LONG"  and price >= tr.target_1) or
            (tr.direction == "SHORT" and price <= tr.target_1)
        )
        if t1_hit and not tr.hit_target_1:
            tr.hit_target_1   = True
            tr.follow_through = True

        # ── TARGET 2 HIT — close trade ────────────────────────────
        t2_hit = (
            (tr.direction == "LONG"  and price >= tr.target_2 and tr.target_2 > 0) or
            (tr.direction == "SHORT" and price <= tr.target_2 and tr.target_2 > 0)
        )
        if t2_hit:
            tr.hit_target_2 = True
            tr.exit_price   = tr.target_2
            tr.pnl_pts      = abs(tr.target_2 - tr.entry_price)
            tr.result       = "WIN"
            tr.close_time   = datetime.now().strftime("%H:%M:%S")
            return self._close_trade(tr)

        # Close at T1 if hit (conservative exit)
        if tr.hit_target_1:
            tr.exit_price = tr.target_1
            tr.pnl_pts    = abs(tr.target_1 - tr.entry_price)
            tr.result     = "WIN"
            tr.close_time = datetime.now().strftime("%H:%M:%S")
            return self._close_trade(tr)

        # ── BREAKEVEN CHECK ────────────────────────────────────────
        be_dist = abs(price - tr.entry_price)
        if be_dist <= self._tick * self._breakeven_ticks and tr.bars_held > 5:
            tr.exit_price = price
            tr.pnl_pts    = 0.0
            tr.result     = "BREAKEVEN"
            tr.close_time = datetime.now().strftime("%H:%M:%S")
            return self._close_trade(tr)

        # ── TIMEOUT ────────────────────────────────────────────────
        if tr.bars_held >= self.MAX_BARS_HELD:
            tr.exit_price = price
            tr.pnl_pts    = price - tr.entry_price if tr.direction == "LONG" \
                            else tr.entry_price - price
            tr.result     = "TIMEOUT"
            tr.close_time = datetime.now().strftime("%H:%M:%S")
            return self._close_trade(tr)

        return None

    # ──────────────────────────────────────────────────────────────
    #  CLOSE TRADE
    # ──────────────────────────────────────────────────────────────

    def _close_trade(self, tr: TradeRecord) -> TradeRecord:
        """Finalizes a trade, updates accumulators, writes to CSV."""
        self._pending = None
        self._history.append(tr)

        # Update accumulators
        if tr.result == "WIN":
            self._wins += 1
            self._score_wins.append(tr.confluence_score)
            self._zone_wins[tr.zone] = self._zone_wins.get(tr.zone, 0) + 1
            self._narr_wins[tr.narrative] = \
                self._narr_wins.get(tr.narrative, 0) + 1
        elif tr.result == "LOSS":
            self._losses += 1
            self._score_loss.append(tr.confluence_score)
        elif tr.result == "BREAKEVEN":
            self._breakevens += 1
        elif tr.result in ("TIMEOUT", "CANCELLED"):
            self._timeouts += 1

        if tr.was_trap:
            self._traps += 1
        if tr.follow_through:
            self._follow_thru += 1

        # Write to CSV
        if self._enabled:
            self._write_csv(tr)

        return tr

    # ──────────────────────────────────────────────────────────────
    #  SUMMARY
    # ──────────────────────────────────────────────────────────────

    def get_summary(self) -> FeedbackSummary:
        """Returns current session accuracy metrics."""
        total = self._wins + self._losses + self._breakevens + self._timeouts

        win_rate = round(self._wins / total * 100, 1) if total > 0 else 0.0

        avg_wins = round(
            sum(self._score_wins) / len(self._score_wins), 1
        ) if self._score_wins else 0.0

        avg_loss = round(
            sum(self._score_loss) / len(self._score_loss), 1
        ) if self._score_loss else 0.0

        ft_rate = round(
            self._follow_thru / total * 100, 1
        ) if total > 0 else 0.0

        best_zone = max(self._zone_wins, key=self._zone_wins.get) \
            if self._zone_wins else ""
        best_narr = max(self._narr_wins, key=self._narr_wins.get) \
            if self._narr_wins else ""

        return FeedbackSummary(
            total_trades         = total,
            wins                 = self._wins,
            losses               = self._losses,
            breakevens           = self._breakevens,
            timeouts             = self._timeouts,
            win_rate             = win_rate,
            avg_score_wins       = avg_wins,
            avg_score_loss       = avg_loss,
            traps_detected       = self._traps,
            follow_through_rate  = ft_rate,
            best_zone            = best_zone,
            best_narrative       = best_narr,
        )

    # ──────────────────────────────────────────────────────────────
    #  LAST CLOSED TRADE
    # ──────────────────────────────────────────────────────────────

    @property
    def last_trade(self) -> Optional[TradeRecord]:
        return self._history[-1] if self._history else None

    @property
    def has_pending(self) -> bool:
        return self._pending is not None

    @property
    def pending(self) -> Optional[TradeRecord]:
        return self._pending

    # ──────────────────────────────────────────────────────────────
    #  CSV WRITER
    # ──────────────────────────────────────────────────────────────

    def _init_csv(self) -> None:
        os.makedirs(self._log_dir, exist_ok=True)
        date_str       = datetime.now().strftime("%Y-%m-%d")
        self._filepath = os.path.join(
            self._log_dir, "gibbz_trades_" + date_str + ".csv"
        )
        if not os.path.exists(self._filepath):
            headers = [
                "trade_id", "open_time", "close_time",
                "direction", "entry_price", "exit_price",
                "stop", "target_1", "target_2",
                "result", "pnl_pts", "bars_held",
                "hit_stop", "hit_t1", "hit_t2",
                "was_trap", "follow_through",
                "confluence_score", "zone", "event",
                "narrative", "conviction", "rr", "session",
                "slippage_ticks", "position_size", "size_unit", "setup_type",
            ]
            with open(self._filepath, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(headers)
        self._initialized = True

    def _write_csv(self, tr: TradeRecord) -> None:
        if not self._initialized:
            self._init_csv()
        row = [
            tr.trade_id, tr.open_time, tr.close_time,
            tr.direction, tr.entry_price, tr.exit_price,
            tr.stop, tr.target_1, tr.target_2,
            tr.result, round(tr.pnl_pts, 2), tr.bars_held,
            1 if tr.hit_stop    else 0,
            1 if tr.hit_target_1 else 0,
            1 if tr.hit_target_2 else 0,
            1 if tr.was_trap     else 0,
            1 if tr.follow_through else 0,
            tr.confluence_score, tr.zone, tr.event,
            tr.narrative, tr.intent_conviction,
            tr.risk_reward, tr.session,
            tr.slippage_ticks, tr.position_size, tr.size_unit, tr.setup_type,
        ]
        try:
            with open(self._filepath, "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(row)
        except Exception as e:
            _log.error(
                "trade CSV write failed (trade_id=%s, result=%s): %s",
                tr.trade_id, tr.result, e
            )
