"""Rotating log file at ~/Library/Logs/RylanFlow/rylanflow.log."""

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_PATH = Path("~/Library/Logs/RylanFlow/rylanflow.log").expanduser()


def setup_logging(path: Path = LOG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(path, maxBytes=512_000, backupCount=2)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("rylanflow")
    root.setLevel(logging.INFO)
    root.addHandler(handler)
