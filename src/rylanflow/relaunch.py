"""Restart the app in a fresh process (the only way to recover from a CoreAudio hang)."""

import os
import shlex
import subprocess
import sys
from pathlib import Path


def relaunch_command(executable: str, frozen: bool) -> list[str]:
    if frozen:  # .../RylanFlow.app/Contents/MacOS/RylanFlow
        return ["/usr/bin/open", str(Path(executable).resolve().parents[2])]
    return [executable, "-m", "rylanflow", "app"]


def relaunch_and_exit(delay: float = 1.5) -> None:
    """Start a new copy after this one has exited, then exit immediately.

    os._exit skips joining threads, one of which may be stuck inside CoreAudio forever.
    """
    command = relaunch_command(sys.executable, getattr(sys, "frozen", False))
    script = f"sleep {delay}; exec {shlex.join(command)}"
    devnull = subprocess.DEVNULL
    subprocess.Popen(
        ["/bin/sh", "-c", script],
        start_new_session=True,
        stdin=devnull,
        stdout=devnull,
        stderr=devnull,
    )
    os._exit(0)
