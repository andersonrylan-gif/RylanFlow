"""Wires the pieces together: record while the key is held, transcribe on release.

Threads:
- The hotkey thread only calls press()/release(), which queue an event and return at once.
  It must never block, or macOS stops delivering key events.
- One "audio-control" thread starts and stops the recorder, in order.
- Transcription happens on a TranscriptionService (its own module): one shared worker thread,
  so dictation and meeting transcription aren't each running their own copy of Whisper.
"""

import logging
import queue
import threading
import time
from collections.abc import Callable

from rylanflow.recorder import SAMPLE_RATE, AudioStuckError, Recorder
from rylanflow.transcriber import Transcriber
from rylanflow.transcription_service import DICTATION_PRIORITY, TranscriptionService

log = logging.getLogger(__name__)

MIN_SECONDS = 0.3  # shorter than this is an accidental tap
MAX_SECONDS = 10 * 60  # stop recording automatically after this long
_TAG = "dictation"


class Pipeline:
    def __init__(
        self,
        recorder: Recorder,
        transcriber: Transcriber,
        on_text: Callable[[str, float], None],
        on_error: Callable[[str], None] | None = None,
        on_cue: Callable[[str], None] | None = None,
        clock=time.monotonic,
        service: TranscriptionService | None = None,
    ) -> None:
        """`service` lets dictation share one Whisper worker with meeting transcription; if
        omitted, Pipeline creates (and owns, and stops on close()) a private one."""
        self._recorder = recorder
        self._transcriber = transcriber
        self._on_text = on_text
        self._on_error = on_error
        self._on_cue = on_cue
        self._clock = clock
        self._service = service or TranscriptionService(clock=clock)
        self._owns_service = service is None
        self._events: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()  # guards the fields below
        self._recording_since: float | None = None
        self._worker = threading.Thread(target=self._loop, name="audio-control", daemon=True)
        self._worker.start()

    # --- called from the hotkey thread: must return immediately ---
    def press(self) -> None:
        self._events.put("press")

    def release(self) -> None:
        self._events.put("release")

    def close(self) -> None:
        self._events.put("quit")
        if self._owns_service:
            self._service.stop()

    # --- read by the UI ---
    @property
    def state(self) -> str:
        with self._lock:
            if self._recording_since is not None:
                return "recording"
        return "working" if self._service.pending_count(_TAG) else "idle"

    def tick(self) -> None:
        """Call periodically. Ends recordings that run past MAX_SECONDS."""
        with self._lock:
            since = self._recording_since
        if since is not None and self._clock() - since > MAX_SECONDS:
            log.warning("recording hit the %d s limit; stopping", MAX_SECONDS)
            self.release()

    def restart_reason(self) -> str | None:
        """Why the app should restart itself, or None. Waits for pending pastes to finish."""
        hung = self._service.restart_reason()
        if hung:
            return hung
        with self._lock:
            busy = self._recording_since is not None
        busy = busy or self._service.pending_count(_TAG) > 0
        if self._recorder.stuck and not busy:
            return "the microphone stopped responding"
        return None

    # --- audio-control thread ---
    def _loop(self) -> None:
        while (event := self._events.get()) != "quit":
            try:
                if event == "press":
                    self._start()
                else:
                    self._stop()
            except AudioStuckError:
                log.exception("microphone is stuck")
                self._error("The microphone stopped responding. RylanFlow will restart.")
            except Exception:
                log.exception("could not %s recording", "start" if event == "press" else "stop")
                self._error("Can't use the microphone. Check Microphone permission and input.")

    def _start(self) -> None:
        if self.state == "recording":
            return
        self._recorder.start()
        with self._lock:
            self._recording_since = self._clock()
        self._cue("start")

    def _stop(self) -> None:
        with self._lock:
            if self._recording_since is None:
                return
            self._recording_since = None
        audio = self._recorder.stop()  # returns immediately; the mic closes in the background
        self._cue("stop")
        if audio.size < MIN_SECONDS * SAMPLE_RATE:
            return
        seconds = audio.size / SAMPLE_RATE
        self._service.submit(
            self._transcriber.transcribe,
            audio,
            DICTATION_PRIORITY,
            callback=lambda text: self._handle_text(text, seconds),
            on_error=self._error,
            tag=_TAG,
        )

    def _handle_text(self, text: str, seconds: float) -> None:
        if text:
            self._on_text(text, seconds)

    def _cue(self, name: str) -> None:
        if self._on_cue:
            try:
                self._on_cue(name)
            except Exception:
                log.exception("sound cue failed")

    def _error(self, message: str) -> None:
        if self._on_error:
            self._on_error(message)
