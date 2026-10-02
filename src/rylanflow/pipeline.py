"""Wires the pieces together: record while the key is held, transcribe on release."""

import logging
import threading
from collections.abc import Callable

import numpy as np

from rylanflow.recorder import SAMPLE_RATE, Recorder
from rylanflow.transcriber import Transcriber

log = logging.getLogger(__name__)

MIN_SECONDS = 0.3  # shorter than this is an accidental tap


class Pipeline:
    def __init__(
        self,
        recorder: Recorder,
        transcriber: Transcriber,
        on_text: Callable[[str], None],
        on_done: Callable[[], None] | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> None:
        self._recorder = recorder
        self._transcriber = transcriber
        self._on_text = on_text
        self._on_done = on_done
        self._on_error = on_error
        self._lock = threading.Lock()  # one transcription at a time

    def start_recording(self) -> None:
        self._recorder.start()

    def stop_and_transcribe(self) -> threading.Thread:
        """Stop recording and transcribe on a worker thread so the hotkey stays responsive."""
        audio = self._recorder.stop()
        thread = threading.Thread(target=self._work, args=(audio,), daemon=True)
        thread.start()
        return thread

    def _work(self, audio: np.ndarray) -> None:
        try:
            if audio.size < MIN_SECONDS * SAMPLE_RATE:
                return
            with self._lock:
                text = self._transcriber.transcribe(audio)
            if text:
                self._on_text(text)
        except Exception:
            log.exception("transcription or insertion failed")
            if self._on_error:
                self._on_error("Transcription failed. See the log for details.")
        finally:
            if self._on_done:
                self._on_done()
