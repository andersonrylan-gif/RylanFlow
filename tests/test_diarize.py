import hashlib
from pathlib import Path

import numpy as np
import pytest

import rylanflow.meetings.diarize as diarize


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RYLANFLOW_DATA_DIR", str(tmp_path))
    return tmp_path


def test_sha256_matches_a_known_file(tmp_path):
    path = tmp_path / "f.bin"
    path.write_bytes(b"hello world")
    assert diarize._sha256(path) == hashlib.sha256(b"hello world").hexdigest()


def _write(dest, content) -> None:
    Path(dest).parent.mkdir(parents=True, exist_ok=True)
    Path(dest).write_bytes(content)


def test_ensure_embedding_model_downloads_and_verifies(tmp_path, monkeypatch):
    content = b"fake embedding model bytes"
    expected = hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(diarize, "EMBEDDING_SHA256", expected)
    monkeypatch.setattr(diarize, "_download", lambda url, dest: _write(dest, content))

    path = diarize.ensure_embedding_model()

    assert path.read_bytes() == content
    assert path == tmp_path / "models" / "nemo_en_titanet_small.onnx"


def test_ensure_embedding_model_is_cached_on_the_second_call(tmp_path, monkeypatch):
    content = b"fake embedding model bytes"
    monkeypatch.setattr(diarize, "EMBEDDING_SHA256", hashlib.sha256(content).hexdigest())
    calls = []

    def fake_download(url, dest):
        calls.append(url)
        _write(dest, content)

    monkeypatch.setattr(diarize, "_download", fake_download)

    diarize.ensure_embedding_model()
    diarize.ensure_embedding_model()

    assert len(calls) == 1


def test_ensure_embedding_model_rejects_a_checksum_mismatch(tmp_path, monkeypatch):
    monkeypatch.setattr(diarize, "EMBEDDING_SHA256", "0" * 64)
    monkeypatch.setattr(diarize, "_download", lambda url, dest: _write(dest, b"wrong bytes"))

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        diarize.ensure_embedding_model()

    assert not (tmp_path / "models" / "nemo_en_titanet_small.onnx").exists()


def test_diarizer_returns_no_turns_for_empty_audio(tmp_path):
    import wave

    wav_path = tmp_path / "empty.wav"
    with wave.open(str(wav_path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b"")

    d = diarize.Diarizer()
    assert d.diarize(wav_path) == []


def test_embed_returns_none_for_empty_audio():
    d = diarize.Diarizer()
    assert d.embed(np.zeros(0, dtype=np.float32)) is None


def _write_wav(path, seconds: float) -> None:
    import wave

    with wave.open(str(path), "wb") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes((np.ones(int(seconds * 16_000), dtype=np.int16) * 1000).tobytes())


def test_concat_audio_for_turns_with_no_turns_returns_none(tmp_path):
    assert diarize.concat_audio_for_turns(tmp_path / "missing.wav", []) is None


def test_concat_audio_for_turns_reads_longest_turns_first_up_to_the_cap(tmp_path):
    wav_path = tmp_path / "system.wav"
    _write_wav(wav_path, seconds=10.0)

    audio = diarize.concat_audio_for_turns(wav_path, [(0.0, 1.0), (2.0, 10.0)], max_duration=3.0)

    assert audio is not None
    assert audio.size == int(3.0 * 16_000)  # capped: all 8s turn, trimmed, none of the 1s turn


def test_concat_audio_for_turns_uses_everything_under_the_cap(tmp_path):
    wav_path = tmp_path / "system.wav"
    _write_wav(wav_path, seconds=10.0)

    audio = diarize.concat_audio_for_turns(wav_path, [(0.0, 1.0), (2.0, 4.0)], max_duration=10.0)

    assert audio is not None
    assert audio.size == int(3.0 * 16_000)  # 2s + 1s turn, both fit under the cap
