"""Spike (docs/BUILD_PLAN.md step 3.0): prove ScreenCaptureKit audio capture works from PyObjC.

Captures ~10s of system audio (play something while this runs) to system.wav at 16kHz mono.

Usage: uv run python scripts/spike_system_audio.py [seconds] [output.wav]

The first run triggers the Screen & System Audio Recording permission prompt for whichever app
launched this (Terminal/VS Code); grant it and restart that app, then run again.
"""

import sys
import threading

import AppKit
import CoreMedia
import numpy as np
import objc
import ScreenCaptureKit as SCK
from Foundation import NSObject
from libdispatch import dispatch_queue_create
from PyObjCTools import AppHelper

sys.path.insert(0, "src")
from rylanflow.recorder import write_wav  # noqa: E402

SOURCE_RATE = 48_000
TARGET_RATE = 16_000


class _Output(NSObject):
    """Implements SCStreamOutput: receives sample buffers on our dispatch queue."""

    def init(self):
        self = objc.super(_Output, self).init()
        if self is None:
            return None
        self.chunks: list[np.ndarray] = []
        self.buffer_count = 0
        return self

    def stream_didOutputSampleBuffer_ofType_(self, _stream, sample_buffer, out_type):
        if out_type != SCK.SCStreamOutputTypeAudio:
            return
        self.buffer_count += 1
        block = CoreMedia.CMSampleBufferGetDataBuffer(sample_buffer)
        if not block:
            return
        length = CoreMedia.CMBlockBufferGetDataLength(block)
        status, data = CoreMedia.CMBlockBufferCopyDataBytes(block, 0, length, None)
        if status != 0 or not data:
            return
        # Float32 mono (we asked for channelCount=1): one sample per 4 bytes.
        samples = np.frombuffer(bytes(data), dtype=np.float32)
        self.chunks.append(samples.copy())


def get_shareable_content():
    result = {}
    done = threading.Event()

    def handler(content, error):
        result["content"] = content
        result["error"] = error
        done.set()

    SCK.SCShareableContent.getShareableContentWithCompletionHandler_(handler)
    if not done.wait(10):
        raise TimeoutError("getShareableContentWithCompletionHandler_ never called back")
    if result["error"] is not None:
        raise RuntimeError(f"SCShareableContent error: {result['error']}")
    return result["content"]


def resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    try:
        from math import gcd

        from scipy.signal import resample_poly

        g = gcd(source_rate, target_rate)
        return resample_poly(audio, target_rate // g, source_rate // g).astype(np.float32)
    except ImportError:
        # Crude fallback: no anti-alias filter, just decimate. Fine for a spike, not for prod.
        step = source_rate // target_rate
        return audio[::step].astype(np.float32)


def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 10.0
    out_path = sys.argv[2] if len(sys.argv) > 2 else "system.wav"

    app = AppKit.NSApplication.sharedApplication()
    app.setActivationPolicy_(AppKit.NSApplicationActivationPolicyAccessory)

    print("Fetching shareable content (this is what triggers the permission prompt)...")
    content = get_shareable_content()
    displays = content.displays()
    if not displays:
        raise RuntimeError("no displays found")
    display = displays[0]
    print(f"Using display: {display}")

    content_filter = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(display, [])

    config = SCK.SCStreamConfiguration.alloc().init()
    config.setCapturesAudio_(True)
    config.setExcludesCurrentProcessAudio_(True)
    config.setSampleRate_(SOURCE_RATE)
    config.setChannelCount_(1)
    # We don't want the video frames, so make them as cheap as possible rather than trying
    # (and likely failing) to disable them outright.
    config.setWidth_(2)
    config.setHeight_(2)
    config.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 1))

    output = _Output.alloc().init()
    queue = dispatch_queue_create(b"rylanflow.spike.audio", None)

    stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(
        content_filter, config, None
    )
    ok, error = stream.addStreamOutput_type_sampleHandlerQueue_error_(
        output, SCK.SCStreamOutputTypeAudio, queue, None
    )
    print(f"addStreamOutput result: ok={ok} error={error}")
    if not ok:
        raise RuntimeError(f"addStreamOutput failed: {error}")

    start_result = {}
    start_done = threading.Event()

    def start_handler(error):
        start_result["error"] = error
        start_done.set()

    stream.startCaptureWithCompletionHandler_(start_handler)
    if not start_done.wait(10):
        raise TimeoutError("startCaptureWithCompletionHandler_ never called back")
    if start_result["error"] is not None:
        raise RuntimeError(f"startCapture error: {start_result['error']}")

    print(f"Capturing for {seconds:g}s -- play some audio now...")

    def finish(_timer):
        stop_done = threading.Event()

        def stop_handler(error):
            if error is not None:
                print(f"stopCapture error: {error}")
            stop_done.set()

        stream.stopCaptureWithCompletionHandler_(stop_handler)
        stop_done.wait(10)
        AppHelper.stopEventLoop()

    AppKit.NSTimer.scheduledTimerWithTimeInterval_repeats_block_(seconds, False, finish)
    AppHelper.runConsoleEventLoop(installInterrupt=True)

    print(f"Received {output.buffer_count} audio buffers, {len(output.chunks)} chunks kept.")
    if not output.chunks:
        raise RuntimeError("no audio chunks captured -- see docs/decisions/0003-system-audio.md")

    audio_48k = np.concatenate(output.chunks)
    print(f"Captured {audio_48k.size / SOURCE_RATE:.2f}s at {SOURCE_RATE} Hz, resampling...")
    audio_16k = resample(audio_48k, SOURCE_RATE, TARGET_RATE)
    write_wav(out_path, np.clip(audio_16k, -1.0, 1.0))
    peak = float(np.abs(audio_16k).max()) if audio_16k.size else 0.0
    duration = audio_16k.size / TARGET_RATE
    print(f"Wrote {out_path}: {duration:.2f}s at {TARGET_RATE} Hz, peak={peak:.3f}")


if __name__ == "__main__":
    main()
