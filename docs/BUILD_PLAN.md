# RylanFlow v0.2–v0.3 build plan: dashboard, dictation pop-up, meeting transcription

> **This is the source of truth for this work.** Do the next unchecked step below; tick its checkbox in the same PR that completes it. See `CLAUDE.md` at the repo root for the short version of the workflow rules.

## 1. Context

RylanFlow is a working push-to-talk dictation app for macOS (v0.1.1 released). You hold Right Option, talk, and release; local Whisper (mlx-whisper) transcribes, and the text is pasted into the focused app. It's a Python menu-bar app (rumps) packaged with PyInstaller as `/Applications/RylanFlow.app`. Repo: `~/code/RylanFlow`, GitHub `andersonrylan-gif/RylanFlow`.

The owner (Rylan) wants three things, in this order:
1. **Dashboard window** (like Wispr Flow's home screen): recent dictations, copy them, search, basic stats, settings.
2. **Dictation pop-up**: a small floating pill at the **bottom-center** of the screen while you dictate (live level bars), and "transcribing…" afterwards.
3. **Meeting transcription** (like Otter or Granola) for Google Meet, Zoom and anything else:
   - Captures your mic **and** system audio.
   - Live transcript with **speaker labels**: "You" from the mic; other people split on-device into Speaker 1..N.
   - **Names** suggested from the calendar invite, and each speaker can be renamed in one click.
   - **Auto-record**: starts automatically when a meeting starts and stops when it ends. No prompt, just a notification that it's recording.
   - **Transcripts only, no AI summaries.** Everything stays on the Mac.

Decisions already made with the owner: local-only processing; no meeting bot; no cloud API; pop-up at the bottom center; auto-record without asking.

The work is planned so a cheaper model can execute it step by step without the earlier conversation.

---

## 2. How to work (rules for every step)

- **One step = one GitHub issue → one branch → one PR → CI green → squash-merge.** The owner wants you to merge PRs yourself once CI passes.
  - Create the issue with `gh issue create --milestone "<milestone>"`.
  - Name branches `feat/…`, `fix/…`, `docs/…` or `build/…`.
  - Put `Closes #N` in the PR body. Wait for CI with `gh pr checks`, then run `gh pr merge --squash --delete-branch`.
- **Milestones:** `v0.2 Dashboard & pop-up` and `v0.3 Meetings` already exist. The older "v0.2 Meetings" milestone holds already-shipped fixes; leave it alone.
- **Commits:** use conventional commits (`feat:`, `fix:`, `docs:`, `build:`, `test:`, `chore:`). End messages with the attribution line from your system prompt. PR bodies get a What, Testing and Not-verified section, ending with the Claude Code line.
- **Quality gate before every PR:** `uv run ruff check . && uv run ruff format --check . && uv run pytest -q`. Line length is 100, and ruff rules are E, F, I, UP and B.
- **Code style:** match the existing code. Use small modules behind small interfaces, docstrings on modules and public classes, and short comments only where something is non-obvious. Write type hints in 3.12 style (`X | None`).
- **Tests:** CI runs on `macos-latest` with **no mic, no screen recording and no keyboard permission**. Anything touching CoreAudio, AppKit windows, ScreenCaptureKit or EventKit must sit behind an interface with a fake in tests. Keep pure logic (debouncers, chunkers, overlap assignment, SQL) in separate, fully tested functions.
- **Honesty:** if something can't be verified (it needs a real meeting, the owner's keyboard or permissions), say so in the PR under "Not verified", and tell the owner what to test.
- **Ask the owner** before anything public beyond normal PRs (releases, deleting data), and before installing system-level things.
- **Dependencies:** add with `uv add <pkg>` (runtime) or `uv add --dev <pkg>`. Commit `uv.lock`.

---

## 3. Hard-won gotchas (read before coding)

1. **Never block the pynput hotkey thread.** Its callbacks must only queue work (see `Pipeline.press/release` in `src/rylanflow/pipeline.py`). Blocking it makes macOS stop delivering key events.
2. **Never stop or close a PortAudio/sounddevice stream synchronously on an important thread.** `Pa_StopStream` can deadlock inside CoreAudio. Copy the pattern in `src/rylanflow/recorder.py`: capture into chunks behind a `_capturing` flag, return the audio immediately, and `abort()`/`close()` the stream on a daemon thread. A close that is still pending after `CLOSE_TIMEOUT` sets `stuck`, and the app relaunches (`src/rylanflow/relaunch.py`). Every new audio capture (meeting mic, system audio) must follow this pattern.
3. **UI (AppKit, rumps, the overlay, the WKWebView window) is touched only on the main thread.** Background threads update plain Python state, and a timer on the main thread renders it (see `RylanFlowApp._render` in `src/rylanflow/app.py`). For one-off main-thread calls from background threads, use `AppHelper.callAfter(fn)` from `PyObjCTools.AppHelper`.
4. **Permissions follow the code signature.** Every rebuild of the ad-hoc-signed `.app` makes macOS drop Accessibility and Input Monitoring.
   - Step 0.2 fixes this with a stable local signing identity.
   - Until then, after each rebuild: `for s in Accessibility ListenEvent PostEvent; do tccutil reset $s com.rylananderson.rylanflow; done`, then the owner re-adds `/Applications/RylanFlow.app` in System Settings → Privacy & Security and opens it from Finder.
   - New permissions in this plan: **Screen & System Audio Recording** (system audio) and **Calendars**.
5. **Frozen app:** `packaging/launcher.py` calls `multiprocessing.freeze_support()` first. Never remove it, or every dictation spawns a new copy of the app.
6. **Single instance:** `src/rylanflow/instance.py` uses a flock on `~/.config/rylanflow/app.lock`. Before testing, kill old copies: `pkill -9 -f "MacOS/RylanFlow"; pkill -f "rylanflow app"`.
7. **Run from source for development:** `uv run rylanflow app`. macOS then attributes permissions to VS Code or Terminal, which the owner has already granted. Build the `.app` only at release steps (`sh packaging/build.sh`), then install with `rm -rf /Applications/RylanFlow.app && ditto packaging/dist/RylanFlow.app /Applications/RylanFlow.app`.
8. **Debugging hangs:** `sample <pid> 3` gives native stacks, and `kill -USR1 <pid>` writes Python stacks to `~/Library/Logs/RylanFlow/rylanflow.log`.
9. **PyInstaller:** new packages with data or native libraries need `--collect-all <pkg>` in `packaging/build.sh`. New PyObjC frameworks need `--hidden-import <Framework>`. Static web files need `--add-data`. Check the built app launches with `open packaging/dist/RylanFlow.app` and read the log.
10. **Whisper hallucinates on silence** ("Thank you.", "Thanks for watching!"). For meeting chunks, pass `condition_on_previous_text=False` and drop segments with `no_speech_prob > 0.6` or `avg_logprob < -1.0`.

---

## 4. Current code map (what exists and should be reused)

| File | What it does |
|---|---|
| `src/rylanflow/app.py` | `RylanFlowApp(rumps.App)`: the menu, `_render` timer (0.2 s, main thread: icon, `pipeline.tick()`, restart check), `_on_text` (filler removal → `ClipboardInserter.insert`), `_notify`, settings toggles saved by `save_config`. `main()`: logging → single-instance lock → run. |
| `src/rylanflow/pipeline.py` | `Pipeline(recorder, transcriber, on_text, on_error, on_cue, clock)`: `press()/release()` queue events; an `audio-control` thread does start/stop; transcription threads run one at a time (`_transcribe_lock`); `state` is recording / working / idle; `restart_reason()`; `tick()` auto-stops at 10 minutes. |
| `src/rylanflow/recorder.py` | `Recorder`: 16 kHz mono float32; `start()` (5 s open timeout), `stop()` (non-blocking, background close), `stuck`; `AudioStuckError`; `write_wav()`. |
| `src/rylanflow/transcriber.py` | `Transcriber` protocol; `MLXWhisperTranscriber(model, language)` with `.transcribe(audio) -> str`; `DEFAULT_MODEL` (large-v3-turbo) and `FAST_MODEL` (base). |
| `src/rylanflow/config.py` | `Config` dataclass (hotkey, model, language, sounds, remove_fillers), `load_config` / `save_config` for `~/.config/rylanflow/config.toml` (`RYLANFLOW_CONFIG` env var overrides the path). |
| `src/rylanflow/inserter.py` | `ClipboardInserter.insert(text)`: clipboard + Cmd+V, then restores the clipboard. |
| `src/rylanflow/hotkey.py` | `PushToTalk(on_press, on_release, key)`, `set_key()`, `parse_key()`. |
| `src/rylanflow/cleanup.py` | `remove_fillers(text)`. |
| `src/rylanflow/sounds.py` | `play("start" or "stop")` via afplay. |
| `src/rylanflow/logs.py` | `setup_logging()`, `dump_threads()`, `LOG_PATH`. |
| `src/rylanflow/relaunch.py` | `relaunch_and_exit()`. |
| `src/rylanflow/autostart.py` | LaunchAgent for login (`rylanflow autostart on`). |
| `src/rylanflow/__main__.py` | CLI: `record`, `transcribe`, `listen`, `app`, `autostart`. |
| `packaging/build.sh`, `packaging/launcher.py` | PyInstaller build (ad-hoc signed, `LSUIElement`, mic usage string). |
| `tests/` | pytest with fakes (`FakeRecorder`, `FakeTranscriber`, `Clock`, `wait_for` in `tests/test_pipeline.py`). Reuse these patterns. |

Installed: mlx-whisper 0.4.3, sounddevice 0.5.6 (PortAudio 19.7), rumps 0.4.0, pyobjc 12.2 (Cocoa, Quartz, ApplicationServices). **Not yet installed:** pyobjc WebKit, ScreenCaptureKit, CoreMedia, EventKit, libdispatch. The Mac runs macOS 26.6 on Apple Silicon.

---

## 5. Target architecture

```
                 ┌──────────── RylanFlowApp (main thread: menu, overlay, dashboard window, timers)
hotkey ─► Pipeline (dictation) ──► TranscriptionService (1 worker, priority queue, shared Whisper)
                                     ▲                                     │
MeetingDetector ─► MeetingSession ───┘ (mic track "You" + system track "Others")
                      │                                                    ▼
                      └──────────────► Store (SQLite) ◄── DashboardServer (127.0.0.1 JSON API)
                                                              ▲
                                       WKWebView window ──────┘ (static HTML/JS)
```

New modules (`src/rylanflow/…`):
- `store.py`: SQLite at `~/Library/Application Support/RylanFlow/rylanflow.db` (`RYLANFLOW_DATA_DIR` overrides the folder).
- `dashboard/server.py`, `dashboard/static/{index.html,app.js,style.css}`, `dashboard/window.py`.
- `overlay.py` (AppKit pill) and `overlay_model.py` (pure logic).
- `transcription_service.py`.
- `meetings/` package:
  - `system_audio.py` (ScreenCaptureKit)
  - `mic_track.py`
  - `session.py` (chunking, live transcription)
  - `chunker.py` (pure)
  - `diarize.py` (sherpa-onnx)
  - `speakers.py` (pure: overlap assignment, echo dedupe)
  - `calendar.py` (EventKit)
  - `detector.py` (CoreAudio probe and window titles)
  - `detector_logic.py` (pure debounce)

### Data model (`store.py`, schema version in `PRAGMA user_version`)
```sql
CREATE TABLE dictations (id INTEGER PRIMARY KEY, created_at TEXT NOT NULL, text TEXT NOT NULL,
  seconds REAL NOT NULL, app_name TEXT, model TEXT);
CREATE TABLE meetings (id INTEGER PRIMARY KEY, title TEXT, started_at TEXT NOT NULL, ended_at TEXT,
  source_app TEXT, calendar_event_id TEXT, attendees_json TEXT DEFAULT '[]',
  status TEXT NOT NULL DEFAULT 'recording');      -- recording | processing | done | failed
CREATE TABLE speakers (id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings ON DELETE CASCADE,
  label TEXT NOT NULL, display_name TEXT, is_me INTEGER NOT NULL DEFAULT 0);   -- label: You, Others, Speaker 1…
CREATE TABLE segments (id INTEGER PRIMARY KEY, meeting_id INTEGER NOT NULL REFERENCES meetings ON DELETE CASCADE,
  speaker_id INTEGER REFERENCES speakers, track TEXT NOT NULL,                -- mic | system
  start_s REAL NOT NULL, end_s REAL NOT NULL, text TEXT NOT NULL);
CREATE INDEX segments_meeting ON segments(meeting_id, start_s);
CREATE VIRTUAL TABLE dictations_fts USING fts5(text, content='dictations', content_rowid='id');  -- search (+ triggers)
```
Store API (thread-safe: one connection, `check_same_thread=False`, guarded by a lock, WAL mode, `foreign_keys=ON`):
- Dictations: `add_dictation(text, seconds, app_name, model) -> int`, `list_dictations(limit, offset, query) -> list[dict]`, `delete_dictation(id)`.
- Stats: `stats() -> {"words_today", "words_week", "words_total", "dictations_total", "minutes_saved"}`. Minutes saved assume 40 wpm typing versus speaking time.
- Meetings: `create_meeting(...)`, `finish_meeting(id, status)`, `list_meetings()`, `get_meeting(id)` (with speakers and segments), `delete_meeting(id)`.
- Speakers and segments: `add_speaker(...)`, `rename_speaker(id, display_name)`, `add_segments(meeting_id, rows)`, `reassign_segments(...)`.

---

## 6. The steps

Each step lists **Files**, **Do**, **Tests**, **Done when**. Keep every PR small: if a step grows past about 400 changed lines, split it.

### Phase 0: Foundation (milestone `v0.2 Dashboard & pop-up`)

- [x] **0.1 Plan into the repo.**
  - **Files:** `docs/BUILD_PLAN.md` (this document), `CLAUDE.md`, `README.md` (roadmap section links to the plan).
  - **Do:** create both milestones with `gh api repos/andersonrylan-gif/RylanFlow/milestones -f title=...`.
  - **Done when:** merged, and a fresh Claude Code session in the repo loads `CLAUDE.md`.

- [x] **0.2 Stable local code signing**, so permissions survive rebuilds.
  - **Files:** `packaging/make_signing_cert.sh` (new), `packaging/build.sh`.
  - **Do:**
    - The script creates a self-signed code-signing certificate named **"RylanFlow Local Signing"** in the login keychain. Use `openssl req -x509 -newkey rsa:2048 -days 3650 -subj "/CN=RylanFlow Local Signing" -addext "extendedKeyUsage=codeSigning" -addext "keyUsage=digitalSignature"`, then `openssl pkcs12 -export` and `security import … -k ~/Library/Keychains/login.keychain-db -T /usr/bin/codesign`.
    - Trust it for code signing with `security add-trusted-cert -p codeSign -k ~/Library/Keychains/login.keychain-db cert.pem`. **This shows a password prompt the owner must approve**, so tell them first.
    - In `build.sh`, if `security find-identity -v -p codesigning | grep "RylanFlow Local Signing"` succeeds, sign with `codesign --force --deep --sign "RylanFlow Local Signing"`. Otherwise fall back to ad-hoc `-`.
  - **Tests:** none automated.
  - **Done when:** build, grant permissions once, rebuild, reinstall, and the log has no "not trusted" warning without re-granting. If `codesign` rejects the identity, debug with `security find-identity -p codesigning`. If it can't be made to work in about an hour, stop, document it in the PR and move on. It's a nice-to-have.

- [x] **0.3 Store.**
  - **Files:** `src/rylanflow/store.py`, `tests/test_store.py`.
  - **Do:** schema and API as in section 5. The `data_dir()` helper honours `RYLANFLOW_DATA_DIR`. Migrations go in a list of SQL scripts indexed by `user_version`. Timestamps are ISO 8601 UTC (`datetime.now(UTC).isoformat()`). Keep FTS in sync with triggers (insert and delete).
  - **Tests:** a tmp-path database covering add, list, search, delete, stats with a fake "now", and meeting, speaker and segment round-trips with cascade delete.
  - **Done when:** tests pass.

- [x] **0.4 Save every dictation.**
  - **Files:** `pipeline.py`, `app.py`, `tests/test_pipeline.py`.
  - **Do:**
    - Change the `Pipeline` `on_text` callback to `on_text(text: str, seconds: float)`. `seconds` is `audio.size / SAMPLE_RATE`.
    - In `RylanFlowApp._on_text`, after filler removal, call `store.add_dictation(text, seconds, frontmost_app(), self._config.model)`, then insert the text.
    - `frontmost_app()` = `NSWorkspace.sharedWorkspace().frontmostApplication().localizedName()`, wrapped in try/except returning `None`.
    - Update `__main__.listen` too: its `on_text` must accept two arguments.
    - A store failure must **never** stop the paste: wrap it, log, and continue.
  - **Done when:** after dictating from source, `sqlite3 ~/Library/Application\ Support/RylanFlow/rylanflow.db "select * from dictations"` shows rows.

### Phase 1: Dashboard (milestone `v0.2 Dashboard & pop-up`)

- [x] **1.1 Dashboard API server.**
  - **Files:** `src/rylanflow/dashboard/__init__.py`, `dashboard/server.py`, `tests/test_dashboard_server.py`.
  - **Do:**
    - Use `http.server.ThreadingHTTPServer` on `127.0.0.1`, port 0 (random), running in a daemon thread. `DashboardServer(store, actions).start() -> url`, `stop()`.
    - **Security:** generate a random token (`secrets.token_urlsafe(24)`). The URL is `http://127.0.0.1:<port>/?t=<token>`.
      - Every `/api/*` request must carry the header `X-RylanFlow-Token: <token>`.
      - Reject any request whose `Host` header isn't `127.0.0.1:<port>` (prevents DNS rebinding).
      - Answer 403 otherwise.
    - Routes (JSON):
      - `GET /api/dictations?q=&limit=50&offset=0`
      - `DELETE /api/dictations/<id>`
      - `POST /api/copy` `{"text": …}` copies via `pyperclip.copy`. Copying happens server-side because WKWebView clipboard access is unreliable.
      - `GET /api/stats`
      - `GET/PUT /api/settings`, through an `actions` object the app provides: `get_settings()` and `apply_settings(dict)`.
      - `GET /` and `/static/*` serve files from `dashboard/static`, located with `importlib.resources`.
    - `actions` is a small Protocol, so tests can pass a fake.
  - **Tests:** start the server on a temp store, hit it with `urllib.request`, and check auth failures (403 without token, wrong Host), list, search, delete, copy (monkeypatch pyperclip) and settings round-trip.
  - **Done when:** tests pass.

- [x] **1.2 Dashboard UI: Dictations page.**
  - **Files:** `dashboard/static/index.html`, `app.js`, `style.css`.
  - **Do:**
    - Vanilla JS, no build step, no CDN (it works offline). Read the token from `location.search` and send it on every fetch.
    - Layout like Wispr Flow's home: a left sidebar (Home, Meetings — hidden until Phase 3 — and Settings), with the main area showing:
      - a stats strip: words this week, total words, dictations, minutes saved;
      - a search box;
      - a list grouped by day ("Today", "Yesterday", date), each item showing time, app name and text (clamped to 3 lines, click to expand), with **Copy** (turns into "Copied ✓" for 1.5 s) and **Delete** (asks confirmation inline, not `confirm()`).
    - Poll `/api/dictations` every 3 s while the page is visible, so new dictations appear.
    - Use the system font and support light and dark mode via `prefers-color-scheme`. Make it look polished: generous spacing, rounded cards, subtle borders.
  - **Tests:** none automated (static). Open the server URL in a browser and check by hand.
  - **Done when:** it works in Safari via the URL and looks clean in light and dark mode.

- [x] **1.3 Native dashboard window.**
  - **Files:** `dashboard/window.py`, `app.py`; `uv add pyobjc-framework-WebKit`.
  - **Do:**
    - `DashboardWindow(url)` creates (lazily, on the main thread) an `NSWindow` of about 1000×700, titled "RylanFlow", with close, miniaturize and resizable styles, and sets `setReleasedWhenClosed_(False)` so it can be reopened.
    - Its content is `WebKit.WKWebView` loading the URL. `show()` centers the window the first time, then calls `makeKeyAndOrderFront_` and `NSApp.activateIgnoringOtherApps_(True)`.
    - **LSUIElement apps can't keep a window in front without activation.** Optionally switch `NSApp.setActivationPolicy_` to Regular while the window is open (a Dock icon appears) and back to Accessory on close, using an `NSWindowDelegate` `windowWillClose_`. Do this; it matches Wispr Flow.
    - In the app:
      - Start `DashboardServer` in `RylanFlowApp.__init__`.
      - Add a menu item **"Open Dashboard…"** at the top of the menu.
      - Open the dashboard automatically on the very first launch (a flag in config, `dashboard_seen`).
      - If the WebKit import fails, fall back to `webbrowser.open(url)`.
  - **Done when:** run from source, choose Open Dashboard, and a native window shows the dictations. Close it and reopen it, and it works again.

- [x] **1.4 Settings page.**
  - **Files:** `static/*`, `app.py`, `config.py`.
  - **Do:**
    - Settings UI controls: Hotkey (Right Option, Command or Control), Model (Fast or Accurate), Sound cues, Remove um/uh, Pop-up position (Bottom, Top or Off; added in 2.2), Start at login (calls `autostart.enable/disable`).
    - `apply_settings` runs on the server thread. It updates `Config`, calls `save_config`, and applies live changes via `AppHelper.callAfter`: `ptt.set_key`, `transcriber.model`, menu checkmarks.
    - Factor the existing menu toggle handlers so the menu and the dashboard share one `_apply(config_changes)` method.
  - **Done when:** changing the hotkey in the dashboard takes effect immediately, and the menu checkmark updates.

### Phase 2: Dictation pop-up (milestone `v0.2 Dashboard & pop-up`)

- [ ] **2.1 Live audio level and overlay model.**
  - **Files:** `recorder.py`, `overlay_model.py`, `tests/test_overlay_model.py`, `tests/test_recorder.py`.
  - **Do:**
    - `Recorder.level` returns the RMS of the most recent callback block, as a float in 0..1. Compute it in `_on_audio` (`float(np.sqrt(np.mean(block**2)))`) and store it as a plain float (assignment is atomic).
    - `overlay_model.OverlayModel` is pure: `update(state, level, now) -> Frame(visible, mode, bars: list[float])`.
      - It keeps the last 9 bar heights with smoothing: attack fast, release slow, scaled with `min(1, (level*25) ** 0.6)`.
      - Mode is `recording` (bars), `working` (3-dot pulse phase from `now`), or hidden.
      - It holds "visible" for 250 ms after going idle, so it fades rather than blinks.
  - **Tests:** model behaviour for silence, loud and decay, state transitions, and fade timing with a fake clock.
  - **Done when:** tests pass.

- [ ] **2.2 Overlay panel.**
  - **Files:** `overlay.py`, `app.py`, `config.py` (`overlay: str = "bottom"`, with `"top"` and `"off"` as options).
  - **Do:**
    - `Overlay` creates an `NSPanel` with style `NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel`, size 120×34. Panel settings:
      - `setLevel_(NSStatusWindowLevel)`, `setOpaque_(False)`, `setBackgroundColor_(NSColor.clearColor())`, `setHasShadow_(True)`, `setIgnoresMouseEvents_(True)`.
      - `setCollectionBehavior_(CanJoinAllSpaces | Stationary | FullScreenAuxiliary)`, so it shows over full-screen apps.
    - Content is an `NSVisualEffectView` (material `HUDWindow`, state Active, layer corner radius 17, masksToBounds) containing a custom `NSView` subclass whose `drawRect_` draws:
      - recording: a small red dot plus 9 rounded white bars from `Frame.bars`;
      - working: 3 pulsing dots.
    - Position: bottom center of the screen that holds the mouse (`NSScreen.screens()` containing `NSEvent.mouseLocation()`), using `visibleFrame` with y = minY + 24, or top: maxY − 24 − height.
    - Drive it from a dedicated `NSTimer` at 30 fps that only runs while visible. `app._render` starts or stops it when the state changes. All AppKit calls go on the main thread.
    - Use `orderFrontRegardless()` so it never steals focus. Pasting must still go to the user's app: verify the paste lands in Notes while the pop-up is visible.
  - **Done when:** holding the hotkey shows the bars moving with your voice at the bottom center; releasing shows the dots, which disappear after the paste. It works over a full-screen app, and the setting can move it to the top or turn it off.

- [ ] **2.3 Release v0.2.0.**
  - **Do:**
    - Update `CHANGELOG.md`, bump `pyproject.toml`, `src/rylanflow/__init__.py` and the build.sh plist version, and update the README (screenshots of the dashboard and pop-up go in `docs/images/`; ask the owner to take them, or use `screencapture -l`).
    - In `packaging/build.sh`, add `--add-data "../src/rylanflow/dashboard/static:rylanflow/dashboard/static"` (check the path with the spec), plus `--hidden-import WebKit`.
    - Build, install, have the owner re-grant permissions if 0.2 failed, and smoke-test: dashboard, pop-up, dictation.
    - **Ask the owner before publishing**, then run `gh release create v0.2.0 <zip>` (zip with `ditto -c -k --sequesterRsrc --keepParent`).
  - **Done when:** the release is live and the owner has confirmed it works from `/Applications`.

### Phase 3: Meetings (milestone `v0.3 Meetings`)

> Highest-risk phase. Steps 3.0 and 3.1 are **spikes**: prove the macOS APIs work from Python before building on them. Record the results in ADRs.

- [ ] **3.0 Spike: system audio capture with ScreenCaptureKit.**
  - **Files:** `scripts/spike_system_audio.py`, `docs/decisions/0003-system-audio.md`; `uv add pyobjc-framework-ScreenCaptureKit pyobjc-framework-CoreMedia pyobjc-framework-libdispatch`.
  - **Do:** the script captures 10 s of system audio (play a YouTube video) to `system.wav` at 16 kHz mono. Sketch:
    ```python
    import ScreenCaptureKit as SCK, CoreMedia, objc, numpy as np
    from Foundation import NSObject
    from libdispatch import dispatch_queue_create


    class Output(NSObject):  # implements SCStreamOutput
        def stream_didOutputSampleBuffer_ofType_(self, stream, sbuf, kind):
            if kind != SCK.SCStreamOutputTypeAudio:
                return
            block = CoreMedia.CMSampleBufferGetDataBuffer(sbuf)
            n = CoreMedia.CMBlockBufferGetDataLength(block)
            err, data = CoreMedia.CMBlockBufferCopyDataBytes(block, 0, n, None)
            chunks.append(np.frombuffer(bytes(data), dtype=np.float32))  # float32 non-interleaved


    # SCShareableContent.getShareableContentWithCompletionHandler_(cb) -> content.displays()[0]
    # filter = SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])
    # cfg = SCStreamConfiguration.new(); cfg.setCapturesAudio_(True); cfg.setExcludesCurrentProcessAudio_(True)
    # cfg.setSampleRate_(48000); cfg.setChannelCount_(1); cfg.setWidth_(2); cfg.setHeight_(2)
    # cfg.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 1))    # we only want audio
    # stream = SCStream.alloc().initWithFilter_configuration_delegate_(filter, cfg, None)
    # stream.addStreamOutput_type_sampleHandlerQueue_error_(out, SCStreamOutputTypeAudio, dispatch_queue_create(b"rf.audio", None), None)
    # stream.startCaptureWithCompletionHandler_(lambda err: ...)
    ```
    The completion handlers run on other threads, so use `threading.Event` to wait. The script needs a run loop: `AppHelper.runConsoleEventLoop()` or `NSRunLoop.currentRunLoop().runUntilDate_`. To resample 48k→16k, use `scipy.signal.resample_poly(x, 1, 3)` if scipy is importable (it's bundled transitively; verify with `uv pip show scipy`). Otherwise apply a simple FIR low-pass and decimate by 3 in numpy. Verify the exact PyObjC signatures by checking `objc` metadata (`SCK.SCStream.addStreamOutput_type_sampleHandlerQueue_error_.__metadata__()`) and the installed version.
  - **Permission:** the first run triggers **Screen & System Audio Recording** for the terminal or VS Code. The owner must allow it and restart that app.
  - **Done when:** `system.wav` plays back the video's audio clearly, and the transcriber can transcribe it (`uv run rylanflow transcribe system.wav`).
  - **Fallback if blocked after about 2 hours:** use the macOS 14.4+ Core Audio process tap (`AudioHardwareCreateProcessTap` with a `CATapDescription` of global mono, excluding our PID, via PyObjC CoreAudio or ctypes), or, last resort, document a BlackHole + Multi-Output setup. Write the decision into ADR 0003 either way.

- [ ] **3.1 Spike: which app is using the mic.**
  - **Files:** `scripts/spike_mic_users.py`, an appendix in ADR 0003.
  - **Do:** using ctypes on `/System/Library/Frameworks/CoreAudio.framework/CoreAudio`, list audio "process objects":
    - `AudioObjectGetPropertyDataSize` / `AudioObjectGetPropertyData` on `kAudioObjectSystemObject` (1) with selector `'prs#'` (kAudioHardwarePropertyProcessObjectList), scope `'glob'`, element 0 → `UInt32[]` object IDs.
    - For each ID read `'pbid'` (bundle ID, a CFStringRef; convert with `objc.objc_object(c_void_p=ptr)` → `str`), `'ppid'` (PID) and `'piri'` (is running input, UInt32).
    - Build fourcc ints with `int.from_bytes(b'prs#', 'big')`.
    - Also list window titles with `Quartz.CGWindowListCopyWindowInfo(kCGWindowListOptionOnScreenOnly, kCGNullWindowID)` → `kCGWindowOwnerName` and `kCGWindowName`. Titles need Screen Recording permission, which 3.0 already grants.
  - **Done when:**
    - During a real Google Meet in Chrome (ask the owner to join a test call; meet.new works alone), the script prints Chrome's bundle ID (probably `com.google.Chrome` or `com.google.Chrome.helper`) with `running input = 1`, and a window title containing "Meet".
    - Record the exact bundle IDs and titles seen in the ADR. Also try Zoom if installed.

- [ ] **3.2 Shared transcription service.**
  - **Files:** `transcription_service.py`, `transcriber.py`, `pipeline.py`, tests.
  - **Do:**
    - Add `MLXWhisperTranscriber.transcribe_segments(audio, *, meeting=False) -> list[Segment(start, end, text, no_speech_prob, avg_logprob)]` from `mlx_whisper.transcribe(...)["segments"]`. With `meeting=True`, pass `condition_on_previous_text=False` and filter hallucinations (gotcha 10). `transcribe()` stays as-is for dictation.
    - `TranscriptionService(transcriber)` is one worker thread with a `queue.PriorityQueue` of `(priority, seq, job)`. `submit(fn_name, audio, priority, callback, on_error)`. Dictation is priority 0 and meeting chunks priority 1, so dictation never waits behind a backlog of meeting chunks except the one in flight.
    - Track job deadlines like `Pipeline._jobs` for `restart_reason`.
    - Refactor `Pipeline` to submit to the service instead of its own threads, keeping its public API and tests green.
  - **Tests:** priority ordering, error callback, deadline-based hang detection with a fake clock.
  - **Done when:** all old tests and the new ones pass, and dictation still works from source.

- [ ] **3.3 Meeting capture tracks.**
  - **Files:** `meetings/__init__.py`, `meetings/mic_track.py`, `meetings/system_audio.py`, `meetings/chunker.py`, tests for the chunker.
  - **Do:**
    - `MicTrack` is a continuous 16 kHz mono capture using the **same safe pattern as `Recorder`**: chunks are appended under a lock; `drain() -> np.ndarray` returns and clears them; `close()` is non-blocking. It opens its own `sd.InputStream` so dictation can keep working during a meeting.
    - `SystemAudioTrack` productionizes the 3.0 spike with the same `drain()/close()` interface; it resamples to 16 kHz.
    - Both write their raw audio incrementally to `data_dir()/meetings/<id>/{mic,system}.wav` with `wave` (append frames), for diarization later.
    - `chunker.Chunker(sample_rate=16000, target_s=30, max_s=40, min_silence_s=0.4)` is pure. `push(audio) -> list[(offset_s, np.ndarray)]` returns windows cut at the quietest 0.4 s within 25–40 s, using RMS energy, and `flush()` returns the remainder. Offsets are absolute seconds from meeting start.
  - **Tests:** the chunker with synthetic audio (sine bursts plus silence gaps): cut points fall in silence, offsets add up, `flush` behaves.
  - **Done when:** tests pass, and a manual script records 60 s of both tracks to WAVs.

- [ ] **3.4 Live meeting session, manual start and stop.**
  - **Files:** `meetings/session.py`, `meetings/speakers.py`, `app.py`, `store.py` (if needed), tests.
  - **Do:**
    - `MeetingSession(store, service, tracks, clock)`:
      - `start(title=None, source_app=None)` creates the meeting row and speakers "You" (is_me=1, display_name = `config.my_name` or `NSFullUserName()`) and "Others".
      - A `meeting-pump` thread drains both tracks every 1 s into each track's `Chunker` and submits windows to `TranscriptionService` at priority 1. Callbacks offset segment times and `store.add_segments` with track and speaker.
      - `stop()` closes the tracks (non-blocking), flushes the chunkers, waits for queued chunks, then sets status to `processing` and kicks off 3.5 when it exists, or `done`.
    - **Echo dedupe** in `speakers.py` (pure): when speakers aren't on headphones, the mic hears other people. Drop a mic segment if a system segment overlaps it in time by at least 50% and `difflib.SequenceMatcher(None, a, b).ratio() >= 0.6`. Also skip mic chunks whose RMS is below 0.01 (silence).
    - Menu: "Start meeting recording" / "Stop meeting recording" (label toggles). While recording, the menu-bar icon shows "🎙●", plus a notification "Recording meeting — open RylanFlow to stop."
    - Dashboard:
      - Meetings list with title, date, duration, status chip.
      - Detail view: speaker-colored transcript with timestamps (mm:ss), auto-scrolling while live (poll every 2 s), and "Copy transcript".
      - API: `GET /api/meetings`, `GET /api/meetings/<id>`, `DELETE /api/meetings/<id>`, `POST /api/meetings/start` and `/stop`.
  - **Tests:** `speakers.py` echo dedupe; a session test with fake tracks, a fake service and a temp store (segments stored with the right offsets and speakers; stop flushes).
  - **Done when:** start a meeting from the menu, play a YouTube interview and talk over it, and the transcript appears live in the dashboard with "You" and "Others" separated.

- [ ] **3.5 Speaker separation (diarization) for "Others".**
  - **Files:** `meetings/diarize.py`, `meetings/speakers.py`, `meetings/session.py`, tests; `uv add sherpa-onnx`.
  - **Do:**
    - After `stop()`, a `meeting-post` thread loads `system.wav` and runs sherpa-onnx offline speaker diarization:
      ```python
      import sherpa_onnx

      cfg = sherpa_onnx.OfflineSpeakerDiarizationConfig(
          segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
              pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=SEG)
          ),
          embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=EMB),
          clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.5),
          min_duration_on=0.3,
          min_duration_off=0.5,
      )
      result = sherpa_onnx.OfflineSpeakerDiarization(cfg).process(samples).sort_by_start_time()
      # each r: r.start, r.end, r.speaker (0-based)
      ```
    - **Verify the API against the installed sherpa-onnx version** (`help(sherpa_onnx.OfflineSpeakerDiarizationConfig)`).
    - Models download once to `data_dir()/models/`: the pyannote segmentation-3.0 ONNX (`sherpa-onnx-pyannote-segmentation-3-0`) and a speaker-embedding ONNX (e.g. `3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx` or `nemo_en_titanet_small.onnx`). Both are on the k2-fsa/sherpa-onnx GitHub releases pages (`speaker-segmentation-models`, `speaker-recongition-models`, sic). Pin the exact URLs and add SHA-256 checks.
    - `speakers.assign(segments, turns) -> {segment_id: speaker_index}` (pure): the speaker with the most time overlap, with ties going to the nearest turn.
    - Create speakers "Speaker 1..N" and reassign the system segments; delete the generic "Others" speaker if it has no segments left. Status then becomes `done`.
    - If anything fails, log it, keep "Others", and set status `done` (never lose the transcript). Delete the WAVs after success unless `config.keep_meeting_audio`.
  - **Tests:** `assign` cases (single speaker, two alternating, segment spanning two turns, no turns); session post-processing with a fake diarizer.
  - **Done when:** after a two-person YouTube interview, the transcript shows Speaker 1 and Speaker 2 mostly correctly. Report the observed quality honestly in the PR.

- [ ] **3.6 Names from the calendar and renaming.**
  - **Files:** `meetings/calendar.py`, `session.py`, `dashboard/server.py`, static files, `packaging/build.sh` (plist `NSCalendarsFullAccessUsageDescription`); `uv add pyobjc-framework-EventKit`.
  - **Do:**
    - `CalendarLookup.current_event(at: datetime) -> Event(title, id, attendees: list[str], url)` uses `EKEventStore`:
      - Ask access with `requestFullAccessToEventsWithCompletion_` (macOS 14+).
      - Search `predicateForEventsWithStartDate_endDate_calendars_(at-15min, at+15min, None)`, preferring events with a meet/zoom/teams URL in `URL()`, `location()` or `notes()`.
      - Attendee names come from `attendees()` → `name()` or the email's local part.
      - Google Calendar works if the owner has added the Google account in macOS Calendar (Internet Accounts). Tell them.
    - On meeting start, set the title (event title, or "Meeting with <app> – <date>") and store `attendees_json`.
    - Dashboard: each speaker chip is clickable and shows a popover with attendee suggestions and a free-text field. `PATCH /api/speakers/<id>` `{"display_name": …}` updates every segment's label live.
  - **Tests:** URL and attendee parsing (pure helpers) with fake event objects; speaker rename API.
  - **Done when:** a test calendar event with attendees gives the meeting its title, and renaming "Speaker 1" to a person updates the whole transcript.

- [ ] **3.7 Auto-record.**
  - **Files:** `meetings/detector.py`, `meetings/detector_logic.py`, `app.py`, `config.py` (`auto_record_meetings: bool = True`), tests.
  - **Do:**
    - `detector.probe() -> Signals(mic_users: set[str], window_titles: list[tuple[owner, title]], now)` uses the 3.1 code. Our own bundle ID and Python PID are excluded.
    - `detector_logic.MeetingDetector` (pure) maps signals to a decision:
      - **Meeting apps:** `us.zoom.xos`, `com.microsoft.teams2`, `com.apple.FaceTime`, `com.cisco.webexmeetingsapp`, plus Slack only if a window title contains "Huddle".
      - **Browsers:** `com.google.Chrome*`, `com.apple.Safari*`, `com.apple.WebKit*`, `company.thebrowser.Browser*`, `com.microsoft.edgemac*`, `org.mozilla.firefox*`, `com.brave.Browser*`. These count only when a browser-owned window title matches `Meet –`, `Google Meet`, `Zoom`, `Microsoft Teams` or `Webex`.
      - Use the exact IDs and titles recorded in ADR 0003 from the spike.
      - Start when "in meeting" holds for 5 s. Stop when false for 45 s (tolerates mute and toggles).
      - Return `Start(app_name)`, `Stop()` or `None`.
      - Muting yourself in Meet usually keeps the mic stream running; if the spike shows otherwise, use window-title presence for "still in meeting".
    - The app polls the probe every 2 s on a background thread and applies decisions on the main thread via `AppHelper.callAfter`. Starting shows a notification "Transcribing your <app> meeting" and the menu-bar "🎙●". A manual stop overrides auto-start until the meeting signal goes false.
    - Settings toggle "Automatically transcribe meetings". Add a README note about consent: tell participants you're transcribing.
  - **Tests:** detector logic covering Zoom start and stop, Chrome with and without a Meet title, flapping signals, manual-stop override, own-process exclusion.
  - **Done when:** joining a Google Meet starts a transcript within about 10 s with no clicks, and leaving finishes it within about 1 minute.

- [ ] **3.8 Meeting polish.**
  - **Do:**
    - Export Markdown (`GET /api/meetings/<id>/export.md` with title, date, attendees, then `**Name** [mm:ss]: text`) and a "Download .md" button.
    - Meeting search (FTS over segments).
    - Delete meeting with inline confirmation.
    - Show "Processing speakers…" while status is `processing`.
    - Handle sleep and lid close: on `NSWorkspaceWillSleepNotification`, stop the meeting cleanly.
  - **Done when:** checked by hand in the dashboard.

- [ ] **3.9 (Optional) Voice memory.**
  - After a speaker is renamed, store that speaker's mean embedding (sherpa `SpeakerEmbeddingExtractor`) in a `voices(name, embedding BLOB, updated_at)` table.
  - In later meetings, auto-name clusters whose cosine similarity is at least 0.7 (shown as "Name?" until the owner confirms).
  - Only do this if 3.5 quality is good.

- [ ] **3.10 Release v0.3.0.**
  - **Do:**
    - In build.sh: `--collect-all sherpa_onnx`; hidden imports `ScreenCaptureKit`, `CoreMedia`, `EventKit`, `libdispatch`; the calendar usage string.
    - Update the changelog, README (meetings section, permissions: Screen & System Audio Recording, Calendars) and architecture doc; add ADR 0004 (local diarization). Write an ADR for anything surprising.
    - Build, install, have the owner run a real meeting, then **ask before publishing** the release.

---

## 7. Verification checklist (run at each release)

1. `uv run ruff check . && uv run ruff format --check . && uv run pytest -q` all green, and CI green on the PR.
2. Run from source: `pkill -9 -f "MacOS/RylanFlow"; uv run rylanflow app`.
3. **Dictation:** 10 dictations in Notes, short and long (more than 60 s). Each pastes once, the pop-up shows and hides, the dashboard lists them all, copy and search work, and there's no ⏳ hang (`tail ~/Library/Logs/RylanFlow/rylanflow.log`).
4. **Meetings (v0.3):** join a Google Meet with a second device or a YouTube interview playing through it. Check:
   - auto-record starts;
   - the live transcript shows "You" and "Others";
   - after leaving, it finishes and Speaker 1/2 appear;
   - calendar names are suggested and renaming works;
   - Markdown export opens correctly.
   - Dictation still works during the meeting.
5. Packaged app: build, install to `/Applications`, grant permissions, then repeat 3 and 4 from the `.app`.
6. The owner signs off before any `gh release create`.

## 8. Risks and fallbacks

- **ScreenCaptureKit from PyObjC (3.0):** sample-buffer conversion is the tricky part. Fallbacks are the Core Audio process tap, then BlackHole. Timebox the spike.
- **Diarization quality on compressed meeting audio:** expect mistakes. Renaming in the UI is the safety net; tune `FastClusteringConfig.threshold` (0.4–0.7) on real recordings.
- **Auto-detect for browser meetings** relies on window titles and needs Screen Recording permission, which we already need. If titles are unavailable, fall back to "browser using mic + a calendar event with a meet URL now".
- **CPU and battery:** large-v3-turbo on 30 s chunks is fine on Apple Silicon. Use the configured model, and add `meeting_model` to config only if needed.
- **App size:** sherpa-onnx adds about 30 MB plus models downloaded at runtime (not bundled).
