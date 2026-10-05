"""Runs one meeting: owns the mic + system audio tracks, pumps their audio through a Chunker
each every PUMP_INTERVAL, submits the resulting windows to the shared TranscriptionService, and
stores the results as they come back -- so the dashboard can show a transcript building up live.

Threading: start() and stop() are both non-blocking (like Recorder.stop()/track.close()
elsewhere in this codebase) -- opening the audio hardware and waiting for queued transcriptions
to finish can both take a few seconds, which would freeze the UI if done on the caller's thread.
Both do their real work on a background thread instead.

Each meeting's mutable state (tracks, chunkers, events, ...) lives in a fresh _Active instance,
passed explicitly through every background method -- never read back off `self` from inside a
background thread. Back-to-back meetings (stop() immediately followed by start(), which a busy
day of consecutive calls does routinely) would otherwise let meeting A's still-running cleanup
thread read `self._mic` etc. after start() had already overwritten them for meeting B, silently
closing B's hardware out from under it or misattributing B's audio to A's transcript.
"""

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from rylanflow.meetings import speakers
from rylanflow.meetings.calendar import CalendarLookup
from rylanflow.meetings.chunker import Chunker
from rylanflow.meetings.diarize import Diarizer
from rylanflow.meetings.mic_track import MicTrack
from rylanflow.meetings.system_audio import SystemAudioTrack
from rylanflow.store import Store, data_dir
from rylanflow.transcriber import MLXWhisperTranscriber
from rylanflow.transcription_service import MEETING_PRIORITY, TranscriptionService

log = logging.getLogger(__name__)

PUMP_INTERVAL = 1.0  # seconds between draining the tracks into the chunkers
STOP_DRAIN_TIMEOUT = 20.0  # how long to wait for the last queued chunks before giving up
_TAG = "meeting"
_MIC_TRACK = "mic"
_SYSTEM_TRACK = "system"


@dataclass
class _Active:
    """Everything specific to one in-progress meeting. See the module docstring for why this
    isn't just more `self.` attributes."""

    mic: MicTrack
    system: SystemAudioTrack
    you_id: int
    others_id: int
    on_error: Callable[[str], None] | None
    mic_chunker: Chunker = field(default_factory=Chunker)
    system_chunker: Chunker = field(default_factory=Chunker)
    stop_event: threading.Event = field(default_factory=threading.Event)
    started_event: threading.Event = field(default_factory=threading.Event)
    pump_thread: threading.Thread | None = None


class MeetingSession:
    def __init__(
        self,
        store: Store,
        service: TranscriptionService,
        transcriber: MLXWhisperTranscriber,
        my_name: str,
        mic_track_factory: Callable[[object], MicTrack] = MicTrack,
        system_track_factory: Callable[[object], SystemAudioTrack] = SystemAudioTrack,
        clock=time.monotonic,
        calendar_lookup: CalendarLookup | None = None,
        diarizer: Diarizer | None = None,
    ) -> None:
        """Factories let tests substitute fake tracks; in production they default to the real
        MicTrack/SystemAudioTrack, each constructed fresh per meeting with that meeting's own
        WAV path (data_dir()/meetings/<id>/{mic,system}.wav). `calendar_lookup` and `diarizer`
        are both optional -- None (the default, and what tests use) skips the calendar-title
        lookup / the post-meeting speaker-splitting step entirely."""
        self._store = store
        self._service = service
        self._transcriber = transcriber
        self._my_name = my_name
        self._mic_track_factory = mic_track_factory
        self._system_track_factory = system_track_factory
        self._clock = clock
        self._calendar_lookup = calendar_lookup
        self._diarizer = diarizer

        self._meeting_id: int | None = None
        self._active: _Active | None = None

    @property
    def active(self) -> bool:
        return self._meeting_id is not None

    @property
    def meeting_id(self) -> int | None:
        return self._meeting_id

    def start(
        self,
        title: str | None = None,
        source_app: str | None = None,
        on_error: Callable[[str], None] | None = None,
    ) -> int:
        """Creates the meeting (and its "You"/"Others" speakers) immediately and returns its id;
        actually opening the audio hardware happens on a background thread right after, since
        that can take a few seconds."""
        if self.active:
            return self._meeting_id
        meeting_id = self._store.create_meeting(title=title, source_app=source_app)
        you_id = self._store.add_speaker(meeting_id, "You", display_name=self._my_name, is_me=True)
        others_id = self._store.add_speaker(meeting_id, "Others")

        meeting_dir = data_dir() / "meetings" / str(meeting_id)
        active = _Active(
            mic=self._mic_track_factory(meeting_dir / "mic.wav"),
            system=self._system_track_factory(meeting_dir / "system.wav"),
            you_id=you_id,
            others_id=others_id,
            on_error=on_error,
        )
        self._meeting_id = meeting_id
        self._active = active

        threading.Thread(
            target=self._start_tracks_and_pump,
            args=(meeting_id, active),
            name="meeting-start",
            daemon=True,
        ).start()
        return meeting_id

    def stop(self) -> int | None:
        """Signals the meeting to stop and returns its id at once. Closing the tracks, flushing
        the last audio, waiting for queued transcriptions and marking the meeting done all
        happen on a background thread."""
        if not self.active:
            return None
        meeting_id = self._meeting_id
        active = self._active
        self._meeting_id = None
        self._active = None
        active.stop_event.set()
        threading.Thread(
            target=self._finish_stop, args=(meeting_id, active), name="meeting-stop", daemon=True
        ).start()
        return meeting_id

    # --- background: starting ---

    def _start_tracks_and_pump(self, meeting_id: int, active: _Active) -> None:
        try:
            active.mic.start()
            active.system.start()
        except Exception:
            log.exception("could not start meeting capture")
            self._store.finish_meeting(meeting_id, "failed")
            if self._meeting_id == meeting_id:
                self._meeting_id = None
                self._active = None
            self._error(active, "Couldn't start capturing the meeting. See the log for details.")
            active.started_event.set()
            return
        active.pump_thread = threading.Thread(
            target=self._pump_loop, args=(meeting_id, active), name="meeting-pump", daemon=True
        )
        active.pump_thread.start()
        active.started_event.set()
        self._apply_calendar_info(meeting_id)

    def _apply_calendar_info(self, meeting_id: int) -> None:
        """Best-effort: suggest a title and attendee list from the calendar event covering
        right now, if any. Runs after _started_event is set, so it never delays stop() --
        a late title update (or none, on failure) is fine either way."""
        if self._calendar_lookup is None:
            return
        try:
            event = self._calendar_lookup.current_event(datetime.now().astimezone())
        except Exception:
            log.exception("calendar lookup failed")
            return
        if event is None:
            return
        if event.title:
            self._store.set_meeting_title(meeting_id, event.title)
        if event.attendees:
            self._store.set_meeting_attendees(meeting_id, event.attendees)

    def _pump_loop(self, meeting_id: int, active: _Active) -> None:
        while not active.stop_event.wait(PUMP_INTERVAL):
            self._process_both(meeting_id, active)

    # --- background: stopping ---

    def _finish_stop(self, meeting_id: int, active: _Active) -> None:
        # Don't race ahead of _start_tracks_and_pump: wait for it to finish setting up (success
        # or failure) before touching active.pump_thread or the tracks at all. Safe to wait
        # unbounded -- mic.start()/system.start() each have their own internal timeouts, so
        # started_event is always eventually set.
        active.started_event.wait()
        if active.pump_thread is not None:
            active.pump_thread.join(timeout=PUMP_INTERVAL * 3)
        active.mic.close()
        active.system.close()
        # One last drain so nothing captured since the pump's last tick is lost, then flush
        # each chunker's remainder (however short) as a final window.
        self._process_both(meeting_id, active, final=True)
        if not self._wait_for_pending(STOP_DRAIN_TIMEOUT):
            log.warning(
                "meeting %s: still-pending transcriptions after the stop timeout", meeting_id
            )
        self._run_diarization(meeting_id)
        self._store.finish_meeting(meeting_id, "done")

    def _run_diarization(self, meeting_id: int) -> None:
        """Best-effort: splits "Others" into Speaker 1..N by running the diarizer over
        system.wav. Any failure (no diarizer configured, model download failed, bad audio, no
        system-track segments at all, etc.) just leaves "Others" as-is -- the transcript itself
        is never at risk, and the caller always moves on to mark the meeting "done" after this."""
        if self._diarizer is None:
            return
        meeting = self._store.get_meeting(meeting_id)
        if meeting is None:
            return
        system_segments = [s for s in meeting["segments"] if s["track"] == _SYSTEM_TRACK]
        if not system_segments:
            return
        self._store.finish_meeting(meeting_id, "processing")
        try:
            meeting_dir = data_dir() / "meetings" / str(meeting_id)
            turns = self._diarizer.diarize(meeting_dir / "system.wav")
            turn_by_segment = speakers.assign(system_segments, turns)
        except Exception:
            log.exception("meeting %s: diarization failed", meeting_id)
            return
        if not turn_by_segment:
            return

        speaker_ids: dict[int, int] = {}
        reassignments: dict[int, int] = {}
        for seg in system_segments:
            turn_index = turn_by_segment.get(seg["id"])
            if turn_index is None:
                continue
            if turn_index not in speaker_ids:
                # Numbered by order of first appearance, not the raw cluster index -- sherpa-
                # onnx's cluster ids aren't guaranteed sequential/compact (e.g. 0, 1, 5, 13 for
                # 4 speakers), which would otherwise show up as "Speaker 1", "Speaker 14", ....
                speaker_number = len(speaker_ids) + 1
                speaker_ids[turn_index] = self._store.add_speaker(
                    meeting_id, f"Speaker {speaker_number}"
                )
            reassignments[seg["id"]] = speaker_ids[turn_index]
        self._store.reassign_segments(reassignments)

        others = next((s for s in meeting["speakers"] if s["label"] == "Others"), None)
        if others is not None:
            self._store.delete_speaker(others["id"])

    def _wait_for_pending(self, timeout: float) -> bool:
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            if self._service.pending_count(_TAG) == 0:
                return True
            time.sleep(0.1)
        return self._service.pending_count(_TAG) == 0

    # --- shared by the pump and the final stop-time drain ---

    def _process_both(self, meeting_id: int, active: _Active, final: bool = False) -> None:
        # System audio is processed first, deliberately: since the shared TranscriptionService
        # has one worker thread, submitting system's chunk before mic's means system's result
        # (and thus its stored segment) is already available by the time the mic chunk's own
        # job -- and its echo check against system_segments -- runs, rather than racing it.
        self._process_track(
            meeting_id,
            _SYSTEM_TRACK,
            active.system,
            active.system_chunker,
            active.others_id,
            final,
        )
        self._process_track(
            meeting_id, _MIC_TRACK, active.mic, active.mic_chunker, active.you_id, final
        )

    def _process_track(
        self,
        meeting_id: int,
        track_name: str,
        track,
        chunker: Chunker,
        speaker_id: int,
        final=False,
    ) -> None:
        audio = track.drain()
        windows = chunker.push(audio)
        if final:
            windows = windows + chunker.flush()
        for offset, chunk in windows:
            self._submit_chunk(meeting_id, track_name, chunk, offset, speaker_id)

    def _submit_chunk(
        self, meeting_id: int, track_name: str, audio, offset: float, speaker_id: int
    ) -> None:
        if track_name == _MIC_TRACK and speakers.is_silent(audio):
            return

        def on_result(segments) -> None:
            self._handle_segments(meeting_id, track_name, segments, offset, speaker_id)

        def on_error(message: str) -> None:
            log.error("meeting chunk (%s) transcription failed: %s", track_name, message)

        self._service.submit(
            lambda a: self._transcriber.transcribe_segments(a, meeting=True),
            audio,
            MEETING_PRIORITY,
            on_result,
            on_error,
            tag=_TAG,
        )

    def _handle_segments(
        self, meeting_id: int, track_name: str, segments, offset: float, speaker_id: int
    ) -> None:
        if not segments:
            return
        if track_name == _MIC_TRACK:
            meeting = self._store.get_meeting(meeting_id)
            system_segments = (
                [
                    (s["start_s"], s["end_s"], s["text"])
                    for s in meeting["segments"]
                    if s["track"] == _SYSTEM_TRACK
                ]
                if meeting
                else []
            )
            segments = [
                s
                for s in segments
                if not speakers.is_echo(s.text, offset + s.start, offset + s.end, system_segments)
            ]
        rows = [
            {
                "speaker_id": speaker_id,
                "track": track_name,
                "start_s": offset + s.start,
                "end_s": offset + s.end,
                "text": s.text,
            }
            for s in segments
        ]
        if rows:
            self._store.add_segments(meeting_id, rows)

    def _error(self, active: _Active, message: str) -> None:
        if active.on_error:
            active.on_error(message)
