from rylanflow.relaunch import relaunch_command


def test_frozen_app_reopens_its_bundle():
    exe = "/Applications/RylanFlow.app/Contents/MacOS/RylanFlow"
    assert relaunch_command(exe, frozen=True) == ["/usr/bin/open", "/Applications/RylanFlow.app"]


def test_source_run_restarts_module():
    assert relaunch_command("/py/python3", frozen=False) == [
        "/py/python3",
        "-m",
        "rylanflow",
        "app",
    ]
