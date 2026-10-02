"""Tests for RylanFlowApp that don't need to run the rumps event loop."""

import pytest

import rylanflow.app as app_module
from rylanflow.app import RylanFlowApp
from rylanflow.config import Config
from rylanflow.hotkey import parse_key
from rylanflow.store import Store
from rylanflow.transcriber import DEFAULT_MODEL


class FakeInserter:
    def __init__(self):
        self.inserted = []

    def insert(self, text):
        self.inserted.append(text)


@pytest.fixture
def make_app(tmp_path):
    apps = []

    def factory(**config_kwargs):
        store = Store(tmp_path / f"test-{len(apps)}.db")
        app = RylanFlowApp(config=Config(**config_kwargs), store=store)
        app._inserter = FakeInserter()
        apps.append(app)
        return app, store

    yield factory
    for app in apps:
        app._dashboard.stop()


def test_on_text_saves_the_dictation_and_pastes_it(make_app, monkeypatch):
    monkeypatch.setattr(app_module, "frontmost_app", lambda: "Notes")
    app, store = make_app(model="test-model")

    app._on_text("hello world", 2.5)

    saved = store.list_dictations()
    assert len(saved) == 1
    assert saved[0]["text"] == "hello world"
    assert saved[0]["seconds"] == 2.5
    assert saved[0]["app_name"] == "Notes"
    assert saved[0]["model"] == "test-model"
    assert app._inserter.inserted == ["hello world"]
    assert app._last_transcript == "hello world"


def test_on_text_saves_text_after_filler_removal(make_app, monkeypatch):
    monkeypatch.setattr(app_module, "frontmost_app", lambda: None)
    app, store = make_app(remove_fillers=True)

    app._on_text("um, hello there", 1.0)

    saved = store.list_dictations()[0]["text"]
    assert "um" not in saved
    assert app._inserter.inserted == [saved]  # pasted the same cleaned-up text that was saved


def test_a_store_failure_does_not_block_the_paste(make_app, monkeypatch):
    monkeypatch.setattr(app_module, "frontmost_app", lambda: "Notes")
    app, store = make_app()

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "add_dictation", boom)

    app._on_text("still gets pasted", 1.0)

    assert app._inserter.inserted == ["still gets pasted"]


def test_empty_text_after_filler_removal_is_not_saved_or_pasted(make_app, monkeypatch):
    monkeypatch.setattr(app_module, "frontmost_app", lambda: "Notes")
    app, store = make_app(remove_fillers=True)

    app._on_text("um", 0.5)

    assert store.list_dictations() == []
    assert app._inserter.inserted == []


def test_frontmost_app_does_not_raise():
    # Exercises the real AppKit path on this machine; must never raise, even off the main thread.
    assert app_module.frontmost_app() is None or isinstance(app_module.frontmost_app(), str)


# --- dashboard wiring ---


def test_dashboard_server_starts_and_serves_real_settings(make_app, monkeypatch):
    # is_enabled() reads this machine's real LaunchAgent state, which this test doesn't own.
    monkeypatch.setattr(app_module.autostart, "is_enabled", lambda: False)
    app, _store = make_app(hotkey="cmd_r", model="my-model")
    assert app._dashboard_url.startswith("http://127.0.0.1:")
    settings = app.get_settings()
    assert settings["hotkey"] == "cmd_r"
    assert settings["model"] == "my-model"
    assert settings["sounds"] is True
    assert settings["remove_fillers"] is True
    assert settings["start_at_login"] is False
    assert settings["overlay"] == "bottom"
    assert settings["available_hotkeys"] == app_module.HOTKEYS
    assert settings["available_models"] == app_module.MODELS
    assert settings["available_overlay_positions"] == app_module.OVERLAY_POSITIONS


def test_apply_settings_main_thread_updates_config_live_state_and_menu(make_app):
    app, _store = make_app()

    result = app._apply_settings_main_thread(
        {"hotkey": "ctrl_r", "model": DEFAULT_MODEL, "sounds": False, "remove_fillers": False}
    )

    assert result == app.get_settings()
    assert app._config.hotkey == "ctrl_r"
    assert app._ptt._key == parse_key("ctrl_r")
    assert app._transcriber.model == DEFAULT_MODEL
    assert app._hotkey_items["Right Control"].state == 1
    assert app._hotkey_items["Right Option"].state == 0
    assert app._model_items["Fast (base)"].state == 0
    assert app._sounds_item.state == 0
    assert app._fillers_item.state == 0


def test_apply_settings_main_thread_toggles_start_at_login(make_app, monkeypatch):
    # Must never touch the real LaunchAgent/launchctl state of the machine running the tests.
    enabled, disabled = [], []
    monkeypatch.setattr(app_module.autostart, "enable", lambda: enabled.append(1))
    monkeypatch.setattr(app_module.autostart, "disable", lambda: disabled.append(1))
    app, _store = make_app()

    app._apply_settings_main_thread({"start_at_login": True})
    assert enabled == [1]
    assert disabled == []

    app._apply_settings_main_thread({"start_at_login": False})
    assert enabled == [1]
    assert disabled == [1]


def test_apply_settings_main_thread_sets_overlay_position(make_app):
    app, _store = make_app(overlay="bottom")

    app._apply_settings_main_thread({"overlay": "top"})

    assert app._config.overlay == "top"
    assert app._overlay._position == "top"


def test_apply_settings_main_thread_off_stops_the_overlay_immediately(make_app, monkeypatch):
    app, _store = make_app(overlay="bottom")
    stopped = []
    monkeypatch.setattr(app._overlay, "stop", lambda: stopped.append(1))

    app._apply_settings_main_thread({"overlay": "off"})

    assert app._config.overlay == "off"
    assert stopped == [1]


def test_render_does_not_run_the_overlay_when_turned_off(make_app, monkeypatch):
    app, _store = make_app(overlay="off")
    # `state` is a read-only property, so it's patched at the class level for this test.
    monkeypatch.setattr(type(app._pipeline), "state", property(lambda self: "recording"))
    ensured = []
    monkeypatch.setattr(app._overlay, "ensure_running", lambda: ensured.append(1))

    app._render(None)

    assert ensured == []


def test_render_runs_the_overlay_while_recording(make_app, monkeypatch):
    app, _store = make_app(overlay="bottom")
    monkeypatch.setattr(type(app._pipeline), "state", property(lambda self: "recording"))
    ensured = []
    monkeypatch.setattr(app._overlay, "ensure_running", lambda: ensured.append(1))

    app._render(None)

    assert ensured == [1]


def test_apply_settings_main_thread_ignores_unknown_values(make_app):
    app, _store = make_app(hotkey="alt_r", model="base-model")

    app._apply_settings_main_thread(
        {"hotkey": "nonexistent_key", "model": "nonexistent_model", "overlay": "sideways"}
    )

    assert app._config.hotkey == "alt_r"
    assert app._config.model == "base-model"


def test_apply_settings_hops_to_the_main_thread_and_waits(make_app, monkeypatch):
    app, _store = make_app()
    calls = []
    # No live NSApplication run loop in tests, so make callAfter run its block immediately
    # instead of actually scheduling it -- this still exercises the Event-based handshake.
    monkeypatch.setattr(app_module.AppHelper, "callAfter", lambda fn: (calls.append(1), fn()))

    app.apply_settings({"sounds": False})

    assert calls == [1]
    assert app._config.sounds is False
    assert app._sounds_item.state == 0


def test_open_dashboard_falls_back_to_the_browser_if_webkit_import_fails(make_app, monkeypatch):
    import sys
    import webbrowser

    app, _store = make_app()
    opened = []
    # A None entry makes `import rylanflow.dashboard.window` raise ImportError, simulating a
    # missing/broken WebKit wheel without needing one to actually be missing.
    monkeypatch.setitem(sys.modules, "rylanflow.dashboard.window", None)
    monkeypatch.setattr(webbrowser, "open", opened.append)

    app._open_dashboard(None)

    assert opened == [app._dashboard_url]
