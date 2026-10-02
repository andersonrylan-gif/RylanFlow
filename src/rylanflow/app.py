"""Menu-bar app: shows state, lets you pick a model, and wires the pieces together."""

import logging
import subprocess

import pyperclip
import rumps

from rylanflow import sounds
from rylanflow.cleanup import remove_fillers
from rylanflow.config import Config, load_config, save_config
from rylanflow.hotkey import PushToTalk
from rylanflow.inserter import ClipboardInserter
from rylanflow.instance import acquire
from rylanflow.logs import LOG_PATH, dump_threads, setup_logging
from rylanflow.pipeline import Pipeline
from rylanflow.recorder import Recorder
from rylanflow.relaunch import relaunch_and_exit
from rylanflow.transcriber import DEFAULT_MODEL, FAST_MODEL, MLXWhisperTranscriber

log = logging.getLogger(__name__)

ICONS = {"idle": "🎙", "recording": "🔴", "working": "⏳"}
HOTKEYS = {
    "Right Option": "alt_r",
    "Right Command": "cmd_r",
    "Right Control": "ctrl_r",
}
MODELS = {
    "Fast (base)": FAST_MODEL,
    "Accurate (large-v3-turbo)": DEFAULT_MODEL,
}


def accessibility_trusted() -> bool:
    """Whether macOS lets this process watch keys and send Cmd+V."""
    try:
        from ApplicationServices import AXIsProcessTrusted
    except ImportError:
        return True  # can't check; don't nag
    return bool(AXIsProcessTrusted())


class RylanFlowApp(rumps.App):
    def __init__(self, config: Config | None = None) -> None:
        super().__init__("RylanFlow", title=ICONS["idle"], quit_button="Quit")
        self._config = config or load_config()
        self._last_transcript = ""
        self._transcriber = MLXWhisperTranscriber(self._config.model, self._config.language)
        self._inserter = ClipboardInserter()
        self._recorder = Recorder()
        self._pipeline = Pipeline(
            self._recorder,
            self._transcriber,
            on_text=self._on_text,
            on_error=self._notify_error,
            on_cue=self._play_cue,
        )
        self._ptt = PushToTalk(
            self._pipeline.press, self._pipeline.release, key=self._config.hotkey
        )
        self._restarting = False

        self._model_items = {}
        model_menu = rumps.MenuItem("Model")
        for label, repo in MODELS.items():
            item = rumps.MenuItem(label, callback=self._pick_model)
            item.state = int(repo == self._config.model)
            model_menu.add(item)
            self._model_items[label] = item

        self._hotkey_items = {}
        hotkey_menu = rumps.MenuItem("Hotkey")
        for label, key in HOTKEYS.items():
            item = rumps.MenuItem(label, callback=self._pick_hotkey)
            item.state = int(key == self._config.hotkey)
            hotkey_menu.add(item)
            self._hotkey_items[label] = item

        self._sounds_item = rumps.MenuItem("Sound cues", callback=self._toggle_sounds)
        self._sounds_item.state = int(self._config.sounds)
        self._fillers_item = rumps.MenuItem("Remove um / uh", callback=self._toggle_fillers)
        self._fillers_item.state = int(self._config.remove_fillers)
        self.menu = [
            rumps.MenuItem("Copy last transcript", callback=self._copy_last),
            None,
            model_menu,
            hotkey_menu,
            self._sounds_item,
            self._fillers_item,
            None,
            rumps.MenuItem("Open log", callback=self._open_log),
        ]

    def _notify(self, message: str) -> None:
        """Show a notification. Needs a bundle ID, so fall back to the log when run unpackaged."""
        try:
            rumps.notification("RylanFlow", "", message)
        except Exception:
            log.warning("notification unavailable: %s", message)

    def _notify_error(self, message: str) -> None:
        self._notify(message)

    def _play_cue(self, name: str) -> None:
        if self._config.sounds:
            sounds.play(name)

    def _on_text(self, text: str) -> None:
        if self._config.remove_fillers:
            text = remove_fillers(text)
        if not text:
            return
        self._last_transcript = text
        log.info("transcribed %d characters", len(text))
        self._inserter.insert(text)

    # Runs on the main thread: the only place that touches the UI.
    @rumps.timer(0.2)
    def _render(self, _) -> None:
        icon = ICONS[self._pipeline.state]
        if self.title != icon:
            self.title = icon
        self._pipeline.tick()
        reason = self._pipeline.restart_reason()
        if reason and not self._restarting:
            self._restarting = True
            log.error("restarting because %s", reason)
            dump_threads()
            relaunch_and_exit()

    def _pick_model(self, sender: rumps.MenuItem) -> None:
        self._config.model = MODELS[sender.title]
        self._transcriber.model = self._config.model
        for item in self._model_items.values():
            item.state = int(item is sender)
        save_config(self._config)

    def _pick_hotkey(self, sender: rumps.MenuItem) -> None:
        self._config.hotkey = HOTKEYS[sender.title]
        self._ptt.set_key(self._config.hotkey)
        for item in self._hotkey_items.values():
            item.state = int(item is sender)
        save_config(self._config)

    def _toggle_sounds(self, sender: rumps.MenuItem) -> None:
        self._config.sounds = not self._config.sounds
        sender.state = int(self._config.sounds)
        save_config(self._config)

    def _toggle_fillers(self, sender: rumps.MenuItem) -> None:
        self._config.remove_fillers = not self._config.remove_fillers
        sender.state = int(self._config.remove_fillers)
        save_config(self._config)

    def _copy_last(self, _) -> None:
        if self._last_transcript:
            pyperclip.copy(self._last_transcript)
        else:
            self._notify("Nothing transcribed yet.")

    def _open_log(self, _) -> None:
        subprocess.run(["open", str(LOG_PATH)], check=False)

    def run(self, **options) -> None:
        if not accessibility_trusted():
            log.warning("process is not trusted for Accessibility / Input Monitoring")
            self._notify_error(
                "Grant Accessibility and Input Monitoring in System Settings, then restart."
            )
        self._ptt.start()
        super().run(**options)


def main() -> None:
    setup_logging()
    if not acquire():
        log.warning("another RylanFlow is already running; exiting")
        return
    log.info("starting RylanFlow")
    RylanFlowApp().run()
