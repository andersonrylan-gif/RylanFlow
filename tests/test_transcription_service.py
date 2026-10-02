import threading
import time

import numpy as np
import pytest

from rylanflow.transcription_service import (
    DICTATION_PRIORITY,
    MEETING_PRIORITY,
    TranscriptionService,
)

ONE_SECOND = np.ones(16_000, dtype=np.float32)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def wait_for(condition, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def service():
    svc = TranscriptionService()
    yield svc
    svc.stop()


def test_submit_runs_fn_and_delivers_the_result(service):
    results = []
    service.submit(lambda audio: audio.size, ONE_SECOND, DICTATION_PRIORITY, results.append)
    assert wait_for(lambda: results == [16_000])


def test_error_callback_fires_on_exception_and_result_callback_does_not(service):
    results, errors = [], []

    def boom(audio):
        raise RuntimeError("model exploded")

    service.submit(boom, ONE_SECOND, DICTATION_PRIORITY, results.append, errors.append)
    assert wait_for(lambda: errors)
    assert errors == ["Transcription failed. See the log for details."]
    assert results == []


def test_a_callback_that_raises_is_also_reported_as_an_error(service):
    # Matches the pre-refactor Pipeline behaviour: a failure in what happens with the result
    # (e.g. saving to the store, pasting) is reported the same way as a transcription failure.
    errors = []

    def bad_callback(_result):
        raise ValueError("on_text blew up")

    service.submit(lambda a: "ok", ONE_SECOND, DICTATION_PRIORITY, bad_callback, errors.append)
    assert wait_for(lambda: errors)


def test_pending_count_reflects_queued_and_running_jobs_by_tag(service):
    gate = threading.Event()

    def slow(audio):
        gate.wait(5)
        return "done"

    assert service.pending_count("dictation") == 0
    service.submit(slow, ONE_SECOND, DICTATION_PRIORITY, lambda r: None, tag="dictation")
    assert wait_for(lambda: service.pending_count("dictation") == 1)
    assert service.pending_count("meeting") == 0
    gate.set()
    assert wait_for(lambda: service.pending_count("dictation") == 0)


def test_dictation_jumps_ahead_of_queued_meeting_work():
    # Block the worker on one long-running job so everything else actually queues up, then
    # submit several "meeting" jobs before a "dictation" one, and confirm dictation runs next.
    service = TranscriptionService()
    try:
        gate = threading.Event()
        order = []

        def blocker(audio):
            gate.wait(5)
            return "blocker"

        def record(name):
            def fn(audio):
                order.append(name)
                return name

            return fn

        service.submit(blocker, ONE_SECOND, DICTATION_PRIORITY, lambda r: None)
        assert wait_for(lambda: service.pending_count("") == 1)  # registered
        time.sleep(0.05)  # give the worker time to actually start executing it, not just queue it

        for i in range(3):
            service.submit(
                record(f"meeting-{i}"), ONE_SECOND, MEETING_PRIORITY, lambda r: None, tag="meeting"
            )
        service.submit(
            record("dictation"), ONE_SECOND, DICTATION_PRIORITY, lambda r: None, tag="dictation"
        )

        gate.set()
        assert wait_for(lambda: len(order) == 4)
        assert order[0] == "dictation"
    finally:
        service.stop()


def test_restart_reason_none_when_nothing_is_hung():
    clock = Clock()
    service = TranscriptionService(clock=clock)
    try:
        assert service.restart_reason() is None
        results = []
        service.submit(lambda a: "ok", ONE_SECOND, DICTATION_PRIORITY, results.append)
        assert wait_for(lambda: results == ["ok"])
        assert service.restart_reason() is None
    finally:
        service.stop()


def test_restart_reason_reports_a_hung_job_past_its_deadline():
    clock = Clock()
    service = TranscriptionService(clock=clock)
    try:
        gate = threading.Event()
        service.submit(lambda a: gate.wait(5), ONE_SECOND, DICTATION_PRIORITY, lambda r: None)
        assert wait_for(lambda: service.pending_count("") == 1)
        assert service.restart_reason() is None  # still within its deadline

        clock.now += 60 + 3 * 1 + 1  # past job_timeout(1 second of audio)
        assert service.restart_reason() == "a transcription stopped responding"
        gate.set()
    finally:
        service.stop()


def test_stop_lets_the_worker_thread_exit():
    service = TranscriptionService()
    worker = service._worker
    service.stop()
    worker.join(timeout=2)
    assert not worker.is_alive()
