import time

import numpy as np
import pytest

from rylanflow.meetings.calendar import Event
from rylanflow.meetings.session import MeetingSession
from rylanflow.store import Store
from rylanflow.transcriber import Segment
from rylanflow.transcription_service import TranscriptionService


def wait_for(condition, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


class FakeTrack:
    """A mic/system track double: feed() queues audio, drain() returns and clears it, just
    like the real MicTrack/SystemAudioTrack -- but with no hardware involved."""

    def __init__(self, path):
        self.path = path
        self.started = False
        self.closed = False
        self._chunks: list[np.ndarray] = []

    def feed(self, audio: np.ndarray) -> None:
        self._chunks.append(audio)

    def start(self) -> None:
        self.started = True

    def drain(self) -> np.ndarray:
        chunks, self._chunks = self._chunks, []
        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)

    def close(self) -> None:
        self.closed = True


class BrokenTrack:
    """Simulates a track whose hardware fails to open."""

    def __init__(self, path):
        pass

    def start(self) -> None:
        raise RuntimeError("no mic available")

    def drain(self) -> np.ndarray:
        return np.zeros(0, dtype=np.float32)

    def close(self) -> None:
        pass


def _same_text(_audio) -> str:
    return "we are discussing the quarterly roadmap today"


class FakeTranscriber:
    """Returns one Segment per non-empty call, covering the whole clip."""

    def __init__(self, text_for=lambda audio: f"audio-{audio.size}"):
        self.text_for = text_for
        self.calls: list[tuple[np.ndarray, bool]] = []

    def transcribe_segments(self, audio: np.ndarray, *, meeting: bool = False) -> list[Segment]:
        self.calls.append((audio.copy(), meeting))
        if audio.size == 0:
            return []
        text = self.text_for(audio)
        if not text:
            return []
        return [Segment(0.0, audio.size / 16_000, text, 0.1, -0.3)]


def make_session(
    tmp_path,
    transcriber=None,
    my_name="Rylan",
    mic_cls=FakeTrack,
    system_cls=FakeTrack,
    calendar_lookup=None,
    diarizer=None,
):
    store = Store(tmp_path / "test.db")
    service = TranscriptionService()
    mic_tracks: list = []
    system_tracks: list = []

    def mic_factory(path):
        track = mic_cls(path)
        mic_tracks.append(track)
        return track

    def system_factory(path):
        track = system_cls(path)
        system_tracks.append(track)
        return track

    session = MeetingSession(
        store,
        service,
        transcriber or FakeTranscriber(),
        my_name,
        mic_factory,
        system_factory,
        calendar_lookup=calendar_lookup,
        diarizer=diarizer,
    )
    return session, store, service, mic_tracks, system_tracks


@pytest.fixture
def env(tmp_path):
    session, store, service, mic_tracks, system_tracks = make_session(tmp_path)
    yield session, store, service, mic_tracks, system_tracks
    service.stop()


LOUD = np.ones(16_000, dtype=np.float32) * 0.3  # 1s, well above the silence threshold


def test_start_creates_the_meeting_and_both_speakers(env):
    session, store, _service, mic_tracks, system_tracks = env

    meeting_id = session.start(title="Standup", source_app="Zoom")

    assert meeting_id is not None
    assert session.active
    assert session.meeting_id == meeting_id
    meeting = store.get_meeting(meeting_id)
    assert meeting["title"] == "Standup"
    assert meeting["source_app"] == "Zoom"
    assert meeting["status"] == "recording"
    speakers_by_label = {s["label"]: s for s in meeting["speakers"]}
    assert speakers_by_label["You"]["is_me"] == 1
    assert speakers_by_label["You"]["display_name"] == "Rylan"
    assert speakers_by_label["Others"]["is_me"] == 0
    assert wait_for(lambda: mic_tracks[0].started and system_tracks[0].started)


def test_start_while_already_active_is_a_noop(env):
    session, _store, _service, mic_tracks, _system_tracks = env
    first_id = session.start()
    assert wait_for(lambda: mic_tracks[0].started)

    second_id = session.start()

    assert second_id == first_id
    assert len(mic_tracks) == 1  # no second track was created


def test_mic_segment_stored_with_the_you_speaker_and_correct_offsets(env):
    session, store, _service, mic_tracks, _system_tracks = env
    meeting_id = session.start()
    assert wait_for(lambda: mic_tracks[0].started)

    mic_tracks[0].feed(LOUD)
    session.stop()

    assert wait_for(lambda: (m := store.get_meeting(meeting_id)) and m["segments"], timeout=5.0)
    meeting = store.get_meeting(meeting_id)
    [segment] = meeting["segments"]
    you_id = next(s["id"] for s in meeting["speakers"] if s["label"] == "You")
    assert segment["speaker_id"] == you_id
    assert segment["track"] == "mic"
    assert segment["start_s"] == pytest.approx(0.0, abs=1e-3)
    assert segment["end_s"] == pytest.approx(1.0, abs=0.05)


def test_system_segment_stored_with_the_others_speaker(env):
    session, store, _service, _mic_tracks, system_tracks = env
    meeting_id = session.start()
    assert wait_for(lambda: system_tracks[0].started)

    system_tracks[0].feed(LOUD)
    session.stop()

    assert wait_for(lambda: (m := store.get_meeting(meeting_id)) and m["segments"], timeout=5.0)
    meeting = store.get_meeting(meeting_id)
    [segment] = meeting["segments"]
    others_id = next(s["id"] for s in meeting["speakers"] if s["label"] == "Others")
    assert segment["speaker_id"] == others_id
    assert segment["track"] == "system"


def test_silent_mic_audio_is_never_submitted_for_transcription(tmp_path):
    transcriber = FakeTranscriber()
    session, store, service, mic_tracks, _system_tracks = make_session(tmp_path, transcriber)
    try:
        meeting_id = session.start()
        assert wait_for(lambda: mic_tracks[0].started)

        mic_tracks[0].feed(np.zeros(16_000, dtype=np.float32))  # pure silence
        session.stop()

        assert wait_for(lambda: store.get_meeting(meeting_id)["status"] == "done", timeout=5.0)
        assert store.get_meeting(meeting_id)["segments"] == []
        assert transcriber.calls == []  # never even submitted, let alone transcribed
    finally:
        service.stop()


def test_stop_flushes_whatever_is_buffered_and_marks_the_meeting_done(env):
    session, store, _service, mic_tracks, _system_tracks = env
    meeting_id = session.start()
    assert wait_for(lambda: mic_tracks[0].started)
    mic_tracks[0].feed(LOUD)  # well under max_s, so only flush() at stop() will emit it

    returned_id = session.stop()

    assert returned_id == meeting_id
    assert not session.active
    assert wait_for(lambda: store.get_meeting(meeting_id)["status"] == "done", timeout=5.0)
    meeting = store.get_meeting(meeting_id)
    assert meeting["ended_at"] is not None
    assert len(meeting["segments"]) == 1


def test_stop_without_an_active_meeting_is_a_noop(env):
    session, _store, _service, _mic_tracks, _system_tracks = env
    assert session.stop() is None


def test_start_and_stop_do_not_block_the_caller(env):
    session, _store, _service, mic_tracks, _system_tracks = env
    began = time.monotonic()
    session.start()
    assert time.monotonic() - began < 0.5  # hardware startup happens in the background
    assert wait_for(lambda: mic_tracks[0].started)

    began = time.monotonic()
    session.stop()
    assert time.monotonic() - began < 0.5


def test_mic_echo_of_system_audio_is_dropped(tmp_path):
    transcriber = FakeTranscriber(text_for=_same_text)
    # system is processed (and stored) strictly before mic for a given flush, since
    # MeetingSession submits system's chunk first and the service has one worker thread -- see
    # _process_both()'s comment. So feeding both before one stop() is enough; no need to wait
    # for an intermediate state between them.
    session, store, service, mic_tracks, system_tracks = make_session(tmp_path, transcriber)
    try:
        meeting_id = session.start()
        assert wait_for(lambda: mic_tracks[0].started and system_tracks[0].started)

        system_tracks[0].feed(LOUD)
        mic_tracks[0].feed(LOUD)  # says the exact same thing at the same (relative) time
        session.stop()

        assert wait_for(lambda: store.get_meeting(meeting_id)["status"] == "done", timeout=5.0)
        meeting = store.get_meeting(meeting_id)
        tracks_present = {s["track"] for s in meeting["segments"]}
        assert tracks_present == {"system"}  # the mic's echoed copy was dropped
    finally:
        service.stop()


def test_mic_audio_with_different_words_is_kept_even_while_system_audio_plays(tmp_path):
    # Distinguished by content, not by timing/call order: the transcriber has no way to know
    # which track it's being called for beyond what's actually in the audio.
    mic_audio = np.full(16_000, 0.3, dtype=np.float32)
    system_audio = np.full(16_000, 0.31, dtype=np.float32)

    def text_for(audio):
        if np.array_equal(audio, mic_audio):
            return "actually I disagree with that"
        return "we are discussing the quarterly roadmap today"

    transcriber = FakeTranscriber(text_for=text_for)
    session, store, service, mic_tracks, system_tracks = make_session(tmp_path, transcriber)
    try:
        meeting_id = session.start()
        assert wait_for(lambda: mic_tracks[0].started and system_tracks[0].started)

        system_tracks[0].feed(system_audio)
        mic_tracks[0].feed(mic_audio)
        session.stop()

        assert wait_for(lambda: store.get_meeting(meeting_id)["status"] == "done", timeout=5.0)
        meeting = store.get_meeting(meeting_id)
        tracks_present = {s["track"] for s in meeting["segments"]}
        assert tracks_present == {"mic", "system"}  # both kept -- different words, not an echo
    finally:
        service.stop()


def test_start_failure_marks_the_meeting_failed_and_reports_the_error(tmp_path):
    store = Store(tmp_path / "test.db")
    service = TranscriptionService()
    try:
        session = MeetingSession(
            store, service, FakeTranscriber(), "Rylan", BrokenTrack, BrokenTrack
        )
        errors = []

        meeting_id = session.start(on_error=errors.append)

        assert wait_for(lambda: errors, timeout=3.0)
        assert not session.active
        meeting = store.get_meeting(meeting_id)
        assert meeting["status"] == "failed"
    finally:
        service.stop()


class FakeCalendarLookup:
    def __init__(self, event=None, raises=False):
        self._event = event
        self._raises = raises
        self.calls = 0

    def current_event(self, at):
        self.calls += 1
        if self._raises:
            raise RuntimeError("calendar access denied")
        return self._event


def test_calendar_event_sets_the_title_and_attendees(tmp_path):
    lookup = FakeCalendarLookup(
        Event(title="Weekly sync", event_id="abc", attendees=["Sarah", "John"], url=None)
    )
    session, store, service, mic_tracks, _system_tracks = make_session(
        tmp_path, calendar_lookup=lookup
    )
    try:
        meeting_id = session.start()
        assert wait_for(lambda: lookup.calls > 0)
        assert wait_for(lambda: store.get_meeting(meeting_id)["title"] == "Weekly sync")
        meeting = store.get_meeting(meeting_id)
        assert meeting["attendees"] == ["Sarah", "John"]
    finally:
        session.stop()
        service.stop()


def test_no_calendar_event_leaves_the_title_unset(tmp_path):
    lookup = FakeCalendarLookup(event=None)
    session, store, service, _mic_tracks, _system_tracks = make_session(
        tmp_path, calendar_lookup=lookup
    )
    try:
        meeting_id = session.start()
        assert wait_for(lambda: lookup.calls > 0)
        assert store.get_meeting(meeting_id)["title"] is None
    finally:
        session.stop()
        service.stop()


def test_a_failed_calendar_lookup_does_not_break_the_meeting(tmp_path):
    lookup = FakeCalendarLookup(raises=True)
    session, store, service, mic_tracks, _system_tracks = make_session(
        tmp_path, calendar_lookup=lookup
    )
    try:
        meeting_id = session.start()
        assert wait_for(lambda: mic_tracks and mic_tracks[0].started)
        assert wait_for(lambda: lookup.calls > 0)
        assert session.active
        assert store.get_meeting(meeting_id)["status"] == "recording"
    finally:
        session.stop()
        service.stop()


def test_without_a_calendar_lookup_nothing_happens(tmp_path):
    # calendar_lookup defaults to None -- the common case in production until the owner
    # grants Calendar access, and always the case in these other tests.
    session, store, service, mic_tracks, _system_tracks = make_session(tmp_path)
    try:
        meeting_id = session.start()
        assert wait_for(lambda: mic_tracks and mic_tracks[0].started)
        assert store.get_meeting(meeting_id)["title"] is None
    finally:
        session.stop()
        service.stop()


class FakeDiarizer:
    def __init__(self, turns=None, raises=False):
        self.turns = turns or []
        self.raises = raises
        self.calls = []

    def diarize(self, wav_path):
        self.calls.append(wav_path)
        if self.raises:
            raise RuntimeError("diarization failed")
        return self.turns


def test_diarization_splits_others_into_a_named_speaker(tmp_path):
    diarizer = FakeDiarizer(turns=[(0.0, 1.0, 0)])
    session, store, service, _mic_tracks, system_tracks = make_session(tmp_path, diarizer=diarizer)
    try:
        meeting_id = session.start()
        assert wait_for(lambda: system_tracks and system_tracks[0].started)
        system_tracks[0].feed(LOUD)
        session.stop()

        assert wait_for(lambda: store.get_meeting(meeting_id)["status"] == "done", timeout=5.0)
        meeting = store.get_meeting(meeting_id)
        labels = {s["label"] for s in meeting["speakers"]}
        assert "Others" not in labels
        assert "Speaker 1" in labels
        [segment] = meeting["segments"]
        speaker = next(s for s in meeting["speakers"] if s["id"] == segment["speaker_id"])
        assert speaker["label"] == "Speaker 1"
        assert diarizer.calls
    finally:
        service.stop()


def test_diarization_failure_keeps_others_and_the_transcript(tmp_path):
    diarizer = FakeDiarizer(raises=True)
    session, store, service, _mic_tracks, system_tracks = make_session(tmp_path, diarizer=diarizer)
    try:
        meeting_id = session.start()
        assert wait_for(lambda: system_tracks and system_tracks[0].started)
        system_tracks[0].feed(LOUD)
        session.stop()

        assert wait_for(lambda: store.get_meeting(meeting_id)["status"] == "done", timeout=5.0)
        meeting = store.get_meeting(meeting_id)
        labels = {s["label"] for s in meeting["speakers"]}
        assert "Others" in labels
        assert len(meeting["segments"]) == 1  # the transcript survives a diarization failure
    finally:
        service.stop()


def test_without_a_diarizer_others_is_left_as_is(tmp_path):
    session, store, service, _mic_tracks, system_tracks = make_session(tmp_path)
    try:
        meeting_id = session.start()
        assert wait_for(lambda: system_tracks and system_tracks[0].started)
        system_tracks[0].feed(LOUD)
        session.stop()

        assert wait_for(lambda: store.get_meeting(meeting_id)["status"] == "done", timeout=5.0)
        labels = {s["label"] for s in store.get_meeting(meeting_id)["speakers"]}
        assert "Others" in labels
    finally:
        service.stop()


def test_diarization_with_no_system_segments_is_never_invoked(tmp_path):
    diarizer = FakeDiarizer(turns=[(0.0, 1.0, 0)])
    session, store, service, mic_tracks, _system_tracks = make_session(tmp_path, diarizer=diarizer)
    try:
        meeting_id = session.start()
        assert wait_for(lambda: mic_tracks and mic_tracks[0].started)
        mic_tracks[0].feed(LOUD)  # only mic audio -- nothing on the system track to diarize
        session.stop()

        assert wait_for(lambda: store.get_meeting(meeting_id)["status"] == "done", timeout=5.0)
        labels = {s["label"] for s in store.get_meeting(meeting_id)["speakers"]}
        assert "Others" in labels
        assert diarizer.calls == []
    finally:
        service.stop()
