# RylanFlow

[![CI](https://github.com/andersonrylan-gif/RylanFlow/actions/workflows/ci.yml/badge.svg)](https://github.com/andersonrylan-gif/RylanFlow/actions/workflows/ci.yml)

**Hold a key, talk, release — your words appear wherever you're typing.**
On-device dictation for macOS, powered by Whisper running locally on Apple Silicon. No cloud, no subscription, works offline.

## How it works

```mermaid
flowchart LR
    H[Hotkey listener<br/>pynput] -->|key held| R[Recorder<br/>sounddevice]
    R -->|16 kHz audio| T[Transcriber<br/>mlx-whisper, local]
    T -->|text| I[Text inserter<br/>clipboard + Cmd+V]
    A[Menu-bar app<br/>rumps] -.coordinates.- H & R & T & I
```

Each stage is a small module behind an interface, so pieces can be swapped or reused (the v0.2 meeting recorder reuses the recorder and transcriber unchanged).

See [docs/architecture.md](docs/architecture.md) and the [decision records](docs/decisions/) for the design.

## Features
- Hold **Right Option**, talk, release: the transcript is pasted into the focused app and your clipboard is restored
- Runs Whisper on-device (mlx-whisper), so it is free, private and works offline
- Menu-bar icon shows idle 🎙, recording 🔴 and transcribing ⏳
- Pick a fast or accurate model from the menu; **Copy last transcript**; **Open log**
- Settings in `~/.config/rylanflow/config.toml` (`hotkey`, `model`, `language`)

## Install and run

Requires an Apple Silicon Mac and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/andersonrylan-gif/RylanFlow.git
cd RylanFlow
uv sync
uv run rylanflow app
```

The first dictation downloads the Whisper model (about 150 MB for the fast model, about 1.6 GB for the accurate one). A standalone `.app` is planned.

Other commands: `rylanflow record`, `rylanflow transcribe FILE.wav`, `rylanflow listen [--paste]`.

### macOS permissions
The app needs **Microphone**, **Accessibility** (to paste text) and **Input Monitoring** (for the global hotkey). macOS grants these to the app that launched it, so when you run from VS Code's terminal, add **Visual Studio Code** in *System Settings → Privacy & Security* (and Terminal, if you run it there), then restart that app.

## Development

```bash
uv run pytest        # tests
uv run ruff check .  # lint
```

## Roadmap
- **v0.1 — Dictation:** ✅ push-to-talk recording, local transcription, paste into any app, menu-bar app. Next: packaged `.app` and start-at-login
- **v0.2 — Meetings:** capture mic + system audio, live chunked transcription, Markdown meeting notes

## License
[MIT](LICENSE)
