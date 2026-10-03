# 0004 — Speaker diarization (step 3.5): a broken sherpa-onnx wheel, and the fix

**Status:** Accepted

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
small to contain a full ONNX Runtime. This reproduces identically across both versions tried, so
it looks like a genuine upstream packaging bug for this platform/Python combination, not a local
environment issue.

The first pass at this ADR stopped here and shipped only the diarization-independent half
(`meetings/speakers.assign`), deliberately avoiding a "fragile, invisible to uv.lock" manual
dylib fix. Revisited directly at the owner's request.

## The fix
The separate `onnxruntime` PyPI package (not `sherpa-onnx`) *does* properly bundle its own
`libonnxruntime.<version>.dylib`, correctly built for the install platform
(`onnxruntime/capi/libonnxruntime.1.30.0.dylib` on this machine, confirmed arm64 via `file`,
matching the arm64 `_sherpa_onnx...so`). Copying that file to where sherpa-onnx's extension
looks for it (`sherpa_onnx/lib/libonnxruntime.dylib`) makes `import sherpa_onnx` work
immediately, with no further issues -- `scripts/fix_sherpa_onnx_dylib.py` does exactly this, is
idempotent (a no-op if the import already works), and is now a required step: CI runs it right
after `uv sync`, and `packaging/build.sh` runs it before PyInstaller's analysis (which needs
`sherpa_onnx` to actually import so it can discover and bundle the now-present dylib via
`--collect-all sherpa_onnx`). This keeps the fix scripted, reproducible, and visible in the repo
-- the earlier objection (that a manual fix would be invisible to `uv.lock` and need repeating
in three places) is addressed by making all three places run the one script, not by baking a
binary into source control.

## Real-world verification
Tested directly against the owner's own 9-person meeting recording (`system.wav` from an actual
Google Meet captured by this app), not synthetic audio:
- `sherpa-onnx-pyannote-segmentation-3-0` (segmentation) and `nemo_en_titanet_small.onnx`
  (speaker embedding), both from the k2-fsa/sherpa-onnx GitHub releases, downloaded and
  SHA-256-verified (hashes pinned in `meetings/diarize.py`).
- At the library's own default clustering threshold (0.5), the real 9 speakers were
  over-segmented into 12 clusters. Threshold 0.6 matched the true count (9) exactly on this
  recording; 0.7 under-segmented to 8. **0.6 is now the default** (`DEFAULT_CLUSTERING_THRESHOLD`
  in `diarize.py`), chosen from this one real data point -- worth revisiting if future real
  meetings show it's systematically off in either direction.
- Ran the full production path end to end (`MeetingSession._run_diarization` against a clone of
  the real stored meeting + its real `system.wav`, not a synthetic fixture): "Others" correctly
  split into 7 named speakers (the 9-cluster run included the owner's own mic-side "You" track
  separately; 7 of the 9 clusters had enough system-track text to produce segments), each
  speaker's lines grouped sensibly by who was actually talking, in under 13 seconds for a
  170-second recording.
- Found and fixed a real bug this surfaced: speaker labels were originally numbered by the raw
  (non-contiguous) cluster index -- e.g. "Speaker 1", "Speaker 14", "Speaker 9" for 7 actual
  speakers, since FastClustering's cluster ids aren't guaranteed sequential. Fixed to number by
  order of first appearance instead, giving a clean "Speaker 1".."Speaker 7".

## Consequences
- ✅ Diarization is live: `MeetingSession` takes an optional `diarizer` (a `meetings.diarize
  .Diarizer`); after a meeting's transcript is fully drained, system-track segments are
  reassigned from "Others" to "Speaker 1..N" via `speakers.assign()` (already-tested pure logic
  from the earlier pass), and "Others" is deleted once nothing references it any more.
- ✅ Models download once (SHA-256 verified) to `data_dir()/models/`, not bundled in the app
  (~46 MB combined) -- consistent with the "app size" risk noted in BUILD_PLAN.md section 8.
- ✅ Never risks the transcript: a diarization failure (denied access, model download failure,
  bad audio, sherpa_onnx still broken for some reason) is caught, logged, and leaves "Others" as
  it was -- the meeting still reaches `status: "done"` either way.
- ⚠️ Diarization quality will still vary on real compressed meeting audio from other sources
  (different mic setups, more background noise, more participants); 0.6 is a starting point
  tuned on one real recording, not a guarantee. Renaming in the dashboard remains the safety net
  the original plan called for (still not built -- see BUILD_PLAN.md step 3.6's remaining
  scope).
- The `_sha256`/`_download`/cache-path logic in `diarize.py` is deliberately generic (not
  sherpa-onnx-specific), so it would carry over cleanly to a different diarization library if
  this wheel issue ever regresses on a future sherpa-onnx release.
