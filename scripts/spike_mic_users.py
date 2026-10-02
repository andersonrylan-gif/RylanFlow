"""Spike (docs/BUILD_PLAN.md step 3.1): figure out which app is using the microphone, and what
its on-screen windows are called, as the signal for "the user is probably in a meeting".

Usage: uv run python scripts/spike_mic_users.py [--watch]

Without --watch, prints a one-shot snapshot. With --watch, re-prints every 2s (Ctrl+C to stop)
-- join a test call (e.g. https://meet.new) in another tab/app while this is running and watch
for its bundle ID to show "running_input=1".
"""

import ctypes
import sys
import time

import objc
import Quartz

CA = ctypes.CDLL("/System/Library/Frameworks/CoreAudio.framework/CoreAudio")

K_AUDIO_OBJECT_SYSTEM_OBJECT = 1
SCOPE_GLOBAL = "glob"
SELECTOR_PROCESS_OBJECT_LIST = "prs#"  # kAudioHardwarePropertyProcessObjectList
SELECTOR_PID = "ppid"  # kAudioProcessPropertyPID
SELECTOR_BUNDLE_ID = "pbid"  # kAudioProcessPropertyBundleID
SELECTOR_IS_RUNNING_INPUT = "piri"  # kAudioProcessPropertyIsRunningInput


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
    address = _PropertyAddress(_fourcc(selector), _fourcc(SCOPE_GLOBAL), 0)
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


def list_process_object_ids() -> list[int]:
    address = _PropertyAddress(_fourcc(SELECTOR_PROCESS_OBJECT_LIST), _fourcc(SCOPE_GLOBAL), 0)
    size = ctypes.c_uint32(0)
    status = CA.AudioObjectGetPropertyDataSize(
        ctypes.c_uint32(K_AUDIO_OBJECT_SYSTEM_OBJECT),
        ctypes.byref(address),
        0,
        None,
        ctypes.byref(size),
    )
    if status != 0:
        raise RuntimeError(f"AudioObjectGetPropertyDataSize failed: {status}")
    count = size.value // ctypes.sizeof(ctypes.c_uint32)
    buf = (ctypes.c_uint32 * count)()
    status = CA.AudioObjectGetPropertyData(
        ctypes.c_uint32(K_AUDIO_OBJECT_SYSTEM_OBJECT),
        ctypes.byref(address),
        0,
        None,
        ctypes.byref(size),
        buf,
    )
    if status != 0:
        raise RuntimeError(f"AudioObjectGetPropertyData failed: {status}")
    return list(buf)


class AudioProcess:
    def __init__(self, object_id: int, pid: int, bundle_id: str | None, running_input: bool):
        self.object_id = object_id
        self.pid = pid
        self.bundle_id = bundle_id
        self.running_input = running_input

    def __repr__(self) -> str:
        flag = "MIC-ON " if self.running_input else "       "
        return f"{flag}pid={self.pid:<7} bundle={self.bundle_id or '(none)'}"


def list_audio_processes() -> list[AudioProcess]:
    processes = []
    for object_id in list_process_object_ids():
        pid_status, pid_val = _get_property(object_id, SELECTOR_PID, ctype=ctypes.c_int32)
        if pid_status != 0 or pid_val.value <= 0:
            continue
        running_status, running_val = _get_property(object_id, SELECTOR_IS_RUNNING_INPUT)
        running_input = running_status == 0 and bool(running_val.value)

        bundle_status, bundle_ptr = _get_property(
            object_id, SELECTOR_BUNDLE_ID, ctype=ctypes.c_void_p
        )
        bundle_id = None
        if bundle_status == 0 and bundle_ptr.value:
            try:
                bundle_id = str(objc.objc_object(c_void_p=bundle_ptr.value)) or None
            except Exception:
                bundle_id = None

        processes.append(AudioProcess(object_id, pid_val.value, bundle_id, running_input))
    return processes


def list_window_titles() -> list[tuple[str, str]]:
    """(owner app name, window title) for every on-screen window with a title."""
    info = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID
    )
    out = []
    for w in info:
        owner = w.get("kCGWindowOwnerName")
        title = w.get("kCGWindowName")
        if owner and title:
            out.append((str(owner), str(title)))
    return out


def snapshot() -> None:
    processes = list_audio_processes()
    using_mic = [p for p in processes if p.running_input]
    print(f"{len(processes)} audio processes, {len(using_mic)} currently using the mic:")
    for p in using_mic or processes[:5]:
        print(" ", p)
    if not using_mic:
        print("  (none active right now -- showing the first 5 of all processes instead)")

    print("Window titles (owner -> title), for apps also in the mic-using set:")
    mic_owners_pids = {p.pid for p in using_mic}
    for owner, title in list_window_titles():
        print(f"  {owner!r} -> {title!r}")
    if not using_mic:
        print("  (skipped matching against mic users since nothing is using the mic)")
    _ = mic_owners_pids  # matching by name vs pid is a step-3.7 problem; this spike just lists both


def main() -> None:
    watch = "--watch" in sys.argv
    if not watch:
        snapshot()
        return
    try:
        while True:
            print("=" * 60, time.strftime("%H:%M:%S"))
            snapshot()
            time.sleep(2)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
