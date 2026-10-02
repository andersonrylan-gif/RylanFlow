"""Short audio cues using macOS system sounds."""

import subprocess
from pathlib import Path

SOUNDS = {
    "start": Path("/System/Library/Sounds/Tink.aiff"),
    "stop": Path("/System/Library/Sounds/Pop.aiff"),
}


def play(name: str) -> None:
    path = SOUNDS[name]
    if path.exists():
        subprocess.Popen(
            ["afplay", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
