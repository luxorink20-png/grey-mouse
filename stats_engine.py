# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — stats_engine.py
#  Institutional Performance Analytics Engine v1.0
#
#  PURPOSE:
#  Reads session CSV logs and generates performance analytics.
#  Answers: WHERE is the edge? WHEN does the system work best?
#
#  DATA SOURCES:
#  - gibbz_session_YYYY-MM-DD.csv  (tick log from logger.py)
#  - gibbz_trades_YYYY-MM-DD.csv   (trade log from feedback_engine.py)
#
#  METRICS:
#  - Win rate by zone, event, session, narrative
#  - Score accuracy (do high scores = wins?)
#  - HPZ effectiveness
#  - Edge decay over time
#  - Best killzone performance
# ╔══════════════════════════════════════════════════════════════════╝

import csv
import os
from datetime import datetime, date
from dataclasses import dataclass, field
from typing import Optional
from collections import defaultdict


# ══════════════════════════════════════════════════════════════════
#  DATA CONTAINERS
# ══════════════════════════════════════════════════════════════════

@dataclass
class ZoneStat:
    zone:       str
    total:      int   = 0
    wins:       int   = 0
    losses:     int   = 0
    avg_score:  float = 0.0
    win_rate:   float = 0.0
    scores:     list  = field(default_factory=list)

    def compute(self):
        total = self.wins + self.losses
        self.win_rate  = round(self.wins / total * 100, 1) if total > 0 else 0.0
        self.avg_score = round(sum(self.scores) / len(self.scores), 1) \
            if self.scores else 0.0


@dataclass
class SessionReport:
    """Full analytics report for one session."""
    date:               str   = ""
    total_ticks:        int   = 0
    total_trades:       int   = 0
    wins:               int   = 0
    losses:             int   = 0
    breakevens:         int   = 0
    timeouts:           int   = 0
    win_rate:           float = 0.0
    avg_score_all:      float = 0.0
    avg_score_wins:     float = 0.0
    avg_score_losses:   float = 0.0
    hpz_win_rate:       float = 0.0
    hpz_total:          int   = 0
    follow_through_rate: float = 0.0
    traps_total:        int   = 0
    best_zone:          str   = ""
    worst_zone:         str   = ""
    best_event:         str   = ""
    best_session:       str   = ""
    best_narrative:     str   = ""
    edge_score:         float = 0.0   # system quality 0-100
    zone_stats:         dict  = field(default_factory=dict)
    session_stats:      dict  = field(default_factory=dict)
    event_stats:        dict  = field(default_factory=dict)
    narrative_stats:    dict  = field(default_factory=dict)
    score_brackets:     dict  = field(default_factory=dict)
    hourly_stats:       dict  = field(default_factory=dict)


# ══════════════════════════════════════════════════════════════════
#  STATS ENGINE
# ══════════════════════════════════════════════════════════════════

class StatsEngine:
    """
    GIBBZ Institutional Performance Analytics Engine v1.0

    Reads CSV logs and generates a full performance report.

    Usage:
        stats = StatsEngine(log_dir="logs")

        # Analyze today's session:
        report = stats.analyze_today()

        # Print full report to terminal:
        stats.print_report(report)

        # Get edge score only:
        edge = stats.get_edge_score()
    """

    SCORE_BRACKETS = [
        (86, 100, "INSTITUTIONAL"),
        (61,  85, "HIGH"),
        (31,  60, "MEDIUM"),
        (0,   30, "LOW"),
    ]

    def __init__(self, log_dir: str = "logs"):
        self._log_dir = log_dir

    # ──────────────────────────────────────────────────────────────
    #  MAIN ENTRY POINTS
    # ──────────────────────────────────────────────────────────────

    def analyze_today(self) -> SessionReport:
        """Analyze today's session logs."""
        today = date.today().strftime("%Y-%m-%d")
        return self.analyze_date(today)

    def analyze_date(self, date_str: str) -> SessionReport:
        """Analyze logs for a specific date."""
        session_file = os.path.join(
            self._log_dir, "gibbz_session_" + date_str + ".csv"
        )
        trades_file = os.path.join(
            self._log_dir, "gibbz_trades_" + date_str + ".csv"
        )

        report = SessionReport(date=date_str)

        # Load tick data
        ticks = self._load_csv(session_file)
        if ticks:
            self._analyze_ticks(ticks, report)

        # Load trade data
        trades = self._load_csv(trades_file)
        if trades:
            self._analyze_trades(trades, report)

        # Compute derived metrics
        self._compute_edge_score(report)
        self._find_best_worst(report)

        return report

    def get_edge_score(self) -> float:
        """Quick edge score for current session (0-100)."""
        report = self.analyze_today()
        return report.edge_score

    # ──────────────────────────────────────────────────────────────
    #  TICK ANALYSIS (from logger.py CSV)
    # ──────────────────────────────────────────────────────────────

    def _analyze_ticks(self, ticks: list, report: SessionReport) -> None:
        """Analyzes tick-level data for signal quality metrics."""
        report.total_ticks = len(ticks)

        scores      = []
        hpz_scores  = []
        zone_data   = defaultdict(list)   # zone → [scores]
        event_data  = defaultdict(list)
        session_data = defaultdict(list)
        hourly_data = defaultdict(list)
        score_bracket_counts = defaultdict(int)

        for row in ticks:
            try:
                score    = int(row.get("score",    0))
                zone     = row.get("zone",         "UNKNOWN")
                event    = row.get("event",        "NONE")
                session  = row.get("session",      "UNKNOWN")
                hpz      = row.get("hpz",          "0")
                ts       = row.get("timestamp",    "")
                action   = row.get("action",       "")

                scores.append(score)

                if hpz == "1":
                    hpz_scores.append(score)

                zone_data[zone].append(score)
                event_data[event].append(score)
                session_data[session].append(score)

                # Hourly breakdown
                if ts:
                    try:
                        hour = ts[11:13]   # "HH" from "YYYY-MM-DD HH:MM:SS"
                        hourly_data[hour].append(score)
                    except Exception:
                        pass

                # Score bracket
                bracket = self._get_bracket(score)
                score_bracket_counts[bracket] += 1

            except Exception:
                continue

        report.avg_score_all = round(
            sum(scores) / len(scores), 1
        ) if scores else 0.0

        report.hpz_total = len(hpz_scores)

        # Zone stats
        for zone, zone_scores in zone_data.items():
            report.zone_stats[zone] = {
                "count":     len(zone_scores),
                "avg_score": round(sum(zone_scores) / len(zone_scores), 1),
            }

        # Event stats
        for event, ev_scores in event_data.items():
            report.event_stats[event] = {
                "count":     len(ev_scores),
                "avg_score": round(sum(ev_scores) / len(ev_scores), 1),
            }

        # Session stats
        for sess, sess_scores in session_data.items():
            report.session_stats[sess] = {
                "count":     len(sess_scores),
                "avg_score": round(sum(sess_scores) / len(sess_scores), 1),
            }

        # Hourly stats
        for hour, h_scores in hourly_data.items():
            report.hourly_stats[hour] = {
                "count":     len(h_scores),
                "avg_score": round(sum(h_scores) / len(h_scores), 1),
            }

        # Score brackets
        report.score_brackets = dict(score_bracket_counts)

    # ──────────────────────────────────────────────────────────────
    #  TRADE ANALYSIS (from feedback_engine.py CSV)
    # ──────────────────────────────────────────────────────────────

    def _analyze_trades(self, trades: list, report: SessionReport) -> None:
        """Analyzes trade outcomes for win rate and edge metrics."""
        report.total_trades = len(trades)

        score_wins   = []
        score_losses = []
        zone_wins    = defaultdict(int)
        zone_total   = defaultdict(int)
        event_wins   = defaultdict(int)
        event_total  = defaultdict(int)
        narr_wins    = defaultdict(int)
        narr_total   = defaultdict(int)
        sess_wins    = defaultdict(int)
        sess_total   = defaultdict(int)
        follow_thru  = 0
        traps        = 0
        hpz_wins     = 0
        hpz_total    = 0

        for row in trades:
            try:
                result    = row.get("result",         "UNKNOWN")
                score     = int(row.get("confluence_score", 0))
                zone      = row.get("zone",           "UNKNOWN")
                event     = row.get("event",          "NONE")
                narrative = row.get("narrative",      "UNCLEAR")
                session   = row.get("session",        "UNKNOWN")
                ft        = row.get("follow_through", "0")
                trap      = row.get("was_trap",       "0")

                is_win  = result == "WIN"
                is_loss = result == "LOSS"

                if is_win:
                    report.wins += 1
                    score_wins.append(score)
                    zone_wins[zone]  += 1
                    event_wins[event] += 1
                    narr_wins[narrative] += 1
                    sess_wins[session]   += 1
                elif is_loss:
                    report.losses += 1
                    score_losses.append(score)
                elif result == "BREAKEVEN":
                    report.breakevens += 1
                elif result == "TIMEOUT":
                    report.timeouts += 1

                zone_total[zone]      += 1
                event_total[event]    += 1
                narr_total[narrative] += 1
                sess_total[session]   += 1

                if ft == "1":
                    follow_thru += 1
                if trap == "1":
                    traps += 1

            except Exception:
                continue

        total = report.wins + report.losses
        report.win_rate = round(
            report.wins / total * 100, 1
        ) if total > 0 else 0.0

        report.avg_score_wins = round(
            sum(score_wins) / len(score_wins), 1
        ) if score_wins else 0.0

        report.avg_score_losses = round(
            sum(score_losses) / len(score_losses), 1
        ) if score_losses else 0.0

        report.follow_through_rate = round(
            follow_thru / total * 100, 1
        ) if total > 0 else 0.0

        report.traps_total = traps

        # Zone win rates
        for zone in zone_total:
            w = zone_wins[zone]
            t = zone_total[zone]
            wr = round(w / t * 100, 1) if t > 0 else 0.0
            if zone in report.zone_stats:
                report.zone_stats[zone]["wins"]    = w
                report.zone_stats[zone]["total"]   = t
                report.zone_stats[zone]["win_rate"] = wr
            else:
                report.zone_stats[zone] = {"wins": w, "total": t,
                                           "win_rate": wr}

        # Narrative win rates
        for narr in narr_total:
            w  = narr_wins[narr]
            t  = narr_total[narr]
            wr = round(w / t * 100, 1) if t > 0 else 0.0
            report.narrative_stats[narr] = {
                "wins": w, "total": t, "win_rate": wr
            }

        # Session win rates
        for sess in sess_total:
            w  = sess_wins[sess]
            t  = sess_total[sess]
            wr = round(w / t * 100, 1) if t > 0 else 0.0
            if sess in report.session_stats:
                report.session_stats[sess]["wins"]    = w
                report.session_stats[sess]["total_trades"] = t
                report.session_stats[sess]["win_rate"] = wr
            else:
                report.session_stats[sess] = {
                    "wins": w, "total_trades": t, "win_rate": wr
                }

    # ──────────────────────────────────────────────────────────────
    #  EDGE SCORE — system quality metric 0-100
    # ──────────────────────────────────────────────────────────────

    def _compute_edge_score(self, report: SessionReport) -> None:
        """
        Computes a single edge score representing system quality.

        Components:
        - Win rate component (0-40 pts)
        - Score accuracy: wins have higher avg score than losses (0-20 pts)
        - Follow-through rate (0-20 pts)
        - Low trap rate (0-20 pts)
        """
        edge = 0.0

        # Win rate component (max 40)
        wr = report.win_rate
        if wr >= 60:   edge += 40
        elif wr >= 50: edge += 30
        elif wr >= 40: edge += 20
        elif wr >= 30: edge += 10

        # Score accuracy component (max 20)
        if report.avg_score_wins > 0 and report.avg_score_losses > 0:
            diff = report.avg_score_wins - report.avg_score_losses
            if diff >= 15:   edge += 20
            elif diff >= 10: edge += 15
            elif diff >= 5:  edge += 10
            elif diff >= 0:  edge += 5

        # Follow-through component (max 20)
        ft = report.follow_through_rate
        if ft >= 70:   edge += 20
        elif ft >= 50: edge += 15
        elif ft >= 30: edge += 10
        elif ft >= 10: edge += 5

        # Trap rate component (max 20)
        # Low traps = good
        if report.total_trades > 0:
            trap_rate = report.traps_total / report.total_trades * 100
            if trap_rate <= 5:    edge += 20
            elif trap_rate <= 10: edge += 15
            elif trap_rate <= 20: edge += 10
            elif trap_rate <= 30: edge += 5

        report.edge_score = round(edge, 1)

    # ──────────────────────────────────────────────────────────────
    #  BEST / WORST FINDER
    # ──────────────────────────────────────────────────────────────

    def _find_best_worst(self, report: SessionReport) -> None:
        """Identifies best and worst performing categories."""

        # Best zone by win rate (min 2 trades)
        best_wr   = -1.0
        worst_wr  = 101.0
        for zone, data in report.zone_stats.items():
            t  = data.get("total", data.get("count", 0))
            wr = data.get("win_rate", 0.0)
            if t >= 2:
                if wr > best_wr:
                    best_wr            = wr
                    report.best_zone   = zone
                if wr < worst_wr:
                    worst_wr           = wr
                    report.worst_zone  = zone

        # Best event by avg score
        best_score = -1.0
        for event, data in report.event_stats.items():
            avg = data.get("avg_score", 0.0)
            if avg > best_score:
                best_score        = avg
                report.best_event = event

        # Best session by avg score
        best_sess = -1.0
        for sess, data in report.session_stats.items():
            avg = data.get("avg_score", 0.0)
            if avg > best_sess and "DEAD" not in sess:
                best_sess             = avg
                report.best_session   = sess

        # Best narrative by win rate
        best_narr = -1.0
        for narr, data in report.narrative_stats.items():
            t  = data.get("total", 0)
            wr = data.get("win_rate", 0.0)
            if t >= 1 and wr > best_narr:
                best_narr             = wr
                report.best_narrative = narr

    # ──────────────────────────────────────────────────────────────
    #  PRINT REPORT
    # ──────────────────────────────────────────────────────────────

    def print_report(self, report: SessionReport) -> None:
        """Prints full analytics report to terminal."""

        W    = 52
        LINE = "=" * W
        DIV  = "-" * W

        def bar(value, max_val=100, width=10):
            filled = int((value / max_val) * width) if max_val > 0 else 0
            filled = min(filled, width)
            return chr(9608) * filled + chr(9617) * (width - filled)

        def wr_label(wr):
            if wr >= 60: return " STRONG EDGE"
            if wr >= 50: return " POSITIVE EDGE"
            if wr >= 40: return " MARGINAL"
            return " NO EDGE"

        print()
        print(LINE)
        print("  GIBBZ SMC COP — SESSION ANALYTICS")
        print("  Date: " + report.date)
        print(LINE)
        print()

        # ── OVERVIEW ──────────────────────────────────────────────
        print("  OVERVIEW")
        print(DIV)
        print("  Total ticks     : " + str(report.total_ticks))
        print("  Total trades    : " + str(report.total_trades))
        print("  Avg tick score  : " + str(report.avg_score_all))
        print()

        # ── PERFORMANCE ───────────────────────────────────────────
        if report.total_trades > 0:
            print("  PERFORMANCE")
            print(DIV)
            wr_bar = bar(report.win_rate)
            print("  Win rate        : " + str(report.win_rate) +
                  "%  " + wr_bar + wr_label(report.win_rate))
            print("  Wins            : " + str(report.wins))
            print("  Losses          : " + str(report.losses))
            print("  Breakevens      : " + str(report.breakevens))
            print("  Timeouts        : " + str(report.timeouts))
            print("  Avg score WINS  : " + str(report.avg_score_wins))
            print("  Avg score LOSS  : " + str(report.avg_score_losses))
            score_diff = round(
                report.avg_score_wins - report.avg_score_losses, 1
            )
            diff_sign = "+" if score_diff >= 0 else ""
            print("  Score edge      : " + diff_sign + str(score_diff) +
                  "  (wins score higher = good)")
            print()

            # ── EDGE SCORE ─────────────────────────────────────────
            print("  SYSTEM EDGE SCORE")
            print(DIV)
            edge_bar = bar(report.edge_score)
            print("  Edge score      : " + str(report.edge_score) +
                  "/100  " + edge_bar)
            if report.edge_score >= 70:
                print("  Status          : STRONG INSTITUTIONAL EDGE")
            elif report.edge_score >= 50:
                print("  Status          : DEVELOPING EDGE")
            elif report.edge_score >= 30:
                print("  Status          : WEAK EDGE — needs calibration")
            else:
                print("  Status          : NO EDGE — review parameters")
            print()

            # ── QUALITY METRICS ────────────────────────────────────
            print("  QUALITY METRICS")
            print(DIV)
            ft_bar = bar(report.follow_through_rate)
            print("  Follow-through  : " + str(report.follow_through_rate) +
                  "%  " + ft_bar)
            print("  Traps detected  : " + str(report.traps_total))
            if report.total_trades > 0:
                trap_rate = round(
                    report.traps_total / report.total_trades * 100, 1
                )
                print("  Trap rate       : " + str(trap_rate) + "%")
            print()

        # ── ZONE PERFORMANCE ──────────────────────────────────────
        if report.zone_stats:
            print("  ZONE PERFORMANCE")
            print(DIV)
            sorted_zones = sorted(
                report.zone_stats.items(),
                key=lambda x: x[1].get("avg_score", 0),
                reverse=True
            )
            for zone, data in sorted_zones:
                avg  = data.get("avg_score", 0.0)
                cnt  = data.get("count", data.get("total", 0))
                wr   = data.get("win_rate", 0.0)
                z_bar = bar(avg)
                wr_str = ("  WR:" + str(wr) + "%") if wr > 0 else ""
                print("  " + zone[:18].ljust(18) +
                      " " + z_bar +
                      "  " + str(avg) + wr_str)
            print()
            if report.best_zone:
                print("  Best zone  : " + report.best_zone)
            if report.worst_zone:
                print("  Worst zone : " + report.worst_zone)
            print()

        # ── EVENT PERFORMANCE ─────────────────────────────────────
        if report.event_stats:
            print("  EVENT PERFORMANCE")
            print(DIV)
            sorted_events = sorted(
                report.event_stats.items(),
                key=lambda x: x[1].get("avg_score", 0),
                reverse=True
            )
            for event, data in sorted_events:
                avg  = data.get("avg_score", 0.0)
                cnt  = data.get("count", 0)
                e_bar = bar(avg)
                print("  " + event[:14].ljust(14) +
                      " " + e_bar +
                      "  " + str(avg) +
                      "  n=" + str(cnt))
            print()

        # ── SESSION PERFORMANCE ───────────────────────────────────
        if report.session_stats:
            print("  SESSION PERFORMANCE")
            print(DIV)
            sorted_sess = sorted(
                report.session_stats.items(),
                key=lambda x: x[1].get("avg_score", 0),
                reverse=True
            )
            for sess, data in sorted_sess:
                if "DEAD" in sess or "OUT_OF" in sess:
                    continue
                avg  = data.get("avg_score", 0.0)
                cnt  = data.get("count", 0)
                wr   = data.get("win_rate", 0.0)
                s_bar = bar(avg)
                wr_str = ("  WR:" + str(wr) + "%") if wr > 0 else ""
                print("  " + sess[:20].ljust(20) +
                      " " + s_bar +
                      "  " + str(avg) + wr_str)
            print()

        # ── NARRATIVE PERFORMANCE ─────────────────────────────────
        if report.narrative_stats:
            print("  NARRATIVE PERFORMANCE")
            print(DIV)
            for narr, data in report.narrative_stats.items():
                w  = data.get("wins",     0)
                t  = data.get("total",    0)
                wr = data.get("win_rate", 0.0)
                n_bar = bar(wr)
                print("  " + narr[:14].ljust(14) +
                      " " + n_bar +
                      "  WR:" + str(wr) + "%" +
                      "  " + str(w) + "/" + str(t))
            print()

        # ── SCORE BRACKETS ────────────────────────────────────────
        if report.score_brackets:
            print("  SIGNAL DISTRIBUTION")
            print(DIV)
            total_sigs = sum(report.score_brackets.values()) or 1
            for bracket in ["INSTITUTIONAL", "HIGH", "MEDIUM", "LOW"]:
                cnt = report.score_brackets.get(bracket, 0)
                pct = round(cnt / total_sigs * 100, 1)
                b_bar = bar(pct)
                print("  " + bracket[:14].ljust(14) +
                      " " + b_bar +
                      "  " + str(pct) + "%" +
                      "  n=" + str(cnt))
            print()

        # ── HOURLY HEATMAP ────────────────────────────────────────
        if report.hourly_stats:
            print("  HOURLY SIGNAL QUALITY")
            print(DIV)
            sorted_hours = sorted(report.hourly_stats.items())
            for hour, data in sorted_hours:
                avg  = data.get("avg_score", 0.0)
                cnt  = data.get("count", 0)
                h_bar = bar(avg)
                print("  " + hour + ":00" +
                      "  " + h_bar +
                      "  " + str(avg) +
                      "  n=" + str(cnt))
            print()

        # ── RECOMMENDATIONS ───────────────────────────────────────
        print("  RECOMMENDATIONS")
        print(DIV)
        recs = self._generate_recommendations(report)
        for rec in recs:
            print("  >> " + rec)
        print()
        print(LINE)
        print()

    # ──────────────────────────────────────────────────────────────
    #  RECOMMENDATIONS ENGINE
    # ──────────────────────────────────────────────────────────────

    def _generate_recommendations(self, report: SessionReport) -> list:
        """Generates actionable recommendations from the data."""
        recs = []

        if report.total_trades == 0:
            recs.append("No trades recorded — lower MIN_BASE_SCORE to generate setups")
            recs.append("Check validator thresholds in validator.py")
            return recs

        # Win rate recommendations
        if report.win_rate < 40:
            recs.append("Win rate below 40% — review confluence matrix weights")
        elif report.win_rate >= 60:
            recs.append("Strong win rate — consider increasing position size")

        # Score accuracy
        if report.avg_score_wins > 0 and report.avg_score_losses > 0:
            diff = report.avg_score_wins - report.avg_score_losses
            if diff < 5:
                recs.append("Score not predicting outcomes — review confluence matrix")
            elif diff >= 15:
                recs.append("Score is highly predictive — trust HIGH QUALITY signals")

        # Follow-through
        if report.follow_through_rate < 40:
            recs.append("Low follow-through — targets may be too ambitious")
        elif report.follow_through_rate >= 70:
            recs.append("High follow-through — consider extending Target 2")

        # Trap rate
        if report.total_trades > 0:
            trap_rate = report.traps_total / report.total_trades * 100
            if trap_rate > 20:
                recs.append("High trap rate — strengthen INDUCTION filter in validator")

        # Best zone focus
        if report.best_zone:
            recs.append("Focus entries on: " + report.best_zone)
        if report.worst_zone:
            recs.append("Avoid entries in: " + report.worst_zone)

        # Best session focus
        if report.best_session:
            recs.append("Highest quality session: " + report.best_session)

        # Edge score
        if report.edge_score < 30:
            recs.append("Edge score critical — system needs calibration before live")
        elif report.edge_score >= 70:
            recs.append("Edge score strong — system ready for live deployment")

        if not recs:
            recs.append("System performing within expected parameters")

        return recs

    # ──────────────────────────────────────────────────────────────
    #  HELPERS
    # ──────────────────────────────────────────────────────────────

    def _load_csv(self, filepath: str) -> list:
        """Loads a CSV file and returns list of row dicts."""
        if not os.path.exists(filepath):
            return []
        rows = []
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    rows.append(dict(row))
        except Exception as e:
            print("[STATS CSV ERROR] " + str(e))
        return rows

    def _get_bracket(self, score: int) -> str:
        for lo, hi, label in self.SCORE_BRACKETS:
            if lo <= score <= hi:
                return label
        return "LOW"