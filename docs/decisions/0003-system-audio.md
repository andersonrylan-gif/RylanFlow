# 0003 — Capture system audio with ScreenCaptureKit

**Status:** Accepted

## Context
Meeting transcription (v0.3) needs to hear the other participants, which means capturing
system audio (whatever Zoom/Meet/etc. is playing), not just the microphone. macOS has no public
"tap into system audio" API before macOS 13; ScreenCaptureKit (`SCStream` configured with
`capturesAudio = true`) is Apple's supported way to do this, even though it's framed as a
screen-capture API. The plan's fallback options were the Core Audio process tap
(macOS 14.4+) or a virtual-device workaround (BlackHole + a Multi-Output device).

## Decision
Use ScreenCaptureKit, consuming only its audio track. Proven end to end with
`scripts/spike_system_audio.py`: captured real system audio (macOS `say` and system sounds),
resampled 48kHz→16kHz, and had `mlx-whisper` transcribe it correctly ("The quick brand Fox
jumps over the lazy dog, testing 1, 2, 3." — the "brand"/"brown" slip is an ordinary Whisper
mishearing, not a capture artifact).

## How it works (verified API shape)
- `SCShareableContent.getShareableContentWithCompletionHandler_(handler)` — async; wait on a
  `threading.Event` set inside the handler. Returns `.displays()`; pick `displays[0]`.
- `SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])`.
- `SCStreamConfiguration`: `setCapturesAudio_(True)`, `setExcludesCurrentProcessAudio_(True)`,
  `setSampleRate_(48000)`, `setChannelCount_(1)`. ScreenCaptureKit is a *screen* capture API, so
  it insists on a video track even when only audio is wanted — keep that track cheap rather than
  fighting to disable it: `setWidth_(2)`, `setHeight_(2)`,
  `setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 1))`.
- `SCStream.alloc().initWithFilter_configuration_delegate_(filter, config, None)`.
- An `NSObject` subclass implementing `stream_didOutputSampleBuffer_ofType_(self, stream, sbuf,
  out_type)`, filtering for `out_type == SCK.SCStreamOutputTypeAudio`. **Must call
  `objc.super(Cls, self).init()`, not plain Python `super()`** — confirmed by hand (plain
  `super()` raises `AttributeError` with an `ObjCSuperWarning`); see also `overlay.py`'s
  `_OverlayView`, which hit the same thing.
- `stream.addStreamOutput_type_sampleHandlerQueue_error_(output, SCK.SCStreamOutputTypeAudio,
  dispatch_queue_create(b"...", None), None)` → returns **a 2-tuple** `(ok, error)`.
- `stream.startCaptureWithCompletionHandler_(handler)` / `stopCaptureWithCompletionHandler_` —
  both async, same `threading.Event` pattern.
- Extracting PCM from a sample buffer:
  `block = CMSampleBufferGetDataBuffer(sample_buffer)`,
  `length = CMBlockBufferGetDataLength(block)`,
  `status, data = CMBlockBufferCopyDataBytes(block, 0, length, None)` — **this is a 2-tuple**
  `(status, data)`, not 3 as an earlier sketch of this assumed (that version crashed hard: an
  uncaught `ValueError: not enough values to unpack` inside a native callback brings down the
  whole process with an Objective-C exception, not a catchable Python one). `data` is raw
  interleaved `float32` bytes → `np.frombuffer(bytes(data), dtype=np.float32)`.
- Resampling 48k→16k: `scipy.signal.resample_poly` (scipy is present transitively; verified
  with `uv run python -c "import scipy.signal"`). Decimate-by-3 would alias; don't use it outside
  a throwaway spike.
- The whole thing runs fine off the main run loop — `stream_didOutputSampleBuffer_` fires on the
  dispatch queue we created, independent of any `NSRunLoop`/`NSApplication.run()` pump. The spike
  still creates an accessory `NSApplication` and uses
  `AppHelper.runConsoleEventLoop()`/`AppHelper.stopEventLoop()` to wait out the capture window,
  since the completion-handler callbacks are async either way and this is the simplest way to
  block "until done" without inventing another synchronization mechanism.

## Permission — surprising result
Capture worked with **no visible permission prompt**, and afterward there was no
`kTCCServiceScreenCapture` row for our Python binary in `TCC.db` at all (checked directly:
`sqlite3 ~/Library/Application\ Support/com.apple.TCC/TCC.db "select * from access"`) — only the
pre-existing `kTCCServiceMicrophone` grant from earlier dictation testing. The working theory:
recent macOS has a narrower **"System Audio Recording Only"** authorization, separate from full
Screen Recording, specifically for apps that configure `capturesAudio = true` without actually
reading the video frames — and either it doesn't gate this particular configuration the way full
Screen Recording does, or it was granted silently. This was only tested on one Mac
(this one), from an already-fairly-permissive dev terminal. **Before shipping 3.3+, re-verify
on a clean permission state** (a fresh user, or after `tccutil reset ScreenCapture
com.rylananderson.rylanflow` against the packaged app) and update this note with what actually
happens — a real prompt, a silent grant, or a hard failure that needs
`NSSystemAudioCaptureUsageDescription` /
an entry in `NSScreenCaptureUsageDescription` in the packaged app's `Info.plist`. Packaging
(step 3.10) should add whichever usage-description key turns out to be required.

## Consequences
- ✅ No virtual audio device, no BlackHole, no asking the user to configure a Multi-Output
  device — the original fallback-of-last-resort is unnecessary.
- ✅ Works entirely through public, documented APIs.
- ⚠️ ScreenCaptureKit's audio-only mode still carries a (cheap, 2×2) video track; this is a
  quirk to carry forward into `meetings/system_audio.py`, not fully eliminate.
- ⚠️ The permission story needs re-verification before the packaged app ships this to anyone
  else, per the note above.
- The 2-tuple-not-3-tuple and `objc.super()` gotchas are exactly the kind of thing that would
  otherwise cost real debugging time in step 3.3; they're now known going in.
