from rylanflow import autostart


def test_plist_launches_app_at_login_and_survives_crashes():
    plist = autostart.build_plist("/py/python3")
    assert plist["Label"] == autostart.LABEL
    assert plist["ProgramArguments"] == ["/py/python3", "-m", "rylanflow", "app"]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}


def test_prefers_packaged_app(tmp_path):
    app = tmp_path / "RylanFlow.app"
    assert autostart.launch_command("/py/python3", app) == ["/usr/bin/open", "-a", str(app)]
    assert autostart.launch_command("/py/python3", None)[:1] == ["/py/python3"]
