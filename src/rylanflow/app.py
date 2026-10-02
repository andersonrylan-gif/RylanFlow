"""Menu-bar app: shows state, lets you pick a model, and wires the pieces together."""

import logging
import subprocess
import threading

import pyperclip
import rumps
from PyObjCTools import AppHelper

from rylanflow import autostart, sounds
from rylanflow.cleanup import remove_fillers
from rylanflow.config import Config, load_config, save_config
from rylanflow.dashboard.server import DashboardServer
from rylanflow.hotkey import PushToTalk
from rylanflow.inserter import ClipboardInserter
from rylanflow.instance import acquire
from rylanflow.logs import LOG_PATH, dump_threads, setup_logging
from rylanflow.pipeline import Pipeline
from rylanflow.recorder import Recorder
from rylanflow.relaunch import relaunch_and_exit
from rylanflow.store import Store
from rylanflow.transcriber import DEFAULT_MODEL, FAST_MODEL, MLXWhisperTranscriber

APPLY_SETTINGS_TIMEOUT = 5.0  # seconds the dashboard's HTTP thread waits for the main thread

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


def frontmost_app() -> str | None:
    """The name of the app the user was dictating into, best-effort."""
    try:
        from AppKit import NSWorkspace

        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        return str(app.localizedName()) if app else None
    except Exception:
        log.exception("could not read the frontmost app")
        return None


class RylanFlowApp(rumps.App):
    def __init__(self, config: Config | None = None, store: Store | None = None) -> None:
        super().__init__("RylanFlow", title=ICONS["idle"], quit_button="Quit")
        self._config = config or load_config()
        self._store = store or Store()
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

        # `self` satisfies the dashboard's Actions protocol (get_settings/apply_settings below).
        self._dashboard = DashboardServer(self._store, self)
        self._dashboard_url = self._dashboard.start()
        log.info("dashboard listening at %s", self._dashboard_url.split("?")[0])
        self._dashboard_window = None  # created lazily, on the main thread, on first use

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
            rumps.MenuItem("Open Dashboard…", callback=self._open_dashboard),
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

    def _on_text(self, text: str, seconds: float) -> None:
        if self._config.remove_fillers:
            text = remove_fillers(text)
        if not text:
            return
        self._last_transcript = text
        log.info("transcribed %d characters", len(text))
        try:
            self._store.add_dictation(text, seconds, frontmost_app(), self._config.model)
        except Exception:
            # Losing the pending paste because the database hiccupped would be far worse
            # than losing the history entry, so this never blocks the insert below.
            log.exception("could not save the dictation to the store")
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
        self._apply_settings_main_thread({"model": MODELS[sender.title]})

    def _pick_hotkey(self, sender: rumps.MenuItem) -> None:
        self._apply_settings_main_thread({"hotkey": HOTKEYS[sender.title]})

    def _toggle_sounds(self, sender: rumps.MenuItem) -> None:
        self._apply_settings_main_thread({"sounds": not self._config.sounds})

    def _toggle_fillers(self, sender: rumps.MenuItem) -> None:
        self._apply_settings_main_thread({"remove_fillers": not self._config.remove_fillers})

    def _apply_settings_main_thread(self, changes: dict) -> dict:
        """The one place settings actually change. Must run on the main thread: it touches
        AppKit menu items. Both the menu callbacks above and the dashboard (via apply_settings,
        hopping over with AppHelper.callAfter) go through this."""
        if (hotkey := changes.get("hotkey")) in HOTKEYS.values():
            self._config.hotkey = hotkey
            self._ptt.set_key(hotkey)
            for item in self._hotkey_items.values():
                item.state = int(HOTKEYS[item.title] == hotkey)
        if (model := changes.get("model")) in MODELS.values():
            self._config.model = model
            self._transcriber.model = model
            for item in self._model_items.values():
                item.state = int(MODELS[item.title] == model)
        if "sounds" in changes:
            self._config.sounds = bool(changes["sounds"])
            self._sounds_item.state = int(self._config.sounds)
        if "remove_fillers" in changes:
            self._config.remove_fillers = bool(changes["remove_fillers"])
            self._fillers_item.state = int(self._config.remove_fillers)
        if "start_at_login" in changes:
            (autostart.enable if changes["start_at_login"] else autostart.disable)()
        save_config(self._config)
        return self.get_settings()

    # --- dashboard.server.Actions protocol: may be called from the dashboard's HTTP thread ---

    def get_settings(self) -> dict:
        return {
            "hotkey": self._config.hotkey,
            "model": self._config.model,
            "sounds": self._config.sounds,
            "remove_fillers": self._config.remove_fillers,
            "start_at_login": autostart.is_enabled(),
            # So the dashboard's Settings page never hardcodes these choices itself.
            "available_hotkeys": HOTKEYS,
            "available_models": MODELS,
        }

    def apply_settings(self, changes: dict) -> None:
        done = threading.Event()

        def work() -> None:
            try:
                self._apply_settings_main_thread(changes)
            finally:
                done.set()

        AppHelper.callAfter(work)
        if not done.wait(APPLY_SETTINGS_TIMEOUT):
            log.warning("applying settings from the dashboard timed out")

    def _open_dashboard(self, _) -> None:
        try:
            from rylanflow.dashboard.window import DashboardWindow

            if self._dashboard_window is None:
                self._dashboard_window = DashboardWindow(self._dashboard_url)
            self._dashboard_window.show()
        except Exception:
            log.exception("could not open the native dashboard window; opening in a browser")
            import webbrowser

            webbrowser.open(self._dashboard_url)

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
        if not self._config.dashboard_seen:
            self._config.dashboard_seen = True
            save_config(self._config)
            # Scheduled rather than called directly: the run loop below hasn't started yet, and
            # AppHelper.callAfter defers this block until it has.
            AppHelper.callAfter(lambda: self._open_dashboard(None))
        super().run(**options)


def main() -> None:
    setup_logging()
    if not acquire():
        log.warning("another RylanFlow is already running; exiting")
        return
    log.info("starting RylanFlow")
    RylanFlowApp().run()
