from pathlib import Path

from rylanflow.config import Config, load_config, save_config


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "nope.toml") == Config()


def test_partial_file_and_unknown_keys(tmp_path):
    path = tmp_path / "c.toml"
    path.write_text('hotkey = "f13"\nbogus = 1\n')
    config = load_config(path)
    assert config.hotkey == "f13"
    assert config.model == Config().model


def test_save_then_load_roundtrip(tmp_path):
    path = tmp_path / "sub" / "c.toml"
    config = Config(hotkey="f13", model="some/model", language="en")
    save_config(config, path)
    assert load_config(path) == config


def test_new_options_roundtrip(tmp_path):
    path = tmp_path / "c.toml"
    config = Config(sounds=False, remove_fillers=False)
    save_config(config, path)
    loaded = load_config(path)
    assert loaded.sounds is False
    assert loaded.remove_fillers is False


def test_save_does_not_leave_a_temp_file_behind(tmp_path):
    path = tmp_path / "c.toml"
    save_config(Config(sounds=False), path)
    assert not path.with_suffix(".toml.tmp").exists()


def test_a_failed_save_never_leaves_a_truncated_file(tmp_path, monkeypatch):
    # Regression: writing config.toml directly meant a process killed mid-write (a crash, a
    # force-quit, `pkill -9`) could leave it truncated at a line boundary -- still valid TOML,
    # just missing whatever fields came after the cut, which silently fall back to their
    # dataclass defaults on the next load with no error anywhere (this is exactly how
    # dashboard_seen was found reset to false days after being set true, with sounds -- written
    # right before it -- intact). Saving to a temp file and renaming it into place means a
    # failed write can never corrupt the real file, which this confirms directly.
    path = tmp_path / "c.toml"
    save_config(Config(hotkey="f13", sounds=True), path)
    original = path.read_text()

    def boom(self, *_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", boom)
    try:
        save_config(Config(hotkey="cmd_r", sounds=False), path)
    except OSError:
        pass

    assert path.read_text() == original  # the old, complete file is untouched
    assert not path.with_suffix(".toml.tmp").exists()
