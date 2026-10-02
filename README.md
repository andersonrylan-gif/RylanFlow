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

Each stage is a small module behind an interface, so pieces can be swapped or reused (the v0.3 meeting recorder reuses the recorder and transcriber unchanged).

See [docs/architecture.md](docs/architecture.md), [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) and the [decision records](docs/decisions/) for the design.

## Features
- Hold **Right Option**, talk, release: the transcript is pasted into the focused app and your clipboard is restored
- Runs Whisper on-device (mlx-whisper), so it is free, private and works offline
- A floating **dictation pop-up** shows a live level meter while you talk, and a pulsing indicator while it transcribes — over full-screen apps, without stealing focus
- A **dashboard** (menu: Open Dashboard…) with your recent dictations, search, stats and settings, opens automatically the first time you launch RylanFlow
- Every dictation is saved locally (SQLite, `~/Library/Application Support/RylanFlow/`) so you can search and copy past ones
- Menu-bar icon shows idle 🎙, recording 🔴 and transcribing ⏳
- Settings (menu or dashboard): hotkey, model, pop-up position, sound cues, um/uh removal, start at login

## Install

**Download the app (Apple Silicon Macs):**
1. Download `RylanFlow-0.2.0-macos-arm64.zip` from the [latest release](https://github.com/andersonrylan-gif/RylanFlow/releases/latest) and unzip it.
2. Drag **RylanFlow** into **Applications**.
3. The app isn't notarized, so the first time, right-click it, choose **Open**, then **Open** again.
4. Grant **Microphone**, **Accessibility** and **Input Monitoring** to RylanFlow in *System Settings → Privacy & Security*, then quit it from the 🎙 menu and open it again.
5. To start it at login: *System Settings → General → Login Items* and add RylanFlow.

Hold **Right Option**, talk, release.

## Run from source

Requires an Apple Silicon Mac and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/andersonrylan-gif/RylanFlow.git
cd RylanFlow
uv sync
uv run rylanflow app
```

The first dictation downloads the Whisper model (about 150 MB for the fast model, about 1.6 GB for the accurate one). 

Other commands: `rylanflow record`, `rylanflow transcribe FILE.wav`, `rylanflow listen [--paste]`.

### macOS permissions
The app needs **Microphone**, **Accessibility** (to paste text) and **Input Monitoring** (for the global hotkey). macOS grants these to the app that launched it, so when you run from VS Code's terminal, add **Visual Studio Code** in *System Settings → Privacy & Security* (and Terminal, if you run it there), then restart that app.

## MCP: use your dictations and meeting notes from Claude (or any MCP client)

`rylanflow mcp` runs a local, read-only MCP server over your own dictation and meeting history (the same SQLite database the app already writes to on your Mac — nothing leaves your machine, and nothing new is captured). It exposes three tools: `list_dictations`, `list_meetings` and `get_meeting_transcript`, each optionally filtered by a search query. It runs standalone; the menu-bar app doesn't need to be running.

Add it to Claude Desktop's config (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "rylanflow": {
      "command": "uv",
      "args": ["run", "--project", "/absolute/path/to/RylanFlow", "rylanflow", "mcp"]
    }
  }
}
```

Restart Claude Desktop, then ask it something like "what did I say in my last dictation?" or "summarize what we decided in my meeting with X."

## Development

```bash
uv run pytest        # tests
uv run ruff check .  # lint
```

## Roadmap
- **v0.1 — Dictation:** ✅ push-to-talk recording, local transcription, paste into any app, menu-bar app, packaged `.app` (v0.1.1)
- **v0.2 — Dashboard & pop-up:** ✅ a dashboard window with your recent dictations, search and settings; a dictation pop-up (v0.2.0)
- **v0.3 — Meetings:** capture mic + system audio, live transcription with speaker labels, calendar-suggested names, auto-record

See [docs/BUILD_PLAN.md](docs/BUILD_PLAN.md) for the detailed, step-by-step plan for v0.2 and v0.3.

## License
[MIT](LICENSE)
