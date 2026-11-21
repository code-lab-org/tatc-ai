"""Pin the model-facing surface the eval sends to a model.

The committed fixture records the tool names, descriptions, input
schemas, and server instructions exactly as the eval presents them. A
model-facing change shows up as a fixture diff to review; regenerate it
in the same PR with

    apps/mcp-server/.venv312/bin/python -m eval.run_eval --write-snapshot

Volatile values (timestamps, absolute paths, package versions, generated
IDs) stay out of the snapshot; the eval clock is added around this
surface at run time.
"""

import re

from eval import run_eval
from eval.run_eval import model_surface

SNAPSHOT = run_eval.SNAPSHOT


def test_model_surface_matches_the_committed_snapshot():
    assert run_eval.snapshot_text(model_surface()) == SNAPSHOT.read_text()


def test_snapshot_is_deterministic_and_free_of_run_specific_values():
    # Two builds in one process must agree: a timestamp, generated ID, or
    # path that leaked into the surface would differ between calls.
    first = run_eval.snapshot_text(model_surface())
    assert first == run_eval.snapshot_text(model_surface())
    assert "Current date and time" not in first  # the eval clock, not the surface
    assert not re.search(r'"/(?:home|tmp|Users|root|opt|usr|app)/', first), "absolute path"
    assert not re.search(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}\b", first), "generated ID"
