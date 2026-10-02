# 0001 — Transcribe locally with Whisper (mlx-whisper)

**Status:** Accepted

## Context
Dictation needs speech-to-text with low latency. Options were a cloud API (OpenAI, Deepgram) or running Whisper on-device.

## Decision
Run Whisper locally via `mlx-whisper`, which uses Apple Silicon's GPU through Apple's MLX framework.

## Consequences
- ✅ Free, private (audio never leaves the machine), works offline
- ✅ Demonstrates on-device ML, not just an API call
- ⚠️ First run downloads a model (~150 MB – 1.5 GB); Apple Silicon only
- The `Transcriber` interface keeps a cloud engine possible later without touching the rest of the app.
