import json
import urllib.error
import urllib.request

import pytest

from rylanflow.dashboard.server import DashboardServer
from rylanflow.store import Store


class FakeActions:
    def __init__(self):
        self.settings = {"hotkey": "alt_r", "model": "base"}
        self.applied = []

    def get_settings(self):
        return dict(self.settings)

    def apply_settings(self, changes):
        self.applied.append(changes)
        self.settings.update(changes)


@pytest.fixture
def server(tmp_path):
    store = Store(tmp_path / "test.db")
    actions = FakeActions()
    srv = DashboardServer(store, actions)
    url = srv.start()
    yield srv, store, actions, url
    srv.stop()
    store.close()


def _base(url: str) -> str:
    # url is ".../?t=<token>"; strip the query string to get the server's origin.
    return url.split("?", 1)[0]


def _request(
    url: str,
    token: str | None,
    method: str = "GET",
    body: dict | None = None,
    parse_json: bool = True,
):
    data = json.dumps(body).encode() if body is not None else None
    headers = {}
    if token is not None:
        headers["X-RylanFlow-Token"] = token
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=5) as resp:
        raw = resp.read()
        if not parse_json:
            return resp.status, raw
        return resp.status, (json.loads(raw) if raw else None)


def _request_expect_error(url: str, **kwargs) -> int:
    try:
        _request(url, **kwargs)
        raise AssertionError("expected an HTTPError")
    except urllib.error.HTTPError as e:
        return e.code


def test_index_page_served_without_a_token(server):
    _srv, _store, _actions, url = server
    status, body = _request(_base(url), token=None, parse_json=False)
    assert status == 200
    assert b"RylanFlow" in body


def test_api_without_token_is_rejected(server):
    _srv, _store, _actions, url = server
    code = _request_expect_error(f"{_base(url)}api/dictations", token=None)
    assert code == 403


def test_api_with_wrong_token_is_rejected(server):
    _srv, _store, _actions, url = server
    code = _request_expect_error(f"{_base(url)}api/dictations", token="wrong-token")
    assert code == 403


def test_wrong_host_header_is_rejected(server):
    srv, _store, _actions, url = server
    req = urllib.request.Request(
        f"{_base(url)}api/dictations",
        headers={"X-RylanFlow-Token": srv.token, "Host": "evil.example.com"},
    )
    with pytest.raises(urllib.error.HTTPError) as exc_info:
        urllib.request.urlopen(req, timeout=5)
    assert exc_info.value.code == 403


def test_list_dictations(server):
    srv, store, _actions, url = server
    store.add_dictation("hello world", 1.0, "Notes", "base")
    status, data = _request(f"{_base(url)}api/dictations", token=srv.token)
    assert status == 200
    assert len(data) == 1
    assert data[0]["text"] == "hello world"


def test_search_dictations(server):
    srv, store, _actions, url = server
    store.add_dictation("the quick fox", 1.0, None, None)
    store.add_dictation("a lazy dog", 1.0, None, None)
    status, data = _request(f"{_base(url)}api/dictations?q=fox", token=srv.token)
    assert status == 200
    assert len(data) == 1
    assert "fox" in data[0]["text"]


def test_pagination_params(server):
    srv, store, _actions, url = server
    for i in range(5):
        store.add_dictation(f"item {i}", 1.0, None, None)
    status, data = _request(f"{_base(url)}api/dictations?limit=2&offset=2", token=srv.token)
    assert status == 200
    assert len(data) == 2


def test_delete_dictation(server):
    srv, store, _actions, url = server
    id_ = store.add_dictation("to delete", 1.0, None, None)
    status, data = _request(f"{_base(url)}api/dictations/{id_}", token=srv.token, method="DELETE")
    assert status == 200
    assert data == {"ok": True}
    assert store.list_dictations() == []


def test_delete_dictation_requires_token(server):
    srv, store, _actions, url = server
    id_ = store.add_dictation("still here", 1.0, None, None)
    code = _request_expect_error(f"{_base(url)}api/dictations/{id_}", token=None, method="DELETE")
    assert code == 403
    assert len(store.list_dictations()) == 1


def test_delete_nonexistent_dictation_returns_404(server):
    srv, _store, _actions, url = server
    code = _request_expect_error(
        f"{_base(url)}api/dictations/999", token=srv.token, method="DELETE"
    )
    assert code == 404


def test_stats_endpoint(server):
    srv, store, _actions, url = server
    store.add_dictation("one two three", 1.0, None, None)
    status, data = _request(f"{_base(url)}api/stats", token=srv.token)
    assert status == 200
    assert data["words_total"] == 3
    assert data["dictations_total"] == 1


def test_copy_endpoint(server, monkeypatch):
    srv, _store, _actions, url = server
    copied = []
    monkeypatch.setattr("rylanflow.dashboard.server.pyperclip.copy", copied.append)
    status, data = _request(
        f"{_base(url)}api/copy", token=srv.token, method="POST", body={"text": "hello clipboard"}
    )
    assert status == 200
    assert data == {"ok": True}
    assert copied == ["hello clipboard"]


def test_get_settings(server):
    srv, _store, actions, url = server
    status, data = _request(f"{_base(url)}api/settings", token=srv.token)
    assert status == 200
    assert data == actions.get_settings()


def test_put_settings_applies_changes_and_returns_the_result(server):
    srv, _store, actions, url = server
    status, data = _request(
        f"{_base(url)}api/settings",
        token=srv.token,
        method="PUT",
        body={"hotkey": "cmd_r"},
    )
    assert status == 200
    assert data["hotkey"] == "cmd_r"
    assert actions.applied == [{"hotkey": "cmd_r"}]


def test_unknown_api_route_is_404(server):
    srv, _store, _actions, url = server
    code = _request_expect_error(f"{_base(url)}api/nope", token=srv.token)
    assert code == 404


def test_static_path_traversal_is_rejected(server):
    srv, _store, _actions, url = server
    code = _request_expect_error(f"{_base(url)}static/../../../etc/passwd", token=None)
    assert code in (403, 404)  # urllib normalizes '..' before sending; either is acceptable


def test_two_servers_get_different_tokens_and_ports(tmp_path):
    store1 = Store(tmp_path / "a.db")
    store2 = Store(tmp_path / "b.db")
    s1 = DashboardServer(store1, FakeActions())
    s2 = DashboardServer(store2, FakeActions())
    url1, url2 = s1.start(), s2.start()
    try:
        assert s1.token != s2.token
        assert url1 != url2
    finally:
        s1.stop()
        s2.stop()
        store1.close()
        store2.close()
