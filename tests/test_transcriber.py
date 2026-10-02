import sys
import types

import numpy as np

from rylanflow.__main__ import read_wav
from rylanflow.recorder import write_wav
from rylanflow.transcriber import MLXWhisperTranscriber, Segment, Transcriber


def _fake_mlx_whisper(monkeypatch, segments, **extra_result):
    def fake_transcribe(audio, **kwargs):
        fake_transcribe.calls.append(kwargs)
        return {"text": " ".join(s["text"] for s in segments), "segments": segments, **extra_result}

    fake_transcribe.calls = []
    monkeypatch.setitem(
        sys.modules, "mlx_whisper", types.SimpleNamespace(transcribe=fake_transcribe)
    )
    return fake_transcribe


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


# --- transcribe_segments ---


def test_transcribe_segments_empty_audio_returns_empty_list():
    assert MLXWhisperTranscriber().transcribe_segments(np.zeros(0, dtype=np.float32)) == []


def test_transcribe_segments_returns_segment_objects(monkeypatch):
    _fake_mlx_whisper(
        monkeypatch,
        [
            {
                "start": 0.0,
                "end": 1.5,
                "text": " hello ",
                "no_speech_prob": 0.1,
                "avg_logprob": -0.2,
            },
            {
                "start": 1.5,
                "end": 3.0,
                "text": " world ",
                "no_speech_prob": 0.2,
                "avg_logprob": -0.3,
            },
        ],
    )
    segments = MLXWhisperTranscriber().transcribe_segments(np.ones(1600, dtype=np.float32))
    assert segments == [
        Segment(0.0, 1.5, "hello", 0.1, -0.2),
        Segment(1.5, 3.0, "world", 0.2, -0.3),
    ]


def test_transcribe_segments_strips_whitespace_and_drops_blank_segments(monkeypatch):
    _fake_mlx_whisper(
        monkeypatch,
        [
            {"start": 0.0, "end": 1.0, "text": "  ", "no_speech_prob": 0.0, "avg_logprob": 0.0},
            {"start": 1.0, "end": 2.0, "text": " ok ", "no_speech_prob": 0.0, "avg_logprob": 0.0},
        ],
    )
    segments = MLXWhisperTranscriber().transcribe_segments(np.ones(1600, dtype=np.float32))
    assert [s.text for s in segments] == ["ok"]


def test_transcribe_segments_meeting_mode_passes_condition_on_previous_text_false(monkeypatch):
    fake = _fake_mlx_whisper(monkeypatch, [])
    MLXWhisperTranscriber().transcribe_segments(np.ones(1600, dtype=np.float32), meeting=False)
    assert "condition_on_previous_text" not in fake.calls[0]

    MLXWhisperTranscriber().transcribe_segments(np.ones(1600, dtype=np.float32), meeting=True)
    assert fake.calls[1]["condition_on_previous_text"] is False


def test_transcribe_segments_meeting_mode_filters_likely_hallucinations(monkeypatch):
    _fake_mlx_whisper(
        monkeypatch,
        [
            # Real speech: kept.
            {
                "start": 0.0,
                "end": 1.0,
                "text": "real speech",
                "no_speech_prob": 0.1,
                "avg_logprob": -0.3,
            },
            # High no_speech_prob: a classic "Thank you." hallucination over silence.
            {
                "start": 1.0,
                "end": 2.0,
                "text": "Thank you.",
                "no_speech_prob": 0.9,
                "avg_logprob": -0.3,
            },
            # Low avg_logprob: the model wasn't confident either.
            {
                "start": 2.0,
                "end": 3.0,
                "text": "Thanks for watching!",
                "no_speech_prob": 0.1,
                "avg_logprob": -2.0,
            },
        ],
    )
    segments = MLXWhisperTranscriber().transcribe_segments(
        np.ones(1600, dtype=np.float32), meeting=True
    )
    assert [s.text for s in segments] == ["real speech"]


def test_transcribe_segments_non_meeting_mode_does_not_filter_hallucinations(monkeypatch):
    # Dictation clips are short and the user was just talking, so transcribe_segments()
    # without meeting=True should not apply the meeting hallucination heuristics.
    _fake_mlx_whisper(
        monkeypatch,
        [
            {
                "start": 0.0,
                "end": 1.0,
                "text": "Thank you.",
                "no_speech_prob": 0.9,
                "avg_logprob": -0.3,
            },
        ],
    )
    segments = MLXWhisperTranscriber().transcribe_segments(np.ones(1600, dtype=np.float32))
    assert [s.text for s in segments] == ["Thank you."]
