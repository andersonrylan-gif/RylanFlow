"""A small local HTTP server that serves the dashboard UI and its JSON API.

Binds to 127.0.0.1 on a random port and never leaves the loopback interface. Every /api/*
request must carry the per-launch token in an `X-RylanFlow-Token` header (the dashboard page
reads it from its own URL's `?t=` query string and sends it on every fetch), and every request
is rejected unless its Host header names this exact 127.0.0.1:<port> -- both of which stop a
malicious web page from poking at the API through the browser's normal same-origin exceptions
for localhost (DNS rebinding and the like).
"""

import json
import logging
import mimetypes
import re
import secrets
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

import pyperclip

from rylanflow.store import Store

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).resolve().parent / "static"
_DICTATION_PATH = re.compile(r"/api/dictations/(\d+)")
_MEETING_PATH = re.compile(r"/api/meetings/(\d+)")


class Actions(Protocol):
    """What the dashboard needs from the running app, so tests can pass a fake."""

    def get_settings(self) -> dict: ...
    def apply_settings(self, changes: dict) -> None: ...
    def start_meeting(self) -> int: ...
    def stop_meeting(self) -> int | None: ...


class DashboardServer:
    """Owns the HTTP server's background thread. Create one, `start()` it, `stop()` it once."""

    def __init__(self, store: Store, actions: Actions) -> None:
        self._store = store
        self._actions = actions
        self.token = secrets.token_urlsafe(24)
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        """Start serving in a daemon thread and return the dashboard's URL."""
        if self._httpd is not None:
            return self.url
        # The handler needs to know its own port to validate the Host header, but the port is
        # only assigned once the socket is bound -- so it reads it back out of this dict.
        port_box: dict[str, int] = {}
        handler_cls = _make_handler(self._store, self._actions, self.token, port_box)
        self._httpd = _Server(("127.0.0.1", 0), handler_cls)
        port_box["port"] = self._httpd.server_port
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="dashboard-http", daemon=True
        )
        self._thread.start()
        return self.url

    @property
    def url(self) -> str:
        if self._httpd is None:
            raise RuntimeError("DashboardServer.start() has not been called")
        return f"http://127.0.0.1:{self._httpd.server_port}/?t={self.token}"

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def _make_handler(
    store: Store, actions: Actions, token: str, port_box: dict[str, int]
) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "RylanFlow/1"
        protocol_version = "HTTP/1.1"

        def log_message(self, fmt: str, *args) -> None:
            log.debug("dashboard: " + fmt, *args)  # noqa: G003 - this is a format template

        def _host_ok(self) -> bool:
            expected = f"127.0.0.1:{port_box['port']}"
            return self.headers.get("Host") == expected

        def _token_ok(self) -> bool:
            return secrets.compare_digest(self.headers.get("X-RylanFlow-Token", ""), token)

        def _deny(self, status: HTTPStatus = HTTPStatus.FORBIDDEN) -> None:
            self.send_error(status)

        def _json(self, status: HTTPStatus, data) -> None:
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            return json.loads(raw) if raw else {}

        def _serve_file(self, path: Path) -> None:
            try:
                data = path.read_bytes()
            except OSError:
                self._deny(HTTPStatus.NOT_FOUND)
                return
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _serve_static(self, rel_path: str) -> None:
            target = (STATIC_DIR / rel_path).resolve()
            if target != STATIC_DIR and STATIC_DIR not in target.parents:
                self._deny(HTTPStatus.FORBIDDEN)  # path traversal attempt
                return
            self._serve_file(target)

        # --- routing ---

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming convention
            if not self._host_ok():
                self._deny()
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/":
                self._serve_file(STATIC_DIR / "index.html")
            elif parsed.path.startswith("/static/"):
                self._serve_static(parsed.path.removeprefix("/static/"))
            elif parsed.path == "/api/dictations":
                if not self._token_ok():
                    self._deny()
                    return
                params = parse_qs(parsed.query)
                limit = int(params.get("limit", ["50"])[0])
                offset = int(params.get("offset", ["0"])[0])
                query = params.get("q", [None])[0] or None
                self._json(HTTPStatus.OK, store.list_dictations(limit, offset, query))
            elif parsed.path == "/api/stats":
                if not self._token_ok():
                    self._deny()
                    return
                self._json(HTTPStatus.OK, store.stats())
            elif parsed.path == "/api/settings":
                if not self._token_ok():
                    self._deny()
                    return
                self._json(HTTPStatus.OK, actions.get_settings())
            elif parsed.path == "/api/meetings":
                if not self._token_ok():
                    self._deny()
                    return
                self._json(HTTPStatus.OK, store.list_meetings())
            elif match := _MEETING_PATH.fullmatch(parsed.path):
                if not self._token_ok():
                    self._deny()
                    return
                meeting = store.get_meeting(int(match.group(1)))
                if meeting is None:
                    self._deny(HTTPStatus.NOT_FOUND)
                else:
                    self._json(HTTPStatus.OK, meeting)
            else:
                self._deny(HTTPStatus.NOT_FOUND)

        def do_DELETE(self) -> None:  # noqa: N802
            if not self._host_ok() or not self._token_ok():
                self._deny()
                return
            if match := _DICTATION_PATH.fullmatch(self.path):
                if store.delete_dictation(int(match.group(1))):
                    self._json(HTTPStatus.OK, {"ok": True})
                else:
                    self._deny(HTTPStatus.NOT_FOUND)
            elif match := _MEETING_PATH.fullmatch(self.path):
                if store.delete_meeting(int(match.group(1))):
                    self._json(HTTPStatus.OK, {"ok": True})
                else:
                    self._deny(HTTPStatus.NOT_FOUND)
            else:
                self._deny(HTTPStatus.NOT_FOUND)

        def do_POST(self) -> None:  # noqa: N802
            if not self._host_ok() or not self._token_ok():
                self._deny()
                return
            if self.path == "/api/copy":
                pyperclip.copy(self._read_json().get("text", ""))
                self._json(HTTPStatus.OK, {"ok": True})
            elif self.path == "/api/meetings/start":
                meeting_id = actions.start_meeting()
                self._json(HTTPStatus.OK, store.get_meeting(meeting_id))
            elif self.path == "/api/meetings/stop":
                meeting_id = actions.stop_meeting()
                result = store.get_meeting(meeting_id) if meeting_id else {"ok": True}
                self._json(HTTPStatus.OK, result)
            else:
                self._deny(HTTPStatus.NOT_FOUND)

        def do_PUT(self) -> None:  # noqa: N802
            if not self._host_ok() or not self._token_ok():
                self._deny()
                return
            if self.path == "/api/settings":
                actions.apply_settings(self._read_json())
                self._json(HTTPStatus.OK, actions.get_settings())
            else:
                self._deny(HTTPStatus.NOT_FOUND)

    return Handler
