import numpy as np

from rylanflow.meetings import mic_track
from rylanflow.meetings.mic_track import MicTrack


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


def test_start_opens_the_mic(tmp_path, monkeypatch):
    monkeypatch.setattr(mic_track.sd, "InputStream", FakeStream)
    track = MicTrack(tmp_path / "mic.wav")
    track.start()
    track.close()


def test_start_recovers_from_a_transient_port_audio_error(tmp_path, monkeypatch):
    """MicTrack delegates mic-opening to recorder.open_input_stream_with_retry -- full
    retry/exhaustion coverage lives in test_recorder.py. This just confirms the wiring: a
    transient failure here doesn't fail the meeting outright."""
    attempts = []

    def flaky_input_stream(callback, **kwargs):
        attempts.append(1)
        if len(attempts) < 2:
            raise mic_track.sd.PortAudioError("Internal PortAudio error")
        return FakeStream(callback, **kwargs)

    monkeypatch.setattr(mic_track.sd, "InputStream", flaky_input_stream)
    monkeypatch.setattr(mic_track.time, "sleep", lambda _s: None)

    track = MicTrack(tmp_path / "mic.wav")
    track.start()

    assert len(attempts) == 2


def test_drain_and_close_work_normally(tmp_path, monkeypatch):
    def fake_input_stream(callback, **kwargs):
        stream = FakeStream(callback, **kwargs)
        callback(np.full((160, 1), 0.5, dtype=np.float32), 160, None, None)
        return stream

    monkeypatch.setattr(mic_track.sd, "InputStream", fake_input_stream)

    track = MicTrack(tmp_path / "mic.wav")
    track.start()

    audio = track.drain()
    assert audio.shape == (160,)
    track.close()
