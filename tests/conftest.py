import os
import sys
import tempfile
from pathlib import Path

# Never touch real runtime state or the Pi from tests.
os.environ.setdefault("BIGSCREEN_STATE", tempfile.mkdtemp(prefix="bigscreen-test-state-"))
os.environ.setdefault("BIGSCREEN_NO_PI", "1")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
