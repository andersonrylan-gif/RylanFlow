"""Tests for RylanFlowApp that don't need to run the rumps event loop."""

import rylanflow.app as app_module
from rylanflow.app import RylanFlowApp
from rylanflow.config import Config
from rylanflow.store import Store


class FakeInserter:
    def __init__(self):
        self.inserted = []

    def insert(self, text):
        self.inserted.append(text)


def make_app(tmp_path, **config_kwargs):
    store = Store(tmp_path / "test.db")
    app = RylanFlowApp(config=Config(**config_kwargs), store=store)
    app._inserter = FakeInserter()
    return app, store


def test_on_text_saves_the_dictation_and_pastes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "frontmost_app", lambda: "Notes")
    app, store = make_app(tmp_path, model="test-model")

    app._on_text("hello world", 2.5)

    saved = store.list_dictations()
    assert len(saved) == 1
    assert saved[0]["text"] == "hello world"
    assert saved[0]["seconds"] == 2.5
    assert saved[0]["app_name"] == "Notes"
    assert saved[0]["model"] == "test-model"
    assert app._inserter.inserted == ["hello world"]
    assert app._last_transcript == "hello world"


def test_on_text_saves_text_after_filler_removal(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "frontmost_app", lambda: None)
    app, store = make_app(tmp_path, remove_fillers=True)

    app._on_text("um, hello there", 1.0)

    saved = store.list_dictations()[0]["text"]
    assert "um" not in saved
    assert app._inserter.inserted == [saved]  # pasted the same cleaned-up text that was saved


def test_a_store_failure_does_not_block_the_paste(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "frontmost_app", lambda: "Notes")
    app, store = make_app(tmp_path)

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(store, "add_dictation", boom)

    app._on_text("still gets pasted", 1.0)

    assert app._inserter.inserted == ["still gets pasted"]


def test_empty_text_after_filler_removal_is_not_saved_or_pasted(tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "frontmost_app", lambda: "Notes")
    app, store = make_app(tmp_path, remove_fillers=True)

    app._on_text("um", 0.5)

    assert store.list_dictations() == []
    assert app._inserter.inserted == []


def test_frontmost_app_does_not_raise():
    # Exercises the real AppKit path on this machine; must never raise, even off the main thread.
    assert app_module.frontmost_app() is None or isinstance(app_module.frontmost_app(), str)
