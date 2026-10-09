"""Saved access results, fetched back by ID.

A coverage run produces more rows and cells than a model should carry
in chat. The run tool saves the full result here and returns a compact
summary plus a result ID. Fetch tools page through daily rows, grid
cells, and revisit numbers using that ID.

The store is in-memory with a 24-hour expiry and a cap of 200 results.
The MCP server runs as a single uvicorn worker, so one process holds
every result. A multi-worker deploy would need a shared store instead.
"""

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

TTL_SECONDS = 24 * 3600
MAX_RESULTS = 200

_lock = threading.Lock()
_results: Dict[str, Dict[str, Any]] = {}
_drafts: Dict[str, Dict[str, Any]] = {}


def _evict_locked(now: float) -> None:
    expired = [
        key for key, entry in _results.items()
        if now - entry["saved_at"] > TTL_SECONDS
    ]
    for key in expired:
        del _results[key]
    while len(_results) > MAX_RESULTS:
        oldest = min(_results, key=lambda k: _results[k]["saved_at"])
        del _results[oldest]


def set_draft_region(draft_id: Any, roi_bbox: Any) -> Dict[str, Any]:
    """Set the region on a staged draft, returning the updated stage.

    Raises:
        ValueError: For unknown, expired, or malformed IDs.
    """
    if not isinstance(draft_id, str) or not draft_id.strip():
        raise ValueError(
            "draft_id must be a non-empty string from the begin tool. "
            "Run the begin tool first"
        )
    key = draft_id.strip()
    now = time.time()
    with _lock:
        entry = _drafts.get(key)
        if entry is None:
            raise ValueError(
                f"Unknown draft_id {key!r}. It may have expired after 24 "
                "hours. Re-run the begin tool for a new one"
            )
        if now - entry["saved_at"] > TTL_SECONDS:
            del _drafts[key]
            raise ValueError(
                f"Draft {key!r} expired after 24 hours. "
                "Re-run the begin tool for a new one"
            )
        entry["payload"]["roi_bbox"] = roi_bbox
        return entry["payload"]


def save_result(payload: Dict[str, Any]) -> str:
    """Save a full access result, returning its result ID."""
    result_id = uuid.uuid4().hex[:16]
    now = time.time()
    with _lock:
        _results[result_id] = {"saved_at": now, "payload": payload}
        _evict_locked(now)
    return result_id


def save_draft(stage: Dict[str, Any]) -> str:
    """Save a staged access run, returning its draft ID."""
    draft_id = "draft_" + uuid.uuid4().hex[:12]
    now = time.time()
    with _lock:
        _drafts[draft_id] = {"saved_at": now, "payload": stage}
        _evict_drafts_locked(now)
    return draft_id


def _evict_drafts_locked(now: float) -> None:
    expired = [
        key for key, entry in _drafts.items()
        if now - entry["saved_at"] > TTL_SECONDS
    ]
    for key in expired:
        del _drafts[key]
    while len(_drafts) > MAX_RESULTS:
        oldest = min(_drafts, key=lambda k: _drafts[k]["saved_at"])
        del _drafts[oldest]


def get_draft(draft_id: Any) -> Dict[str, Any]:
    """Return the staged payload for a draft ID.

    Raises:
        ValueError: For unknown, expired, or malformed IDs.
    """
    if not isinstance(draft_id, str) or not draft_id.strip():
        raise ValueError(
            "draft_id must be a non-empty string from the begin tool. "
            "Run the begin tool first"
        )
    key = draft_id.strip()
    now = time.time()
    with _lock:
        entry = _drafts.get(key)
        if entry is None:
            raise ValueError(
                f"Unknown draft_id {key!r}. It may have expired after 24 "
                "hours. Re-run the begin tool for a new one"
            )
        if now - entry["saved_at"] > TTL_SECONDS:
            del _drafts[key]
            raise ValueError(
                f"Draft {key!r} expired after 24 hours. "
                "Re-run the begin tool for a new one"
            )
        return entry["payload"]


def get_result(result_id: Any) -> Dict[str, Any]:
    """Return the saved payload for a result ID.

    Raises:
        ValueError: For unknown, expired, or malformed IDs.
    """
    if not isinstance(result_id, str) or not result_id.strip():
        raise ValueError(
            "result_id must be a non-empty string from a coverage run. "
            "Run the access analysis tool first"
        )
    key = result_id.strip()
    now = time.time()
    with _lock:
        entry = _results.get(key)
        if entry is None:
            raise ValueError(
                f"Unknown result_id {key!r}. It may have expired after 24 "
                "hours. Re-run the access analysis tool for a new one"
            )
        if now - entry["saved_at"] > TTL_SECONDS:
            del _results[key]
            raise ValueError(
                f"Result {key!r} expired after 24 hours. "
                "Re-run the access analysis tool for a new one"
            )
        return entry["payload"]


def describe_result(result_id: str) -> Dict[str, Any]:
    """Return the ID plus its age, without the payload."""
    payload = get_result(result_id)
    with _lock:
        saved_at = _results[result_id.strip()]["saved_at"]
    return {
        "result_id": result_id.strip(),
        "age_hours": round((time.time() - saved_at) / 3600, 3),
        "satellite": payload.get("satellite"),
        "window_start_utc": payload.get("window_start_utc"),
        "window_end_utc_exclusive": payload.get("window_end_utc_exclusive"),
    }


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


def result_ids() -> List[str]:
    """Return stored IDs. Used by tests."""
    with _lock:
        return list(_results.keys())


def _saved_at(result_id: str) -> Optional[float]:
    with _lock:
        entry = _results.get(result_id)
        return entry["saved_at"] if entry else None
