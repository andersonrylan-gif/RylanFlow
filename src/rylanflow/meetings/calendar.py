"""EventKit lookup for "what calendar event is happening right now", used to suggest a
meeting's title and attendee list when one starts. Impure (real Calendar access via EventKit);
_find_meeting_url below is the one piece of pure logic, factored out for testing.
"""

import dataclasses
import logging
import re
import threading
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

LOOKUP_WINDOW = timedelta(minutes=15)
REQUEST_ACCESS_TIMEOUT = 10.0

_MEETING_URL_PATTERN = re.compile(
    r"https?://[^\s]*(zoom\.us|meet\.google\.com|teams\.microsoft\.com|webex\.com)[^\s]*",
    re.IGNORECASE,
)


@dataclasses.dataclass
class Event:
    title: str | None
    event_id: str
    attendees: list[str]
    url: str | None


def _find_meeting_url(texts: list[str | None]) -> str | None:
    """The first zoom/meet/teams/webex URL found across a list of candidate strings (an event's
    URL field, location and notes, in that preference order)."""
    for text in texts:
        if text and (match := _MEETING_URL_PATTERN.search(text)):
            return match.group(0)
    return None


class CalendarLookup:
    """Create one, call current_event() whenever a meeting starts. The first call triggers
    macOS's Calendar permission prompt and blocks (on whatever thread calls it -- never the
    main thread) until the user responds or REQUEST_ACCESS_TIMEOUT elapses."""

    def __init__(self) -> None:
        import EventKit

        self._store = EventKit.EKEventStore.alloc().init()
        self._access_granted: bool | None = None

    def _request_access(self) -> bool:
        if self._access_granted is not None:
            return self._access_granted
        done = threading.Event()
        result: dict[str, bool] = {}

        def handler(granted, _error):
            result["granted"] = bool(granted)
            done.set()

        self._store.requestFullAccessToEventsWithCompletion_(handler)
        done.wait(REQUEST_ACCESS_TIMEOUT)
        self._access_granted = result.get("granted", False)
        return self._access_granted

    def current_event(self, at: datetime) -> Event | None:
        """The calendar event closest to covering `at`, preferring one with a meeting URL, or
        None if access was denied or nothing is scheduled nearby."""
        if not self._request_access():
            return None
        from Foundation import NSDate

        def nsdate(dt: datetime) -> NSDate:
            return NSDate.dateWithTimeIntervalSince1970_(dt.timestamp())

        predicate = self._store.predicateForEventsWithStartDate_endDate_calendars_(
            nsdate(at - LOOKUP_WINDOW), nsdate(at + LOOKUP_WINDOW), None
        )
        ek_events = self._store.eventsMatchingPredicate_(predicate) or []
        events = [_to_event(e) for e in ek_events]
        if not events:
            return None
        return next((e for e in events if e.url), events[0])


def _to_event(ek_event) -> Event:
    url_field = ek_event.URL()
    url_text = url_field.absoluteString() if url_field else None
    location = ek_event.location()
    notes = ek_event.notes()
    url = _find_meeting_url([url_text, str(location) if location else None, notes])

    attendees = []
    for participant in ek_event.attendees() or []:
        if name := participant.name():
            attendees.append(str(name))

    title = ek_event.title()
    return Event(
        title=str(title) if title else None,
        event_id=str(ek_event.eventIdentifier()),
        attendees=attendees,
        url=url,
    )
