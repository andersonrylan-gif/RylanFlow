"""Speech-to-text behind a small interface so engines can be swapped."""

from dataclasses import dataclass
from typing import Protocol

import numpy as np

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
FAST_MODEL = "mlx-community/whisper-base-mlx"

# Segments this quiet/unconfident are usually Whisper hallucinating on silence or background
# noise ("Thank you.", "Thanks for watching!") rather than real speech -- seen often enough in
# long, mostly-silent meeting chunks that it's worth filtering out there. Dictation clips are
# short and the user was just actively talking, so transcribe() doesn't need this.
HALLUCINATION_NO_SPEECH_PROB = 0.6
HALLUCINATION_AVG_LOGPROB = -1.0


@dataclass
class Segment:
    start: float
    end: float
    text: str
    no_speech_prob: float
    avg_logprob: float


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray) -> str:
        """Turn 16 kHz mono float32 audio into text."""
        ...


class MLXWhisperTranscriber:
    """Runs Whisper on-device using Apple's MLX framework."""

    def __init__(self, model: str = DEFAULT_MODEL, language: str | None = None) -> None:
        self.model = model
        self.language = language

    def transcribe(self, audio: np.ndarray) -> str:
        if audio.size == 0:
            return ""
        import mlx_whisper  # heavy import, load lazily

        result = mlx_whisper.transcribe(
            audio.astype(np.float32),
            path_or_hf_repo=self.model,
            language=self.language,
        )
        return result["text"].strip()

    def transcribe_segments(self, audio: np.ndarray, *, meeting: bool = False) -> list[Segment]:
        """Like transcribe(), but with per-segment timing -- for meeting chunks, which need to
        know *when* each bit of text was said, not just the text as a whole."""
        if audio.size == 0:
            return []
        import mlx_whisper

        kwargs = {}
        if meeting:
            # Long, partly-silent meeting audio is exactly where Whisper tends to latch onto an
            # earlier hallucinated phrase and keep repeating it; this stops it feeding its own
            # previous (possibly wrong) output back in as context.
            kwargs["condition_on_previous_text"] = False
        result = mlx_whisper.transcribe(
            audio.astype(np.float32),
            path_or_hf_repo=self.model,
            language=self.language,
            **kwargs,
        )
        segments = []
        for raw in result.get("segments", []):
            text = raw.get("text", "").strip()
            no_speech_prob = raw.get("no_speech_prob", 0.0)
            avg_logprob = raw.get("avg_logprob", 0.0)
            if not text:
                continue
            if meeting and (
                no_speech_prob > HALLUCINATION_NO_SPEECH_PROB
                or avg_logprob < HALLUCINATION_AVG_LOGPROB
            ):
                continue
            segments.append(Segment(raw["start"], raw["end"], text, no_speech_prob, avg_logprob))
        return segments
