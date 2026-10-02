"""Start RylanFlow at login using a per-user macOS LaunchAgent."""

import os
import plistlib
import subprocess
import sys
from pathlib import Path

LABEL = "com.rylananderson.rylanflow"
PLIST_PATH = Path(f"~/Library/LaunchAgents/{LABEL}.plist").expanduser()
LOG_DIR = Path("~/Library/Logs/RylanFlow").expanduser()


APP_PATHS = [
    Path("/Applications/RylanFlow.app"),
    Path("~/Applications/RylanFlow.app").expanduser(),
]


def find_app() -> Path | None:
    return next((p for p in APP_PATHS if p.exists()), None)


def launch_command(python: str, app: Path | None) -> list[str]:
    """Prefer the packaged app (launched through LaunchServices so its own permissions apply)."""
    if app is not None:
        return ["/usr/bin/open", "-a", str(app)]
    return [python, "-m", "rylanflow", "app"]


def build_plist(python: str, app: Path | None = None) -> dict:
    return {
        "Label": LABEL,
        "ProgramArguments": launch_command(python, app),
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


def is_enabled() -> bool:
    """Cheap check for UI purposes: whether the LaunchAgent is installed."""
    return PLIST_PATH.exists()


def enable() -> str:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    with PLIST_PATH.open("wb") as f:
        plistlib.dump(build_plist(sys.executable, find_app()), f)
    _launchctl("bootout", f"{_domain()}/{LABEL}")  # replace any older copy
    result = _launchctl("bootstrap", _domain(), str(PLIST_PATH))
    if result.returncode != 0:
        return f"Installed {PLIST_PATH}, but launchctl said: {result.stderr.strip()}"
    what = "the app in Applications" if find_app() else "the Python version (no .app found)"
    return f"RylanFlow will now start at login using {what} (installed {PLIST_PATH})."


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
