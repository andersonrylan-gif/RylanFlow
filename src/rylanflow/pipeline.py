"""Wires the pieces together: record while the key is held, transcribe on release.

Threads:
- The hotkey thread only calls press()/release(), which queue an event and return at once.
  It must never block, or macOS stops delivering key events.
- One "audio-control" thread starts and stops the recorder, in order.
- Each transcription runs on its own worker thread, one at a time.
"""

import logging
import queue
import threading
import time
from collections.abc import Callable

import numpy as np

from rylanflow.recorder import SAMPLE_RATE, AudioStuckError, Recorder
from rylanflow.transcriber import Transcriber

log = logging.getLogger(__name__)

MIN_SECONDS = 0.3  # shorter than this is an accidental tap
MAX_SECONDS = 10 * 60  # stop recording automatically after this long


def transcribe_timeout(audio_seconds: float) -> float:
    """How long a transcription may take before we treat it as hung."""
    return 60 + 3 * audio_seconds


class Pipeline:
    def __init__(
        self,
        recorder: Recorder,
        transcriber: Transcriber,
        on_text: Callable[[str], None],
        on_error: Callable[[str], None] | None = None,
        on_cue: Callable[[str], None] | None = None,
        clock=time.monotonic,
    ) -> None:
        self._recorder = recorder
        self._transcriber = transcriber
        self._on_text = on_text
        self._on_error = on_error
        self._on_cue = on_cue
        self._clock = clock
        self._events: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()  # guards the fields below
        self._recording_since: float | None = None
        self._jobs: dict[int, float] = {}  # running transcription id -> deadline
        self._next_job = 0
        self._transcribe_lock = threading.Lock()  # one transcription at a time
        self._worker = threading.Thread(target=self._loop, name="audio-control", daemon=True)
        self._worker.start()

    # --- called from the hotkey thread: must return immediately ---
    def press(self) -> None:
        self._events.put("press")

    def release(self) -> None:
        self._events.put("release")

    def close(self) -> None:
        self._events.put("quit")

    # --- read by the UI ---
    @property
    def state(self) -> str:
        with self._lock:
            if self._recording_since is not None:
                return "recording"
            return "working" if self._jobs else "idle"

    def tick(self) -> None:
        """Call periodically. Ends recordings that run past MAX_SECONDS."""
        with self._lock:
            since = self._recording_since
        if since is not None and self._clock() - since > MAX_SECONDS:
            log.warning("recording hit the %d s limit; stopping", MAX_SECONDS)
            self.release()

    def restart_reason(self) -> str | None:
        """Why the app should restart itself, or None. Waits for pending pastes to finish."""
        now = self._clock()
        with self._lock:
            hung = any(now > deadline for deadline in self._jobs.values())
            busy = bool(self._jobs) or self._recording_since is not None
        if hung:
            return "a transcription stopped responding"
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
        with self._lock:
            job = self._next_job
            self._next_job += 1
            self._jobs[job] = self._clock() + transcribe_timeout(audio.size / SAMPLE_RATE)
        threading.Thread(
            target=self._transcribe, args=(job, audio), name="transcribe", daemon=True
        ).start()

    # --- transcription thread ---
    def _transcribe(self, job: int, audio: np.ndarray) -> None:
        try:
            with self._transcribe_lock:
                text = self._transcriber.transcribe(audio)
            if text:
                self._on_text(text)
        except Exception:
            log.exception("transcription or insertion failed")
            self._error("Transcription failed. See the log for details.")
        finally:
            with self._lock:
                self._jobs.pop(job, None)

    def _cue(self, name: str) -> None:
        if self._on_cue:
            try:
                self._on_cue(name)
            except Exception:
                log.exception("sound cue failed")

    def _error(self, message: str) -> None:
        if self._on_error:
            self._on_error(message)
