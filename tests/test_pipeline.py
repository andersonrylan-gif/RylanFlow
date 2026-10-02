import threading
import time

import numpy as np
import pytest
from pynput import keyboard

from rylanflow.hotkey import PushToTalk, parse_key
from rylanflow.pipeline import MAX_SECONDS, Pipeline
from rylanflow.recorder import SAMPLE_RATE, AudioStuckError


class FakeRecorder:
    def __init__(self, audio):
        self.audio = audio
        self.starts = 0
        self.stops = 0
        self.stuck = False
        self.fail_start = None

    def start(self):
        if self.fail_start:
            raise self.fail_start
        self.starts += 1

    def stop(self):
        self.stops += 1
        return self.audio


class FakeTranscriber:
    def __init__(self, text="hello", gate=None, error=None):
        self.text = text
        self.gate = gate
        self.error = error

    def transcribe(self, audio):
        if self.gate:
            self.gate.wait(5)
        if self.error:
            raise self.error
        return self.text


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


ONE_SECOND = np.ones(SAMPLE_RATE, dtype=np.float32)


@pytest.fixture
def make():
    pipelines = []

    def factory(audio=ONE_SECOND, transcriber=None, **kwargs):
        rec = FakeRecorder(audio)
        texts, errors, cues = [], [], []
        p = Pipeline(
            rec,
            transcriber or FakeTranscriber(),
            texts.append,
            on_error=errors.append,
            on_cue=cues.append,
            **kwargs,
        )
        pipelines.append(p)
        return p, rec, texts, errors, cues

    yield factory
    for p in pipelines:
        p.close()


def test_press_release_emits_text(make):
    p, rec, texts, errors, cues = make()
    p.press()
    assert wait_for(lambda: p.state == "recording")
    p.release()
    assert wait_for(lambda: texts == ["hello"])
    assert wait_for(lambda: p.state == "idle")
    assert cues == ["start", "stop"]
    assert errors == []


def test_accidental_tap_is_ignored(make):
    p, rec, texts, *_ = make(audio=np.ones(100, dtype=np.float32))
    p.press()
    p.release()
    assert wait_for(lambda: rec.stops == 1)
    assert wait_for(lambda: p.state == "idle")
    assert texts == []


def test_new_recording_during_transcription_still_stops(make):
    # Regression: finishing the first transcription used to reset the state to idle,
    # so the second release was ignored and the mic never stopped.
    gate = threading.Event()
    p, rec, texts, *_ = make(transcriber=FakeTranscriber(gate=gate))
    p.press()
    p.release()
    assert wait_for(lambda: p.state == "working")
    p.press()
    assert wait_for(lambda: p.state == "recording")
    gate.set()
    assert wait_for(lambda: texts == ["hello"])
    assert p.state == "recording"
    p.release()
    assert wait_for(lambda: rec.stops == 2)
    assert wait_for(lambda: texts == ["hello", "hello"])


def test_press_returns_immediately_even_if_mic_is_slow(make):
    p, rec, *_ = make()
    rec.start = lambda: time.sleep(1)
    began = time.monotonic()
    p.press()
    assert time.monotonic() - began < 0.1


def test_stuck_mic_reports_error_and_requests_restart(make):
    p, rec, texts, errors, _ = make()
    rec.fail_start = AudioStuckError("stuck")
    rec.stuck = True
    p.press()
    assert wait_for(lambda: errors)
    assert "restart" in errors[0]
    assert p.state == "idle"
    assert p.restart_reason() == "the microphone stopped responding"


def test_restart_waits_for_pending_paste(make):
    gate = threading.Event()
    p, rec, texts, *_ = make(transcriber=FakeTranscriber(gate=gate))
    p.press()
    p.release()
    assert wait_for(lambda: p.state == "working")
    rec.stuck = True
    assert p.restart_reason() is None  # still transcribing; don't lose the text
    gate.set()
    assert wait_for(lambda: p.state == "idle")
    assert p.restart_reason() == "the microphone stopped responding"


def test_hung_transcription_requests_restart(make):
    clock = Clock()
    gate = threading.Event()
    p, *_ = make(transcriber=FakeTranscriber(gate=gate), clock=clock)
    p.press()
    p.release()
    assert wait_for(lambda: p.state == "working")
    assert p.restart_reason() is None
    clock.now += 60 + 3 + 1
    assert p.restart_reason() == "a transcription stopped responding"
    gate.set()


def test_transcription_failure_is_reported(make):
    p, rec, texts, errors, _ = make(transcriber=FakeTranscriber(error=RuntimeError("boom")))
    p.press()
    p.release()
    assert wait_for(lambda: errors == ["Transcription failed. See the log for details."])
    assert wait_for(lambda: p.state == "idle")


def test_long_recording_stops_automatically(make):
    clock = Clock()
    p, rec, *_ = make(clock=clock)
    p.press()
    assert wait_for(lambda: p.state == "recording")
    clock.now += MAX_SECONDS + 1
    p.tick()
    assert wait_for(lambda: rec.stops == 1)


def test_push_to_talk_fires_once_per_hold():
    events = []
    ptt = PushToTalk(lambda: events.append("down"), lambda: events.append("up"), key="alt_r")
    other = keyboard.Key.shift
    for key in [other, keyboard.Key.alt_r, keyboard.Key.alt_r]:  # second is auto-repeat
        ptt._press(key)
    ptt._release(other)
    ptt._release(keyboard.Key.alt_r)
    assert events == ["down", "up"]


def test_parse_key():
    assert parse_key("alt_r") == keyboard.Key.alt_r
    assert parse_key("a") == keyboard.KeyCode.from_char("a")
    with pytest.raises(ValueError):
        parse_key("nope")


def test_push_to_talk_set_key_changes_trigger():
    events = []
    ptt = PushToTalk(lambda: events.append("down"), lambda: events.append("up"), key="alt_r")
    ptt.set_key("cmd_r")
    ptt._press(keyboard.Key.alt_r)
    ptt._press(keyboard.Key.cmd_r)
    ptt._release(keyboard.Key.cmd_r)
    assert events == ["down", "up"]
