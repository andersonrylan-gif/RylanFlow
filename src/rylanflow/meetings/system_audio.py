"""Continuous system audio capture for a meeting, via ScreenCaptureKit.

Productionizes scripts/spike_system_audio.py -- see docs/decisions/0003-system-audio.md for the
full verified API shape, the gotchas already found (CMBlockBufferCopyDataBytes is a 2-tuple, a
custom NSObject subclass needs objc.super()), and the still-open permission question.

Imports AppKit/ScreenCaptureKit at module load time, like overlay.py and dashboard/window.py do;
callers on a non-macOS or ScreenCaptureKit-less system should import this module lazily.
"""

import logging
import threading
import time
from math import gcd
from pathlib import Path

import AppKit
import CoreMedia
import numpy as np
import objc
import ScreenCaptureKit as SCK
from Foundation import NSObject
from libdispatch import dispatch_queue_create

from rylanflow.recorder import CLOSE_TIMEOUT, SAMPLE_RATE, IncrementalWavWriter

log = logging.getLogger(__name__)

SOURCE_SAMPLE_RATE = 48_000  # what we ask ScreenCaptureKit for; resampled down after
START_TIMEOUT = 10.0


class _Output(NSObject):
    """Implements SCStreamOutput: forwards each audio sample buffer to on_audio."""

    def init(self):
        self = objc.super(_Output, self).init()
        if self is None:
            return None
        self.on_audio = None
        return self

    def stream_didOutputSampleBuffer_ofType_(self, _stream, sample_buffer, out_type):
        if out_type != SCK.SCStreamOutputTypeAudio:
            return
        samples = _extract_audio(sample_buffer)
        if samples is not None and samples.size and self.on_audio is not None:
            self.on_audio(samples)


def _extract_audio(sample_buffer) -> np.ndarray | None:
    block = CoreMedia.CMSampleBufferGetDataBuffer(sample_buffer)
    if not block:
        return None
    length = CoreMedia.CMBlockBufferGetDataLength(block)
    status, data = CoreMedia.CMBlockBufferCopyDataBytes(block, 0, length, None)
    if status != 0 or not data:
        return None
    return np.frombuffer(bytes(data), dtype=np.float32)


def _get_shareable_content():
    result: dict = {}
    done = threading.Event()

    def handler(content, error):
        result["content"] = content
        result["error"] = error
        done.set()

    SCK.SCShareableContent.getShareableContentWithCompletionHandler_(handler)
    if not done.wait(START_TIMEOUT):
        raise TimeoutError("getShareableContentWithCompletionHandler_ never called back")
    if result["error"] is not None:
        raise RuntimeError(f"SCShareableContent error: {result['error']}")
    return result["content"]


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    from scipy.signal import resample_poly

    divisor = gcd(source_rate, target_rate)
    return resample_poly(audio, target_rate // divisor, source_rate // divisor).astype(np.float32)


class SystemAudioTrack:
    """Captures whatever the Mac is playing -- in a meeting, the other participants."""

    def __init__(
        self, wav_path: str | Path, sample_rate: int = SAMPLE_RATE, clock=time.monotonic
    ) -> None:
        self.sample_rate = sample_rate
        self._clock = clock
        self._chunks: list[np.ndarray] = []
        self._lock = threading.Lock()
        self._writer = IncrementalWavWriter(wav_path, sample_rate)
        self._stream = None
        self._output = None
        self._closing: list[tuple[threading.Thread, float]] = []

    @property
    def stuck(self) -> bool:
        now = self._clock()
        self._closing = [(t, since) for t, since in self._closing if t.is_alive()]
        return any(now - since > CLOSE_TIMEOUT for _, since in self._closing)

    def start(self) -> None:
        if self._stream is not None:
            return
        content = _get_shareable_content()
        displays = content.displays()
        if not displays:
            raise RuntimeError("no displays available for system audio capture")

        content_filter = SCK.SCContentFilter.alloc().initWithDisplay_excludingWindows_(
            displays[0], []
        )
        config = SCK.SCStreamConfiguration.alloc().init()
        config.setCapturesAudio_(True)
        config.setExcludesCurrentProcessAudio_(True)
        config.setSampleRate_(SOURCE_SAMPLE_RATE)
        config.setChannelCount_(1)
        # ScreenCaptureKit is a *screen* capture API and insists on a video track even when only
        # audio is wanted; keep it as cheap as possible rather than fighting to disable it.
        config.setWidth_(2)
        config.setHeight_(2)
        config.setMinimumFrameInterval_(CoreMedia.CMTimeMake(1, 1))

        output = _Output.alloc().init()
        output.on_audio = self._on_audio
        queue = dispatch_queue_create(b"rylanflow.meeting.system-audio", None)

        stream = SCK.SCStream.alloc().initWithFilter_configuration_delegate_(
            content_filter, config, None
        )
        ok, error = stream.addStreamOutput_type_sampleHandlerQueue_error_(
            output, SCK.SCStreamOutputTypeAudio, queue, None
        )
        if not ok:
            raise RuntimeError(f"addStreamOutput failed: {error}")

        done = threading.Event()
        start_result: dict = {}

        def start_handler(error):
            start_result["error"] = error
            done.set()

        stream.startCaptureWithCompletionHandler_(start_handler)
        if not done.wait(START_TIMEOUT):
            raise TimeoutError("system audio capture did not start in time")
        if start_result["error"] is not None:
            raise RuntimeError(f"startCapture error: {start_result['error']}")

        self._stream = stream
        self._output = output

    def drain(self) -> np.ndarray:
        """Returns and clears whatever's been captured (and already resampled) since the last
        drain()."""
        with self._lock:
            chunks, self._chunks = self._chunks, []
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)

    def close(self) -> None:
        """Non-blocking, like Recorder.stop() / MicTrack.close()."""
        stream, self._stream = self._stream, None
        if stream is None:
            self._writer.close()
            return
        thread = threading.Thread(
            target=self._finish_close, args=(stream,), name="system-audio-close", daemon=True
        )
        thread.start()
        self._closing.append((thread, self._clock()))

    def _finish_close(self, stream) -> None:
        done = threading.Event()

        def stop_handler(error):
            if error is not None:
                log.warning("stopCapture error: %s", error)
            done.set()

        try:
            stream.stopCaptureWithCompletionHandler_(stop_handler)
            done.wait(START_TIMEOUT)
        except Exception:
            log.exception("stopping system audio capture failed")
        finally:
            self._writer.close()

    def _on_audio(self, raw_48k: np.ndarray) -> None:
        try:
            resampled = _resample(raw_48k, SOURCE_SAMPLE_RATE, self.sample_rate)
        except Exception:
            log.exception("resampling system audio failed")
            return
        with self._lock:
            self._chunks.append(resampled)
        try:
            self._writer.append(resampled)
        except Exception:
            log.exception("writing system audio to disk failed")


# AppKit's shared NSApplication must exist before ScreenCaptureKit's delegate/completion-handler
# machinery works reliably; harmless to call repeatedly, and callers (the meeting session) don't
# need to know this is required.
AppKit.NSApplication.sharedApplication()
