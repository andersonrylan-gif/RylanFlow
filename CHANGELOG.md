# Changelog

## 0.1.0 - 2026-10-02
First release: push-to-talk dictation for macOS.

- Hold Right Option to record, release to transcribe with local Whisper (mlx-whisper) and paste into the focused app; the clipboard is restored afterwards
- Menu-bar app with idle / recording / transcribing icons, model picker, copy-last-transcript and open-log items
- Config file (`~/.config/rylanflow/config.toml`), rotating log file, and handling of mic, permission and transcription errors
- CLI commands: `app`, `record`, `transcribe`, `listen`
- CI on macOS (ruff + pytest)

Known limitations: runs from source (`uv run rylanflow app`), no standalone `.app` yet; notifications are unavailable until it is packaged.
