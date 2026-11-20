"""Build and serve a self-contained study export.

The export is the durable record: it carries the exact orbit inputs, the
accepted setup, software versions, assumptions, results, and a replay block.
The server still keeps the live result for 24 hours; the downloaded file is
what survives that window.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import Response

from . import result_store
from .conical_study import EVENT_FILTER_DEFINITION

EXPORT_SCHEMA_VERSION = "tatc-study-export-v1"
EXPORT_ROUTE = "/study-export/"
_SECRET = secrets.token_bytes(32)


def _iso(value: float) -> str:
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )


def _public(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: _public(item)
            for key, item in value.items()
            if not str(key).startswith("_")
        }
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value


def _region(roi_bbox: List[float]) -> Dict[str, float]:
    west, south, east, north = (float(value) for value in roi_bbox)
    return {
        "west": west,
        "south": south,
        "east": east,
        "north": north,
    }


def _single_results(payload: Dict[str, Any]) -> Dict[str, Any]:
    combined = {
        "summary_counts": payload.get("summary_counts"),
        "metrics": payload.get("metrics"),
        "daily": payload.get("daily", []),
        "cells": payload.get("grid_cells", {}),
        "events": payload.get("events", []),
        "event_spacing": payload.get("event_spacing", []),
        "event_filter": payload.get("event_filter"),
    }
    configuration_id = payload.get("configuration_id")
    return {
        "combined": combined,
        "by_configuration": {
            str(configuration_id): combined,
        },
    }


def _mixed_results(payload: Dict[str, Any]) -> Dict[str, Any]:
    combined = {
        "summary_counts": payload.get("summary_counts"),
        "metrics": payload.get("metrics"),
        "daily": payload.get("daily", []),
        "cells": payload.get("cells", {}),
        "events": payload.get("events", []),
        "event_spacing": payload.get("event_spacing", []),
        "event_filter": payload.get("event_filter"),
    }
    by_configuration = {}
    for configuration_id, result in payload.get("configuration_results", {}).items():
        by_configuration[configuration_id] = {
            "summary_counts": result.get("summary_counts"),
            "metrics": result.get("metrics"),
            "daily": result.get("daily", []),
            "cells": result.get("cells", {}),
            "events": result.get("events", []),
            "event_spacing": result.get("event_spacing", []),
            "event_filter": result.get("event_filter"),
        }
    return {
        "combined": combined,
        "by_configuration": by_configuration,
    }


def _single_configurations(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    setup = payload.get("provenance", {}).get("setup_snapshot", {})
    configuration = setup.get("configuration")
    if not configuration:
        return []
    return [
        {
            "configuration_id": payload.get("configuration_id"),
            "configuration_revision": payload.get("configuration_revision"),
            "configuration": configuration,
            "satellite": setup.get("satellite"),
            "source_choices": setup.get("source_choices", []),
            "applicability": setup.get("applicability"),
            "orbit_direction_filter": payload.get("event_filter", {}).get(
                "orbit_direction"
            ),
            "warnings": payload.get("warnings", []),
        }
    ]


def _mixed_configurations(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    setup = payload.get("provenance", {}).get("setup_snapshot", {})
    result = []
    for item in setup.get("configurations", []):
        event_filter = (
            payload.get("configuration_results", {})
            .get(item.get("configuration_id"), {})
            .get("event_filter")
        )
        result.append(
            {
                "configuration_id": item.get("configuration_id"),
                "configuration_revision": item.get("configuration_revision"),
                "configuration": item.get("configuration"),
                "satellite": item.get("satellite"),
                "source_choices": item.get("source_choices", []),
                "applicability": item.get("applicability"),
                "applicability_assumptions": item.get(
                    "applicability_assumptions", []
                ),
                "orbit_direction_filter": (event_filter or {}).get(
                    "orbit_direction"
                ),
                "warnings": [
                    warning
                    for warning in payload.get("warnings", [])
                    if str(warning).startswith(f"{item.get('configuration_id')}:")
                ],
            }
        )
    return result


def _single_replay(payload: Dict[str, Any]) -> Dict[str, Any]:
    setup = payload.get("provenance", {}).get("setup_snapshot", {})
    window = setup.get("window", {})
    rows, columns = payload.get("grid_shape", [None, None])
    prepare = {
        "grid_rows": rows,
        "grid_columns": columns,
    }
    if payload.get("configuration_id"):
        prepare["configuration_id"] = payload.get("configuration_id")
        prepare["configuration_revision"] = payload.get("configuration_revision")
    else:
        prepare["configuration"] = setup.get("configuration")
    direction = payload.get("event_filter", {}).get("orbit_direction")
    if direction and direction != "both":
        prepare["orbit_direction"] = direction
    assumptions = setup.get("applicability_assumptions", [])
    if assumptions:
        prepare["applicability_assumptions"] = assumptions
    return {
        "begin": {
            "satellite_identifier": payload.get("satellite"),
            "start_time": window.get("start_utc"),
            "end_time": window.get("end_utc_exclusive"),
        },
        "region": _region(payload.get("roi_bbox", [0, 0, 0, 0])),
        "prepare": prepare,
    }


def _mixed_replay(payload: Dict[str, Any]) -> Dict[str, Any]:
    setup = payload.get("provenance", {}).get("setup_snapshot", {})
    window = setup.get("window", {})
    rows, columns = payload.get("grid_shape", [None, None])
    configurations = []
    assumptions = []
    for item in setup.get("configurations", []):
        direction = item.get("orbit_direction_filter")
        if item.get("registry_selection") is not None:
            selection = {
                "configuration_id": item.get("configuration_id"),
                "configuration_revision": item.get("configuration_revision"),
            }
        else:
            selection = {"configuration": item.get("configuration")}
        if direction:
            selection["orbit_direction"] = direction
        configurations.append(selection)
        assumptions.extend(item.get("applicability_assumptions", []))
    omissions = []
    for item in setup.get("omissions", []):
        if item.get("status") != "omitted":
            continue
        omissions.append(
            {
                key: item[key]
                for key in (
                    "instrument",
                    "spacecraft",
                    "norad_id",
                    "mode",
                    "status",
                    "reason_code",
                    "reason",
                    "question",
                    "evidence",
                )
                if key in item
            }
        )
    prepare = {
        "grid_rows": rows,
        "grid_columns": columns,
        "configurations": configurations,
    }
    if omissions:
        prepare["omissions"] = omissions
    if assumptions:
        prepare["applicability_assumptions"] = assumptions
    return {
        "begin": {
            "satellite_identifier": ", ".join(
                str(item.get("satellite")) for item in setup.get("satellites", [])
            ),
            "start_time": window.get("start_utc"),
            "end_time": window.get("end_utc_exclusive"),
        },
        "region": _region(payload.get("roi_bbox", [0, 0, 0, 0])),
        "prepare": prepare,
    }


def build_study_export(result_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return the complete, JSON-serializable study export."""
    mixed = payload.get("result_kind") == "mixed_study"
    setup = payload.get("provenance", {}).get("setup_snapshot", {})
    window = setup.get("window", {})
    study = {
        "result_id": result_id,
        "result_kind": payload.get("result_kind"),
        "accounting_method": payload.get("accounting_method"),
        "status": payload.get("completeness_status"),
        "window": {
            "start_utc": window.get("start_utc"),
            "end_utc_exclusive": window.get("end_utc_exclusive"),
        },
        "region": _region(payload.get("roi_bbox", [0, 0, 0, 0])),
        "grid": {
            "rows": payload.get("grid_shape", [None, None])[0],
            "columns": payload.get("grid_shape", [None, None])[1],
        },
        "applicability": payload.get("applicability"),
        "applicability_assumptions": payload.get("applicability_assumptions", []),
        "event_filter": payload.get("event_filter"),
        "event_filter_definition": EVENT_FILTER_DEFINITION,
        "warnings": payload.get("warnings", []),
        "limits": payload.get("limits", []),
        "definitions": payload.get("definitions", {}),
        "storage": payload.get("storage", {}),
        "evidence_note": payload.get("evidence_note"),
    }
    document = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "export_kind": "tatc-ai-study",
        "exported_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "study": study,
        "configurations": (
            _mixed_configurations(payload)
            if mixed
            else _single_configurations(payload)
        ),
        "omissions": setup.get("omissions", []) if mixed else [],
        "orbits": payload.get("orbits", []),
        "software": payload.get("provenance", {}).get("software"),
        "results": _mixed_results(payload) if mixed else _single_results(payload),
        "replay": _mixed_replay(payload) if mixed else _single_replay(payload),
    }
    document = _public(document)
    document["content_sha256"] = hashlib.sha256(
        json.dumps(
            document,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()
    return document


def _base_url() -> str:
    base = os.environ.get("TATC_VIEWER_BASE_URL") or os.environ.get(
        "MCP_BASE_URL", "http://localhost:8000"
    )
    parts = urlsplit(base)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "TATC_VIEWER_BASE_URL or MCP_BASE_URL must be a browser-accessible "
            "HTTP(S) server URL"
        )
    return base.rstrip("/")


def _token(result_id: str, expires_at: float) -> str:
    payload = f"{result_id}:{int(expires_at)}"
    signature = hmac.new(_SECRET, payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode().rstrip(
        "="
    )


def _verify(token: str) -> Tuple[str, float]:
    padded = token + "=" * (-len(token) % 4)
    try:
        decoded = base64.urlsafe_b64decode(padded).decode()
        result_id, expiry, signature = decoded.rsplit(":", 2)
        expires_at = float(expiry)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid export token") from exc
    expected = hmac.new(
        _SECRET, f"{result_id}:{expiry}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid export token")
    return result_id, expires_at


def export_link(result_id: Any) -> Dict[str, Any]:
    """Return download metadata for one saved result."""
    key = str(result_id).strip() if isinstance(result_id, str) else ""
    if not key:
        raise ValueError("result_id must be a non-empty string from a saved study")
    payload = result_store.get_result(key)
    if payload.get("result_kind") not in (
        "conical_study",
        "pointed_study",
        "mixed_study",
    ):
        raise ValueError(
            "export_study supports conical, pointed, or mixed studies"
        )
    ttl = result_store.result_ttl_seconds(key)
    expires_at = time.time() + max(1.0, min(ttl, 24 * 3600))
    return {
        "result_id": key,
        "file_name": f"tatc-study-{key}.json",
        "expires_at_utc": _iso(expires_at),
        "download_url": _base_url() + EXPORT_ROUTE + _token(key, expires_at),
    }


def register_export_route(mcp) -> None:
    @mcp.custom_route(EXPORT_ROUTE + "{token}", methods=["GET"])
    async def study_export(request: Request):
        try:
            result_id, expires_at = _verify(request.path_params["token"])
        except ValueError:
            return Response("Invalid export link", status_code=404)
        if time.time() > expires_at:
            return Response("This export link has expired", status_code=410)
        try:
            payload = result_store.get_result(result_id)
        except ValueError:
            return Response("This study result is no longer available", status_code=404)
        document = build_study_export(result_id, payload)
        body = json.dumps(document, indent=2, allow_nan=False)
        return Response(
            body,
            media_type="application/json",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="tatc-study-{result_id}.json"'
                ),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
