import pytest

from rylanflow import inserter
from rylanflow.inserter import ClipboardInserter


class FakeClipboard:
    def __init__(self, value):
        self.value = value
        self.history = []

    def paste(self):
        return self.value

    def copy(self, text):
        self.value = text
        self.history.append(text)


class FakeKeyboard:
    def __init__(self):
        self.taps = []

    def pressed(self, key):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def tap(self, key):
        self.taps.append(key)


@pytest.fixture
def setup(monkeypatch):
    clip = FakeClipboard("original")
    monkeypatch.setattr(inserter.pyperclip, "paste", clip.paste)
    monkeypatch.setattr(inserter.pyperclip, "copy", clip.copy)
    monkeypatch.setattr(inserter, "PASTE_SETTLE_SECONDS", 0)
    ins = ClipboardInserter()
    ins._keyboard = FakeKeyboard()
    return ins, clip


def test_pastes_then_restores_clipboard(setup):
    ins, clip = setup
    ins.insert("hello")
    assert clip.history == ["hello", "original"]
    assert ins._keyboard.taps == ["v"]
    assert clip.value == "original"


def test_restores_clipboard_if_paste_fails(setup):
    ins, clip = setup

    def boom(key):
        raise RuntimeError("no accessibility permission")

    ins._keyboard.tap = boom
    with pytest.raises(RuntimeError):
        ins.insert("hello")
    assert clip.value == "original"


def test_empty_text_is_a_noop(setup):
    ins, clip = setup
    ins.insert("")
    assert clip.history == []
