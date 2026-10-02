# RylanFlow — notes for Claude

Push-to-talk dictation + meeting transcription for macOS. Python 3.12, uv, rumps menu-bar app, local mlx-whisper, packaged with PyInstaller.

**Start here:** `docs/BUILD_PLAN.md` — the step-by-step plan with checkboxes. Do the next unchecked step; tick it in the same PR.

## Workflow
- Every step: `gh issue create` (milestone from the plan) → branch → PR with `Closes #N` → wait for CI (`gh pr checks`) → `gh pr merge --squash --delete-branch`. The owner wants you to merge once CI passes.
- Gate: `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`.
- Conventional commits. Ask before releases, deleting data or system-level changes.
- Say plainly what you could not verify (mic, keyboard, permissions, real meetings) and ask the owner to test it.

## Rules that prevent real bugs (see BUILD_PLAN.md section 3)
- Never block the pynput hotkey thread; only queue work.
- Never stop/close audio streams synchronously; use the background-close pattern in `recorder.py`.
- AppKit/UI only on the main thread (`AppHelper.callAfter` from other threads).
- Keep `multiprocessing.freeze_support()` in `packaging/launcher.py`.
- Rebuilding the .app resets macOS permissions unless signed with "RylanFlow Local Signing".
- CI has no mic/screen/keyboard: put OS APIs behind interfaces and test logic with fakes.

## Commands
- Run: `uv run rylanflow app` (kill old copies first: `pkill -9 -f "MacOS/RylanFlow"; pkill -f "rylanflow app"`)
- Build: `sh packaging/build.sh` → `packaging/dist/RylanFlow.app`; install with `ditto` to /Applications
- Logs: `~/Library/Logs/RylanFlow/rylanflow.log`; hang debugging: `sample <pid> 3`, `kill -USR1 <pid>`
- Data: `~/Library/Application Support/RylanFlow/` (SQLite, meeting audio, models); config `~/.config/rylanflow/config.toml`
