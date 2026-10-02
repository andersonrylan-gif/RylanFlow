# 0004 — Speaker diarization (step 3.5) blocked on a broken sherpa-onnx wheel

**Status:** Blocked, not accepted/rejected

## Context
Step 3.5 splits the "Others" speaker into Speaker 1..N by running offline diarization
(segmentation + speaker-embedding clustering) over each meeting's `system.wav`, via
`sherpa-onnx` as specified in BUILD_PLAN.md.

## What happened
`uv add sherpa-onnx` resolves and installs cleanly (tried 1.13.8 and 1.13.1), but the package
fails to import on this machine (macOS arm64, Python 3.12):

```
ImportError: dlopen(.../sherpa_onnx/lib/_sherpa_onnx.cpython-312-darwin.so, 0x0002):
Library not loaded: @rpath/libonnxruntime.dylib
```

The compiled extension (`_sherpa_onnx...so`) references a bundled `libonnxruntime*.dylib` that
the PyPI wheel never actually includes -- the whole downloaded wheel is only ~2 MB, far too
small to contain a full ONNX Runtime. Installing the separate `onnxruntime` PyPI package doesn't
help either: the extension's hardcoded `@rpath` search list (visible in the dlopen error) never
looks in `onnxruntime`'s own install location. This reproduces identically across both versions
tried, so it looks like a genuine upstream packaging bug for this platform/Python combination
(or an artifact of how PyPI's 100 MB-ish size limits interact with a dylib that size), not a
local environment issue.

## Decision
Don't vendor a manually-downloaded `libonnxruntime.dylib` into the package as a workaround --
that would be fragile (version-pinned to whatever's hardcoded in each sherpa-onnx release),
invisible to `uv.lock`, and would need to be repeated in CI and in `packaging/build.sh`, which
defeats the point of a reproducible build. Instead:
- Ship the diarization-independent half now: `meetings/speakers.assign(segments, turns)`, pure
  and fully unit-tested, ready to consume whatever `meetings/diarize.py` eventually produces.
- Leave `sherpa-onnx` out of `pyproject.toml` until there's a working wheel.
- Re-attempt step 3.5 (the actual model-running half, model downloads, wiring into
  `MeetingSession`) once that's resolved -- check for a newer sherpa-onnx release, or consider
  an alternative diarization library if this persists.

## Consequences
- "Others" stays a single bucket for now; the owner's real multi-person meeting (9 participants)
  transcribed correctly otherwise -- this is purely a "who said it" gap, not a transcription one.
- `speakers.assign()` is tested against the exact shape step 3.5 will eventually need (segment
  dicts + (start, end, speaker_index) turns), so wiring it up later should be mechanical once
  diarization itself works.
