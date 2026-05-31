# ╔══════════════════════════════════════════════════════════════════╗
#  GIBBZ SMC COP — log_config.py
#  Centralized logging setup.
#
#  Usage:
#    from log_config import get_logger
#    log = get_logger(__name__)
#    log.error("Something failed: %s", e)
#    log.warning("Degraded: %s", reason)
#    log.info("Trade signal: %s", event)
#
#  Log file: logs/gibbz.log  (rotates at 5 MB, keeps 3 backups)
#  Console:  WARNING and above (so INFO stays out of the terminal)
#  Override log level: $env:GIBBZ_LOG_LEVEL = "DEBUG"
# ╚══════════════════════════════════════════════════════════════════╝

import logging
import os
from logging.handlers import RotatingFileHandler

_LOG_DIR   = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
_LOG_FILE  = os.path.join(_LOG_DIR, "gibbz.log")
_LOG_LEVEL = os.environ.get("GIBBZ_LOG_LEVEL", "INFO").upper()
_FMT       = "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
_DATE_FMT  = "%Y-%m-%d %H:%M:%S"

_configured = False


def _configure() -> None:
    global _configured
    if _configured:
        return
    _configured = True

    os.makedirs(_LOG_DIR, exist_ok=True)

    root = logging.getLogger("gibbz")
    root.setLevel(getattr(logging, _LOG_LEVEL, logging.INFO))

    if not root.handlers:
        fh = RotatingFileHandler(
            _LOG_FILE,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
        root.addHandler(fh)

        ch = logging.StreamHandler()
        ch.setLevel(logging.WARNING)
        ch.setFormatter(logging.Formatter(_FMT, datefmt=_DATE_FMT))
        root.addHandler(ch)


def get_logger(name: str) -> logging.Logger:
    _configure()
    return logging.getLogger("gibbz." + name.lstrip("gibbz."))
