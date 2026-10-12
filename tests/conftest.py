import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    """Keep settings/profiles written by tests out of the real user folder."""
    monkeypatch.setenv("APPDATA", str(tmp_path))
