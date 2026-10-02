"""Rotating log file at ~/Library/Logs/RylanFlow/rylanflow.log."""

import faulthandler
import logging
import signal
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_PATH = Path("~/Library/Logs/RylanFlow/rylanflow.log").expanduser()

_fault_file = None  # stays open so thread dumps can be written at any time


def setup_logging(path: Path = LOG_PATH) -> None:
    global _fault_file
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=2)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("rylanflow")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    # `kill -USR1 <pid>` writes every thread's Python stack to the log, for debugging hangs.
    _fault_file = path.open("a")
    faulthandler.register(signal.SIGUSR1, file=_fault_file, all_threads=True)


def dump_threads() -> None:
    """Write every thread's Python stack to the log file."""
    if _fault_file is not None:
        _fault_file.write("--- thread dump ---\n")
        _fault_file.flush()
        faulthandler.dump_traceback(file=_fault_file, all_threads=True)
