import numpy as np
import pytest
from pynput import keyboard

from rylanflow.hotkey import PushToTalk, parse_key
from rylanflow.pipeline import Pipeline
from rylanflow.recorder import SAMPLE_RATE


class FakeRecorder:
    def __init__(self, audio):
        self.audio = audio
        self.started = False

    def start(self):
        self.started = True

    def stop(self):
        return self.audio


class FakeTranscriber:
    def transcribe(self, audio):
        return "hello"


def run(audio):
    texts = []
    rec = FakeRecorder(audio)
    pipeline = Pipeline(rec, FakeTranscriber(), texts.append)
    pipeline.start_recording()
    assert rec.started
    pipeline.stop_and_transcribe().join()
    return texts


def test_pipeline_emits_text():
    assert run(np.ones(SAMPLE_RATE, dtype=np.float32)) == ["hello"]


def test_pipeline_ignores_accidental_tap():
    assert run(np.ones(100, dtype=np.float32)) == []


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


def test_on_done_fires_even_for_taps_and_failures():
    done, errors = [], []

    class Boom:
        def transcribe(self, audio):
            raise RuntimeError("model failed")

    for audio, transcriber in [
        (np.ones(100, dtype=np.float32), FakeTranscriber()),
        (np.ones(SAMPLE_RATE, dtype=np.float32), Boom()),
    ]:
        pipeline = Pipeline(
            FakeRecorder(audio), transcriber, lambda t: None, lambda: done.append(1), errors.append
        )
        pipeline.stop_and_transcribe().join()
    assert done == [1, 1]
    assert len(errors) == 1  # only the model failure is reported
