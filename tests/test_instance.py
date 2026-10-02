import subprocess
import sys

from rylanflow import instance


def test_second_holder_is_refused(tmp_path):
    path = tmp_path / "app.lock"
    assert instance.acquire(path)
    code = (
        "import sys; from pathlib import Path; from rylanflow import instance; "
        "sys.exit(0 if instance.acquire(Path(sys.argv[1])) else 1)"
    )
    other = subprocess.run([sys.executable, "-c", code, str(path)])
    assert other.returncode == 1
