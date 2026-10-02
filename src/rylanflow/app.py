"""Menu-bar app: shows state, lets you pick a model, and wires the pieces together."""

import logging
import subprocess
import threading
import time

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
from rylanflow.meeting_indicator import MeetingIndicator
from rylanflow.meetings import detector
from rylanflow.meetings.calendar import CalendarLookup
from rylanflow.meetings.detector_logic import MeetingDetector, Start, Stop
from rylanflow.meetings.session import MeetingSession
from rylanflow.overlay import Overlay
from rylanflow.pipeline import Pipeline
from rylanflow.recorder import Recorder
from rylanflow.relaunch import relaunch_and_exit
from rylanflow.store import Store
from rylanflow.transcriber import DEFAULT_MODEL, FAST_MODEL, MLXWhisperTranscriber
from rylanflow.transcription_service import TranscriptionService

APPLY_SETTINGS_TIMEOUT = 5.0  # seconds the dashboard's HTTP thread waits for the main thread
DETECTOR_POLL_INTERVAL = 2.0  # seconds between auto-record probes

log = logging.getLogger(__name__)

ICONS = {"idle": "🎙", "recording": "🔴", "working": "⏳"}
MEETING_ICON = " 📹"
HOTKEYS = {
    "Right Option": "alt_r",
    "Right Command": "cmd_r",
    "Right Control": "ctrl_r",
}
MODELS = {
    "Fast (base)": FAST_MODEL,
    "Accurate (large-v3-turbo)": DEFAULT_MODEL,
}
OVERLAY_POSITIONS = {
    "Bottom": "bottom",
    "Top": "top",
    "Off": "off",
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


def my_display_name() -> str:
    """The name shown for the "You" speaker in meeting transcripts, best-effort."""
    try:
        from AppKit import NSFullUserName

        return str(NSFullUserName()) or "You"
    except Exception:
        return "You"


def _build_calendar_lookup() -> CalendarLookup | None:
    """None if EventKit isn't available for some reason -- meetings just keep their app-based
    title in that case, same as before calendar lookup existed."""
    try:
        return CalendarLookup()
    except Exception:
        log.exception("could not set up calendar lookup; meeting titles won't use it")
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
        # Shared so dictation and meeting transcription run on one Whisper worker, with
        # dictation always jumping the queue ahead of meeting chunks (see TranscriptionService).
        self._transcription_service = TranscriptionService()
        self._pipeline = Pipeline(
            self._recorder,
            self._transcriber,
            on_text=self._on_text,
            on_error=self._notify_error,
            on_cue=self._play_cue,
            service=self._transcription_service,
        )
        self._ptt = PushToTalk(
            self._pipeline.press, self._pipeline.release, key=self._config.hotkey
        )
        self._restarting = False

        self._meeting_session = MeetingSession(
            self._store,
            self._transcription_service,
            self._transcriber,
            my_display_name(),
            calendar_lookup=_build_calendar_lookup(),
        )
        self._meeting_was_active = False
        self._meeting_detector = MeetingDetector()
        self._meeting_indicator = MeetingIndicator()
        self._watch_for_sleep()

        self._overlay = Overlay(position=self._config.overlay)
        self._overlay.set_sources(lambda: self._pipeline.state, lambda: self._recorder.level)

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
        self._meeting_item = rumps.MenuItem(
            "Start meeting recording", callback=self._toggle_meeting
        )
        self.menu = [
            rumps.MenuItem("Open Dashboard…", callback=self._open_dashboard),
            rumps.MenuItem("Copy last transcript", callback=self._copy_last),
            self._meeting_item,
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
        meeting_active = self._meeting_session.active
        icon = ICONS[self._pipeline.state] + (MEETING_ICON if meeting_active else "")
        if self.title != icon:
            self.title = icon
        if meeting_active != self._meeting_was_active:
            self._meeting_was_active = meeting_active
            self._meeting_item.title = (
                "Stop meeting recording" if meeting_active else "Start meeting recording"
            )
            if meeting_active:
                self._meeting_indicator.start()
            else:
                self._meeting_indicator.stop()
        self._pipeline.tick()
        if self._config.overlay != "off" and self._pipeline.state != "idle":
            self._overlay.ensure_running()  # cheap no-op once it's already running
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

    def _toggle_meeting(self, _sender: rumps.MenuItem) -> None:
        if self._meeting_session.active:
            self.stop_meeting()
        else:
            self.start_meeting()

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
        if (overlay := changes.get("overlay")) in OVERLAY_POSITIONS.values():
            self._config.overlay = overlay
            if overlay == "off":
                self._overlay.stop()  # hide immediately, don't wait for the current fade
            else:
                self._overlay.set_position(overlay)
        if "auto_record_meetings" in changes:
            self._config.auto_record_meetings = bool(changes["auto_record_meetings"])
            # Fresh debounce state either way, so a stale timer from before the toggle can't
            # cause an instant start/stop the moment it's flipped back on.
            self._meeting_detector = MeetingDetector()
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
            "overlay": self._config.overlay,
            "auto_record_meetings": self._config.auto_record_meetings,
            # So the dashboard's Settings page never hardcodes these choices itself.
            "available_hotkeys": HOTKEYS,
            "available_models": MODELS,
            "available_overlay_positions": OVERLAY_POSITIONS,
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

    def start_meeting(self, source_app: str | None = None) -> int:
        """Starts a meeting (a no-op returning the same id if one is already active). Safe to
        call from any thread -- MeetingSession.start() itself is, and _render picks up the menu
        label, icon and indicator changes on its own without needing to hop to the main thread
        here. `source_app` lets the auto-record detector report the app it actually saw."""
        meeting_id = self._meeting_session.start(
            source_app=source_app or frontmost_app(), on_error=self._notify_error
        )
        self._meeting_detector.note_external_start()
        self._notify("Recording meeting — open RylanFlow to stop.")
        return meeting_id

    def stop_meeting(self) -> int | None:
        self._meeting_detector.note_external_stop()
        return self._meeting_session.stop()

    def _watch_for_sleep(self) -> None:
        """Stop an active meeting cleanly when the Mac is about to sleep (lid close, Apple menu
        > Sleep, etc.) -- otherwise it would just sit "recording" with a closed lid, capturing
        nothing, until the owner notices. A block-based observer needs no NSObject subclass,
        unlike addObserver_selector_name_object_."""
        from AppKit import NSWorkspace, NSWorkspaceWillSleepNotification

        NSWorkspace.sharedWorkspace().notificationCenter().addObserverForName_object_queue_usingBlock_(
            NSWorkspaceWillSleepNotification, None, None, lambda _note: self._on_system_sleep()
        )

    def _on_system_sleep(self) -> None:
        if self._meeting_session.active:
            log.info("system is going to sleep; stopping the active meeting")
            self.stop_meeting()

    # --- auto-record: a background thread probes for "looks like a meeting" every 2s and
    # applies the (debounced) decision on the main thread ---

    def _meeting_detector_loop(self) -> None:
        last_mic_ids: frozenset[str] = frozenset()
        while True:
            time.sleep(DETECTOR_POLL_INTERVAL)
            if not self._config.auto_record_meetings:
                continue
            try:
                signals = detector.probe()
                decision = self._meeting_detector.update(signals)
            except Exception:
                log.exception("meeting detector probe failed")
                continue
            # Logged only on change (not every poll), so a long meeting doesn't flood the log --
            # this is the one line that answers "did the detector ever even see a meeting app
            # using the mic?" when auto-record doesn't behave as expected.
            mic_ids = frozenset(signals.mic_bundle_ids)
            if mic_ids != last_mic_ids:
                log.info("meeting detector: mic now in use by %s", sorted(mic_ids) or "nothing")
                last_mic_ids = mic_ids
            if isinstance(decision, Start):
                log.info("meeting detector: starting (looks like %s)", decision.app_name)
                AppHelper.callAfter(lambda app_name=decision.app_name: self._auto_start(app_name))
            elif isinstance(decision, Stop):
                log.info("meeting detector: stopping (signal has been gone for a while)")
                AppHelper.callAfter(self._auto_stop)

    def _auto_start(self, app_name: str) -> None:
        self._meeting_session.start(source_app=app_name, on_error=self._notify_error)
        self._notify(f"Transcribing your {app_name} meeting.")

    def _auto_stop(self) -> None:
        self._meeting_session.stop()

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
        log.info("auto-record meetings: %s", self._config.auto_record_meetings)
        threading.Thread(
            target=self._meeting_detector_loop, name="meeting-detector", daemon=True
        ).start()
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
