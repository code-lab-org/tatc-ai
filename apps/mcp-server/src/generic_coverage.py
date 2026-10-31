"""Generic modeled access coverage for any satellite or constellation.

TAT-C does the modeling: collect_multi_observations finds each satellite's
accesses to every grid-cell center (optionally only in daylight through
the instrument's req_target_sunlit), aggregate_observations merges
overlapping accesses across satellites, and reduce_observations gives
per-cell access, revisit, and sample counts. This module only stages the
inputs, validates them, and turns TAT-C's output into daily area rows and
gap metrics, which TAT-C has no equivalent for.

The work is split into small calls so small models can emit each one:
begin locks the satellites and window; region sets the bounds; finish
adds grid plus geometry and runs the analysis. Finish picks its limits
from the window: up to 144 cells for 3 days or less, fewer for longer
windows, which also use worker processes.
"""

import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import geopandas as gpd
import pandas as pd
from tatc.analysis import (
    aggregate_observations,
    collect_multi_observations,
    reduce_observations,
)
from tatc.schemas import Instrument, Point, Satellite

from . import celestrak_client
from .coverage_core import (
    MAX_GRID_CELLS,
    MAX_OMM_AGE,
    MAX_OMM_WINDOW_OFFSET,
    MAX_YEAR_GRID_CELLS,
    MAX_YEAR_CELL_CONFIGURATIONS,
    MAX_YEAR_WINDOW,
    MAX_YEAR_WORKERS,
    SHORT_WINDOW_MAX,
    _build_grid,
    _geojson_cells,
    _parse_utc,
    _public_satellites,
    _summarize_coverage,
    _validate_bbox,
    _validate_omm_freshness,
    _validate_window_against_omm,
    field_of_regard_to_swath_width,
)
from .geometry import (
    GeometryAssessment,
    convert_swath_to_for,
    validate_geometry_inputs,
)
from .result_store import get_draft as _get_draft
from .result_store import get_result as _get_saved
from .result_store import paginate as _paginate
from .result_store import save_draft as _save_draft
from .result_store import save_result as _save_result
from .result_store import set_draft_region as _set_draft_region
from .tatc_integration import create_orbit_from_omm

MAX_SATELLITES = 6
# Long windows cost cells x satellites x days; this keeps a full year under
# the client's 10-minute tool timeout with four workers.
MAX_YEAR_CELL_SATELLITES = MAX_YEAR_CELL_CONFIGURATIONS
# Below this many cell-satellite-days, starting worker processes costs more
# than it saves.
POOL_MIN_CELL_SATELLITE_DAYS = 100

EVIDENCE_NOTE = (
    "Orbit and instrument could view this cell. This does not confirm "
    "a recorded acquisition, cloud-free pixels, or usable imagery."
)

ILLUMINATION_UNFILTERED = (
    "Illumination is not filtered or claimed; daylight suitability for "
    "optical products is unknown."
)
ILLUMINATION_DAYLIGHT = (
    "Only accesses with the sun above the horizon at the cell center are "
    "kept (TAT-C req_target_sunlit), checked at each access midpoint."
)

BASE_LIMITS = [
    "Modeled orbital access is not confirmation of an actual instrument acquisition.",
    "Cloud conditions are not modeled.",
    (
        "The geometry assessment is the assistant's interpretation of supplied "
        "source text, not independent source verification."
    ),
    "Grid-cell center sampling is not pixel-resolution mapping; an observed cell does not mean every point in that cell or part of the ROI was scanned.",
    "The within-14-day window relative to the latest OMM epoch is our chosen request guardrail, not a guarantee of prediction accuracy.",
    "A daily gap_area_km2 of 0 means every grid-cell center had at least one modeled access that UTC day, not continuous observation.",
]

YEAR_LIMITS = [
    "Long-horizon runs propagate one OMM epoch far beyond the 14-day short-window guardrail; timing of individual accesses drifts and only aggregate statistics should be quoted.",
    f"Windows over 3 days allow at most {MAX_YEAR_GRID_CELLS} cells and {MAX_YEAR_CELL_SATELLITES} cells times satellites; each cell spans at most 10 degrees per axis.",
]


def _evidence(daylight_only: bool) -> Dict[str, bool]:
    return {
        "modeled_access_only": True,
        "actual_acquisitions_known": False,
        "cloud_conditions_known": False,
        "illumination_known_or_filtered": bool(daylight_only),
    }


def _utc_iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _split_identifiers(satellite_identifier: Any) -> List[str]:
    if not isinstance(satellite_identifier, str) or not satellite_identifier.strip():
        raise ValueError("satellite_identifier must be a non-empty string")
    names = [part.strip() for part in satellite_identifier.split(",") if part.strip()]
    if not names:
        raise ValueError("satellite_identifier must be a non-empty string")
    if len(names) > MAX_SATELLITES:
        raise ValueError(
            f"Model at most {MAX_SATELLITES} satellites together, got {len(names)}"
        )
    return names


def _check_run_inputs(
    instrument_name: Optional[str],
    field_of_regard_deg: Optional[Any],
    swath_width_km: Optional[Any],
    geometry_reference: Optional[str],
    geometry_assessment: Any,
    daylight_only: Any,
) -> Tuple[Optional[float], Optional[float], str, GeometryAssessment]:
    """Validate numeric inputs and the assistant's structured assessment."""
    if instrument_name is not None and (
        not isinstance(instrument_name, str) or not instrument_name.strip()
    ):
        raise ValueError("instrument_name must be a non-empty string when given")
    if not isinstance(daylight_only, bool):
        raise ValueError("daylight_only must be true or false")
    return validate_geometry_inputs(
        field_of_regard_deg,
        swath_width_km,
        geometry_reference,
        geometry_assessment,
    )


def _stage_satellite(
    identifier: str,
    start: datetime,
    end: datetime,
    now: Optional[datetime],
) -> Dict[str, Any]:
    """Fetch one satellite's OMM and check it against the window."""
    sat_info = celestrak_client.get_satellite_info(identifier)
    norad_id = int(sat_info.get("norad_id", -1))
    name = str(sat_info.get("name") or identifier)
    omm_records = sat_info.get("omm")
    latest_epoch, epoch_age_hours = _validate_omm_freshness(omm_records, norad_id, now)
    if end - start <= SHORT_WINDOW_MAX:
        _validate_window_against_omm(start, end, latest_epoch)
    elif abs(start - latest_epoch) > MAX_OMM_WINDOW_OFFSET:
        raise ValueError(
            f"Long windows must start within 14 days of {name}'s latest OMM "
            f"epoch {latest_epoch.isoformat()} (our chosen request guardrail, "
            "not a guarantee of prediction accuracy)"
        )
    mean_altitude_m = float(create_orbit_from_omm(omm_records).get_mean_altitude())
    return {
        "satellite": name,
        "norad_id": norad_id,
        "omm_records": omm_records,
        "omm_epoch_utc": _utc_iso(latest_epoch),
        "omm_age_hours": round(epoch_age_hours, 3),
        "omm_mean_altitude_km": round(mean_altitude_m / 1000, 3),
    }


def _stage_access(
    satellite_identifier: str,
    start_time: str,
    end_time: Optional[str],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Validate satellites and window; fetch OMM for each satellite."""
    identifiers = _split_identifiers(satellite_identifier)
    start = _parse_utc(start_time, "start_time")
    # A missing end means one UTC day from the start; the tool
    # description documents this so no model has to guess.
    end = start + timedelta(days=1) if end_time is None else _parse_utc(end_time, "end_time")
    if end <= start:
        raise ValueError("end_time must be after start_time")
    if end - start > MAX_YEAR_WINDOW:
        raise ValueError("Coverage window must be no longer than 366 days")
    satellites: List[Dict[str, Any]] = []
    for identifier in identifiers:
        staged = _stage_satellite(identifier, start, end, now)
        if any(s["norad_id"] == staged["norad_id"] for s in satellites):
            raise ValueError(f"{staged['satellite']} is listed more than once")
        satellites.append(staged)
    return {
        "satellite": " + ".join(s["satellite"] for s in satellites),
        "satellites": satellites,
        "window_start_utc": _utc_iso(start),
        "window_end_utc_exclusive": _utc_iso(end),
        "roi_bbox": None,
    }


def _collect_cell_observations(task: Tuple[Any, ...]) -> Any:
    """Collect one grid cell for every satellite. Module-level for spawn pickling."""
    specs, daylight_only, cell_id, latitude, longitude, start, end = task
    satellites = [
        Satellite(
            name=name,
            orbit=create_orbit_from_omm(omm_records),
            instruments=[
                Instrument(
                    name=label,
                    field_of_regard=field_of_regard,
                    req_target_sunlit=True if daylight_only else None,
                )
            ],
        )
        for omm_records, name, label, field_of_regard in specs
    ]
    return collect_multi_observations(
        Point(id=cell_id, latitude=latitude, longitude=longitude),
        satellites,
        start,
        end,
        omit_solar=True,
    )


def _restore_cell_ids(
    results: gpd.GeoDataFrame, cell_id_by_hash: Dict[str, int]
) -> gpd.GeoDataFrame:
    """Restore stable grid IDs after TAT-C groups observations by target hash."""
    restored = results.copy()
    restored["point_id"] = restored["target_hash"].map(cell_id_by_hash)
    if restored["point_id"].isna().any():
        raise ValueError("TAT-C returned an observation for an unknown grid cell")
    restored["point_id"] = restored["point_id"].astype(int)
    return restored


def _geometry_model(
    stage: Dict[str, Any],
    for_value: Optional[float],
    swath_value: Optional[float],
    reference: str,
) -> Tuple[Dict[str, Any], List[float]]:
    """Resolve each satellite's field of regard and describe the source."""
    if swath_value is not None:
        input_kind, input_value = "swath_width_km", swath_value
        note = (
            "Converted swath_width_km to field_of_regard_deg at each "
            "satellite's OMM mean altitude with TAT-C swath_width_to_field_of_regard."
        )
    else:
        assert for_value is not None
        input_kind, input_value = "field_of_regard_deg", for_value
        note = "Used the provided field_of_regard_deg directly for every satellite."
    per_satellite = []
    resolved = []
    for s in stage["satellites"]:
        altitude_m = s["omm_mean_altitude_km"] * 1000
        field_of_regard = (
            convert_swath_to_for(swath_value, altitude_m) if swath_value is not None else for_value
        )
        resolved.append(field_of_regard)
        per_satellite.append(
            {
                "satellite": s["satellite"],
                "field_of_regard_deg": round(field_of_regard, 6),
                "modeled_swath_width_km": round(
                    field_of_regard_to_swath_width(altitude_m, field_of_regard) / 1000.0, 3
                ),
                "omm_mean_altitude_km": s["omm_mean_altitude_km"],
            }
        )
    return (
        {
            "geometry_input_kind": input_kind,
            "geometry_input_value": input_value,
            "geometry_reference": reference,
            "conversion_note": note,
            "per_satellite": per_satellite,
        },
        resolved,
    )


def _check_grid_size(cells: Sequence[Any], satellite_count: int, long_window: bool) -> None:
    if not long_window:
        if len(cells) > MAX_GRID_CELLS:
            raise ValueError(f"Grid must contain no more than {MAX_GRID_CELLS} cells")
        return
    if len(cells) > MAX_YEAR_GRID_CELLS:
        raise ValueError(
            f"Grid must contain no more than {MAX_YEAR_GRID_CELLS} cells for "
            "windows over 3 days"
        )
    if len(cells) * satellite_count > MAX_YEAR_CELL_SATELLITES:
        raise ValueError(
            f"Windows over 3 days allow at most {MAX_YEAR_CELL_SATELLITES} "
            f"cells times satellites; {len(cells)} cells x {satellite_count} "
            "satellites is too many, so use fewer grid rows or columns"
        )


def _run_stage(
    stage: Dict[str, Any],
    grid_rows: Any,
    grid_columns: Any,
    field_of_regard_deg: Optional[Any],
    swath_width_km: Optional[Any],
    geometry_reference: Optional[str],
    instrument_name: Optional[str],
    daylight_only: Any = False,
    geometry_assessment: Any = None,
    *,
    max_workers: int = 1,
) -> Dict[str, Any]:
    """Run TAT-C over staged satellites, window, and region."""
    if stage.get("roi_bbox") is None:
        raise ValueError(
            "This draft has no region yet. Call the region tool with "
            "four separate numeric bounds first"
        )
    for_value, swath_value, reference, geometry_assessment = _check_run_inputs(
        instrument_name,
        field_of_regard_deg,
        swath_width_km,
        geometry_reference,
        geometry_assessment,
        daylight_only,
    )
    start = _parse_utc(stage["window_start_utc"], "window_start_utc")
    end = _parse_utc(stage["window_end_utc_exclusive"], "window_end_utc_exclusive")
    duration = end - start
    long_window = duration > SHORT_WINDOW_MAX
    cells = _build_grid(stage["roi_bbox"], grid_rows, grid_columns)
    _check_grid_size(cells, len(stage["satellites"]), long_window)

    geometry, resolved = _geometry_model(stage, for_value, swath_value, reference)
    label = instrument_name.strip() if instrument_name else "modeled scan"
    specs = tuple(
        (s["omm_records"], s["satellite"], label, field_of_regard)
        for s, field_of_regard in zip(stage["satellites"], resolved)
    )
    tasks = [
        (specs, daylight_only, int(cell["id"]), float(cell["center_lat"]),
         float(cell["center_lon"]), start, end)
        for cell in cells
    ]
    work = len(cells) * len(specs) * duration.total_seconds() / 86400
    if max_workers == 1 or len(tasks) == 1 or work < POOL_MIN_CELL_SATELLITE_DAYS:
        collected = [_collect_cell_observations(task) for task in tasks]
    else:
        with ProcessPoolExecutor(
            max_workers=min(max_workers, len(tasks)),
            mp_context=multiprocessing.get_context("spawn"),
        ) as pool:
            collected = list(pool.map(_collect_cell_observations, tasks))
    # TAT-C 3.6 identifies targets by geometry hash and does not retain
    # Point.id. Keep the input task's row-major cell ID beside each result.
    for task, frame in zip(tasks, collected):
        frame["point_id"] = int(task[2])
    observations = gpd.GeoDataFrame(
        pd.concat(collected, ignore_index=True), geometry="geometry", crs="EPSG:4326"
    )
    cell_id_by_hash = (
        observations[["target_hash", "point_id"]]
        .drop_duplicates("target_hash")
        .set_index("target_hash")["point_id"]
        .to_dict()
    )
    aggregated = _restore_cell_ids(
        aggregate_observations(observations), cell_id_by_hash
    )
    reduced = _restore_cell_ids(reduce_observations(aggregated), cell_id_by_hash)
    summary = _summarize_coverage(cells, aggregated, reduced, start, end)
    limits = (
        BASE_LIMITS[:2]
        + [ILLUMINATION_DAYLIGHT if daylight_only else ILLUMINATION_UNFILTERED]
        + BASE_LIMITS[2:]
        + (YEAR_LIMITS if long_window else [])
    )
    interpretation = (
        "Observed and gap areas assign each whole geodesic grid cell based "
        "only on modeled access at that cell's center"
        + (" by any listed satellite" if len(specs) > 1 else "")
        + ". This does not establish coverage of every point in an observed "
        "cell or every part of the ROI; never claim the whole ROI was "
        "scanned. Modeled access does not confirm an actual instrument "
        "acquisition. See geometry_assessment for the selected geometry and caveat."
    )
    return {
        "accounting_method": "legacy_intervals_v1",
        "satellite": stage["satellite"],
        "satellites": _public_satellites(stage),
        "instrument": label,
        "daylight_only": daylight_only,
        "window_start_utc": stage["window_start_utc"],
        "window_end_utc_exclusive": stage["window_end_utc_exclusive"],
        "window_duration_hours": round(duration.total_seconds() / 3600, 3),
        "roi_bbox": list(stage["roi_bbox"]),
        "grid_shape": [grid_rows, grid_columns],
        "interpretation": interpretation,
        "total_roi_area_km2": round(sum(cell["area_km2"] for cell in cells), 3),
        "geometry_model": geometry,
        "geometry_assessment": geometry_assessment.model_dump(exclude_none=True),
        "omm_freshness_limit_days": MAX_OMM_AGE.days,
        "method": {
            "observation_source": (
                "TAT-C collect_multi_observations, aggregate_observations, and "
                "reduce_observations using current CelesTrak OMM elements."
            ),
            "spatial_approximation": "Each whole grid cell's WGS84 geodesic area is classified using modeled access at its center only; this does not establish coverage of every point in the cell or ROI.",
            "time_semantics": "Half-open UTC window [start, end); a visit ending exactly at midnight does not cover the next UTC day.",
            "limits": limits,
        },
        "evidence": _evidence(daylight_only),
        "metrics": summary["metrics"],
        "daily": summary["daily"],
        "grid_cells": _geojson_cells(cells, summary["per_cell"]),
    }


def _compact_summary(full: Dict[str, Any], result_id: str) -> Dict[str, Any]:
    days = full["daily"]
    observed_days = sum(1 for row in days if row["observed_cell_ids"])
    return {
        "result_id": result_id,
        "accounting_method": full["accounting_method"],
        "satellite": full["satellite"],
        "satellites": full["satellites"],
        "instrument": full["instrument"],
        "daylight_only": full["daylight_only"],
        "window_start_utc": full["window_start_utc"],
        "window_end_utc_exclusive": full["window_end_utc_exclusive"],
        "window_duration_hours": full["window_duration_hours"],
        "roi_bbox": full["roi_bbox"],
        "grid_shape": full["grid_shape"],
        "geometry_model": full["geometry_model"],
        "geometry_assessment": full["geometry_assessment"],
        "interpretation": full["interpretation"],
        "summary_counts": {
            "total_days": len(days),
            "observed_days": observed_days,
            "gap_days": len(days) - observed_days,
            "grid_cell_count": full["metrics"]["grid_cell_count"],
            "observed_cell_count": full["metrics"]["observed_cell_count"],
            "never_observed_cell_count": full["metrics"]["never_observed_cell_count"],
        },
        "metrics": full["metrics"],
        "evidence": full["evidence"],
        "evidence_note": EVIDENCE_NOTE,
        "fetch": (
            "Daily rows, grid cells with per-cell revisit, and revisit "
            "numbers are saved under result_id. Use the daily, cells, and "
            "revisit fetch tools with that ID instead of re-running the analysis. "
            "Preserve geometry_assessment.warning with any quoted result."
        ),
    }


def begin_access_run(
    satellite_identifier: str,
    start_time: str,
    end_time: Optional[str],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Lock satellites and window; return a draft ID plus altitudes.

    Each satellite's OMM mean altitude comes back so a swath width can be
    picked or converted before finishing.
    """
    stage = _stage_access(satellite_identifier, start_time, end_time, now=now)
    return {
        "draft_id": _save_draft(stage),
        "satellite": stage["satellite"],
        "satellites": _public_satellites(stage),
        "window_start_utc": stage["window_start_utc"],
        "window_end_utc_exclusive": stage["window_end_utc_exclusive"],
        "roi_bbox": None,
        "next_step": (
            "Call the region tool with this draft_id and four separate "
            "numeric bounds, then the finish tool with grid size, exactly "
            "one of field_of_regard_deg or swath_width_km, the geometry_reference "
            "citation, and a geometry_assessment with status, source_text, "
            "explanation, and a warning for approximate geometry."
        ),
    }


def set_access_region(
    draft_id: Any,
    roi_bbox: Sequence[float],
) -> Dict[str, Any]:
    """Set the region on a staged draft with four numeric bounds."""
    validated = list(_validate_bbox(roi_bbox))
    stage = _set_draft_region(draft_id, validated)
    return {
        "draft_id": draft_id.strip(),
        "satellite": stage["satellite"],
        "window_start_utc": stage["window_start_utc"],
        "window_end_utc_exclusive": stage["window_end_utc_exclusive"],
        "roi_bbox": validated,
        "next_step": (
            "Call the finish tool with this draft_id, grid rows and "
            "columns, exactly one of field_of_regard_deg or swath_width_km, "
            "the geometry_reference citation, and a geometry_assessment with "
            "status, source_text, explanation, and a warning for approximate geometry."
        ),
    }


def finish_access_run(
    draft_id: Any,
    grid_rows: Any = 6,
    grid_columns: Any = 6,
    field_of_regard_deg: Optional[Any] = None,
    swath_width_km: Optional[Any] = None,
    geometry_reference: Optional[str] = None,
    instrument_name: Optional[str] = None,
    daylight_only: Any = False,
    *,
    geometry_assessment: Optional[Any] = None,
    _max_workers: int = MAX_YEAR_WORKERS,
) -> Dict[str, Any]:
    """Run a draft of any length and return a result ID with a summary.

    Large jobs fan cells out across worker processes, so a full year takes
    minutes; long-horizon drift is recorded in the method limits.
    """
    if (
        isinstance(_max_workers, bool)
        or not isinstance(_max_workers, int)
        or not 1 <= _max_workers <= MAX_YEAR_WORKERS
    ):
        raise ValueError(
            f"_max_workers must be an integer between 1 and {MAX_YEAR_WORKERS}"
        )
    stage = _get_draft(draft_id)
    if stage.get("mixed_proposal"):
        raise ValueError(
            "This draft has a prepared mixed-study workflow proposal. Use the mixed "
            "finish tool with its setup revision, and do not pass legacy geometry arguments."
        )
    full = _run_stage(
        stage,
        grid_rows,
        grid_columns,
        field_of_regard_deg,
        swath_width_km,
        geometry_reference,
        instrument_name,
        daylight_only,
        geometry_assessment,
        max_workers=_max_workers,
    )
    return _compact_summary(full, _save_result(full))


def _fetch_header(result_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "result_id": result_id.strip(),
        "accounting_method": payload.get("accounting_method", "legacy_intervals_v1"),
        "satellite": payload["satellite"],
        "daylight_only": payload["daylight_only"],
        "window_start_utc": payload["window_start_utc"],
        "window_end_utc_exclusive": payload["window_end_utc_exclusive"],
        "geometry_reference": payload["geometry_model"]["geometry_reference"],
        "geometry_mode": payload["geometry_assessment"].get("mode"),
        "geometry_status": payload["geometry_assessment"]["status"],
        "geometry_warning": payload["geometry_assessment"].get("warning"),
        "interpretation": payload["interpretation"],
    }


def _fetch_footer(payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "evidence": payload["evidence"],
        "evidence_note": EVIDENCE_NOTE,
    }


def get_access_daily(
    result_id: Any, offset: Any = 0, limit: Any = 31
) -> Dict[str, Any]:
    """Page through saved daily observed and gap areas."""
    # Validate paging before touching the store so broken args get an
    # arg error even when the ID is also unknown.
    _paginate([], offset, limit, "daily")
    payload = _get_saved(result_id)
    if payload.get("result_kind") == "conical_study":
        from . import conical_study

        return conical_study.get_conical_daily(result_id, offset, limit)
    page = _paginate(payload["daily"], offset, limit, "daily")
    return {
        **_fetch_header(result_id, payload),
        "offset": page["offset"],
        "limit": page["limit"],
        "total_days": page["total"],
        "daily": page["items"],
        **_fetch_footer(payload),
    }


def get_access_cells(
    result_id: Any, include: Any = "all", offset: Any = 0, limit: Any = 144
) -> Dict[str, Any]:
    """Return the saved grid as GeoJSON, optionally filtered and paged."""
    # Validate the filter before touching the store so a bad value gets
    # a filter error even when the ID is also unknown.
    _paginate([], offset, limit, "cell")
    if not isinstance(include, str) or include.strip().lower() not in (
        "all", "observed", "gaps",
    ):
        raise ValueError(
            "include must be one of all, observed, or gaps, "
            f"got {include!r}"
        )
    payload = _get_saved(result_id)
    if payload.get("result_kind") == "conical_study":
        from . import conical_study

        return conical_study.get_conical_cells(result_id, include, offset, limit)
    wanted = include.strip().lower()
    features = []
    for cell in payload["grid_cells"]:
        is_observed = cell["access_count"] > 0
        if (wanted == "observed" and not is_observed) or (wanted == "gaps" and is_observed):
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": cell["id"],
                    "area_km2": cell["area_km2"],
                    "status": "observed" if is_observed else "gap",
                    "access_count": cell["access_count"],
                    "mean_access_minutes": cell["mean_access_minutes"],
                    "mean_revisit_hours": cell["mean_revisit_hours"],
                    "geometry_status": payload["geometry_assessment"]["status"],
                    "geometry_warning": payload["geometry_assessment"].get("warning"),
                },
                "geometry": cell["geometry"],
            }
        )
    page = _paginate(features, offset, limit, "cell")
    return {
        **_fetch_header(result_id, payload),
        "include": wanted,
        "offset": page["offset"],
        "limit": page["limit"],
        "matched_cells": page["total"],
        "cell_count": len(page["items"]),
        "total_cells": len(payload["grid_cells"]),
        "geojson": {
            "type": "FeatureCollection",
            "metadata": {
                "geometry_reference": payload["geometry_model"]["geometry_reference"],
                "geometry_mode": payload["geometry_assessment"].get("mode"),
                "geometry_status": payload["geometry_assessment"]["status"],
                "geometry_warning": payload["geometry_assessment"].get("warning"),
                "interpretation": payload["interpretation"],
                "evidence": payload["evidence"],
            },
            "features": page["items"],
        },
        **_fetch_footer(payload),
    }


def get_access_revisit(result_id: Any) -> Dict[str, Any]:
    """Return the saved revisit numbers with their definitions."""
    payload = _get_saved(result_id)
    if payload.get("result_kind") == "conical_study":
        from . import conical_study

        return conical_study.get_conical_revisit(result_id)
    return {
        **_fetch_header(result_id, payload),
        "instrument": payload["instrument"],
        "window_duration_hours": payload["window_duration_hours"],
        "revisit": dict(payload["metrics"]),
        **_fetch_footer(payload),
    }
