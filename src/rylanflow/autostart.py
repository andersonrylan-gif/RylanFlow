"""Start RylanFlow at login using a per-user macOS LaunchAgent."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.rylananderson.rylanflow"
PLIST_PATH = Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()
LOG_DIR = Path("~/Library/Logs/RylanFlow").expanduser()


def build_plist(python: str) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": [python, "-m", "rylanflow", "app"],
        "RunAtLoad": True,
        # Restart after a crash, but not after the user picks Quit (a clean exit).
        "KeepAlive": {"SuccessfulExit": False},
        "StandardOutPath": str(LOG_DIR / "launchd.out.log"),
        "StandardErrorPath": str(LOG_DIR / "launchd.err.log"),
    }


def _launchctl(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True)


def _domain() -> str:
    return f"gui/{os.getuid()}"


def real_python() -> str:
    """The interpreter that macOS will see; Accessibility permission must be granted to it."""
    return os.path.realpath(sys.executable)


def enable() -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PLIST_PATH.open("wb") as f:
        plistlib.dump(build_plist(sys.executable), f)
    _launchctl("bootout", f"{_domain()}/{LABEL}")  # replace any older copy
    result = _launchctl("bootstrap", _domain(), str(PLIST_PATH))
    if result.returncode != 0:
        return f"Installed {PLIST_PATH}, but launchctl said: {result.stderr.strip()}"
    return f"RylanFlow will now start at login (installed {PLIST_PATH})."


def disable() -> str:
    _launchctl("bootout", f"{_domain()}/{LABEL}")
    PLIST_PATH.unlink(missing_ok=True)
    return "Start at login is off."


def status() -> str:
    installed = PLIST_PATH.exists()
    running = _launchctl("print", f"{_domain()}/{LABEL}").returncode == 0
    return (
        f"start at login: {'on' if installed else 'off'}; loaded now: {'yes' if running else 'no'}"
    )
