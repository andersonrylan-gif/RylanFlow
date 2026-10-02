"""One shared Whisper worker, so dictation and (later) meeting transcription don't each load
and run their own copy of the model.

Backed by a single worker thread draining a priority queue: dictation submissions (priority 0)
always jump ahead of any queued-but-not-yet-started meeting work (priority 1), so dictation
never waits behind a backlog of meeting chunks -- except the one chunk already being processed,
since the worker only does one thing at a time.
"""

import dataclasses
import itertools
import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from rylanflow.recorder import SAMPLE_RATE

log = logging.getLogger(__name__)

DICTATION_PRIORITY = 0
MEETING_PRIORITY = 1


def job_timeout(audio_seconds: float) -> float:
    """How long a single job may take before we treat it as hung."""
    return 60 + 3 * audio_seconds


@dataclasses.dataclass(order=True)
class _Job:
    priority: int
    seq: int
    id: int = dataclasses.field(compare=False)
    fn: Callable[[np.ndarray], Any] | None = dataclasses.field(compare=False)
    audio: np.ndarray | None = dataclasses.field(compare=False)
    callback: Callable[[Any], None] | None = dataclasses.field(compare=False)
    on_error: Callable[[str], None] | None = dataclasses.field(compare=False)
    tag: str = dataclasses.field(compare=False, default="")


class TranscriptionService:
    """Create one, share it between Pipeline and the meeting pipeline, call submit() from any
    thread. Call stop() once, from whoever owns it, when it's no longer needed."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._queue: queue.PriorityQueue[_Job] = queue.PriorityQueue()
        self._seq = itertools.count()
        self._ids = itertools.count()
        self._lock = threading.Lock()
        self._jobs: dict[int, tuple[float, str]] = {}  # id -> (deadline, tag)
        self._worker = threading.Thread(target=self._loop, name="transcription", daemon=True)
        self._worker.start()

    def submit(
        self,
        fn: Callable[[np.ndarray], Any],
        audio: np.ndarray,
        priority: int,
        callback: Callable[[Any], None],
        on_error: Callable[[str], None] | None = None,
        *,
        tag: str = "",
    ) -> int:
        """Queue fn(audio) to run on the worker thread; callback(result) fires when it's done.
        Returns a job id, usable with pending_count()'s tag to ask "is my work still queued?"."""
        job_id = next(self._ids)
        deadline = self._clock() + job_timeout(audio.size / SAMPLE_RATE)
        with self._lock:
            self._jobs[job_id] = (deadline, tag)
        self._queue.put(_Job(priority, next(self._seq), job_id, fn, audio, callback, on_error, tag))
        return job_id

    def pending_count(self, tag: str) -> int:
        """How many jobs with this tag are queued or currently running."""
        with self._lock:
            return sum(1 for _, t in self._jobs.values() if t == tag)

    def restart_reason(self) -> str | None:
        now = self._clock()
        with self._lock:
            hung = any(now > deadline for deadline, _ in self._jobs.values())
        return "a transcription stopped responding" if hung else None

    def stop(self) -> None:
        """Ask the worker thread to exit once it's drained anything already queued ahead of
        this. Jumps the queue (priority -1) so pending-but-not-started work doesn't delay it."""
        self._queue.put(_Job(-1, next(self._seq), -1, None, None, None, None))

    def _loop(self) -> None:
        while True:
            job = self._queue.get()
            if job.fn is None:  # the sentinel stop() enqueues
                return
            try:
                result = job.fn(job.audio)
                job.callback(result)
            except Exception:
                log.exception("transcription failed")
                if job.on_error:
                    job.on_error("Transcription failed. See the log for details.")
            finally:
                with self._lock:
                    self._jobs.pop(job.id, None)
