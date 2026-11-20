"""Build and serve a self-contained study export.

The export is the durable record: it carries the exact orbit inputs, the
accepted setup, software versions, assumptions, results, and a replay block.
The server still keeps the live result for 24 hours; the downloaded file is
what survives that window.
"""

from __future__ import annotations

import base64
import hmac
import json
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import Response

from . import result_store
from .conical_study import EVENT_FILTER_DEFINITION
from .coverage_view import public_base_url

EXPORT_SCHEMA_VERSION = "tatc-study-export-v1"
EXPORT_ROUTE = "/study-export/"
EXPORT_TTL_SECONDS = 15 * 60
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


def _mixed(payload: Dict[str, Any]) -> bool:
    return payload.get("result_kind") == "mixed_study"


def _region(payload: Dict[str, Any]) -> Dict[str, float]:
    west, south, east, north = (float(value) for value in payload["roi_bbox"])
    return {"west": west, "south": south, "east": east, "north": north}


def _result_block(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "summary_counts": payload.get("summary_counts"),
        "metrics": payload.get("metrics"),
        "daily": payload.get("daily", []),
        "cells": payload.get("grid_cells", payload.get("cells", {})),
        "events": payload.get("events", []),
        "event_spacing": payload.get("event_spacing", []),
        "event_filter": payload.get("event_filter"),
    }


def _results(payload: Dict[str, Any]) -> Dict[str, Any]:
    combined = _result_block(payload)
    if not _mixed(payload):
        return {"combined": combined}
    return {
        "combined": combined,
        "by_configuration": {
            configuration_id: _result_block(result)
            for configuration_id, result in payload.get(
                "configuration_results", {}
            ).items()
        },
    }


def _configurations(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    setup = payload.get("provenance", {}).get("setup_snapshot", {})
    if _mixed(payload):
        return [
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
                "orbit_direction_filter": item.get("orbit_direction_filter"),
                "warnings": [
                    warning
                    for warning in payload.get("warnings", [])
                    if str(warning).startswith(f"{item.get('configuration_id')}:")
                ],
            }
            for item in setup.get("configurations", [])
        ]
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
            "applicability_assumptions": setup.get(
                "applicability_assumptions", []
            ),
            "orbit_direction_filter": setup.get("orbit_direction_filter"),
            "warnings": payload.get("warnings", []),
        }
    ]


def _replay_configuration(value: Any) -> Any:
    """Drop TAT-C derived fields that the preparation schema does not accept."""
    if not isinstance(value, dict):
        return value
    return {
        key: item
        for key, item in value.items()
        if key not in ("tatc_mapping", "software")
    }


def _single_replay(payload: Dict[str, Any]) -> Dict[str, Any]:
    setup = payload.get("provenance", {}).get("setup_snapshot", {})
    window = setup.get("window", {})
    rows, columns = payload.get("grid_shape", [None, None])
    prepare = {"grid_rows": rows, "grid_columns": columns}
    if setup.get("registry_selection") is not None:
        prepare["configuration_id"] = payload.get("configuration_id")
        prepare["configuration_revision"] = payload.get("configuration_revision")
    else:
        prepare["configuration"] = _replay_configuration(
            setup.get("configuration")
        )
    direction = setup.get("orbit_direction_filter")
    if direction:
        prepare["orbit_direction"] = direction
    if setup.get("applicability_assumptions"):
        prepare["applicability_assumptions"] = setup["applicability_assumptions"]
    return {
        "begin": {
            "satellite_identifier": payload.get("satellite"),
            "start_time": window.get("start_utc"),
            "end_time": window.get("end_utc_exclusive"),
        },
        "region": _region(payload),
        "prepare": prepare,
    }


def _mixed_replay(payload: Dict[str, Any]) -> Dict[str, Any]:
    setup = payload.get("provenance", {}).get("setup_snapshot", {})
    window = setup.get("window", {})
    rows, columns = payload.get("grid_shape", [None, None])
    configurations = []
    assumptions = []
    for item in setup.get("configurations", []):
        selection = (
            {
                "configuration_id": item.get("configuration_id"),
                "configuration_revision": item.get("configuration_revision"),
            }
            if item.get("registry_selection") is not None
            else {"configuration": _replay_configuration(item.get("configuration"))}
        )
        if item.get("orbit_direction_filter"):
            selection["orbit_direction"] = item["orbit_direction_filter"]
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
        "region": _region(payload),
        "prepare": prepare,
    }


def _software(payload: Dict[str, Any]) -> Any:
    raw = payload.get("provenance", {}).get("software")
    if not isinstance(raw, list):
        return raw
    versions = [
        {
            key: value
            for key, value in item.items()
            if key not in ("configuration_id", "configuration_revision")
        }
        for item in raw
    ]
    unique = []
    for item in versions:
        if item not in unique:
            unique.append(item)
    return unique[0] if len(unique) == 1 else unique


def build_study_export(result_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return the complete, JSON-serializable study export."""
    setup = payload.get("provenance", {}).get("setup_snapshot", {})
    window = setup.get("window", {})
    document = {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "export_kind": "tatc-ai-study",
        "exported_at_utc": datetime.now(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z"),
        "study": {
            "result_id": result_id,
            "result_kind": payload.get("result_kind"),
            "accounting_method": payload.get("accounting_method"),
            "status": payload.get("completeness_status"),
            "window": {
                "start_utc": window.get("start_utc"),
                "end_utc_exclusive": window.get("end_utc_exclusive"),
            },
            "region": _region(payload),
            "grid": {
                "rows": payload.get("grid_shape", [None, None])[0],
                "columns": payload.get("grid_shape", [None, None])[1],
            },
            "applicability": payload.get("applicability"),
            "applicability_assumptions": payload.get(
                "applicability_assumptions", []
            ),
            "event_filter": payload.get("event_filter"),
            "event_filter_definition": EVENT_FILTER_DEFINITION,
            "warnings": payload.get("warnings", []),
            "limits": payload.get("limits", []),
            "definitions": payload.get("definitions", {}),
            "storage": payload.get("storage", {}),
            "evidence_note": payload.get("evidence_note"),
        },
        "configurations": _configurations(payload),
        "omissions": setup.get("omissions", []) if _mixed(payload) else [],
        "orbits": payload.get("orbits", []),
        "software": _software(payload),
        "results": _results(payload),
        "replay": _mixed_replay(payload) if _mixed(payload) else _single_replay(payload),
    }
    return _public(document)


def _token(result_id: str, expires_at: float) -> str:
    payload = f"{result_id}:{int(expires_at)}"
    signature = hmac.new(_SECRET, payload.encode(), "sha256").hexdigest()
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
        _SECRET, f"{result_id}:{expiry}".encode(), "sha256"
    ).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid export token")
    return result_id, expires_at


def export_link(result_id: Any) -> Dict[str, Any]:
    """Return a short-lived download link for one saved study."""
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
    expires_at = time.time() + EXPORT_TTL_SECONDS
    return {
        "result_id": key,
        "file_name": f"tatc-study-{key}.json",
        "expires_at_utc": _iso(expires_at),
        "download_url": public_base_url() + EXPORT_ROUTE + _token(key, expires_at),
    }


def _render_export(result_id: str, payload: Dict[str, Any]) -> str:
    return json.dumps(
        build_study_export(result_id, payload), indent=2, allow_nan=False
    )


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
        body = await run_in_threadpool(_render_export, result_id, payload)
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
