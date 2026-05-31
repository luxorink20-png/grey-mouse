# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — logger.py
#  CSV Data Logger — records every tick for edge analysis
#
#  Usage:
#      logger = GibbzLogger()
#      logger.log(price, event_result, level_context, analysis)
# ╔══════════════════════════════════════════════════════════════════╝

import csv
import os
from datetime import datetime


HEADERS = [
    "timestamp",
    "price",
    "event",
    "confidence",
    "zone",
    "bias",
    "score",
    "classification",
    "action",
    "hpz",
    "bias_aligned",
    "confluence",
    "session",
    "setup_type",
    "setup_confidence",
    "setup_env",
]


class GibbzLogger:
    """
    Appends one row per tick to a CSV file.
    Non-blocking — writes synchronously but only on closed bars.
    File is created automatically if it doesn't exist.
    Headers written once on first run.
    """

    def __init__(self,
                 log_dir:  str  = "logs",
                 enabled:  bool = True):
        self.enabled = enabled
        self._log_dir = log_dir
        self._filepath = ""
        self._initialized = False

    def _init_file(self) -> None:
        """Creates log directory and file with headers if needed."""
        os.makedirs(self._log_dir, exist_ok=True)

        date_str       = datetime.now().strftime("%Y-%m-%d")
        self._filepath = os.path.join(
            self._log_dir, f"gibbz_session_{date_str}.csv"
        )

        # Write headers only if file is new
        write_headers = not os.path.exists(self._filepath)
        if write_headers:
            with open(self._filepath, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(HEADERS)

        self._initialized = True

    def log(self,
            price:         float,
            event_result:  dict,
            level_context,
            analysis,
            session_name:  str = "UNKNOWN",
            setup_type:    str = "NO_SETUP",
            setup_confidence: int = 0,
            setup_env:     str = "") -> None:
        """
        Appends one row to the CSV file.

        Args:
            price:         current price
            event_result:  dict from EventEngine.process()
            level_context: LevelContext from InstitutionalLevels
            analysis:      ConfluenceResult from ConfluenceEngine
            session_name:  string from SessionFilter.get_session_name()
        """
        if not self.enabled:
            return

        if not self._initialized:
            self._init_file()

        # Safely extract all fields
        event          = event_result.get("event",      "NONE")
        confidence     = event_result.get("confidence", 0)
        zone           = getattr(level_context, "zone",            "UNKNOWN")
        bias           = getattr(level_context, "reaction_bias",   "NEUTRAL")
        score          = getattr(analysis,      "score",           0)
        classification = getattr(analysis,      "classification",  "UNKNOWN")
        action         = getattr(analysis,      "action",          "IGNORE")
        hpz            = getattr(level_context, "high_prob_zone",  False)
        bias_aligned   = getattr(analysis,      "bias_aligned",    False)
        confluence     = getattr(analysis,      "confluence",      "")

        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            round(price, 2),
            event,
            confidence,
            zone,
            bias,
            score,
            classification,
            action,
            1 if hpz else 0,
            1 if bias_aligned else 0,
            confluence,
            session_name,
            setup_type,
            setup_confidence,
            setup_env,
        ]

        try:
            with open(self._filepath, "a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(row)
        except Exception as e:
            print(f"[LOGGER ERROR] {e}")

    @property
    def filepath(self) -> str:
        return self._filepath if self._initialized else "not started"

    def get_today_stats(self) -> dict:
        """
        Reads today's log and returns basic performance stats.
        Useful for end-of-session review.
        """
        if not self._initialized or not os.path.exists(self._filepath):
            return {}

        stats = {
            "total_ticks":    0,
            "enter_signals":  0,
            "watch_signals":  0,
            "ignore_signals": 0,
            "avg_score":      0.0,
            "hpz_count":      0,
            "by_event":       {},
        }

        scores = []

        try:
            with open(self._filepath, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    stats["total_ticks"] += 1
                    action = row.get("action", "")
                    score  = int(row.get("score", 0))
                    event  = row.get("event", "NONE")
                    hpz    = row.get("hpz", "0")

                    scores.append(score)

                    if action == "ENTER":   stats["enter_signals"]  += 1
                    if action == "WATCH":   stats["watch_signals"]  += 1
                    if action == "IGNORE":  stats["ignore_signals"] += 1
                    if hpz == "1":         stats["hpz_count"]      += 1

                    stats["by_event"][event] = (
                        stats["by_event"].get(event, 0) + 1
                    )

            if scores:
                stats["avg_score"] = round(
                    sum(scores) / len(scores), 1
                )

        except Exception as e:
            print(f"[LOGGER STATS ERROR] {e}")

        return stats