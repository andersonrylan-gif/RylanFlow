"""Single-instance guard: a second copy of the app exits instead of competing for the hotkey."""

import fcntl
from pathlib import Path

LOCK_PATH = Path("~/.config/rylanflow/app.lock").expanduser()

_lock_file = None  # kept open for the life of the process; the lock dies with it


def acquire(path: Path = LOCK_PATH) -> bool:
    """Take the lock. Returns False if another copy already holds it."""
    global _lock_file
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        f.close()
        return False
    _lock_file = f
    return True
