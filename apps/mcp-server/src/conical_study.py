"""Staged conical, pointed, and mixed instrument studies.

The workflow mirrors the legacy staged access path: begin locks the
spacecraft and window, region sets the bounds, prepare resolves a sourced
instrument configuration and returns an acceptance revision, and finish runs
only the accepted proposal. TAT-C does all propagation and viewing-event
geometry; application code only validates inputs and applies UTC-day and
spacing accounting.
"""

from __future__ import annotations

import copy
import hashlib
import json
import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from pydantic import ValidationError

from .conical_accounting import (
    ACCOUNTING_METHOD,
    EVENT_DEFINITION,
    MIXED_ACCOUNTING_METHOD,
    MIXED_CONFIGURATION_ACCOUNTING_METHOD,
    POINTED_ACCOUNTING_METHOD,
    POINTED_EVENT_DEFINITION,
    normalize_mixed_events,
    normalize_events,
    summarize_events,
)
from .configuration_registry import resolve_instrument_configuration
from .conical_config import (
    SCHEMA_VERSION,
    ConicalConfiguration,
    configuration_from_snapshot as conical_configuration_from_snapshot,
    iso_utc,
    source_choices_from_snapshot,
)
from .instrument_tatc import collect_cell_events, resolved_snapshot
from .pointed_config import (
    PointedConfiguration,
    configuration_from_snapshot as pointed_configuration_from_snapshot,
)
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
    set_draft_study_proposal as _set_proposal,
)

InstrumentConfiguration = Union[ConicalConfiguration, PointedConfiguration]

MAX_EVALUATED_CONFIGURATIONS = 1
MAX_MIXED_CONFIGURATIONS = 6
MAX_MIXED_EVENT_PAGE_SIZE = 20
MAX_MIXED_CELL_PAGE_SIZE = 10
MAX_MIXED_SPACING_PAGE_SIZE = 20
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
    configuration: InstrumentConfiguration, start: datetime, end: datetime
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
    registry_selection: Optional[Dict[str, Any]] = None,
    study_kind: str = "conical",
) -> Dict[str, Any]:
    satellite = stage["satellites"][0]
    start, end = _parse_window(stage)
    return {
        "study_kind": f"single_{study_kind}_configuration",
        "schema_version": snapshot["schema_version"],
        "accounting_method": (
            POINTED_ACCOUNTING_METHOD
            if study_kind == "pointed"
            else ACCOUNTING_METHOD
        ),
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
        "registry_selection": registry_selection,
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
    registry_selection: Optional[Dict[str, Any]] = None,
    study_kind: str = "conical",
) -> Tuple[str, Dict[str, Any]]:
    consequential = _consequential_payload(
        stage, snapshot, cells, grid_rows, grid_columns, registry_selection, study_kind
    )
    return "sha256:" + _sha256(consequential), consequential


def _configuration_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"])
        parts.append(f"{location}: {error['msg']}" if location else error["msg"])
    return "; ".join(parts)


def _load_configuration(configuration: Any, study_kind: str) -> InstrumentConfiguration:
    expected = PointedConfiguration if study_kind == "pointed" else ConicalConfiguration
    if isinstance(configuration, expected):
        return configuration
    if not isinstance(configuration, dict):
        if study_kind == "conical":
            raise ValueError(
                "configuration must be one conical configuration object; "
                "multi-instrument and multi-configuration studies are not supported "
                "in this increment"
            )
        raise ValueError(
            f"configuration must be one {study_kind} configuration object; "
            "multiple configured instruments are not supported in this increment"
        )
    try:
        return expected.model_validate(configuration)
    except ValidationError as exc:
        raise ValueError(f"Configuration rejected: {_configuration_error(exc)}") from exc


def _check_staged_spacecraft(
    stage: Dict[str, Any],
    configuration: InstrumentConfiguration,
    study_kind: str,
) -> Dict[str, Any]:
    if len(stage["satellites"]) != 1:
        raise ValueError(
            f"{study_kind.capitalize()} studies support exactly one spacecraft "
            "in this increment. "
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


def _limits(
    stage: Dict[str, Any],
    configuration: InstrumentConfiguration,
    long_window: bool,
) -> List[str]:
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
    configuration: InstrumentConfiguration,
    proposal: Optional[bool] = False,
    registry_selection: Optional[Dict[str, Any]] = None,
) -> List[str]:
    warnings = configuration.warnings()
    registry_selection = registry_selection or {}
    if registry_selection.get("source") == "source_refresh_candidate":
        warnings.append(
            "This configuration is a source-refresh candidate supplied for this "
            "server session. A shared registry revision requires a reviewed "
            "repository change."
        )
    for check in registry_selection.get("source_change_notices", []):
        if check.get("status") == "geometry_changed":
            changes = ", ".join(
                f"{item['parameter']}: {item['previous_value']} -> {item['new_value']}"
                for item in check.get("geometry_changes", [])
            )
            warnings.append(
                f"An explicit source refresh reports changed geometry in revision "
                f"{check.get('new_revision')}: {changes}. Choose and accept one "
                "exact configuration revision for this study."
            )
        elif check.get("status") == "applicability_changed":
            changes = ", ".join(
                f"{item['parameter']}: {item['previous_value']} -> "
                f"{item['new_value']}"
                for item in check.get("applicability_changes", [])
            )
            warnings.append(
                "An explicit source refresh reports changed applicability in "
                f"revision {check.get('new_revision')}: {changes}. Choose and "
                "accept one exact configuration revision for this study."
            )
        elif check.get("status") == "documentation_changed":
            warnings.append(
                "An explicit source refresh reports a documentation change without "
                "a recorded geometry change. Review its source evidence before use."
            )
    for check in registry_selection.get("refresh_uncertainty", []):
        warnings.append(
            "An explicit source refresh failed; the current source state is unknown. "
            f"Failure: {check.get('failure_reason')}. The selected revision remains "
            "limited to its recorded applicability and evidence."
        )
    if proposal:
        warnings.append(
            "No calculation has run. The proposal is awaiting the researcher's "
            "acceptance of this setup revision."
        )
    return warnings


def _proposal_payload(
    stage: Dict[str, Any],
    configuration: InstrumentConfiguration,
    snapshot: Dict[str, Any],
    cells: Sequence[Dict[str, Any]],
    grid_rows: int,
    grid_columns: int,
    setup_revision: str,
    consequential: Dict[str, Any],
    registry_selection: Optional[Dict[str, Any]] = None,
    study_kind: str = "conical",
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
    if registry_selection is not None:
        consequential_settings.append(
            {
                "setting": "registry_revision",
                "value": {
                    "configuration_id": registry_selection["configuration_id"],
                    "revision": registry_selection["revision"],
                    "source": registry_selection["source"],
                    "source_change_notices": registry_selection[
                        "source_change_notices"
                    ],
                    "refresh_uncertainty": registry_selection["refresh_uncertainty"],
                },
                "why": "The exact source revision and known source changes define the configuration accepted for this study.",
            }
        )
    return {
        "draft_id": stage.get("draft_id"),
        "status": "awaiting_acceptance",
        "setup_revision": setup_revision,
        "satellite": stage["satellite"],
        "satellites": _public_satellites(stage),
        "instrument": configuration.instrument,
        "configuration_id": configuration.configuration_id,
        "configuration_revision": configuration.revision,
        "registry_selection": registry_selection,
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
        "warnings": _warnings(
            configuration, proposal=True, registry_selection=registry_selection
        ),
        "limits": limits,
        "evidence_note": EVIDENCE_NOTE,
        "acceptance": {
            "required": True,
            "setup_revision": setup_revision,
            "instruction": (
                "Present the chosen source options, approximations, warnings, "
                "region, grid, and window to the researcher. If they accept, "
                f"call the {study_kind} finish tool with this exact setup revision and "
                "accept_setup=true. Any change to a consequential setting "
                "requires a new preparation and acceptance."
            ),
        },
        "next_step": (
            "Ask the researcher to accept this proposal. If accepted, call the "
            f"{study_kind} finish tool with draft_id, accepted_setup_revision set to "
            f"{setup_revision!r}, and accept_setup=true. Do not add legacy "
            f"geometry arguments to the {study_kind} finish call."
        ),
    }


def _prepare_study(
    draft_id: Any,
    grid_rows: Any,
    grid_columns: Any,
    configuration: Any = None,
    configuration_id: Optional[str] = None,
    configuration_revision: Optional[str] = None,
    *,
    study_kind: str,
) -> Dict[str, Any]:
    """Resolve one sourced instrument configuration into a proposal."""
    registry_selection = None
    if configuration_id is not None or configuration_revision is not None:
        if configuration is not None:
            raise ValueError(
                "Supply either a study-specific configuration or an exact registry "
                "configuration_id and configuration_revision, not both"
            )
        if configuration_id is None:
            raise ValueError(
                "configuration_id is required when selecting a registry revision"
            )
        resolved_configuration, registry_selection = resolve_instrument_configuration(
            configuration_id, configuration_revision
        )
        expected = PointedConfiguration if study_kind == "pointed" else ConicalConfiguration
        if not isinstance(resolved_configuration, expected):
            raise ValueError(
                f"Registry ID {configuration_id!r} does not identify a {study_kind} "
                "configuration. Choose an exact revision from the matching lookup."
            )
    else:
        if configuration is None:
            raise ValueError(
                "Supply a study-specific configuration or an exact registry "
                "configuration_id and configuration_revision"
            )
        resolved_configuration = _load_configuration(configuration, study_kind)
    stage = copy.deepcopy(_get_draft(draft_id))
    if stage.get("mixed_proposal"):
        raise ValueError(
            "This draft already has a mixed proposal. Use the mixed finish tool "
            "with its accepted setup revision, or start a new draft for a "
            "single-configuration study."
        )
    if stage.get("roi_bbox") is None:
        raise ValueError(
            f"This draft has no region yet. Call the region tool with four "
            f"separate numeric bounds before preparing a {study_kind} study"
        )
    _check_staged_spacecraft(stage, resolved_configuration, study_kind)
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
        raise ValueError(
            f"Could not resolve the {study_kind} configuration: {exc}"
        ) from exc
    setup_revision, consequential = _setup_revision(
        stage,
        snapshot,
        cells,
        grid_rows,
        grid_columns,
        registry_selection,
        study_kind,
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
        registry_selection,
        study_kind,
    )
    proposal["draft_id"] = str(draft_id).strip()
    _set_proposal(
        draft_id,
        study_kind,
        {
            "setup_revision": setup_revision,
            "grid_rows": int(grid_rows),
            "grid_columns": int(grid_columns),
            "configuration": snapshot,
            "registry_selection": registry_selection,
            "consequential": consequential,
            "consequential_settings": proposal["consequential_settings"],
            "prepared_at_utc": iso_utc(_utcnow()),
        },
    )
    return proposal


def prepare_conical_study(
    draft_id: Any,
    grid_rows: Any,
    grid_columns: Any,
    configuration: Any = None,
    configuration_id: Optional[str] = None,
    configuration_revision: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a sourced conical configuration into an acceptance proposal."""
    return _prepare_study(
        draft_id,
        grid_rows,
        grid_columns,
        configuration,
        configuration_id,
        configuration_revision,
        study_kind="conical",
    )


def prepare_pointed_study(
    draft_id: Any,
    grid_rows: Any,
    grid_columns: Any,
    configuration: Any = None,
    configuration_id: Optional[str] = None,
    configuration_revision: Optional[str] = None,
) -> Dict[str, Any]:
    """Resolve a sourced pointed configuration into an acceptance proposal."""
    return _prepare_study(
        draft_id,
        grid_rows,
        grid_columns,
        configuration,
        configuration_id,
        configuration_revision,
        study_kind="pointed",
    )


def _resolve_mixed_configuration(selection: Any) -> Tuple[
    InstrumentConfiguration, Optional[Dict[str, Any]]
]:
    if not isinstance(selection, dict):
        raise ValueError("Each mixed configuration must be an object")
    if "configuration_id" in selection or "configuration_revision" in selection:
        if set(selection) != {"configuration_id", "configuration_revision"}:
            raise ValueError(
                "A registry selection must contain only configuration_id and "
                "configuration_revision; do not combine it with a study-specific "
                "configuration."
            )
        if not selection.get("configuration_id") or not selection.get(
            "configuration_revision"
        ):
            raise ValueError(
                "Mixed registry selections require both configuration_id and "
                "configuration_revision. Choose an exact revision from lookup."
            )
        configuration, registry_selection = resolve_instrument_configuration(
            selection["configuration_id"], selection["configuration_revision"]
        )
        if not isinstance(configuration, (ConicalConfiguration, PointedConfiguration)):
            raise ValueError(
                f"Registry ID {selection['configuration_id']!r} is not a supported "
                "conical or pointed configuration."
            )
        return configuration, registry_selection

    if set(selection) != {"configuration"}:
        raise ValueError(
            "Each mixed configuration must be either an exact registry selection "
            "with configuration_id and configuration_revision, or a complete "
            "study-specific configuration under configuration."
        )
    raw = selection["configuration"]
    if not isinstance(raw, dict):
        raise ValueError("A study-specific configuration must be an object")
    version = raw.get("schema_version")
    expected = {
        "conical-configuration-v1": ConicalConfiguration,
        "pointed-configuration-v1": PointedConfiguration,
    }.get(version)
    if expected is None:
        raise ValueError(
            f"Unsupported mixed configuration schema_version {version!r}; use a "
            "supported conical or pointed configuration."
        )
    try:
        return expected.model_validate(raw), None
    except ValidationError as exc:
        raise ValueError(
            f"Configuration rejected: {_configuration_error(exc)}"
        ) from exc


def _check_mixed_spacecraft(
    stage: Dict[str, Any],
    configurations: Sequence[InstrumentConfiguration],
) -> Dict[int, Dict[str, Any]]:
    staged_by_id = {int(item["norad_id"]): item for item in stage["satellites"]}
    configured_ids = {int(item.spacecraft.norad_id) for item in configurations}
    if len(configured_ids) > MAX_MIXED_CONFIGURATIONS:
        raise ValueError(
            f"A mixed study may use at most {MAX_MIXED_CONFIGURATIONS} spacecraft."
        )
    if configured_ids != set(staged_by_id):
        missing = sorted(configured_ids - set(staged_by_id))
        extra = sorted(set(staged_by_id) - configured_ids)
        details = []
        if missing:
            details.append("missing staged NORAD IDs " + ", ".join(map(str, missing)))
        if extra:
            details.append("staged spacecraft without a selected configuration " + ", ".join(map(str, extra)))
        raise ValueError(
            "Every staged spacecraft must match at least one selected configuration "
            "and every selected configuration must match a staged spacecraft: "
            + "; ".join(details)
            + ". Begin a new draft with the exact spacecraft set."
        )
    for configuration in configurations:
        satellite = staged_by_id[int(configuration.spacecraft.norad_id)]
        stage_name = str(satellite["satellite"]).lower()
        names = [configuration.spacecraft.name, *configuration.spacecraft.aliases]
        if not any(name and name.lower() in stage_name for name in names):
            raise ValueError(
                f"Configuration {configuration.configuration_id!r} names spacecraft "
                f"{configuration.spacecraft.name!r} and aliases "
                f"{configuration.spacecraft.aliases!r}, but staged NORAD "
                f"{satellite['norad_id']} resolves to {satellite['satellite']!r}. "
                "Correct the identity or add a sourced alias."
            )
    return staged_by_id


def _mixed_consequential_payload(
    stage: Dict[str, Any],
    prepared_configurations: Sequence[Dict[str, Any]],
    grid_rows: int,
    grid_columns: int,
    cells: Sequence[Dict[str, Any]],
) -> Dict[str, Any]:
    start, end = _parse_window(stage)
    return {
        "study_kind": "mixed_instrument_configuration_study",
        "window_start_utc": iso_utc(start),
        "window_end_utc_exclusive": iso_utc(end),
        "roi_bbox": [float(value) for value in stage["roi_bbox"]],
        "grid": {
            "rows": int(grid_rows),
            "columns": int(grid_columns),
            "cell_count": len(cells),
        },
        "satellites": [
            {
                "norad_id": int(item["norad_id"]),
                "satellite": item["satellite"],
                "omm_epoch_utc": item["omm_epoch_utc"],
                "omm_age_hours": item["omm_age_hours"],
                "omm_hash": _omm_hash(item["omm_records"]),
                "omm_mean_altitude_km": item["omm_mean_altitude_km"],
            }
            for item in stage["satellites"]
        ],
        "configurations": [
            {
                "configuration_id": item["configuration_id"],
                "configuration_revision": item["configuration_revision"],
                "configuration_digest": item["configuration_digest"],
                "registry_selection": item["registry_selection"],
                "source_choices": item["source_choices"],
                "applicability": item["applicability"],
                "tatc_mapping": item["configuration"]["tatc_mapping"],
            }
            for item in prepared_configurations
        ],
        "evaluated_configuration_count": len(prepared_configurations),
        "spacecraft_count": len(stage["satellites"]),
        "software": [item["configuration"]["software"] for item in prepared_configurations],
    }


def prepare_mixed_study(
    draft_id: Any,
    grid_rows: Any,
    grid_columns: Any,
    configurations: Any,
) -> Dict[str, Any]:
    """Resolve and accept a bounded set of conical and pointed configurations."""
    if not isinstance(configurations, list) or not configurations:
        raise ValueError("configurations must contain between 1 and 6 selections")
    if len(configurations) > MAX_MIXED_CONFIGURATIONS:
        raise ValueError(
            f"A mixed study may evaluate at most {MAX_MIXED_CONFIGURATIONS} "
            "instrument configurations. Reduce the selection and prepare again."
        )

    resolved: List[Tuple[InstrumentConfiguration, Optional[Dict[str, Any]]]] = [
        _resolve_mixed_configuration(selection) for selection in configurations
    ]
    identities: Dict[str, Tuple[str, str, int]] = {}
    for configuration, _registry in resolved:
        identity = (
            configuration.revision,
            configuration.instrument,
            int(configuration.spacecraft.norad_id),
        )
        previous = identities.get(configuration.configuration_id)
        if previous is not None:
            if previous == identity:
                raise ValueError(
                    f"Configuration {configuration.configuration_id!r} was selected "
                    "more than once. Remove the duplicate selection."
                )
            raise ValueError(
                f"Configuration ID {configuration.configuration_id!r} has "
                "inconsistent revisions or instrument identity in this study. "
                "Select one exact revision for each configuration ID."
            )
        identities[configuration.configuration_id] = identity

    stage = copy.deepcopy(_get_draft(draft_id))
    conflicting_kinds = [
        kind
        for kind in ("conical", "pointed")
        if stage.get(f"{kind}_proposal")
    ]
    if conflicting_kinds:
        raise ValueError(
            "This draft already has a single-configuration "
            f"{', '.join(conflicting_kinds)} proposal. Start a new draft for a "
            "mixed study so its acceptance applies to one workflow."
        )
    if stage.get("roi_bbox") is None:
        raise ValueError(
            "This draft has no region yet. Set the region before preparing a mixed study."
        )
    staged_by_id = _check_mixed_spacecraft(
        stage, [configuration for configuration, _ in resolved]
    )
    start, end = _parse_window(stage)
    long_window = end - start > SHORT_WINDOW_MAX
    cells = _build_grid(stage["roi_bbox"], grid_rows, grid_columns)
    if not long_window and len(cells) > MAX_GRID_CELLS:
        raise ValueError(f"Grid must contain no more than {MAX_GRID_CELLS} cells")
    if long_window:
        if len(cells) > MAX_YEAR_GRID_CELLS:
            raise ValueError(
                f"Windows over 3 days allow at most {MAX_YEAR_GRID_CELLS} cells."
            )
        workload = len(cells) * len(resolved)
        if workload > MAX_YEAR_CELL_CONFIGURATIONS:
            raise ValueError(
                f"Windows over 3 days allow at most {MAX_YEAR_CELL_CONFIGURATIONS} "
                "cells times evaluated configurations; "
                f"{len(cells)} cells x {len(resolved)} configurations = "
                f"{workload}. Reduce the grid or configuration list."
            )

    prepared_configurations = []
    for configuration, registry_selection in resolved:
        app_start, app_end = _validate_applicability(configuration, start, end)
        satellite = staged_by_id[int(configuration.spacecraft.norad_id)]
        try:
            snapshot = resolved_snapshot(configuration, satellite["omm_records"])
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(
                f"Could not resolve configuration "
                f"{configuration.configuration_id!r} through TAT-C: {exc}"
            ) from exc
        satellite_snapshot = _public_satellites({"satellites": [satellite]})[0]
        satellite_snapshot["omm_hash"] = _omm_hash(satellite["omm_records"])
        prepared_configurations.append(
            {
                "configuration_id": configuration.configuration_id,
                "configuration_revision": configuration.revision,
                "instrument": configuration.instrument,
                "spacecraft": configuration.spacecraft.model_dump(mode="json"),
                "satellite": satellite_snapshot,
                "configuration": snapshot,
                "configuration_digest": _sha256(snapshot),
                "registry_selection": registry_selection,
                "source_choices": source_choices_from_snapshot(snapshot),
                "applicability": {
                    "start_utc": iso_utc(app_start),
                    "end_utc_exclusive": iso_utc(app_end),
                },
                "warnings": _warnings(configuration, registry_selection=registry_selection),
            }
        )

    consequential = _mixed_consequential_payload(
        stage, prepared_configurations, grid_rows, grid_columns, cells
    )
    revision = "sha256:" + _sha256(consequential)
    limits = [
        *BASE_LIMITS,
        *(YEAR_LIMITS if long_window else []),
        f"This mixed study evaluates {len(prepared_configurations)} configurations "
        f"across {len(staged_by_id)} spacecraft.",
        f"At most {MAX_MIXED_CONFIGURATIONS} configurations and "
        f"{MAX_MIXED_CONFIGURATIONS} spacecraft are allowed.",
        (
            f"Windows up to 3 days allow at most {MAX_GRID_CELLS} cells."
            if not long_window
            else f"Windows over 3 days allow at most {MAX_YEAR_GRID_CELLS} cells and "
            f"{MAX_YEAR_CELL_CONFIGURATIONS} cells times configurations."
        ),
        f"No more than {MAX_YEAR_WORKERS} workers are used; each worker evaluates "
        "one configuration and cell at a time.",
    ]
    proposal = {
        "status": "awaiting_acceptance",
        "draft_id": str(draft_id).strip(),
        "setup_revision": revision,
        "satellite": stage["satellite"],
        "satellites": _public_satellites(stage),
        "window_start_utc": iso_utc(start),
        "window_end_utc_exclusive": iso_utc(end),
        "window_duration_hours": round((end - start).total_seconds() / 3600, 3),
        "roi_bbox": [float(value) for value in stage["roi_bbox"]],
        "grid": {
            **consequential["grid"],
            "max_cell_axis_span_deg": round(
                max(
                    (float(stage["roi_bbox"][2]) - float(stage["roi_bbox"][0]))
                    / int(grid_columns),
                    (float(stage["roi_bbox"][3]) - float(stage["roi_bbox"][1]))
                    / int(grid_rows),
                ),
                6,
            ),
        },
        "evaluated_configuration_count": len(prepared_configurations),
        "spacecraft_count": len(staged_by_id),
        "configurations": prepared_configurations,
        "consequential_settings": consequential,
        "warnings": [
            f"{item['configuration_id']}: {warning}"
            for item in prepared_configurations
            for warning in item["warnings"]
        ]
        + ["No calculation has run; accept this exact setup revision to execute."],
        "limits": limits,
        "acceptance": {
            "required": True,
            "setup_revision": revision,
            "instruction": (
                "Present each configuration, revision, source, illumination setting, "
                "assumption, warning, region, grid, and window. Run only after the "
                "researcher accepts this exact setup revision."
            ),
        },
        "next_step": (
            "After acceptance, call the mixed finish tool with this draft_id and "
            "exact setup_revision. Do not add legacy geometry arguments."
        ),
    }
    _set_proposal(
        draft_id,
        "mixed",
        {
            "setup_revision": revision,
            "grid_rows": int(grid_rows),
            "grid_columns": int(grid_columns),
            "configurations": prepared_configurations,
            "consequential": consequential,
            "proposal": proposal,
            "prepared_at_utc": iso_utc(_utcnow()),
        },
    )
    return proposal


def _configuration_from_mixed_snapshot(snapshot: Dict[str, Any]) -> InstrumentConfiguration:
    if snapshot.get("schema_version") == SCHEMA_VERSION:
        return conical_configuration_from_snapshot(snapshot)
    if snapshot.get("schema_version") == "pointed-configuration-v1":
        return pointed_configuration_from_snapshot(snapshot)
    raise ValueError(
        f"Unsupported mixed configuration schema_version "
        f"{snapshot.get('schema_version')!r}"
    )


def _mixed_tasks_for_cells(
    stage: Dict[str, Any],
    configurations: Sequence[Dict[str, Any]],
    cells: Sequence[Dict[str, Any]],
    start: datetime,
    end: datetime,
) -> List[Dict[str, Any]]:
    staged_by_id = {int(item["norad_id"]): item for item in stage["satellites"]}
    tasks = []
    for item in configurations:
        configuration = _configuration_from_mixed_snapshot(item["configuration"])
        satellite = staged_by_id[int(configuration.spacecraft.norad_id)]
        for cell in cells:
            tasks.append(
                {
                    "configuration": configuration.portable(),
                    "omm_records": satellite["omm_records"],
                    "cell_id": int(cell["id"]),
                    "latitude": float(cell["center_lat"]),
                    "longitude": float(cell["center_lon"]),
                    "start": start,
                    "end": end,
                }
            )
    return tasks


def _event_summary_counts(accounting: Dict[str, Any]) -> Dict[str, int]:
    daily = accounting["daily"]
    metrics = accounting["metrics"]
    return {
        "total_days": len(daily),
        "days_with_events": sum(row["event_count"] > 0 for row in daily),
        "gap_days": sum(row["event_count"] == 0 for row in daily),
        "grid_cell_count": metrics["grid_cell_count"],
        "observed_cell_count": metrics["observed_cell_count"],
        "never_observed_cell_count": metrics["never_observed_cell_count"],
        "event_count": metrics["event_count"],
        "envelope_period_count": metrics["envelope_period_count"],
    }


def _mixed_result_payload(
    stage: Dict[str, Any],
    proposal: Dict[str, Any],
    cells: Sequence[Dict[str, Any]],
    raw_events: Sequence[Dict[str, Any]],
    start: datetime,
    end: datetime,
    accepted_at_utc: datetime,
) -> Dict[str, Any]:
    configuration_results: Dict[str, Dict[str, Any]] = {}
    configuration_summaries = []
    modeled_configurations = []
    accepted_choices = []
    all_warnings = []

    for item in proposal["configurations"]:
        configuration = _configuration_from_mixed_snapshot(item["configuration"])
        configuration_id = item["configuration_id"]
        config_raw_events = [
            event
            for event in raw_events
            if event["configuration_id"] == configuration_id
        ]
        normalized = normalize_events(
            config_raw_events,
            start,
            end,
            applicability_start=configuration.applicability.start_utc,
            applicability_end=configuration.applicability.end_utc_exclusive,
        )
        contributor = {
            "configuration_id": configuration_id,
            "configuration_revision": configuration.revision,
            "instrument": configuration.instrument,
            "satellite": item["satellite"]["satellite"],
        }
        for event in normalized:
            event["contributor_configurations"] = [contributor]
        accounting = summarize_events(
            cells,
            normalized,
            start,
            end,
            accounting_method=MIXED_CONFIGURATION_ACCOUNTING_METHOD,
            event_definition=(
                f"{EVENT_DEFINITION if isinstance(configuration, ConicalConfiguration) else POINTED_EVENT_DEFINITION} "
                f"Configuration {configuration_id!r}, revision {configuration.revision}."
            ),
        )
        counts = _event_summary_counts(accounting)
        result = {
            "configuration_id": configuration_id,
            "configuration_revision": configuration.revision,
            "instrument": configuration.instrument,
            "satellite": item["satellite"]["satellite"],
            "configuration": item["configuration"],
            "registry_selection": item["registry_selection"],
            "warnings": item["warnings"],
            "summary_counts": counts,
            **accounting,
            "events": normalized,
        }
        configuration_results[configuration_id] = result
        configuration_summaries.append(
            {
                "configuration_id": configuration_id,
                "configuration_revision": configuration.revision,
                "instrument": configuration.instrument,
                "satellite": item["satellite"]["satellite"],
                "summary_counts": counts,
                "metrics": accounting["metrics"],
                "warnings": item["warnings"],
            }
        )
        modeled_configurations.append(
            {
                "configuration_id": configuration_id,
                "configuration_revision": configuration.revision,
                "instrument": configuration.instrument,
                "satellite": item["satellite"]["satellite"],
                "status": "modeled",
                "event_count": accounting["metrics"]["event_count"],
                "applicability": item["applicability"],
            }
        )
        accepted_choices.append(
            {
                "configuration_id": configuration_id,
                "configuration_revision": configuration.revision,
                "choices": item["source_choices"],
            }
        )
        all_warnings.extend(
            f"{configuration_id}: {warning}" for warning in item["warnings"]
        )

    combined_events = normalize_mixed_events(raw_events, start, end)
    combined = summarize_events(
        cells,
        combined_events,
        start,
        end,
        accounting_method=MIXED_ACCOUNTING_METHOD,
        event_definition=(
            "The exact-time union of valid TAT-C conical and pointed viewing epochs "
            "at each cell center. Simultaneous contributors are retained together; "
            "nearby epochs remain distinct."
        ),
    )
    combined["definitions"]["combined_daily_area"] = (
        "For each half-open UTC day, count the WGS84 geodesic area of the set union "
        "of cell IDs visited by any selected configuration. A cell contributes once."
    )
    combined["definitions"]["combined_contributors"] = (
        "Each combined event retains every configuration that contributed at the "
        "same cell and exact viewing epoch. Events at different epochs are not merged."
    )
    combined_counts = _event_summary_counts(combined)
    start_utc, end_utc = iso_utc(start), iso_utc(end)
    long_window = end - start > SHORT_WINDOW_MAX
    limits = [
        *BASE_LIMITS,
        *(YEAR_LIMITS if long_window else []),
        f"The result evaluates {len(proposal['configurations'])} configurations "
        f"across {len(stage['satellites'])} spacecraft.",
        f"No more than {MAX_MIXED_CONFIGURATIONS} configurations or spacecraft "
        "are supported.",
        (
            f"Windows up to 3 days allow at most {MAX_GRID_CELLS} cells."
            if not long_window
            else f"Windows over 3 days allow at most {MAX_YEAR_GRID_CELLS} cells and "
            f"{MAX_YEAR_CELL_CONFIGURATIONS} cells times configurations."
        ),
        f"Execution uses one worker pool with at most {MAX_YEAR_WORKERS} workers; "
        "workers do not start nested pools.",
    ]
    provenance = {
        "setup_revision": proposal["setup_revision"],
        "accepted_setup_revision": proposal["setup_revision"],
        "accepted_at_utc": iso_utc(accepted_at_utc),
        "setup_snapshot": {
            "configurations": [
                {
                    "configuration_id": item["configuration_id"],
                    "configuration_revision": item["configuration_revision"],
                    "configuration": item["configuration"],
                    "registry_selection": item["registry_selection"],
                    "source_choices": item["source_choices"],
                    "applicability": item["configuration"]["applicability"],
                    "satellite": item["satellite"],
                }
                for item in proposal["configurations"]
            ],
            "window": {"start_utc": start_utc, "end_utc_exclusive": end_utc},
            "region": [float(value) for value in stage["roi_bbox"]],
            "grid": proposal["consequential"]["grid"],
            "satellites": proposal["consequential"]["satellites"],
        },
        "accepted_choices": accepted_choices,
        "software": [
            {
                "configuration_id": item["configuration_id"],
                "configuration_revision": item["configuration_revision"],
                **item["configuration"]["software"],
            }
            for item in proposal["configurations"]
        ],
    }
    summary_counts = {
        **combined_counts,
        "configuration_count": len(proposal["configurations"]),
        "spacecraft_count": len(stage["satellites"]),
    }
    return {
        "result_kind": "mixed_study",
        "accounting_method": MIXED_ACCOUNTING_METHOD,
        "setup_revision": proposal["setup_revision"],
        "accepted_setup_revision": proposal["setup_revision"],
        "satellite": stage["satellite"],
        "satellites": _public_satellites(stage),
        "configuration_id": None,
        "configuration_revision": None,
        "configurations": proposal["configurations"],
        "configuration_summaries": configuration_summaries,
        "configuration_results": configuration_results,
        "window_start_utc": start_utc,
        "window_end_utc_exclusive": end_utc,
        "window_duration_hours": round((end - start).total_seconds() / 3600, 3),
        "roi_bbox": [float(value) for value in stage["roi_bbox"]],
        "grid_shape": [proposal["grid_rows"], proposal["grid_columns"]],
        "total_roi_area_km2": combined["total_roi_area_km2"],
        "summary_counts": summary_counts,
        "completeness_status": "complete",
        "modeled_configurations": modeled_configurations,
        "omitted_configurations": [],
        "applicable_dates": {
            "start_utc": start_utc,
            "end_utc_exclusive": end_utc,
        },
        "unresolved_dates": [],
        "consequential_settings": proposal["consequential"],
        "provenance": provenance,
        "warnings": all_warnings,
        "limits": limits,
        "definitions": combined["definitions"],
        "metrics": combined["metrics"],
        "daily": combined["daily"],
        "cells": combined["cells"],
        "grid_cells": combined["cells"],
        "event_spacing": combined["event_spacing"],
        "events": combined_events,
        "method": {
            "observation_source": (
                "TAT-C collect_observations runs once per selected configuration and "
                "grid-cell center with that configuration's geometry, orbit, and "
                "illumination settings."
            ),
            "combined_coverage": combined["definitions"]["combined_daily_area"],
            "event_spacing": combined["definitions"]["event_spacing"],
            "unavailable": combined["definitions"]["unavailable_metrics"],
        },
        "storage": _storage_note(),
        "evidence_note": EVIDENCE_NOTE,
    }


def _compact_mixed_summary(full: Dict[str, Any], result_id: str) -> Dict[str, Any]:
    return {
        "result_id": result_id,
        "result_kind": full["result_kind"],
        "accounting_method": full["accounting_method"],
        "setup_revision": full["setup_revision"],
        "accepted_setup_revision": full["accepted_setup_revision"],
        "satellite": full["satellite"],
        "satellites": full["satellites"],
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
        "configuration_summaries": full["configuration_summaries"],
        "metrics": full["metrics"],
        "definitions": full["definitions"],
        "warnings": full["warnings"],
        "limits": full["limits"],
        "provenance": full["provenance"],
        "storage": full["storage"],
        "evidence_note": full["evidence_note"],
        "fetch": (
            "Omit configuration_id to fetch the combined cell union. Pass one exact "
            "configuration_id to each mixed event, daily, cell, revisit, or spacing "
            "tool for that configuration's contribution. Results expire after 24 hours."
        ),
    }


def finish_mixed_study(
    draft_id: Any,
    accepted_setup_revision: Any,
    accept_setup: Any,
    *,
    _max_workers: int = MAX_YEAR_WORKERS,
) -> Dict[str, Any]:
    """Run only the current accepted mixed-configuration proposal."""
    if not isinstance(accept_setup, bool):
        raise ValueError("accept_setup must be true or false")
    if not accept_setup:
        raise ValueError(
            "The mixed study must be explicitly accepted before calculation. "
            "Present the proposal and call again with accept_setup=true only after acceptance."
        )
    if not isinstance(accepted_setup_revision, str) or not accepted_setup_revision.strip():
        raise ValueError(
            "accepted_setup_revision must match the setup_revision returned by mixed preparation"
        )
    if (
        isinstance(_max_workers, bool)
        or not isinstance(_max_workers, int)
        or not 1 <= _max_workers <= MAX_YEAR_WORKERS
    ):
        raise ValueError(f"_max_workers must be an integer between 1 and {MAX_YEAR_WORKERS}")

    stage = copy.deepcopy(_get_draft(draft_id))
    conflicting_kinds = [
        kind
        for kind in ("conical", "pointed")
        if stage.get(f"{kind}_proposal")
    ]
    if conflicting_kinds:
        raise ValueError(
            "This draft also has a single-configuration "
            f"{', '.join(conflicting_kinds)} proposal. Use a separate draft for "
            "each accepted workflow."
        )
    proposal = stage.get("mixed_proposal")
    if not proposal:
        raise ValueError(
            "This draft has no prepared mixed proposal. Call mixed preparation with "
            "the region, grid, and exact configuration selections first."
        )
    if accepted_setup_revision.strip() != proposal["setup_revision"]:
        raise ValueError(
            "Accepted setup revision does not match the current mixed proposal. "
            "Prepare again and obtain acceptance of the updated setup. No calculation was run."
        )
    if stage.get("roi_bbox") is None:
        raise ValueError("This draft has no region; set the region before finishing")

    configurations = [
        _configuration_from_mixed_snapshot(item["configuration"])
        for item in proposal["configurations"]
    ]
    staged_by_id = _check_mixed_spacecraft(stage, configurations)
    start, end = _parse_window(stage)
    long_window = end - start > SHORT_WINDOW_MAX
    cells = _build_grid(stage["roi_bbox"], proposal["grid_rows"], proposal["grid_columns"])
    if not long_window and len(cells) > MAX_GRID_CELLS:
        raise ValueError(f"Grid must contain no more than {MAX_GRID_CELLS} cells")
    if long_window and (
        len(cells) > MAX_YEAR_GRID_CELLS
        or len(cells) * len(configurations) > MAX_YEAR_CELL_CONFIGURATIONS
    ):
        raise ValueError(
            f"Windows over 3 days allow at most {MAX_YEAR_GRID_CELLS} cells and "
            f"{MAX_YEAR_CELL_CONFIGURATIONS} cells times configurations."
        )
    if len(configurations) > MAX_MIXED_CONFIGURATIONS or len(staged_by_id) > MAX_MIXED_CONFIGURATIONS:
        raise ValueError("Mixed studies support at most six configurations and six spacecraft")
    for configuration in configurations:
        _validate_applicability(configuration, start, end)

    for item in proposal["configurations"]:
        selected = item.get("registry_selection")
        if selected is None:
            continue
        _, current = resolve_instrument_configuration(
            selected["configuration_id"], selected["revision"]
        )
        material_keys = (
            "configuration_id",
            "revision",
            "source",
            "base_revision",
            "source_change_notices",
            "refresh_uncertainty",
            "shared_registry_update_required",
        )
        if _canonical({key: current.get(key) for key in material_keys}) != _canonical(
            {key: selected.get(key) for key in material_keys}
        ):
            raise ValueError(
                f"Configuration {item['configuration_id']!r} has new source-change "
                "information since preparation. Prepare again and obtain acceptance "
                "of the updated notice. No calculation was run."
            )

    current_consequential = _mixed_consequential_payload(
        stage,
        proposal["configurations"],
        proposal["grid_rows"],
        proposal["grid_columns"],
        cells,
    )
    current_revision = "sha256:" + _sha256(current_consequential)
    if current_revision != proposal["setup_revision"]:
        raise ValueError(
            "The prepared mixed setup is stale: a consequential draft setting "
            "changed after preparation. Prepare again and obtain a new acceptance. "
            "No calculation was run."
        )

    tasks = _mixed_tasks_for_cells(
        stage, proposal["configurations"], cells, start, end
    )
    work = len(tasks) * (end - start).total_seconds() / 86400
    raw_events = _collect_tasks(tasks, _max_workers, work)
    full = _mixed_result_payload(
        stage, proposal, cells, raw_events, start, end, _utcnow()
    )
    result_id = _save_result(full)
    return _compact_mixed_summary(full, result_id)


def _tasks_for_cells(
    stage: Dict[str, Any],
    configuration: InstrumentConfiguration,
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
    configuration: InstrumentConfiguration,
    proposal: Dict[str, Any],
    cells: Sequence[Dict[str, Any]],
    events: Sequence[Dict[str, Any]],
    accounting: Dict[str, Any],
    accepted_setup_revision: str,
    accepted_at_utc: datetime,
    study_kind: str,
    accounting_method: str,
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
    result_kind = f"{study_kind}_study"
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
            "registry_selection": proposal.get("registry_selection"),
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
        "result_kind": result_kind,
        "accounting_method": accounting_method,
        "setup_revision": accepted_setup_revision,
        "accepted_setup_revision": accepted_setup_revision,
        "satellite": stage["satellite"],
        "satellites": _public_satellites(stage),
        "instrument": configuration.instrument,
        "configuration_id": configuration.configuration_id,
        "configuration_revision": configuration.revision,
        "registry_selection": proposal.get("registry_selection"),
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
        "warnings": _warnings(
            configuration, registry_selection=proposal.get("registry_selection")
        ),
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
                if study_kind == "conical"
                else "TAT-C 3.6.0 collect_observations with one PointedInstrument "
                "using the accepted frame and attitude assumptions per grid-cell center."
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
        "registry_selection": full["registry_selection"],
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
            (
                "Use the viewing-event fetch tool for paged events, the daily fetch "
                "for half-open UTC daily areas, the cell fetch for GeoJSON cells and "
                "per-cell event counts, the revisit fetch for event-spacing metrics, "
                "and the event-spacing fetch for paged per-cell spacing values. "
            )
            if full["result_kind"] == "conical_study"
            else (
                "Use get_pointed_events, get_pointed_daily, get_pointed_cells, "
                "get_pointed_revisit and get_pointed_event_spacing to page through "
                "the saved pointed result. "
            )
        )
        + (
            "Do not describe field-of-regard periods as continuous viewing. Results "
            "expire after 24 hours."
        ),
    }


def _finish_study(
    draft_id: Any,
    accepted_setup_revision: Any,
    accept_setup: Any,
    *,
    study_kind: str,
    _max_workers: int = MAX_YEAR_WORKERS,
) -> Dict[str, Any]:
    """Run only the current accepted proposal for one instrument family."""
    if not isinstance(accept_setup, bool):
        raise ValueError("accept_setup must be true or false")
    if accept_setup is not True:
        raise ValueError(
            f"The {study_kind} setup must be explicitly accepted before calculation. "
            "Present the proposal to the researcher and call again with "
            "accept_setup=true only after they accept it."
        )
    if (
        not isinstance(accepted_setup_revision, str)
        or not accepted_setup_revision.strip()
    ):
        raise ValueError(
            "accepted_setup_revision must be the setup_revision returned by the "
            f"{study_kind} preparation tool"
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
    if stage.get("mixed_proposal"):
        raise ValueError(
            "This draft has a mixed proposal. Use finish_mixed_study with its "
            "accepted setup revision."
        )
    proposal = stage.get(f"{study_kind}_proposal")
    if not proposal:
        raise ValueError(
            f"This draft has no prepared {study_kind} proposal. Call the "
            f"{study_kind} preparation tool with the region, grid, and sourced configuration "
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
    if proposal["configuration"]["schema_version"] == "pointed-configuration-v1":
        configuration = pointed_configuration_from_snapshot(proposal["configuration"])
    elif proposal["configuration"]["schema_version"] == SCHEMA_VERSION:
        configuration = conical_configuration_from_snapshot(proposal["configuration"])
    else:
        raise ValueError("Prepared proposal has an unsupported configuration schema")
    expected = PointedConfiguration if study_kind == "pointed" else ConicalConfiguration
    if not isinstance(configuration, expected):
        raise ValueError("Prepared proposal does not match its instrument study kind")
    registry_selection = proposal.get("registry_selection")
    if registry_selection is not None:
        _, current_selection = resolve_instrument_configuration(
            registry_selection["configuration_id"], registry_selection["revision"]
        )
        material_keys = (
            "configuration_id",
            "revision",
            "source",
            "base_revision",
            "source_change_notices",
            "refresh_uncertainty",
            "shared_registry_update_required",
        )
        if _canonical(
            {key: current_selection.get(key) for key in material_keys}
        ) != _canonical(
            {key: registry_selection.get(key) for key in material_keys}
        ):
            raise ValueError(
                "The selected registry revision has new source-change information "
                "since preparation. Prepare again and obtain acceptance of the "
                "updated notice. No calculation was run."
            )
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
        registry_selection,
        study_kind,
    )
    if current_revision != proposed_revision:
        raise ValueError(
            f"The prepared {study_kind} setup is stale: a consequential draft setting "
            "(region, window, orbit, grid, configuration, or software version) "
            "changed after preparation. Prepare the study again and obtain a new "
            "acceptance. No calculation was run."
        )
    tasks = _tasks_for_cells(stage, configuration, cells, start, end)
    work = len(tasks) * (end - start).total_seconds() / 86400
    raw_events = _collect_tasks(tasks, _max_workers, work)
    accounting_method = (
        POINTED_ACCOUNTING_METHOD if study_kind == "pointed" else ACCOUNTING_METHOD
    )
    events = normalize_events(
        raw_events,
        start,
        end,
        applicability_start=configuration.applicability.start_utc,
        applicability_end=configuration.applicability.end_utc_exclusive,
    )
    accounting = summarize_events(
        cells,
        events,
        start,
        end,
        accounting_method=accounting_method,
        event_definition=(
            POINTED_EVENT_DEFINITION if study_kind == "pointed" else EVENT_DEFINITION
        ),
    )
    full = _result_payload(
        stage,
        configuration,
        proposal,
        cells,
        events,
        accounting,
        proposed_revision,
        _utcnow(),
        study_kind,
        accounting_method,
    )
    result_id = _save_result(full)
    return _compact_summary(full, result_id)


def finish_conical_study(
    draft_id: Any,
    accepted_setup_revision: Any,
    accept_setup: Any,
    *,
    _max_workers: int = MAX_YEAR_WORKERS,
) -> Dict[str, Any]:
    """Run only a fully accepted, current conical proposal."""
    return _finish_study(
        draft_id,
        accepted_setup_revision,
        accept_setup,
        study_kind="conical",
        _max_workers=_max_workers,
    )


def finish_pointed_study(
    draft_id: Any,
    accepted_setup_revision: Any,
    accept_setup: Any,
    *,
    _max_workers: int = MAX_YEAR_WORKERS,
) -> Dict[str, Any]:
    """Run only a fully accepted, current pointed proposal."""
    return _finish_study(
        draft_id,
        accepted_setup_revision,
        accept_setup,
        study_kind="pointed",
        _max_workers=_max_workers,
    )


def _study_payload(
    result_id: Any, expected_result_kind: str
) -> Tuple[str, Dict[str, Any]]:
    payload = _get_saved(result_id)
    if payload.get("result_kind") != expected_result_kind:
        if expected_result_kind == "conical_study":
            raise ValueError(
                "This result is a legacy interval-based access run, not a conical "
                "event study. Use the legacy daily, cell, and revisit fetches; "
                "there is no viewing-event table for it."
            )
        raise ValueError(
            f"This result is not a {expected_result_kind.replace('_', ' ')}. "
            "Use the fetch tools that match the saved result kind."
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
        "registry_selection": payload["registry_selection"],
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


def get_conical_daily(
    result_id: Any,
    offset: Any = 0,
    limit: Any = 31,
    *,
    _expected_result_kind: str = "conical_study",
) -> Dict[str, Any]:
    _paginate([], offset, limit, "daily")
    result_id, payload = _study_payload(result_id, _expected_result_kind)
    page = _paginate(payload["daily"], offset, limit, "daily")
    return {
        **_header(result_id, payload),
        "offset": page["offset"],
        "limit": page["limit"],
        "total_days": page["total"],
        "daily": page["items"],
    }


def get_conical_cells(
    result_id: Any,
    include: Any = "all",
    offset: Any = 0,
    limit: Any = 144,
    *,
    _expected_result_kind: str = "conical_study",
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
    result_id, payload = _study_payload(result_id, _expected_result_kind)
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
            },
            "features": page["items"],
        },
    }


def get_conical_revisit(
    result_id: Any, *, _expected_result_kind: str = "conical_study"
) -> Dict[str, Any]:
    result_id, payload = _study_payload(result_id, _expected_result_kind)
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
    result_id: Any,
    offset: Any = 0,
    limit: Any = 100,
    *,
    _expected_result_kind: str = "conical_study",
) -> Dict[str, Any]:
    _paginate([], offset, limit, "event")
    result_id, payload = _study_payload(result_id, _expected_result_kind)
    page = _paginate(payload["events"], offset, limit, "event")
    return {
        **_header(result_id, payload),
        "offset": page["offset"],
        "limit": page["limit"],
        "total_events": page["total"],
        "events": page["items"],
    }


def get_conical_event_spacing(
    result_id: Any,
    offset: Any = 0,
    limit: Any = 50,
    *,
    _expected_result_kind: str = "conical_study",
) -> Dict[str, Any]:
    _paginate([], offset, limit, "event_spacing")
    result_id, payload = _study_payload(result_id, _expected_result_kind)
    page = _paginate(payload["event_spacing"], offset, limit, "event_spacing")
    return {
        **_header(result_id, payload),
        "offset": page["offset"],
        "limit": page["limit"],
        "total_cells": page["total"],
        "event_spacing": page["items"],
    }


def get_pointed_daily(result_id: Any, offset: Any = 0, limit: Any = 31) -> Dict[str, Any]:
    """Fetch half-open UTC-day accounting for a pointed result."""
    return get_conical_daily(
        result_id, offset, limit, _expected_result_kind="pointed_study"
    )


def get_pointed_cells(
    result_id: Any, include: Any = "all", offset: Any = 0, limit: Any = 144
) -> Dict[str, Any]:
    """Fetch paged GeoJSON cells for a pointed result."""
    return get_conical_cells(
        result_id,
        include,
        offset,
        limit,
        _expected_result_kind="pointed_study",
    )


def get_pointed_revisit(result_id: Any) -> Dict[str, Any]:
    """Fetch summary event-spacing metrics for a pointed result."""
    return get_conical_revisit(result_id, _expected_result_kind="pointed_study")


def get_pointed_events(
    result_id: Any, offset: Any = 0, limit: Any = 100
) -> Dict[str, Any]:
    """Fetch paged pointed-instrument viewing epochs."""
    return get_conical_events(
        result_id, offset, limit, _expected_result_kind="pointed_study"
    )


def get_pointed_event_spacing(
    result_id: Any, offset: Any = 0, limit: Any = 50
) -> Dict[str, Any]:
    """Fetch per-cell event-spacing values for a pointed result."""
    return get_conical_event_spacing(
        result_id, offset, limit, _expected_result_kind="pointed_study"
    )


def _mixed_scope(
    result_id: Any, configuration_id: Any = None
) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    payload = _get_saved(result_id)
    if payload.get("result_kind") != "mixed_study":
        raise ValueError("This result is not a mixed study; use the matching fetch tools")
    result_id = str(result_id).strip()
    if configuration_id is None:
        return result_id, payload, payload
    if not isinstance(configuration_id, str) or not configuration_id.strip():
        raise ValueError("configuration_id must be a non-empty mixed-study configuration ID")
    configuration_id = configuration_id.strip()
    selected = payload["configuration_results"].get(configuration_id)
    if selected is None:
        available = ", ".join(
            item["configuration_id"] for item in payload["modeled_configurations"]
        )
        raise ValueError(
            f"Configuration {configuration_id!r} is not in this result. Choose one of: "
            f"{available}. Omit configuration_id for the combined union."
        )
    return result_id, payload, selected


def _mixed_fetch_provenance(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Return compact provenance references for repeated mixed-study fetches."""
    provenance = payload["provenance"]
    setup_snapshot = provenance["setup_snapshot"]
    configurations = []
    for item in setup_snapshot["configurations"]:
        configuration = item["configuration"]
        source_evidence = configuration["evidence"]
        registry_selection = item["registry_selection"]
        if registry_selection is not None:
            registry_selection = {
                key: registry_selection[key]
                for key in (
                    "source",
                    "configuration_id",
                    "revision",
                    "selection",
                    "current_source_status",
                    "base_revision",
                    "shared_registry_update_required",
                )
                if key in registry_selection
            }
            for name in (
                "source_change_checks",
                "source_change_notices",
                "refresh_uncertainty",
                "superseded_refresh_failures",
            ):
                registry_selection[name] = [
                    {
                        key: check[key]
                        for key in ("check_id", "kind", "status", "checked_at_utc")
                        if key in check
                    }
                    for check in item["registry_selection"].get(name, [])
                    if isinstance(check, dict)
                ]
        full_geometry = configuration["geometry"]
        configurations.append(
            {
                "configuration_id": item["configuration_id"],
                "configuration_revision": item["configuration_revision"],
                "instrument": configuration["instrument"],
                "configuration_sha256": _sha256(configuration),
                "source_revision_id": configuration["source_revision"]["revision_id"],
                "source_evidence": [
                    {"evidence_id": evidence["evidence_id"], "url": evidence["url"]}
                    for evidence in source_evidence
                ],
                "registry_selection": registry_selection,
                "source_choices": item["source_choices"],
                "applicability": item["applicability"],
                "geometry": {
                    "model_family": full_geometry["model_family"],
                    "illumination_requirement": full_geometry[
                        "illumination_requirement"
                    ],
                    "illumination_requirement_basis": full_geometry.get(
                        "illumination_requirement_basis"
                    ),
                    "illumination_requirement_note": full_geometry.get(
                        "illumination_requirement_note"
                    ),
                    "tatc_mapping": configuration["tatc_mapping"],
                },
                "spacecraft": item["satellite"],
            }
        )
    return {
        "setup_revision": provenance["setup_revision"],
        "accepted_setup_revision": provenance["accepted_setup_revision"],
        "accepted_at_utc": provenance["accepted_at_utc"],
        "setup_snapshot": {
            "configurations": configurations,
            "window": setup_snapshot["window"],
            "region": setup_snapshot["region"],
            "grid": setup_snapshot["grid"],
        },
        "software": provenance["software"],
    }


def _mixed_fetch_header(
    result_id: str,
    payload: Dict[str, Any],
    selected: Dict[str, Any],
    configuration_id: Any,
) -> Dict[str, Any]:
    is_combined = configuration_id is None
    warnings = (
        payload["warnings"]
        if is_combined
        else [
            f"{selected['configuration_id']}: {warning}"
            for warning in selected["warnings"]
        ]
    )
    return {
        "result_id": result_id,
        "result_kind": payload["result_kind"],
        "scope": "combined" if is_combined else "configuration",
        "accounting_method": (
            MIXED_ACCOUNTING_METHOD
            if is_combined
            else MIXED_CONFIGURATION_ACCOUNTING_METHOD
        ),
        "configuration_id": None if is_combined else selected["configuration_id"],
        "configuration_revision": None if is_combined else selected["configuration_revision"],
        "configurations": [
            {
                "configuration_id": item["configuration_id"],
                "configuration_revision": item["configuration_revision"],
                "instrument": item["instrument"],
                "satellite": item["satellite"],
            }
            for item in payload["modeled_configurations"]
        ],
        "setup_revision": payload["setup_revision"],
        "window_start_utc": payload["window_start_utc"],
        "window_end_utc_exclusive": payload["window_end_utc_exclusive"],
        "provenance": _mixed_fetch_provenance(payload),
        "warnings": warnings,
        "limits": payload["limits"],
        "completeness_status": payload["completeness_status"],
        "modeled_configurations": payload["modeled_configurations"],
        "omitted_configurations": payload["omitted_configurations"],
        "applicable_dates": payload["applicable_dates"],
        "unresolved_dates": payload["unresolved_dates"],
        "definitions": selected["definitions"],
        "summary_counts": selected["summary_counts"],
        "metrics": selected["metrics"],
        "evidence_note": payload["evidence_note"],
    }


def _paginate_mixed(
    items: Sequence[Any], offset: Any, limit: Any, name: str, maximum: int
) -> Dict[str, Any]:
    requested = _paginate([], offset, limit, name)
    return _paginate(
        list(items), requested["offset"], min(requested["limit"], maximum), name
    )


def get_mixed_events(
    result_id: Any,
    configuration_id: Any = None,
    offset: Any = 0,
    limit: Any = 5,
) -> Dict[str, Any]:
    _paginate([], offset, limit, "event")
    result_id, payload, selected = _mixed_scope(result_id, configuration_id)
    page = _paginate_mixed(
        selected["events"], offset, limit, "event", MAX_MIXED_EVENT_PAGE_SIZE
    )
    return {
        **_mixed_fetch_header(result_id, payload, selected, configuration_id),
        "offset": page["offset"],
        "limit": page["limit"],
        "total_events": page["total"],
        "events": _public_events(page["items"]),
    }


def get_mixed_daily(
    result_id: Any,
    configuration_id: Any = None,
    offset: Any = 0,
    limit: Any = 31,
) -> Dict[str, Any]:
    _paginate([], offset, limit, "daily")
    result_id, payload, selected = _mixed_scope(result_id, configuration_id)
    page = _paginate(selected["daily"], offset, limit, "daily")
    return {
        **_mixed_fetch_header(result_id, payload, selected, configuration_id),
        "offset": page["offset"],
        "limit": page["limit"],
        "total_days": page["total"],
        "daily": page["items"],
    }


def get_mixed_cells(
    result_id: Any,
    configuration_id: Any = None,
    include: Any = "all",
    offset: Any = 0,
    limit: Any = 2,
) -> Dict[str, Any]:
    _paginate([], offset, limit, "cell")
    if not isinstance(include, str) or include.strip().lower() not in ("all", "observed", "gaps"):
        raise ValueError("include must be one of all, observed, or gaps")
    result_id, payload, selected = _mixed_scope(result_id, configuration_id)
    wanted = include.strip().lower()
    features = []
    for cell in selected["cells"]:
        observed = cell["event_count"] > 0
        if (wanted == "observed" and not observed) or (wanted == "gaps" and observed):
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {key: value for key, value in cell.items() if key != "geometry"},
                "geometry": cell["geometry"],
            }
        )
    page = _paginate_mixed(
        features, offset, limit, "cell", MAX_MIXED_CELL_PAGE_SIZE
    )
    header = _mixed_fetch_header(result_id, payload, selected, configuration_id)
    return {
        **header,
        "include": wanted,
        "offset": page["offset"],
        "limit": page["limit"],
        "matched_cells": page["total"],
        "cell_count": len(page["items"]),
        "total_cells": len(selected["cells"]),
        "geojson": {
            "type": "FeatureCollection",
            "metadata": {
                key: header[key]
                for key in (
                    "result_id",
                    "result_kind",
                    "scope",
                    "accounting_method",
                    "configuration_id",
                    "configuration_revision",
                    "setup_revision",
                    "window_start_utc",
                    "window_end_utc_exclusive",
                )
            },
            "features": page["items"],
        },
    }


def get_mixed_revisit(
    result_id: Any, configuration_id: Any = None
) -> Dict[str, Any]:
    result_id, payload, selected = _mixed_scope(result_id, configuration_id)
    return {
        **_mixed_fetch_header(result_id, payload, selected, configuration_id),
        "event_spacing": {
            key: selected["metrics"][key]
            for key in (
                "cells_with_event_spacing",
                "event_spacing_sample_count",
                "mean_event_spacing_hours",
                "median_event_spacing_hours",
                "min_event_spacing_hours",
                "max_event_spacing_hours",
                "continuous_viewing_duration_available",
                "continuous_uncovered_duration_available",
            )
        },
    }


def get_mixed_event_spacing(
    result_id: Any,
    configuration_id: Any = None,
    offset: Any = 0,
    limit: Any = 5,
) -> Dict[str, Any]:
    _paginate([], offset, limit, "event spacing")
    result_id, payload, selected = _mixed_scope(result_id, configuration_id)
    page = _paginate_mixed(
        selected["event_spacing"],
        offset,
        limit,
        "event spacing",
        MAX_MIXED_SPACING_PAGE_SIZE,
    )
    return {
        **_mixed_fetch_header(result_id, payload, selected, configuration_id),
        "offset": page["offset"],
        "limit": page["limit"],
        "total_cells": page["total"],
        "event_spacing": page["items"],
    }
