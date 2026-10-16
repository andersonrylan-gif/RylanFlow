"""Keep the test suite away from the owner's real settings and data.

RylanFlowApp calls save_config() with its default path, so without this every pytest run rewrote
~/.config/rylanflow/config.toml with test values (sounds off, a different hotkey, ...). The env
vars are read at import time by config.py / store.py, so they must be set before those import:
conftest.py is imported before any test module, which is why this is module-level, not a fixture.
"""

import atexit
import os
import shutil
import tempfile

_SANDBOX = tempfile.mkdtemp(prefix="rylanflow-tests-")
atexit.register(shutil.rmtree, _SANDBOX, ignore_errors=True)
os.environ["RYLANFLOW_CONFIG"] = os.path.join(_SANDBOX, "config.toml")
os.environ["RYLANFLOW_DATA_DIR"] = os.path.join(_SANDBOX, "data")
