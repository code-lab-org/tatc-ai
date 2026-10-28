"""Staged single-configuration conical study through the temporary store.

The workflow mirrors the legacy staged access path: begin locks the
spacecraft and window, region sets the bounds, prepare resolves a sourced
conical configuration and returns an acceptance revision, and finish runs
only the accepted proposal.  TAT-C does all propagation and viewing-event
geometry; application code only validates inputs and applies the study's
UTC-day and spacing accounting.
"""

from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple

from pydantic import ValidationError

from .conical_accounting import (
    ACCOUNTING_METHOD,
    normalize_events,
    summarize_events,
)
from .conical_config import (
    SCHEMA_VERSION,
    ConicalConfiguration,
    configuration_from_snapshot,
    iso_utc,
    source_choices_from_snapshot,
)
from .conical_tatc import collect_cell_events, resolved_snapshot
from .coverage_core import (
    MAX_GRID_CELLS,
    MAX_YEAR_CELL_CONFIGURATIONS,
    MAX_YEAR_GRID_CELLS,
    MAX_YEAR_WORKERS,
    SHORT_WINDOW_MAX,
    _build_grid,
    _parse_utc,
    _public_satellites,
)
from .result_store import (
    get_draft as _get_draft,
    get_result as _get_saved,
    paginate as _paginate,
    save_result as _save_result,
    set_draft_conical_proposal as _set_proposal,
)

MAX_EVALUATED_CONFIGURATIONS = 1
# Below this many cell-days, starting worker processes costs more than it saves.
POOL_MIN_CELL_DAYS = 100

EVIDENCE_NOTE = (
    "Viewing events are modeled access at grid-cell centers. This does not "
    "confirm an actual acquisition, cloud-free pixels, or usable imagery."
)

BASE_LIMITS = [
    "Modeled orbital access is not confirmation of an actual instrument acquisition.",
    "Cloud conditions and retrieval quality are not modeled.",
    "Grid-cell center sampling is not pixel-resolution mapping; an observed cell does not mean every point in that cell or part of the ROI was viewed.",
    "The within-14-day window relative to the latest OMM epoch is our chosen request guardrail, not a guarantee of prediction accuracy.",
    "A daily observed area counts each visited cell once per day; it is not continuous observation time.",
    "Field-of-regard envelopes are retained as event metadata only. Their durations are not viewing duration or uncovered duration.",
    "Continuous viewing duration and continuous uncovered duration are unavailable for this event-based result.",
    "The established configuration applicability interval must contain the full requested window; this increment does not model partial or additional configurations.",
    "Configuration applicability is not orbit availability or a confirmed acquisition schedule; orbit freshness is checked separately.",
    "The server validates configuration structure, units, ranges, and evidence references; it does not authenticate source text or independently verify the researcher's conversation.",
    "This is a geometric-access and software-integration exercise, not mission validation or an acquisition schedule.",
    "Results and drafts expire 24 hours after saving; an earlier server restart clears them and capacity eviction can remove older entries.",
]

YEAR_LIMITS = [
    "Long-horizon runs propagate one OMM epoch far beyond the 14-day short-window guardrail; timing of individual events drifts and only aggregate statistics should be quoted.",
    f"Windows over 3 days allow at most {MAX_YEAR_GRID_CELLS} cells and {MAX_YEAR_CELL_CONFIGURATIONS} cells times evaluated configurations.",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _canonical(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _omm_hash(omm_records: Sequence[Dict[str, Any]]) -> str:
    return _sha256(list(omm_records))


def _parse_window(stage: Dict[str, Any]) -> Tuple[datetime, datetime]:
    return (
        _parse_utc(stage["window_start_utc"], "window_start_utc"),
        _parse_utc(stage["window_end_utc_exclusive"], "window_end_utc_exclusive"),
    )


def _check_grid_size(
    cells: Sequence[Dict[str, Any]], long_window: bool
) -> None:
    if not long_window:
        if len(cells) > MAX_GRID_CELLS:
            raise ValueError(f"Grid must contain no more than {MAX_GRID_CELLS} cells")
        return
    if len(cells) > MAX_YEAR_GRID_CELLS:
        raise ValueError(
            f"Grid must contain no more than {MAX_YEAR_GRID_CELLS} cells for "
            "windows over 3 days"
        )
    if len(cells) * MAX_EVALUATED_CONFIGURATIONS > MAX_YEAR_CELL_CONFIGURATIONS:
        raise ValueError(
            f"Windows over 3 days allow at most {MAX_YEAR_CELL_CONFIGURATIONS} "
            f"cells times evaluated configurations; {len(cells)} cells x "
            f"{MAX_EVALUATED_CONFIGURATIONS} configuration is too many"
        )


def _validate_applicability(
    configuration: ConicalConfiguration, start: datetime, end: datetime
) -> Tuple[datetime, datetime]:
    app_start = _parse_utc(
        iso_utc(configuration.applicability.start_utc), "applicability.start_utc"
    )
    app_end = _parse_utc(
        iso_utc(configuration.applicability.end_utc_exclusive),
        "applicability.end_utc_exclusive",
    )
    uncovered: List[str] = []
    if start < app_start:
        uncovered.append(
            f"{iso_utc(start)} to {iso_utc(min(end, app_start))} (before applicability)"
        )
    if end > app_end:
        uncovered.append(
            f"{iso_utc(max(start, app_end))} to {iso_utc(end)} (after applicability)"
        )
    if uncovered:
        raise ValueError(
            "The requested window is not fully covered by the configuration's "
            "established applicability interval "
            f"[{iso_utc(app_start)}, {iso_utc(app_end)}). Uncovered: "
            + "; ".join(uncovered)
            + ". This increment requires a fully covered window; partial-study "
            "applicability is not supported yet. Choose a window inside the "
            "interval or supply a different sourced configuration."
        )
    return app_start, app_end


def _consequential_payload(
    stage: Dict[str, Any],
    snapshot: Dict[str, Any],
    cells: Sequence[Dict[str, Any]],
    grid_rows: int,
    grid_columns: int,
) -> Dict[str, Any]:
    satellite = stage["satellites"][0]
    start, end = _parse_window(stage)
    return {
        "study_kind": "single_conical_configuration",
        "schema_version": SCHEMA_VERSION,
        "accounting_method": ACCOUNTING_METHOD,
        "window_start_utc": iso_utc(start),
        "window_end_utc_exclusive": iso_utc(end),
        "roi_bbox": [float(value) for value in stage["roi_bbox"]],
        "grid": {
            "rows": int(grid_rows),
            "columns": int(grid_columns),
            "cell_count": len(cells),
        },
        "satellite": {
            "name": str(satellite["satellite"]),
            "norad_id": int(satellite["norad_id"]),
            "omm_epoch_utc": satellite["omm_epoch_utc"],
            "omm_hash": _omm_hash(satellite["omm_records"]),
            "omm_mean_altitude_km": satellite["omm_mean_altitude_km"],
        },
        "configuration_digest": _sha256(snapshot),
        "configuration": snapshot,
        "source_choices": source_choices_from_snapshot(snapshot),
        "approximation_ids": [
            approximation["approximation_id"]
            for approximation in snapshot["approximations"]
        ],
        "applicability": {
            "start_utc": snapshot["applicability"]["start_utc"],
            "end_utc_exclusive": snapshot["applicability"]["end_utc_exclusive"],
        },
        "software": snapshot["software"],
        "evaluated_configuration_count": MAX_EVALUATED_CONFIGURATIONS,
    }


def _setup_revision(
    stage: Dict[str, Any],
    snapshot: Dict[str, Any],
    cells: Sequence[Dict[str, Any]],
    grid_rows: int,
    grid_columns: int,
) -> Tuple[str, Dict[str, Any]]:
    consequential = _consequential_payload(
        stage, snapshot, cells, grid_rows, grid_columns
    )
    return "sha256:" + _sha256(consequential), consequential


def _configuration_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        parts.append(f"{location}: {error['msg']}" if location else error["msg"])
    return "; ".join(parts)


def _load_configuration(configuration: Any) -> ConicalConfiguration:
    if isinstance(configuration, ConicalConfiguration):
        return configuration
    if not isinstance(configuration, dict):
        raise ValueError(
            "configuration must be one conical configuration object; "
            "multi-instrument and multi-configuration studies are not supported "
            "in this increment"
        )
    try:
        return ConicalConfiguration.model_validate(configuration)
    except ValidationError as exc:
        raise ValueError(f"Configuration rejected: {_configuration_error(exc)}") from exc


def _check_staged_spacecraft(
    stage: Dict[str, Any], configuration: ConicalConfiguration
) -> Dict[str, Any]:
    if len(stage["satellites"]) != 1:
        raise ValueError(
            "Conical studies support exactly one spacecraft in this increment. "
            "Start a new draft with one satellite identifier."
        )
    satellite = stage["satellites"][0]
    if int(satellite["norad_id"]) != int(configuration.spacecraft.norad_id):
        raise ValueError(
            "The configuration spacecraft "
            f"{configuration.spacecraft.name!r} (NORAD "
            f"{configuration.spacecraft.norad_id}) does not match the staged "
            f"satellite {satellite['satellite']!r} (NORAD {satellite['norad_id']}). "
            "Begin a new draft for the configured spacecraft."
        )
    stage_name = str(satellite["satellite"]).lower()
    names = [configuration.spacecraft.name, *configuration.spacecraft.aliases]
    if not any(name and name.lower() in stage_name for name in names):
        raise ValueError(
            f"Configuration spacecraft name {configuration.spacecraft.name!r} "
            f"and aliases {configuration.spacecraft.aliases!r} do not match the "
            f"orbit-provider name {satellite['satellite']!r}, even though the "
            "NORAD ID matches. Use the orbit-provider identity or add an alias "
            "before preparing the study."
        )
    return satellite


def _limits(stage: Dict[str, Any], configuration: ConicalConfiguration, long_window: bool) -> List[str]:
    start, end = _parse_window(stage)
    limits = [
        *BASE_LIMITS,
        "Configuration applicability is ["
        f"{iso_utc(configuration.applicability.start_utc)}, "
        f"{iso_utc(configuration.applicability.end_utc_exclusive)}); only the "
        "requested window is modeled.",
        f"Exactly one configuration and one spacecraft are evaluated in this increment; requested window length is {round((end - start).total_seconds() / 3600, 3)} hours.",
    ]
    if long_window:
        limits.extend(YEAR_LIMITS)
    return limits


def _warnings(
    configuration: ConicalConfiguration, proposal: Optional[bool] = False
) -> List[str]:
    warnings = configuration.warnings()
    if proposal:
        warnings.append(
            "No calculation has run. The proposal is awaiting the researcher's "
            "acceptance of this setup revision."
        )
    return warnings


def _proposal_payload(
    stage: Dict[str, Any],
    configuration: ConicalConfiguration,
    snapshot: Dict[str, Any],
    cells: Sequence[Dict[str, Any]],
    grid_rows: int,
    grid_columns: int,
    setup_revision: str,
    consequential: Dict[str, Any],
) -> Dict[str, Any]:
    start, end = _parse_window(stage)
    long_window = end - start > SHORT_WINDOW_MAX
    limits = _limits(stage, configuration, long_window)
    consequential_settings = [
        {
            "setting": "window",
            "value": {
                "start_utc": iso_utc(start),
                "end_utc_exclusive": iso_utc(end),
            },
            "why": "Event membership and daily accounting depend on the exact UTC window.",
        },
        {
            "setting": "region",
            "value": [float(value) for value in stage["roi_bbox"]],
            "why": "Cell centers and geodesic cell areas are derived from the region.",
        },
        {
            "setting": "grid",
            "value": consequential["grid"],
            "why": "Cell centers, cell identities, and area accounting depend on the grid.",
        },
        {
            "setting": "satellite_orbit",
            "value": consequential["satellite"],
            "why": "The orbit epoch and element set change propagation and event times.",
        },
        {
            "setting": "configuration",
            "value": {
                "configuration_id": configuration.configuration_id,
                "revision": configuration.revision,
                "digest": consequential["configuration_digest"],
            },
            "why": "Geometry, frames, evidence, interpolations, and approximations define the study.",
        },
        {
            "setting": "source_choices",
            "value": consequential["source_choices"],
            "why": "The chosen sector, angle interpretation, and frame must be the accepted ones.",
        },
        {
            "setting": "approximations",
            "value": consequential["approximation_ids"],
            "why": "Accepted approximations remain attached to the result.",
        },
        {
            "setting": "applicability",
            "value": consequential["applicability"],
            "why": "The full window must lie inside the established interval.",
        },
        {
            "setting": "software",
            "value": consequential["software"],
            "why": "A TAT-C or Python change can change numerical results.",
        },
    ]
    return {
        "draft_id": stage.get("draft_id"),
        "status": "awaiting_acceptance",
        "setup_revision": setup_revision,
        "satellite": stage["satellite"],
        "satellites": _public_satellites(stage),
        "instrument": configuration.instrument,
        "configuration_id": configuration.configuration_id,
        "configuration_revision": configuration.revision,
        "configuration": snapshot,
        "window_start_utc": iso_utc(start),
        "window_end_utc_exclusive": iso_utc(end),
        "window_duration_hours": round((end - start).total_seconds() / 3600, 3),
        "roi_bbox": [float(value) for value in stage["roi_bbox"]],
        "grid": {
            "rows": int(grid_rows),
            "columns": int(grid_columns),
            "cell_count": len(cells),
            "max_cell_axis_span_deg": round(
                max(
                    (
                        float(stage["roi_bbox"][2]) - float(stage["roi_bbox"][0])
                    )
                    / grid_columns,
                    (
                        float(stage["roi_bbox"][3]) - float(stage["roi_bbox"][1])
                    )
                    / grid_rows,
                ),
                6,
            ),
        },
        "consequential_settings": consequential_settings,
        "source_conflicts": snapshot["source_conflicts"],
        "approximations": snapshot["approximations"],
        "approximation_status": snapshot["approximation_status"],
        "geometry_checks": snapshot["geometry_checks"],
        "interpretation": snapshot["interpretation"],
        "warnings": _warnings(configuration, proposal=True),
        "limits": limits,
        "evidence_note": EVIDENCE_NOTE,
        "acceptance": {
            "required": True,
            "setup_revision": setup_revision,
            "instruction": (
                "Present the chosen source options, approximations, warnings, "
                "region, grid, and window to the researcher. If they accept, "
                "call the conical finish tool with this exact setup revision and "
                "accept_setup=true. Any change to a consequential setting "
                "requires a new preparation and acceptance."
            ),
        },
        "next_step": (
            "Ask the researcher to accept this proposal. If accepted, call the "
            "conical finish tool with draft_id, accepted_setup_revision set to "
            f"{setup_revision!r}, and accept_setup=true. Do not add legacy "
            "geometry arguments to the conical finish call."
        ),
    }


def prepare_conical_study(
    draft_id: Any,
    grid_rows: Any,
    grid_columns: Any,
    configuration: Any,
) -> Dict[str, Any]:
    """Resolve a sourced conical configuration into an acceptance proposal."""
    resolved_configuration = _load_configuration(configuration)
    stage = copy.deepcopy(_get_draft(draft_id))
    if stage.get("roi_bbox") is None:
        raise ValueError(
            "This draft has no region yet. Call the region tool with four "
            "separate numeric bounds before preparing a conical study"
        )
    _check_staged_spacecraft(stage, resolved_configuration)
    start, end = _parse_window(stage)
    _validate_applicability(resolved_configuration, start, end)
    cells = _build_grid(stage["roi_bbox"], grid_rows, grid_columns)
    _check_grid_size(cells, end - start > SHORT_WINDOW_MAX)
    try:
        snapshot = resolved_snapshot(
            resolved_configuration, stage["satellites"][0]["omm_records"]
        )
    except ValueError:
        raise
    except Exception as exc:  # TAT-C object construction failures
        raise ValueError(f"Could not resolve the conical configuration: {exc}") from exc
    setup_revision, consequential = _setup_revision(
        stage, snapshot, cells, grid_rows, grid_columns
    )
    proposal = _proposal_payload(
        stage,
        resolved_configuration,
        snapshot,
        cells,
        grid_rows,
        grid_columns,
        setup_revision,
        consequential,
    )
    proposal["draft_id"] = str(draft_id).strip()
    _set_proposal(
        draft_id,
        {
            "setup_revision": setup_revision,
            "grid_rows": int(grid_rows),
            "grid_columns": int(grid_columns),
            "configuration": snapshot,
            "consequential": consequential,
            "consequential_settings": proposal["consequential_settings"],
            "prepared_at_utc": iso_utc(_utcnow()),
        },
    )
    return proposal


def _tasks_for_cells(
    stage: Dict[str, Any],
    configuration: ConicalConfiguration,
    cells: Sequence[Dict[str, Any]],
    start: datetime,
    end: datetime,
) -> List[Dict[str, Any]]:
    omm_records = stage["satellites"][0]["omm_records"]
    configuration_payload = configuration.portable()
    return [
        {
            "configuration": configuration_payload,
            "omm_records": omm_records,
            "cell_id": int(cell["id"]),
            "latitude": float(cell["center_lat"]),
            "longitude": float(cell["center_lon"]),
            "start": start,
            "end": end,
        }
        for cell in cells
    ]


def _collect_tasks(tasks: Sequence[Dict[str, Any]], max_workers: int, work: float) -> List[Dict[str, Any]]:
    if max_workers == 1 or len(tasks) == 1 or work < POOL_MIN_CELL_DAYS:
        frames = [collect_cell_events(task) for task in tasks]
    else:
        with ProcessPoolExecutor(
            max_workers=min(max_workers, len(tasks)),
            mp_context=multiprocessing.get_context("spawn"),
        ) as pool:
            frames = list(pool.map(collect_cell_events, tasks))
    return [event for frame in frames for event in frame]


def _public_events(events: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {key: value for key, value in event.items() if not key.startswith("_")}
        for event in events
    ]


def _storage_note() -> Dict[str, Any]:
    return {
        "retention_hours": 24,
        "capacity_entries": 200,
        "restart_clears": True,
        "eviction": "least recently saved entries are removed when capacity is exceeded",
        "note": (
            "The result and its resolved configuration snapshot live together "
            "in the temporary in-memory store. There is no durable archive."
        ),
    }


def _result_payload(
    stage: Dict[str, Any],
    configuration: ConicalConfiguration,
    proposal: Dict[str, Any],
    cells: Sequence[Dict[str, Any]],
    events: Sequence[Dict[str, Any]],
    accounting: Dict[str, Any],
    accepted_setup_revision: str,
    accepted_at_utc: datetime,
) -> Dict[str, Any]:
    start, end = _parse_window(stage)
    snapshot = proposal["configuration"]
    long_window = end - start > SHORT_WINDOW_MAX
    public_events = _public_events(events)
    daily = accounting["daily"]
    summary_counts = {
        "total_days": len(daily),
        "days_with_events": sum(1 for row in daily if row["event_count"] > 0),
        "gap_days": sum(1 for row in daily if row["event_count"] == 0),
        "grid_cell_count": accounting["metrics"]["grid_cell_count"],
        "observed_cell_count": accounting["metrics"]["observed_cell_count"],
        "never_observed_cell_count": accounting["metrics"][
            "never_observed_cell_count"
        ],
        "event_count": accounting["metrics"]["event_count"],
        "envelope_period_count": accounting["metrics"]["envelope_period_count"],
    }
    provenance = {
        "setup_revision": accepted_setup_revision,
        "accepted_setup_revision": accepted_setup_revision,
        "accepted_at_utc": iso_utc(accepted_at_utc),
        "setup_snapshot": {
            "configuration": snapshot,
            "window": {
                "start_utc": iso_utc(start),
                "end_utc_exclusive": iso_utc(end),
            },
            "region": [float(value) for value in stage["roi_bbox"]],
            "grid": proposal["consequential"]["grid"],
            "satellite": proposal["consequential"]["satellite"],
            "source_choices": proposal["consequential"]["source_choices"],
            "approximation_ids": proposal["consequential"]["approximation_ids"],
            "applicability": proposal["consequential"]["applicability"],
            "consequential_settings": proposal["consequential_settings"],
        },
        "accepted_choices": proposal["consequential"]["source_choices"],
        "software": snapshot["software"],
        "orbit": {
            "norad_id": stage["satellites"][0]["norad_id"],
            "satellite": stage["satellites"][0]["satellite"],
            "omm_epoch_utc": stage["satellites"][0]["omm_epoch_utc"],
            "omm_age_hours": stage["satellites"][0]["omm_age_hours"],
            "omm_mean_altitude_km": stage["satellites"][0]["omm_mean_altitude_km"],
            "omm_hash": _omm_hash(stage["satellites"][0]["omm_records"]),
        },
    }
    return {
        "result_kind": "conical_study",
        "accounting_method": ACCOUNTING_METHOD,
        "setup_revision": accepted_setup_revision,
        "accepted_setup_revision": accepted_setup_revision,
        "satellite": stage["satellite"],
        "satellites": _public_satellites(stage),
        "instrument": configuration.instrument,
        "configuration_id": configuration.configuration_id,
        "configuration_revision": configuration.revision,
        "operating_mode": configuration.operating_mode,
        "channel": snapshot["channel"],
        "window_start_utc": iso_utc(start),
        "window_end_utc_exclusive": iso_utc(end),
        "window_duration_hours": round((end - start).total_seconds() / 3600, 3),
        "roi_bbox": [float(value) for value in stage["roi_bbox"]],
        "grid_shape": [proposal["grid_rows"], proposal["grid_columns"]],
        "total_roi_area_km2": accounting["total_roi_area_km2"],
        "summary_counts": summary_counts,
        "completeness_status": "complete",
        "modeled_configurations": [
            {
                "configuration_id": configuration.configuration_id,
                "configuration_revision": configuration.revision,
                "status": "modeled",
                "event_count": accounting["metrics"]["event_count"],
                "applicability": proposal["consequential"]["applicability"],
            }
        ],
        "omitted_configurations": [],
        "applicable_dates": {
            "start_utc": snapshot["applicability"]["start_utc"],
            "end_utc_exclusive": snapshot["applicability"]["end_utc_exclusive"],
        },
        "unresolved_dates": [],
        "consequential_settings": proposal["consequential_settings"],
        "provenance": provenance,
        "evidence": snapshot["evidence"],
        "source_revision": snapshot["source_revision"],
        "source_conflicts": snapshot["source_conflicts"],
        "approximations": snapshot["approximations"],
        "approximation_status": snapshot["approximation_status"],
        "geometry_checks": snapshot["geometry_checks"],
        "interpretation": snapshot["interpretation"],
        "warnings": _warnings(configuration),
        "limits": _limits(stage, configuration, long_window),
        "definitions": accounting["definitions"],
        "metrics": accounting["metrics"],
        "daily": daily,
        "grid_cells": accounting["cells"],
        "event_spacing": accounting["event_spacing"],
        "events": public_events,
        "method": {
            "observation_source": (
                "TAT-C 3.6.0 collect_observations with one ConicalInstrument and "
                "one satellite attitude velocity frame per grid-cell center."
            ),
            "event_source": (
                "One row per valid TAT-C viewing epoch; the row's field-of-regard "
                "start/end is retained only as envelope metadata."
            ),
            "spatial_approximation": (
                "Each whole grid cell's WGS84 geodesic area is classified using "
                "modeled events at its center only; this does not establish "
                "coverage of every point in the cell or ROI."
            ),
            "time_semantics": (
                "Half-open UTC window [start, end); a day counts a cell only when "
                "an event epoch falls in its half-open day interval."
            ),
            "event_spacing": (
                "Consecutive distinct viewing epochs at the same cell, in hours; "
                "null with fewer than two distinct events."
            ),
            "unavailable": (
                "No continuous viewing-duration or continuous uncovered-duration "
                "metric is derived from field-of-regard envelopes."
            ),
            "limits": _limits(stage, configuration, long_window),
        },
        "storage": _storage_note(),
        "evidence_note": EVIDENCE_NOTE,
    }


def _compact_summary(full: Dict[str, Any], result_id: str) -> Dict[str, Any]:
    return {
        "result_id": result_id,
        "result_kind": full["result_kind"],
        "accounting_method": full["accounting_method"],
        "setup_revision": full["setup_revision"],
        "satellite": full["satellite"],
        "instrument": full["instrument"],
        "configuration_id": full["configuration_id"],
        "configuration_revision": full["configuration_revision"],
        "operating_mode": full["operating_mode"],
        "channel": full["channel"],
        "window_start_utc": full["window_start_utc"],
        "window_end_utc_exclusive": full["window_end_utc_exclusive"],
        "window_duration_hours": full["window_duration_hours"],
        "roi_bbox": full["roi_bbox"],
        "grid_shape": full["grid_shape"],
        "total_roi_area_km2": full["total_roi_area_km2"],
        "summary_counts": full["summary_counts"],
        "completeness_status": full["completeness_status"],
        "modeled_configurations": full["modeled_configurations"],
        "omitted_configurations": full["omitted_configurations"],
        "applicable_dates": full["applicable_dates"],
        "unresolved_dates": full["unresolved_dates"],
        "metrics": full["metrics"],
        "definitions": full["definitions"],
        "approximation_status": full["approximation_status"],
        "source_conflicts": full["source_conflicts"],
        "approximations": full["approximations"],
        "geometry_checks": full["geometry_checks"],
        "interpretation": full["interpretation"],
        "warnings": full["warnings"],
        "limits": full["limits"],
        "provenance": full["provenance"],
        "storage": full["storage"],
        "evidence_note": full["evidence_note"],
        "fetch": (
            "Use the viewing-event fetch tool for paged events, the daily fetch "
            "for half-open UTC daily areas, the cell fetch for GeoJSON cells and "
            "per-cell event counts, the revisit fetch for event-spacing metrics, "
            "and the event-spacing fetch for paged per-cell spacing values. Do "
            "not describe envelope periods as continuous viewing. Results expire "
            "after 24 hours."
        ),
    }


def finish_conical_study(
    draft_id: Any,
    accepted_setup_revision: Any,
    accept_setup: Any,
    *,
    _max_workers: int = MAX_YEAR_WORKERS,
) -> Dict[str, Any]:
    """Run only a fully accepted, current conical proposal."""
    if not isinstance(accept_setup, bool):
        raise ValueError("accept_setup must be true or false")
    if accept_setup is not True:
        raise ValueError(
            "The conical setup must be explicitly accepted before calculation. "
            "Present the proposal to the researcher and call again with "
            "accept_setup=true only after they accept it."
        )
    if (
        not isinstance(accepted_setup_revision, str)
        or not accepted_setup_revision.strip()
    ):
        raise ValueError(
            "accepted_setup_revision must be the setup_revision returned by the "
            "conical preparation tool"
        )
    if (
        isinstance(_max_workers, bool)
        or not isinstance(_max_workers, int)
        or not 1 <= _max_workers <= MAX_YEAR_WORKERS
    ):
        raise ValueError(
            f"_max_workers must be an integer between 1 and {MAX_YEAR_WORKERS}"
        )
    # Freeze the accepted draft inputs before calculating. The store is shared
    # by concurrent tool calls, so later edits must not change the evidence
    # saved beside this result.
    stage = copy.deepcopy(_get_draft(draft_id))
    proposal = stage.get("conical_proposal")
    if not proposal:
        raise ValueError(
            "This draft has no prepared conical proposal. Call the conical "
            "preparation tool with the region, grid, and sourced configuration "
            "first."
        )
    proposed_revision = proposal["setup_revision"]
    accepted = accepted_setup_revision.strip()
    if accepted != proposed_revision:
        raise ValueError(
            f"Accepted setup revision {accepted!r} does not match the current "
            f"proposal {proposed_revision!r}. If any consequential setting "
            "changed, prepare the study again and obtain a new acceptance."
        )
    configuration = configuration_from_snapshot(proposal["configuration"])
    if stage.get("roi_bbox") is None:
        raise ValueError("This draft has no region; set the region before finishing")
    start, end = _parse_window(stage)
    _validate_applicability(configuration, start, end)
    cells = _build_grid(
        stage["roi_bbox"], proposal["grid_rows"], proposal["grid_columns"]
    )
    _check_grid_size(cells, end - start > SHORT_WINDOW_MAX)
    current_revision, _ = _setup_revision(
        stage,
        proposal["configuration"],
        cells,
        proposal["grid_rows"],
        proposal["grid_columns"],
    )
    if current_revision != proposed_revision:
        raise ValueError(
            "The prepared conical setup is stale: a consequential draft setting "
            "(region, window, orbit, grid, configuration, or software version) "
            "changed after preparation. Prepare the study again and obtain a new "
            "acceptance. No calculation was run."
        )
    tasks = _tasks_for_cells(stage, configuration, cells, start, end)
    work = len(tasks) * (end - start).total_seconds() / 86400
    raw_events = _collect_tasks(tasks, _max_workers, work)
    events = normalize_events(
        raw_events,
        start,
        end,
        applicability_start=configuration.applicability.start_utc,
        applicability_end=configuration.applicability.end_utc_exclusive,
    )
    accounting = summarize_events(cells, events, start, end)
    full = _result_payload(
        stage,
        configuration,
        proposal,
        cells,
        events,
        accounting,
        proposed_revision,
        _utcnow(),
    )
    result_id = _save_result(full)
    return _compact_summary(full, result_id)


def _conical_payload(result_id: Any) -> Tuple[str, Dict[str, Any]]:
    payload = _get_saved(result_id)
    if payload.get("result_kind") != "conical_study":
        raise ValueError(
            "This result is a legacy interval-based access run, not a conical "
            "event study. Use the legacy daily, cell, and revisit fetches; there "
            "is no viewing-event table for it."
        )
    return str(result_id).strip(), payload


def _header(result_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "result_id": result_id,
        "result_kind": payload["result_kind"],
        "accounting_method": payload["accounting_method"],
        "setup_revision": payload["setup_revision"],
        "satellite": payload["satellite"],
        "instrument": payload["instrument"],
        "configuration_id": payload["configuration_id"],
        "configuration_revision": payload["configuration_revision"],
        "window_start_utc": payload["window_start_utc"],
        "window_end_utc_exclusive": payload["window_end_utc_exclusive"],
        "provenance": payload["provenance"],
        "warnings": payload["warnings"],
        "limits": payload["limits"],
        "completeness_status": payload["completeness_status"],
        "modeled_configurations": payload["modeled_configurations"],
        "omitted_configurations": payload["omitted_configurations"],
        "applicable_dates": payload["applicable_dates"],
        "unresolved_dates": payload["unresolved_dates"],
        "definitions": payload["definitions"],
        "interpretation": payload["interpretation"],
        "evidence_note": payload["evidence_note"],
    }


def get_conical_daily(result_id: Any, offset: Any = 0, limit: Any = 31) -> Dict[str, Any]:
    _paginate([], offset, limit, "daily")
    result_id, payload = _conical_payload(result_id)
    page = _paginate(payload["daily"], offset, limit, "daily")
    return {
        **_header(result_id, payload),
        "offset": page["offset"],
        "limit": page["limit"],
        "total_days": page["total"],
        "daily": page["items"],
    }


def get_conical_cells(
    result_id: Any, include: Any = "all", offset: Any = 0, limit: Any = 144
) -> Dict[str, Any]:
    _paginate([], offset, limit, "cell")
    if not isinstance(include, str) or include.strip().lower() not in (
        "all",
        "observed",
        "gaps",
    ):
        raise ValueError(
            "include must be one of all, observed, or gaps, " f"got {include!r}"
        )
    result_id, payload = _conical_payload(result_id)
    wanted = include.strip().lower()
    features = []
    for cell in payload["grid_cells"]:
        is_observed = cell["event_count"] > 0
        if (wanted == "observed" and not is_observed) or (
            wanted == "gaps" and is_observed
        ):
            continue
        properties = {
            "id": cell["id"],
            "area_km2": cell["area_km2"],
            "status": "observed" if is_observed else "gap",
            "event_count": cell["event_count"],
            "observed_day_count": cell["observed_day_count"],
            "mean_event_spacing_hours": cell["mean_event_spacing_hours"],
            "event_spacing_sample_count": cell["event_spacing_sample_count"],
            "first_event_utc": cell["first_event_utc"],
            "last_event_utc": cell["last_event_utc"],
            "contributors": cell["contributors"],
            "configuration_id": payload["configuration_id"],
            "configuration_revision": payload["configuration_revision"],
            "geometry_status": payload["approximation_status"],
            "geometry_warning": "; ".join(payload["warnings"][:3]),
        }
        features.append(
            {
                "type": "Feature",
                "properties": properties,
                "geometry": cell["geometry"],
            }
        )
    page = _paginate(features, offset, limit, "cell")
    return {
        **_header(result_id, payload),
        "include": wanted,
        "offset": page["offset"],
        "limit": page["limit"],
        "matched_cells": page["total"],
        "cell_count": len(page["items"]),
        "total_cells": len(payload["grid_cells"]),
        "geojson": {
            "type": "FeatureCollection",
            "metadata": {
                "configuration_id": payload["configuration_id"],
                "configuration_revision": payload["configuration_revision"],
                "setup_revision": payload["setup_revision"],
                "accounting_method": payload["accounting_method"],
                "interpretation": payload["interpretation"],
                "definitions": payload["definitions"],
                "warnings": payload["warnings"],
                "provenance": payload["provenance"],
            },
            "features": page["items"],
        },
    }


def get_conical_revisit(result_id: Any) -> Dict[str, Any]:
    result_id, payload = _conical_payload(result_id)
    return {
        **_header(result_id, payload),
        "instrument": payload["instrument"],
        "window_duration_hours": payload["window_duration_hours"],
        "event_spacing": payload["metrics"],
        "unavailable_metrics": {
            "continuous_viewing_duration": payload["definitions"][
                "unavailable_metrics"
            ],
            "continuous_uncovered_duration": payload["definitions"][
                "unavailable_metrics"
            ],
        },
        "fetch": (
            "Use the event-spacing fetch tool for paged per-cell spacing values "
            "and the viewing-event fetch tool for individual epochs."
        ),
    }


def get_conical_events(
    result_id: Any, offset: Any = 0, limit: Any = 100
) -> Dict[str, Any]:
    _paginate([], offset, limit, "event")
    result_id, payload = _conical_payload(result_id)
    page = _paginate(payload["events"], offset, limit, "event")
    return {
        **_header(result_id, payload),
        "offset": page["offset"],
        "limit": page["limit"],
        "total_events": page["total"],
        "events": page["items"],
    }


def get_conical_event_spacing(
    result_id: Any, offset: Any = 0, limit: Any = 50
) -> Dict[str, Any]:
    _paginate([], offset, limit, "event_spacing")
    result_id, payload = _conical_payload(result_id)
    page = _paginate(payload["event_spacing"], offset, limit, "event_spacing")
    return {
        **_header(result_id, payload),
        "offset": page["offset"],
        "limit": page["limit"],
        "total_cells": page["total"],
        "event_spacing": page["items"],
    }
