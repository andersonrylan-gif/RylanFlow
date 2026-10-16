"""Short audio cues using macOS system sounds."""

import logging
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

SOUNDS = {
    "start": Path("/System/Library/Sounds/Tink.aiff"),
    "stop": Path("/System/Library/Sounds/Pop.aiff"),
}


def play(name: str) -> None:
    """Fire and forget. afplay runs on a daemon thread so it's reaped (no zombie per cue) and a
    failure -- missing file, no audio device -- lands in the log instead of vanishing."""
    path = SOUNDS[name]
    if not path.exists():
        log.warning("cue %r: %s is missing", name, path)
        return
    threading.Thread(target=_run_afplay, args=(name, path), daemon=True).start()


def _run_afplay(name: str, path: Path) -> None:
    try:
        result = subprocess.run(
            ["afplay", str(path)], capture_output=True, text=True, timeout=10, check=False
        )
    except Exception:
        log.exception("cue %r: could not run afplay", name)
        return
    if result.returncode != 0:
        log.warning("cue %r: afplay exited %d: %s", name, result.returncode, result.stderr.strip())
