"""Runs one meeting: owns the mic + system audio tracks, pumps their audio through a Chunker
each every PUMP_INTERVAL, submits the resulting windows to the shared TranscriptionService, and
stores the results as they come back -- so the dashboard can show a transcript building up live.

Threading: start() and stop() are both non-blocking (like Recorder.stop()/track.close()
elsewhere in this codebase) -- opening the audio hardware and waiting for queued transcriptions
to finish can both take a few seconds, which would freeze the UI if done on the caller's thread.
Both do their real work on a background thread instead.
"""

import logging
import threading
import time
from collections.abc import Callable

from rylanflow.meetings import speakers
from rylanflow.meetings.chunker import Chunker
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
    ) -> None:
        """Factories let tests substitute fake tracks; in production they default to the real
        MicTrack/SystemAudioTrack, each constructed fresh per meeting with that meeting's own
        WAV path (data_dir()/meetings/<id>/{mic,system}.wav)."""
        self._store = store
        self._service = service
        self._transcriber = transcriber
        self._my_name = my_name
        self._mic_track_factory = mic_track_factory
        self._system_track_factory = system_track_factory
        self._clock = clock

        self._meeting_id: int | None = None
        self._you_id: int | None = None
        self._others_id: int | None = None
        self._mic = None
        self._system = None
        self._mic_chunker = Chunker()
        self._system_chunker = Chunker()
        self._stop_event = threading.Event()
        self._pump_thread: threading.Thread | None = None
        self._started_event = threading.Event()  # set once startup finishes, success or not
        self._on_error: Callable[[str], None] | None = None

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
        self._on_error = on_error
        self._meeting_id = self._store.create_meeting(title=title, source_app=source_app)
        self._you_id = self._store.add_speaker(
            self._meeting_id, "You", display_name=self._my_name, is_me=True
        )
        self._others_id = self._store.add_speaker(self._meeting_id, "Others")

        meeting_dir = data_dir() / "meetings" / str(self._meeting_id)
        self._mic = self._mic_track_factory(meeting_dir / "mic.wav")
        self._system = self._system_track_factory(meeting_dir / "system.wav")
        self._mic_chunker = Chunker()
        self._system_chunker = Chunker()
        self._pump_thread = None
        self._stop_event.clear()
        self._started_event.clear()

        threading.Thread(
            target=self._start_tracks_and_pump, name="meeting-start", daemon=True
        ).start()
        return self._meeting_id

    def stop(self) -> int | None:
        """Signals the meeting to stop and returns its id at once. Closing the tracks, flushing
        the last audio, waiting for queued transcriptions and marking the meeting done all
        happen on a background thread."""
        if not self.active:
            return None
        meeting_id = self._meeting_id
        self._meeting_id = None
        self._stop_event.set()
        threading.Thread(
            target=self._finish_stop, args=(meeting_id,), name="meeting-stop", daemon=True
        ).start()
        return meeting_id

    # --- background: starting ---

    def _start_tracks_and_pump(self) -> None:
        try:
            self._mic.start()
            self._system.start()
        except Exception:
            log.exception("could not start meeting capture")
            self._store.finish_meeting(self._meeting_id, "failed")
            self._meeting_id = None
            self._error("Couldn't start capturing the meeting. See the log for details.")
            self._started_event.set()
            return
        self._pump_thread = threading.Thread(
            target=self._pump_loop, name="meeting-pump", daemon=True
        )
        self._pump_thread.start()
        self._started_event.set()

    def _pump_loop(self) -> None:
        # Captured once: stop() clears self._meeting_id (for the `active` property) separately
        # from this loop's own lifecycle, and every chunk this loop submits belongs to this id.
        meeting_id = self._meeting_id
        while not self._stop_event.wait(PUMP_INTERVAL):
            self._process_both(meeting_id)

    # --- background: stopping ---

    def _finish_stop(self, meeting_id: int) -> None:
        # Don't race ahead of _start_tracks_and_pump: wait for it to finish setting up (success
        # or failure) before touching self._pump_thread or the tracks at all. Safe to wait
        # unbounded -- mic.start()/system.start() each have their own internal timeouts, so
        # _started_event is always eventually set.
        self._started_event.wait()
        if self._pump_thread is not None:
            self._pump_thread.join(timeout=PUMP_INTERVAL * 3)
        self._mic.close()
        self._system.close()
        # One last drain so nothing captured since the pump's last tick is lost, then flush
        # each chunker's remainder (however short) as a final window.
        self._process_both(meeting_id, final=True)
        if not self._wait_for_pending(STOP_DRAIN_TIMEOUT):
            log.warning(
                "meeting %s: still-pending transcriptions after the stop timeout", meeting_id
            )
        # Step 3.5 (diarization) will hook in here later, as a further status transition; for
        # now there's nothing after transcription, so the meeting is simply done.
        self._store.finish_meeting(meeting_id, "done")

    def _wait_for_pending(self, timeout: float) -> bool:
        deadline = self._clock() + timeout
        while self._clock() < deadline:
            if self._service.pending_count(_TAG) == 0:
                return True
            time.sleep(0.1)
        return self._service.pending_count(_TAG) == 0

    # --- shared by the pump and the final stop-time drain ---

    def _process_both(self, meeting_id: int, final: bool = False) -> None:
        # System audio is processed first, deliberately: since the shared TranscriptionService
        # has one worker thread, submitting system's chunk before mic's means system's result
        # (and thus its stored segment) is already available by the time the mic chunk's own
        # job -- and its echo check against system_segments -- runs, rather than racing it.
        self._process_track(
            meeting_id, _SYSTEM_TRACK, self._system, self._system_chunker, self._others_id, final
        )
        self._process_track(
            meeting_id, _MIC_TRACK, self._mic, self._mic_chunker, self._you_id, final
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

    def _error(self, message: str) -> None:
        if self._on_error:
            self._on_error(message)
