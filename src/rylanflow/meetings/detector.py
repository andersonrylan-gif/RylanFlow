"""Probes CoreAudio and on-screen window titles for the "looks like you're in a meeting" signal
that detector_logic.MeetingDetector turns into a start/stop decision. No detection logic lives
here -- see docs/decisions/0003-system-audio.md's appendix for what's verified against a real
Google Meet call (scripts/spike_mic_users.py is the original spike this was productionized from).
"""

import ctypes
import dataclasses
import time

import objc
import Quartz

CA = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")

_SYSTEM_OBJECT = 1
_SCOPE_GLOBAL = "glob"
_SELECTOR_PROCESS_OBJECT_LIST = "prs#"  # kAudioHardwarePropertyProcessObjectList
_SELECTOR_BUNDLE_ID = "pbid"  # kAudioProcessPropertyBundleID
_SELECTOR_IS_RUNNING_INPUT = "piri"  # kAudioProcessPropertyIsRunningInput


@dataclasses.dataclass
class Signals:
    mic_bundle_ids: set[str]
    window_titles: list[tuple[str, str]]  # (owner app name, window title)
    now: float = dataclasses.field(default_factory=time.monotonic)


def probe(clock=time.monotonic) -> Signals:
    return Signals(_mic_bundle_ids(), _window_titles(), clock())


def _fourcc(s: str) -> int:
    return int.from_bytes(s.encode("ascii"), "big")


class _PropertyAddress(ctypes.Structure):
    _fields_ = [
        ("mSelector", ctypes.c_uint32),
        ("mScope", ctypes.c_uint32),
        ("mElement", ctypes.c_uint32),
    ]


def _get_property(object_id: int, selector: str, ctype=ctypes.c_uint32):
    """Returns (status, value). status == 0 (noErr) means value is meaningful."""
    address = _PropertyAddress(_fourcc(selector), _fourcc(_SCOPE_GLOBAL), 0)
    size = ctypes.c_uint32(ctypes.sizeof(ctype))
    value = ctype()
    status = CA.AudioObjectGetPropertyData(
        ctypes.c_uint32(object_id),
        ctypes.byref(address),
        0,
        None,
        ctypes.byref(size),
        ctypes.byref(value),
    )
    return status, value


def _process_object_ids() -> list[int]:
    address = _PropertyAddress(_fourcc(_SELECTOR_PROCESS_OBJECT_LIST), _fourcc(_SCOPE_GLOBAL), 0)
    size = ctypes.c_uint32(0)
    status = CA.AudioObjectGetPropertyDataSize(
        ctypes.c_uint32(_SYSTEM_OBJECT), ctypes.byref(address), 0, None, ctypes.byref(size)
    )
    if status != 0:
        return []
    count = size.value // ctypes.sizeof(ctypes.c_uint32)
    buf = (ctypes.c_uint32 * count)()
    status = CA.AudioObjectGetPropertyData(
        ctypes.c_uint32(_SYSTEM_OBJECT), ctypes.byref(address), 0, None, ctypes.byref(size), buf
    )
    return list(buf) if status == 0 else []


def _mic_bundle_ids() -> set[str]:
    """Bundle IDs of every process CoreAudio currently reports as actively recording input.
    More than one can be true at once (e.g. a noise-cancellation helper alongside the real
    meeting app) -- that's expected, not a bug."""
    ids = set()
    for object_id in _process_object_ids():
        running_status, running_val = _get_property(object_id, _SELECTOR_IS_RUNNING_INPUT)
        if running_status != 0 or not running_val.value:
            continue
        bundle_status, bundle_ptr = _get_property(
            object_id, _SELECTOR_BUNDLE_ID, ctype=ctypes.c_void_p
        )
        if bundle_status != 0 or not bundle_ptr.value:
            continue
        try:
            bundle_id = str(objc.objc_object(c_void_p=bundle_ptr.value))
        except Exception:
            continue
        if bundle_id:
            ids.add(bundle_id)
    return ids


def _window_titles() -> list[tuple[str, str]]:
    """(owner app name, window title) for every window, not just on-screen ones -- a browser tab
    playing a meeting isn't always the frontmost one (verified in the ADR 0003 appendix)."""
    info = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID)
    out = []
    for w in info:
        owner = w.get("kCGWindowOwnerName")
        title = w.get("kCGWindowName")
        if owner and title:
            out.append((str(owner), str(title)))
    return out
