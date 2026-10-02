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
