from rylanflow import autostart


def test_plist_launches_app_at_login_and_survives_crashes():
    plist = autostart.build_plist("/py/python3")
    assert plist["Label"] == autostart.LABEL
    assert plist["ProgramArguments"] == ["/py/python3", "-m", "rylanflow", "app"]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] == {"SuccessfulExit": False}
