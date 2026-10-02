import sys
import types

import numpy as np

from rylanflow.__main__ import read_wav
from rylanflow.recorder import write_wav
from rylanflow.transcriber import MLXWhisperTranscriber, Transcriber


def test_empty_audio_returns_empty_string():
    assert MLXWhisperTranscriber().transcribe(np.zeros(0, dtype=np.float32)) == ""


def test_transcribe_strips_text_and_passes_options(monkeypatch):
    calls = {}

    def fake_transcribe(audio, **kwargs):
        calls.update(kwargs)
        return {"text": "  hello world \n"}

    monkeypatch.setitem(
        sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=fake_transcribe)
    )
    t: Transcriber = MLXWhisperTranscriber(model="m", language="en")
    assert t.transcribe(np.ones(1600, dtype=np.float32)) == "hello world"
    assert calls == {"path_or_hf_repo": "m", "language": "en"}


def test_read_wav_roundtrip(tmp_path):
    path = tmp_path / "a.wav"
    audio = np.linspace(-0.5, 0.5, 1600, dtype=np.float32)
    write_wav(path, audio)
    assert np.allclose(read_wav(str(path)), audio, atol=1e-3)
