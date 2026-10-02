import threading
import time
import wave

import numpy as np
import pytest

from rylanflow import recorder
from rylanflow.recorder import AudioStuckError, Recorder, write_wav


class FakeStream:
    def __init__(self, callback, **kwargs):
        self.callback = callback

    def start(self):
        for _ in range(3):
            self.callback(np.full((160, 1), 0.5, dtype=np.float32), 160, None, None)

    def stop(self):
        pass

    def abort(self):
        pass

    def close(self):
        pass


def test_recorder_collects_chunks(monkeypatch):
    monkeypatch.setattr(recorder.sd, "InputStream", FakeStream)
    r = Recorder()
    r.start()
    assert r.recording
    audio = r.stop()
    assert not r.recording
    assert audio.shape == (480,)
    assert audio.dtype == np.float32


def test_level_reflects_the_most_recent_block(monkeypatch):
    monkeypatch.setattr(recorder.sd, "InputStream", FakeStream)
    r = Recorder()
    assert r.level == 0.0  # nothing recorded yet
    r.start()
    # FakeStream feeds blocks that are all 0.5, so the RMS is exactly 0.5.
    assert r.level == pytest.approx(0.5)
    r.stop()
    assert r.level == 0.0  # reset once we're done, so the overlay doesn't show a stale meter


def test_level_is_zero_for_silence(monkeypatch):
    class SilentStream(FakeStream):
        def start(self):
            self.callback(np.zeros((160, 1), dtype=np.float32), 160, None, None)

    monkeypatch.setattr(recorder.sd, "InputStream", SilentStream)
    r = Recorder()
    r.start()
    assert r.level == 0.0


def test_stop_without_audio_returns_empty():
    assert Recorder().stop().size == 0


def test_write_wav_roundtrip(tmp_path):
    path = tmp_path / "out.wav"
    write_wav(path, np.zeros(1600, dtype=np.float32))
    with wave.open(str(path)) as f:
        assert f.getframerate() == 16_000
        assert f.getnchannels() == 1
        assert f.getnframes() == 1600


class HangingStream(FakeStream):
    """Mimics the CoreAudio deadlock: closing never returns."""

    release = threading.Event()

    def abort(self):
        self.release.wait(5)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


def test_stop_returns_immediately_when_close_hangs(monkeypatch):
    monkeypatch.setattr(recorder.sd, "InputStream", HangingStream)
    clock = Clock()
    r = Recorder(clock=clock)
    r.start()
    began = time.monotonic()
    audio = r.stop()
    assert time.monotonic() - began < 0.5
    assert audio.size == 480  # audio captured before the stop is kept
    assert not r.stuck  # just started closing
    clock.now += recorder.CLOSE_TIMEOUT + 1
    assert r.stuck
    try:
        r.start()
        raise AssertionError("expected AudioStuckError")
    except AudioStuckError:
        pass
    HangingStream.release.set()


def test_audio_after_stop_is_ignored(monkeypatch):
    streams = []

    def factory(**kwargs):
        stream = FakeStream(**kwargs)
        streams.append(stream)
        return stream

    monkeypatch.setattr(recorder.sd, "InputStream", factory)
    r = Recorder()
    r.start()
    r.stop()
    streams[0].callback(np.ones((160, 1), dtype=np.float32), 160, None, None)
    r.start()
    assert r.stop().size == 480  # only the new recording's audio


def test_start_times_out_when_mic_hangs(monkeypatch):
    class SlowStream(FakeStream):
        def start(self):
            time.sleep(1)

    monkeypatch.setattr(recorder.sd, "InputStream", SlowStream)
    monkeypatch.setattr(recorder, "OPEN_TIMEOUT", 0.05)
    r = Recorder()
    try:
        r.start()
        raise AssertionError("expected AudioStuckError")
    except AudioStuckError:
        pass
    assert not r.recording
