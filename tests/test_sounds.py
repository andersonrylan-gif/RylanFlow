import subprocess
import threading

from rylanflow import sounds


def test_play_runs_afplay_with_the_system_sound(monkeypatch):
    done = threading.Event()
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        done.set()
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(sounds.subprocess, "run", fake_run)
    monkeypatch.setattr(sounds, "SOUNDS", {"start": sounds.SOUNDS["start"]})
    monkeypatch.setattr(type(sounds.SOUNDS["start"]), "exists", lambda self: True)
    sounds.play("start")
    assert done.wait(2)
    assert calls[0][0] == "afplay"
    assert calls[0][1].endswith("Tink.aiff")


def test_a_failing_afplay_is_logged_not_raised(monkeypatch, caplog):
    done = threading.Event()

    def fake_run(cmd, **kwargs):
        done.set()
        return subprocess.CompletedProcess(cmd, 1, "", "no output device")

    monkeypatch.setattr(sounds.subprocess, "run", fake_run)
    monkeypatch.setattr(type(sounds.SOUNDS["stop"]), "exists", lambda self: True)
    with caplog.at_level("WARNING"):
        sounds.play("stop")
        assert done.wait(2)
        for _ in range(100):  # the warning is logged just after run() returns
            if "no output device" in caplog.text:
                break
            threading.Event().wait(0.02)
    assert "no output device" in caplog.text


def test_a_missing_sound_file_is_logged(monkeypatch, caplog):
    monkeypatch.setattr(type(sounds.SOUNDS["start"]), "exists", lambda self: False)
    with caplog.at_level("WARNING"):
        sounds.play("start")
    assert "is missing" in caplog.text
