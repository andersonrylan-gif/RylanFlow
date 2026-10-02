import wave

import numpy as np

from rylanflow import recorder
from rylanflow.recorder import Recorder, write_wav


class FakeStream:
    def __init__(self, callback, **kwargs):
        self.callback = callback

    def start(self):
        for _ in range(3):
            self.callback(np.full((160, 1), 0.5, dtype=np.float32), 160, None, None)

    def stop(self):
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


def test_stop_without_audio_returns_empty():
    assert Recorder().stop().size == 0


def test_write_wav_roundtrip(tmp_path):
    path = tmp_path / "out.wav"
    write_wav(path, np.zeros(1600, dtype=np.float32))
    with wave.open(str(path)) as f:
        assert f.getframerate() == 16_000
        assert f.getnchannels() == 1
        assert f.getnframes() == 1600
