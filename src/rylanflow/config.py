"""User settings in ~/.config/rylanflow/config.toml."""

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

from rylanflow.transcriber import FAST_MODEL

CONFIG_PATH = Path(
    os.environ.get("RYLANFLOW_CONFIG", "~/.config/rylanflow/config.toml")
).expanduser()


@dataclass
class Config:
    hotkey: str = "alt_r"  # pynput key name; alt_r is Right Option
    model: str = FAST_MODEL  # Hugging Face repo of an mlx-whisper model
    language: str | None = None  # e.g. "en"; None lets Whisper auto-detect
    sounds: bool = True  # play a cue when recording starts and stops
    remove_fillers: bool = True  # drop "um" / "uh" from transcripts
    dashboard_seen: bool = False  # True once the dashboard has auto-opened on first launch
    overlay: str = "bottom"  # where the dictation pop-up appears: "bottom", "top", or "off"
    auto_record_meetings: bool = True  # auto-start/stop meeting recording from detector.py


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Read settings, falling back to defaults for a missing file or missing keys."""
    if not path.exists():
        return Config()
    with path.open("rb") as f:
        data = tomllib.load(f)
    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
    return Config(**known)


def save_config(config: Config, path: Path = CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f'hotkey = "{config.hotkey}"',
        f'model = "{config.model}"',
        f"sounds = {str(config.sounds).lower()}",
        f"remove_fillers = {str(config.remove_fillers).lower()}",
        f"dashboard_seen = {str(config.dashboard_seen).lower()}",
        f'overlay = "{config.overlay}"',
        f"auto_record_meetings = {str(config.auto_record_meetings).lower()}",
    ]
    if config.language:
        lines.append(f'language = "{config.language}"')
    path.write_text("\n".join(lines) + "\n")
