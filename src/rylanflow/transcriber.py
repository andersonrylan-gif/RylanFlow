"""Speech-to-text behind a small interface so engines can be swapped."""

from typing import Protocol

import numpy as np

DEFAULT_MODEL = "mlx-community/whisper-large-v3-turbo"
FAST_MODEL = "mlx-community/whisper-base-mlx"


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
