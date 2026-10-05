import numpy as np
import pytest

from rylanflow.meetings import mic_track
from rylanflow.meetings.mic_track import MIC_OPEN_RETRIES, MicTrack


class FakeStream:
    def __init__(self, callback, **kwargs):
        self.callback = callback

    def start(self):
        pass

    def stop(self):
        pass

    def abort(self):
        pass

    def close(self):
        pass


def test_start_opens_on_the_first_try(tmp_path, monkeypatch):
    calls = []

    def fake_input_stream(callback, **kwargs):
        calls.append(1)
        return FakeStream(callback, **kwargs)

    monkeypatch.setattr(mic_track.sd, "InputStream", fake_input_stream)
    monkeypatch.setattr(mic_track.time, "sleep", lambda _s: None)

    track = MicTrack(tmp_path / "mic.wav")
    track.start()

    assert len(calls) == 1


def test_start_retries_after_a_transient_port_audio_error(tmp_path, monkeypatch):
    attempts = []

    def flaky_input_stream(callback, **kwargs):
        attempts.append(1)
        if len(attempts) < MIC_OPEN_RETRIES:
            raise mic_track.sd.PortAudioError("Internal PortAudio error")
        return FakeStream(callback, **kwargs)

    monkeypatch.setattr(mic_track.sd, "InputStream", flaky_input_stream)
    monkeypatch.setattr(mic_track.time, "sleep", lambda _s: None)

    track = MicTrack(tmp_path / "mic.wav")
    track.start()  # succeeds on the last retry instead of raising

    assert len(attempts) == MIC_OPEN_RETRIES


def test_start_raises_once_every_retry_is_exhausted(tmp_path, monkeypatch):
    attempts = []

    def always_fails(callback, **kwargs):
        attempts.append(1)
        raise mic_track.sd.PortAudioError("Internal PortAudio error")

    monkeypatch.setattr(mic_track.sd, "InputStream", always_fails)
    monkeypatch.setattr(mic_track.time, "sleep", lambda _s: None)

    track = MicTrack(tmp_path / "mic.wav")
    with pytest.raises(mic_track.sd.PortAudioError):
        track.start()

    assert len(attempts) == MIC_OPEN_RETRIES


def test_drain_and_close_still_work_after_a_successful_retry(tmp_path, monkeypatch):
    attempts = []

    def flaky_input_stream(callback, **kwargs):
        attempts.append(1)
        if len(attempts) < 2:
            raise mic_track.sd.PortAudioError("Internal PortAudio error")
        stream = FakeStream(callback, **kwargs)
        callback(np.full((160, 1), 0.5, dtype=np.float32), 160, None, None)
        return stream

    monkeypatch.setattr(mic_track.sd, "InputStream", flaky_input_stream)
    monkeypatch.setattr(mic_track.time, "sleep", lambda _s: None)

    track = MicTrack(tmp_path / "mic.wav")
    track.start()

    audio = track.drain()
    assert audio.shape == (160,)
    track.close()
