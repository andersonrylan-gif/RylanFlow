"""User settings in ~/.config/rylanflow/config.toml."""

import json
import os
import tomllib
from dataclasses import dataclass, field
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
    snippets: dict[str, str] = field(default_factory=dict)  # spoken trigger -> text to paste
    vocabulary: list[str] = field(default_factory=list)  # names/jargon Whisper should spell right


def load_config(path: Path = CONFIG_PATH) -> Config:
    """Read settings, falling back to defaults for a missing file or missing keys."""
    if not path.exists():
        return Config()
    with path.open("rb") as f:
        data = tomllib.load(f)
    known = {k: v for k, v in data.items() if k in Config.__dataclass_fields__}
    if "snippets" in known:
        table = known["snippets"]
        known["snippets"] = (
            {k: v for k, v in table.items() if isinstance(v, str)}
            if isinstance(table, dict)
            else {}
        )
    if "vocabulary" in known:
        words = known["vocabulary"]
        known["vocabulary"] = (
            [w for w in words if isinstance(w, str)] if isinstance(words, list) else []
        )
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
        # A JSON string is a valid TOML basic string, so json.dumps handles quoting/escaping.
        "snippets = {"
        + ", ".join(f"{json.dumps(k)} = {json.dumps(v)}" for k, v in config.snippets.items())
        + "}",
        f"vocabulary = [{', '.join(json.dumps(w) for w in config.vocabulary)}]",
    ]
    if config.language:
        lines.append(f'language = "{config.language}"')
    # Write to a temp file and rename it into place rather than writing config.toml directly --
    # a process killed mid-write (a crash, a force-quit, `pkill -9`) can otherwise leave the
    # file truncated at a line boundary, which is still valid-but-incomplete TOML: fields
    # written before the cut survive, everything after silently reverts to its dataclass
    # default on the next load, with no error anywhere. os.replace() is atomic on the same
    # filesystem, so readers only ever see the old complete file or the new complete file.
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text("\n".join(lines) + "\n")
    tmp_path.replace(path)
