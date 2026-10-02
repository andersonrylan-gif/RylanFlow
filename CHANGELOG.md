# Changelog

## 0.2.0 - 2026-10-02
- **Dashboard** (menu: Open Dashboard…): a native window showing your recent dictations, search, stats (words today/this week/total, minutes saved), copy and delete. Opens automatically the first time you launch RylanFlow. Backed by a local-only HTTP server (127.0.0.1, token-authenticated).
- **Settings page** in the dashboard: hotkey, model, dictation pop-up position, sound cues, remove um/uh, start at login — all apply live.
- **Dictation pop-up**: a small floating pill (bottom or top of the screen, or off) shows a live level meter while you dictate and a pulsing indicator while transcribing. Never steals focus, shows over full-screen apps.
- Every dictation is now saved to a local SQLite database (`~/Library/Application Support/RylanFlow/`), searchable from the dashboard.
- Stable local code signing (`packaging/make_signing_cert.sh`): rebuilding the app no longer resets Accessibility/Input Monitoring permissions.

## 0.1.1 - 2026-10-02
- Standalone `RylanFlow.app` (PyInstaller): menu-bar only, asks for its own permissions
- Fixed: the packaged app no longer starts extra copies on each dictation (duplicate icons and repeated pastes); a second copy now exits immediately
- New menu items: **Hotkey** (Right Option / Command / Control), **Sound cues**, **Remove um / uh**
- `rylanflow autostart on` starts the app at login and launches the packaged app when it is in Applications
- Design notes: ADR 0002 (clipboard paste) and `docs/architecture.md`

## 0.1.0 - 2026-10-02
First release: push-to-talk dictation for macOS.

- Hold Right Option to record, release to transcribe with local Whisper (mlx-whisper) and paste into the focused app; the clipboard is restored afterwards
- Menu-bar app with idle / recording / transcribing icons, model picker, copy-last-transcript and open-log items
- Config file (`~/.config/rylanflow/config.toml`), rotating log file, and handling of mic, permission and transcription errors
- CLI commands: `app`, `record`, `transcribe`, `listen`
- CI on macOS (ruff + pytest)

Known limitations: runs from source (`uv run rylanflow app`), no standalone `.app` yet; notifications are unavailable until it is packaged.
