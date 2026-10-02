"""Menu-bar app: shows state, lets you pick a model, and wires the pieces together."""

import threading

import rumps

from rylanflow.hotkey import PushToTalk
from rylanflow.inserter import ClipboardInserter
from rylanflow.pipeline import Pipeline
from rylanflow.recorder import Recorder
from rylanflow.transcriber import DEFAULT_MODEL, FAST_MODEL, MLXWhisperTranscriber

ICONS = {"idle": "🎙", "recording": "🔴", "working": "⏳"}
MODELS = {
    "Fast (base)": FAST_MODEL,
    "Accurate (large-v3-turbo)": DEFAULT_MODEL,
}


class RylanFlowApp(rumps.App):
    def __init__(self, model: str = FAST_MODEL, key: str = "alt_r") -> None:
        super().__init__("RylanFlow", title=ICONS["idle"], quit_button="Quit")
        self._state = "idle"
        self._state_lock = threading.Lock()
        self._transcriber = MLXWhisperTranscriber(model)
        self._inserter = ClipboardInserter()
        self._pipeline = Pipeline(
            Recorder(), self._transcriber, on_text=self._inserter.insert, on_done=self._on_done
        )
        self._ptt = PushToTalk(self._on_press, self._on_release, key=key)

        self._model_items = {}
        model_menu = rumps.MenuItem("Model")
        for label, repo in MODELS.items():
            item = rumps.MenuItem(label, callback=self._pick_model)
            item.state = int(repo == model)
            model_menu.add(item)
            self._model_items[label] = item
        self.menu = [model_menu]

    # Hotkey and worker threads only set state; the UI timer below renders it on the main thread.
    def _set_state(self, state: str) -> None:
        with self._state_lock:
            self._state = state

    def _on_press(self) -> None:
        self._set_state("recording")
        self._pipeline.start_recording()

    def _on_release(self) -> None:
        self._set_state("working")
        self._pipeline.stop_and_transcribe()

    def _on_done(self) -> None:
        self._set_state("idle")

    @rumps.timer(0.2)
    def _render(self, _) -> None:
        with self._state_lock:
            icon = ICONS[self._state]
        if self.title != icon:
            self.title = icon

    def _pick_model(self, sender: rumps.MenuItem) -> None:
        self._transcriber.model = MODELS[sender.title]
        for item in self._model_items.values():
            item.state = int(item is sender)

    def run(self, **options) -> None:
        self._ptt.start()
        super().run(**options)


def main(model: str = FAST_MODEL, key: str = "alt_r") -> None:
    RylanFlowApp(model, key).run()
