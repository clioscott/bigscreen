import os
import sys
import tempfile
from pathlib import Path

# Never touch real runtime state or the Pi from tests.
os.environ.setdefault("BIGSCREEN_STATE", tempfile.mkdtemp(prefix="bigscreen-test-state-"))
os.environ.setdefault("BIGSCREEN_NO_PI", "1")
os.environ["BIGSCREEN_STATE_DIR"] = tempfile.mkdtemp(prefix="bigscreen-test-display-state-")   # rotation pass lengths
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _fresh_rotation_state(tmp_path, monkeypatch):
    """Each test starts with no remembered pass lengths (display/rotation.py keeps them across restarts)."""
    from display import rotation
    monkeypatch.setattr(rotation, "PASS_DIR", str(tmp_path / "display-state"))
