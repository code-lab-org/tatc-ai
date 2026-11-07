import sys
from pathlib import Path

# Make `import src.server` work regardless of where pytest is invoked from.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(autouse=True)
def isolated_celestrak_cache(monkeypatch, tmp_path):
    monkeypatch.setenv("CELESTRAK_CACHE_PATH", str(tmp_path / "cache.sqlite3"))
