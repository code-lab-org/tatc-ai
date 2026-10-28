"""Saved access results and drafts, fetched back by ID.

A coverage run produces more rows and cells than a model should carry
in chat. The run tool saves the full result here and returns a compact
summary plus a result ID. Fetch tools page through daily rows, grid
cells, and revisit numbers using that ID. Staged runs keep their
validated inputs here as drafts between the begin and finish calls.

The store is in-memory with a 24-hour expiry and a cap of 200 entries
per kind. The MCP server runs as a single uvicorn worker, so one process
holds every entry. A multi-worker deploy would need a shared store instead.
"""

import threading
import time
import uuid
from typing import Any, Dict, List

TTL_SECONDS = 24 * 3600
MAX_RESULTS = 200

_lock = threading.Lock()
_results: Dict[str, Dict[str, Any]] = {}
_drafts: Dict[str, Dict[str, Any]] = {}

# Per kind: label, where the ID comes from, and which tool makes a new one.
_RESULT = ("result", "a coverage run", "the access analysis tool")
_DRAFT = ("draft", "the begin tool", "the begin tool")


def _evict_locked(table: Dict[str, Dict[str, Any]], now: float) -> None:
    for key in [k for k, entry in table.items() if now - entry["saved_at"] > TTL_SECONDS]:
        del table[key]
    while len(table) > MAX_RESULTS:
        del table[min(table, key=lambda k: table[k]["saved_at"])]


def _save(table: Dict[str, Dict[str, Any]], key: str, payload: Dict[str, Any]) -> str:
    now = time.time()
    with _lock:
        table[key] = {"saved_at": now, "payload": payload}
        _evict_locked(table, now)
    return key


def _lookup_locked(
    table: Dict[str, Dict[str, Any]], kind: tuple, raw_id: Any
) -> Dict[str, Any]:
    """Return the live payload for an ID, with model-friendly errors.

    Raises:
        ValueError: For unknown, expired, or malformed IDs.
    """
    label, source, tool = kind
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise ValueError(
            f"{label}_id must be a non-empty string from {source}. "
            f"Run {tool} first"
        )
    key = raw_id.strip()
    entry = table.get(key)
    if entry is None:
        raise ValueError(
            f"Unknown {label}_id {key!r}. It may have expired after 24 "
            f"hours. Re-run {tool} for a new one"
        )
    if time.time() - entry["saved_at"] > TTL_SECONDS:
        del table[key]
        raise ValueError(
            f"{label.capitalize()} {key!r} expired after 24 hours. "
            f"Re-run {tool} for a new one"
        )
    return entry["payload"]


def save_result(payload: Dict[str, Any]) -> str:
    """Save a full access result, returning its result ID."""
    return _save(_results, uuid.uuid4().hex[:16], payload)


def save_draft(stage: Dict[str, Any]) -> str:
    """Save a staged access run, returning its draft ID."""
    return _save(_drafts, "draft_" + uuid.uuid4().hex[:12], stage)


def get_result(result_id: Any) -> Dict[str, Any]:
    """Return the saved payload for a result ID."""
    with _lock:
        return _lookup_locked(_results, _RESULT, result_id)


def get_draft(draft_id: Any) -> Dict[str, Any]:
    """Return the staged payload for a draft ID."""
    with _lock:
        return _lookup_locked(_drafts, _DRAFT, draft_id)


def set_draft_region(draft_id: Any, roi_bbox: Any) -> Dict[str, Any]:
    """Set the region on a staged draft, returning the updated stage."""
    with _lock:
        payload = _lookup_locked(_drafts, _DRAFT, draft_id)
        payload["roi_bbox"] = roi_bbox
        return payload


def set_draft_conical_proposal(draft_id: Any, proposal: Dict[str, Any]) -> Dict[str, Any]:
    """Attach the latest conical proposal to a staged draft."""
    with _lock:
        payload = _lookup_locked(_drafts, _DRAFT, draft_id)
        payload["conical_proposal"] = proposal
        return payload


def clear() -> None:
    """Drop every saved result and draft. Used by tests."""
    with _lock:
        _results.clear()
        _drafts.clear()


def count() -> int:
    """Return the number of stored results. Used by tests."""
    with _lock:
        return len(_results)


def paginate(items: List[Any], offset: Any, limit: Any, name: str) -> Dict[str, Any]:
    """Slice a list with model-friendly validation errors."""
    if isinstance(offset, bool):
        raise ValueError(f"{name} offset must be an integer")
    if isinstance(limit, bool):
        raise ValueError(f"{name} limit must be an integer")
    try:
        offset_value = int(offset)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} offset must be an integer") from exc
    try:
        limit_value = int(limit)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} limit must be an integer") from exc
    if offset_value < 0:
        raise ValueError(f"{name} offset must be 0 or more, got {offset!r}")
    if not 1 <= limit_value <= 366:
        raise ValueError(
            f"{name} limit must be between 1 and 366, got {limit!r}"
        )
    return {
        "offset": offset_value,
        "limit": limit_value,
        "total": len(items),
        "items": items[offset_value:offset_value + limit_value],
    }
