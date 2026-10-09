"""Generic modeled access coverage for any satellite.

This module runs TAT-C math for any CelesTrak satellite with an explicit
geometry: exactly one of field_of_regard_deg or swath_width_km, plus a
source string.

Two paths share one core. The single run takes everything at once.
The begin and finish steps split the same work into small calls so
small models can emit each call: begin locks the satellite and window;
region sets the bounds; finish adds grid plus geometry and runs the
analysis.
"""

import multiprocessing
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

import geopandas as gpd
import pandas as pd
from tatc.schemas import Instrument, Point, Satellite

from . import celestrak_client
from .geometry import convert_swath_to_for, validate_geometry_inputs
from .coverage_core import (
    MAX_GRID_CELLS,
    MAX_OMM_AGE,
    MAX_OMM_WINDOW_OFFSET,
    MAX_YEAR_GRID_CELLS,
    MAX_YEAR_WORKERS,
    _build_grid,
    _geojson_cells,
    _parse_utc,
    _summarize_coverage,
    _validate_bbox,
    _validate_omm_freshness,
    _validate_window_against_omm,
    field_of_regard_to_swath_width,
)
from .result_store import get_draft as _get_draft
from .result_store import get_result as _get_saved
from .result_store import paginate as _paginate
from .result_store import save_draft as _save_draft
from .result_store import save_result as _save_result
from .tatc_integration import create_orbit_from_omm
from tatc.analysis.coverage import aggregate_observations, collect_observations


EVIDENCE = {
    "modeled_access_only": True,
    "actual_acquisitions_known": False,
    "cloud_conditions_known": False,
    "illumination_known_or_filtered": False,
}

EVIDENCE_NOTE = (
    "Orbit and instrument could view this cell. This does not confirm "
    "a recorded acquisition, cloud-free pixels, or usable imagery."
)


def _stage_access(
    satellite_identifier: str,
    start_time: str,
    end_time: Optional[str],
    roi_bbox: Optional[Sequence[float]] = None,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Validate satellite, window, and optional region; fetch OMM."""
    if not isinstance(satellite_identifier, str) or not satellite_identifier.strip():
        raise ValueError("satellite_identifier must be a non-empty string")
    if end_time is None:
        # A missing end means one UTC day from the start. Documented in
        # the tool description so no model has to guess the default.
        from dateutil import parser as date_parser

        try:
            parsed_start = date_parser.parse(start_time)
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(
                f"start_time must be an explicit UTC ISO-8601 value, got {start_time!r}"
            ) from exc
        if parsed_start is None or parsed_start.tzinfo is None:
            raise ValueError(
                f"start_time must carry a UTC offset or Z, got {start_time!r}"
            )
        end_time = (parsed_start + timedelta(days=1)).isoformat().replace("+00:00", "Z")
    start = _parse_utc(start_time, "start_time")
    end = _parse_utc(end_time, "end_time")
    if end <= start:
        raise ValueError("end_time must be after start_time")
    duration = end - start
    if duration > MAX_YEAR_WINDOW:
        raise ValueError("Coverage window must be no longer than 366 days")
    validated_bbox = (
        list(_validate_bbox(roi_bbox)) if roi_bbox is not None else None
    )
    sat_info = celestrak_client.get_satellite_info(satellite_identifier.strip())
    norad_id = int(sat_info.get("norad_id", -1))
    sat_name = str(sat_info.get("name") or satellite_identifier.strip())
    omm_records = sat_info.get("omm")
    latest_epoch, epoch_age_hours = _validate_omm_freshness(omm_records, norad_id, now)
    if duration <= MIN_YEAR_WINDOW:
        _validate_window_against_omm(start, end, latest_epoch)
    elif abs(start - latest_epoch) > MAX_OMM_WINDOW_OFFSET:
        raise ValueError(
            "Long windows must start within 14 days of the latest OMM epoch "
            f"{latest_epoch.isoformat()} (our chosen request guardrail, not a "
            "guarantee of prediction accuracy)"
        )
    orbit = create_orbit_from_omm(omm_records)
    mean_altitude_m = float(orbit.get_mean_altitude())
    return {
        "satellite": sat_name,
        "norad_id": norad_id,
        "omm_records": omm_records,
        "window_start_utc": start.isoformat().replace("+00:00", "Z"),
        "window_end_utc_exclusive": end.isoformat().replace("+00:00", "Z"),
        "roi_bbox": validated_bbox,
        "omm_epoch_utc": latest_epoch.isoformat().replace("+00:00", "Z"),
        "omm_age_hours": round(epoch_age_hours, 3),
        "omm_mean_altitude_km": round(mean_altitude_m / 1000, 3),
    }


def _execute_access(
    stage: Dict[str, Any],
    grid_rows: int,
    grid_columns: int,
    field_of_regard_deg: Optional[Any],
    swath_width_km: Optional[Any],
    geometry_reference: Optional[str],
    instrument_name: Optional[str],
    max_cells: int = MAX_GRID_CELLS,
) -> Dict[str, Any]:
    """Run TAT-C over staged satellite, window, and region."""
    if stage.get("roi_bbox") is None:
        raise ValueError(
            "This draft has no region yet. Call the region tool with "
            "four separate numeric bounds first"
        )
    if instrument_name is not None and (
        not isinstance(instrument_name, str) or not instrument_name.strip()
    ):
        raise ValueError("instrument_name must be a non-empty string when given")
    for_value, swath_value, reference = validate_geometry_inputs(
        field_of_regard_deg, swath_width_km, geometry_reference
    )
    cells = _build_grid(stage["roi_bbox"], grid_rows, grid_columns)
    if len(cells) > max_cells:
        raise ValueError(f"Grid must contain no more than {max_cells} cells")
    from dateutil import parser as date_parser

    start = date_parser.parse(stage["window_start_utc"])
    end = date_parser.parse(stage["window_end_utc_exclusive"])
    if end - start > MIN_YEAR_WINDOW:
        raise ValueError(
            "Short runs cover at most 3 days; use the year finish tool "
            "for this draft"
        )
    orbit = create_orbit_from_omm(stage["omm_records"])
    mean_altitude_m = float(orbit.get_mean_altitude())

    if swath_value is not None:
        resolved_for = convert_swath_to_for(swath_value, mean_altitude_m)
        input_kind = "swath_width_km"
        input_value = swath_value
        method_note = (
            "Converted swath_width_km to field_of_regard_deg at the OMM "
            "mean altitude with TAT-C swath_width_to_field_of_regard."
        )
    else:
        assert for_value is not None
        resolved_for = for_value
        input_kind = "field_of_regard_deg"
        input_value = for_value
        method_note = "Used the provided field_of_regard_deg directly."

    sat_name = stage["satellite"]
    label = instrument_name.strip() if instrument_name else f"{sat_name} modeled scan"
    instrument = Instrument(name=label, field_of_regard=resolved_for)
    satellite = Satellite(name=f"{sat_name} {label}", orbit=orbit, instruments=[instrument])

    collected = [
        collect_observations(
            Point(id=cell["id"], latitude=cell["center_lat"], longitude=cell["center_lon"]),
            satellite,
            start,
            end,
            instrument_index=0,
            omit_solar=True,
        )
        for cell in cells
    ]
    observations = gpd.GeoDataFrame(
        pd.concat(collected, ignore_index=True), geometry="geometry", crs="EPSG:4326"
    )
    aggregated = aggregate_observations(observations)
    summary = _summarize_coverage(cells, aggregated, start, end)
    summary["instrument_label"] = label
    return _assemble_full_result(
        stage,
        cells,
        grid_rows,
        grid_columns,
        resolved_for,
        input_kind,
        input_value,
        reference,
        method_note,
        mean_altitude_m,
        start,
        end,
        summary,
    )


MAX_YEAR_WINDOW = timedelta(days=366)
MIN_YEAR_WINDOW = timedelta(days=3)


def _collect_year_cell_observations(
    task: Tuple[Any, ...],
) -> Any:
    """Collect one cell over a long window. Module-level for spawn pickling."""
    (
        omm_records,
        sat_label,
        field_of_regard,
        cell_id,
        latitude,
        longitude,
        start,
        end,
    ) = task
    orbit = create_orbit_from_omm(omm_records)
    instrument = Instrument(name=sat_label, field_of_regard=field_of_regard)
    satellite = Satellite(
        name=sat_label, orbit=orbit, instruments=[instrument]
    )
    return collect_observations(
        Point(id=cell_id, latitude=latitude, longitude=longitude),
        satellite,
        start,
        end,
        instrument_index=0,
        omit_solar=True,
    )


def _assemble_full_result(
    stage: Dict[str, Any],
    cells: Sequence[Dict[str, Any]],
    grid_rows: int,
    grid_columns: int,
    resolved_for: float,
    input_kind: str,
    input_value: float,
    reference: str,
    method_note: str,
    mean_altitude_m: float,
    start: datetime,
    end: datetime,
    summary: Dict[str, Any],
    extra_limits: Optional[List[str]] = None,
) -> Dict[str, Any]:
    modeled_swath_km = field_of_regard_to_swath_width(
        mean_altitude_m, resolved_for
    ) / 1000.0
    total_area = sum(cell["area_km2"] for cell in cells)
    limits = [
        "Modeled orbital access is not confirmation of an actual instrument acquisition.",
        "Cloud conditions are not modeled.",
        "Illumination is not filtered or claimed; daylight suitability for optical products is unknown.",
        "The provided viewing geometry is taken as given; a wrong angle or swath gives a wrong swath and wrong access.",
        "Grid-cell center sampling is not pixel-resolution mapping; an observed cell does not mean every point in that cell or part of the ROI was scanned.",
        "The within-14-day window relative to the latest OMM epoch is our chosen request guardrail, not a guarantee of prediction accuracy.",
        "A daily gap_area_km2 of 0 means every grid-cell center had at least one modeled access that UTC day, not continuous observation.",
    ]
    if extra_limits:
        limits = limits + extra_limits
    label = summary.get("instrument_label", "")
    return {
        "satellite": stage["satellite"],
        "norad_id": stage["norad_id"],
        "instrument": label,
        "window_start_utc": stage["window_start_utc"],
        "window_end_utc_exclusive": stage["window_end_utc_exclusive"],
        "window_duration_hours": round((end - start).total_seconds() / 3600, 3),
        "roi_bbox": list(stage["roi_bbox"]),
        "grid_shape": [grid_rows, grid_columns],
        "interpretation": (
            "Observed and gap areas assign each whole geodesic grid cell based "
            "only on modeled access at that cell's center. This does not establish "
            "coverage of every point in an observed cell or every part of the ROI; "
            "never claim the whole ROI was scanned. Modeled access does not confirm "
            "an actual instrument acquisition."
        ),
        "total_roi_area_km2": round(total_area, 3),
        "geometry_model": {
            "field_of_regard_deg": round(resolved_for, 6),
            "geometry_input_kind": input_kind,
            "geometry_input_value": input_value,
            "geometry_reference": reference,
            "conversion_note": method_note,
            "modeled_swath_width_km_at_omm_mean_altitude": round(modeled_swath_km, 3),
            "omm_mean_altitude_km": round(mean_altitude_m / 1000, 3),
        },
        "omm": {
            "epoch_utc": stage["omm_epoch_utc"],
            "age_hours_at_calculation": stage["omm_age_hours"],
            "freshness_limit_days": MAX_OMM_AGE.days,
        },
        "method": {
            "observation_source": "TAT-C collect_observations and aggregate_observations using current CelesTrak OMM elements.",
            "spatial_approximation": "Each whole grid cell's WGS84 geodesic area is classified using modeled access at its center only; this does not establish coverage of every point in the cell or ROI.",
            "time_semantics": "Half-open UTC window [start, end); a visit ending exactly at midnight does not cover the next UTC day.",
            "actual_acquisitions_known": False,
            "cloud_conditions_known": False,
            "illumination_known_or_filtered": False,
            "limits": limits,
        },
        "metrics": summary["metrics"],
        "daily": summary["daily"],
        "grid_cells": _geojson_cells(cells),
    }


def compute_satellite_coverage(
    satellite_identifier: str,
    start_time: str,
    end_time: str,
    roi_bbox: Sequence[float],
    grid_rows: int = 6,
    grid_columns: int = 6,
    field_of_regard_deg: Optional[Any] = None,
    swath_width_km: Optional[Any] = None,
    geometry_reference: Optional[str] = None,
    instrument_name: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Compute modeled access for one satellite with explicit geometry."""
    if not isinstance(satellite_identifier, str) or not satellite_identifier.strip():
        raise ValueError("satellite_identifier must be a non-empty string")
    if instrument_name is not None and (
        not isinstance(instrument_name, str) or not instrument_name.strip()
    ):
        raise ValueError("instrument_name must be a non-empty string when given")
    for_value, swath_value, reference = validate_geometry_inputs(
        field_of_regard_deg, swath_width_km, geometry_reference
    )
    stage = _stage_access(
        satellite_identifier, start_time, end_time, roi_bbox, now=now
    )
    return _execute_access(
        stage,
        grid_rows,
        grid_columns,
        for_value,
        swath_value,
        reference,
        instrument_name,
    )


def _compact_summary(full: Dict[str, Any], result_id: str) -> Dict[str, Any]:
    days = full["daily"]
    observed_days = sum(1 for row in days if row["observed_cell_ids"])
    return {
        "result_id": result_id,
        "satellite": full["satellite"],
        "norad_id": full["norad_id"],
        "instrument": full["instrument"],
        "window_start_utc": full["window_start_utc"],
        "window_end_utc_exclusive": full["window_end_utc_exclusive"],
        "window_duration_hours": full["window_duration_hours"],
        "roi_bbox": full["roi_bbox"],
        "grid_shape": full["grid_shape"],
        "geometry_model": full["geometry_model"],
        "omm": full["omm"],
        "summary_counts": {
            "total_days": len(days),
            "observed_days": observed_days,
            "gap_days": len(days) - observed_days,
            "grid_cell_count": full["metrics"]["grid_cell_count"],
            "observed_cell_count": full["metrics"]["observed_cell_count"],
            "never_observed_cell_count": full["metrics"]["never_observed_cell_count"],
        },
        "metrics": full["metrics"],
        "evidence": dict(EVIDENCE),
        "evidence_note": EVIDENCE_NOTE,
        "fetch": (
            "Daily rows, grid cells, and revisit numbers are saved under "
            "result_id. Use the daily, cells, and revisit fetch tools with "
            "that ID instead of re-running the analysis."
        ),
    }


def run_satellite_access(
    satellite_identifier: str,
    start_time: str,
    end_time: str,
    roi_bbox: Sequence[float],
    grid_rows: int = 6,
    grid_columns: int = 6,
    field_of_regard_deg: Optional[Any] = None,
    swath_width_km: Optional[Any] = None,
    geometry_reference: Optional[str] = None,
    instrument_name: Optional[str] = None,
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Run coverage, save the full result, and return a compact summary.

    The summary fits in chat. Daily rows, grid cells, and revisit numbers
    stay in the store under the returned result ID for the fetch helpers.
    """
    full = compute_satellite_coverage(
        satellite_identifier,
        start_time,
        end_time,
        roi_bbox,
        grid_rows=grid_rows,
        grid_columns=grid_columns,
        field_of_regard_deg=field_of_regard_deg,
        swath_width_km=swath_width_km,
        geometry_reference=geometry_reference,
        instrument_name=instrument_name,
        now=now,
    )
    return _compact_summary(full, _save_result(full))


def begin_access_run(
    satellite_identifier: str,
    start_time: str,
    end_time: Optional[str],
    *,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Lock satellite and window; return a draft ID plus altitude.

    The draft holds validated inputs and OMM elements. Set the region
    with the region tool, then finish with grid plus exactly one geometry
    and source. The OMM mean altitude comes back so a swath width can be
    picked or converted before finishing.
    """
    stage = _stage_access(
        satellite_identifier, start_time, end_time, None, now=now
    )
    draft_id = _save_draft(stage)
    return {
        "draft_id": draft_id,
        "satellite": stage["satellite"],
        "norad_id": stage["norad_id"],
        "window_start_utc": stage["window_start_utc"],
        "window_end_utc_exclusive": stage["window_end_utc_exclusive"],
        "roi_bbox": None,
        "omm_epoch_utc": stage["omm_epoch_utc"],
        "omm_mean_altitude_km": stage["omm_mean_altitude_km"],
        "next_step": (
            "Call the region tool with this draft_id and four separate "
            "numeric bounds, then the finish tool with grid size, exactly "
            "one of field_of_regard_deg or swath_width_km, and the "
            "geometry_reference source string."
        ),
    }


def set_access_region(
    draft_id: Any,
    roi_bbox: Sequence[float],
) -> Dict[str, Any]:
    """Set the region on a staged draft with four numeric bounds."""
    from .result_store import set_draft_region as _set_region

    validated = list(_validate_bbox(roi_bbox))
    stage = _set_region(draft_id, validated)
    key = draft_id.strip() if isinstance(draft_id, str) else draft_id
    return {
        "draft_id": key,
        "satellite": stage["satellite"],
        "window_start_utc": stage["window_start_utc"],
        "window_end_utc_exclusive": stage["window_end_utc_exclusive"],
        "roi_bbox": validated,
        "next_step": (
            "Call the finish tool with this draft_id, grid rows and "
            "columns, exactly one of field_of_regard_deg or swath_width_km, "
            "and the geometry_reference source string."
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
) -> Dict[str, Any]:
    """Run the analysis for a draft and return a result ID with summary."""
    stage = _get_draft(draft_id)
    full = _execute_access(
        stage,
        grid_rows,
        grid_columns,
        field_of_regard_deg,
        swath_width_km,
        geometry_reference,
        instrument_name,
    )
    return _compact_summary(full, _save_result(full))


def finish_year_access_run(
    draft_id: Any,
    grid_rows: Any = 6,
    grid_columns: Any = 6,
    field_of_regard_deg: Optional[Any] = None,
    swath_width_km: Optional[Any] = None,
    geometry_reference: Optional[str] = None,
    instrument_name: Optional[str] = None,
    *,
    _max_workers: int = MAX_YEAR_WORKERS,
) -> Dict[str, Any]:
    """Run a long-window analysis for a draft and return results by ID.

    Same shape as the short finish tool, but for staged windows longer
    than 3 days up to 366 days with at most 36 grid cells. Collection
    fans out across worker processes, so a full year takes minutes. The
    window start must lie within 14 days of the draft OMM epoch; the end
    may extend far beyond it, and long-horizon drift is recorded in the
    method limits.
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
    from dateutil import parser as date_parser

    start = date_parser.parse(stage["window_start_utc"])
    end = date_parser.parse(stage["window_end_utc_exclusive"])
    latest_epoch = date_parser.parse(stage["omm_epoch_utc"])
    duration = end - start
    if duration <= MIN_YEAR_WINDOW:
        raise ValueError(
            "Year runs need a staged window longer than 3 days; "
            "use the short finish tool for this draft"
        )
    if duration > MAX_YEAR_WINDOW:
        raise ValueError(
            "Year runs cover at most 366 days; stage a shorter window"
        )
    if abs(start - latest_epoch) > MAX_OMM_WINDOW_OFFSET:
        raise ValueError(
            "Year runs must start within 14 days of the draft OMM epoch "
            f"{stage['omm_epoch_utc']} (our chosen request guardrail, not a "
            "guarantee of prediction accuracy)"
        )
    if instrument_name is not None and (
        not isinstance(instrument_name, str) or not instrument_name.strip()
    ):
        raise ValueError("instrument_name must be a non-empty string when given")
    for_value, swath_value, reference = validate_geometry_inputs(
        field_of_regard_deg, swath_width_km, geometry_reference
    )
    cells = _build_grid(stage["roi_bbox"], grid_rows, grid_columns)
    if len(cells) > MAX_YEAR_GRID_CELLS:
        raise ValueError(
            f"Year grid must contain no more than {MAX_YEAR_GRID_CELLS} cells"
        )
    orbit = create_orbit_from_omm(stage["omm_records"])
    mean_altitude_m = float(orbit.get_mean_altitude())
    if swath_value is not None:
        resolved_for = convert_swath_to_for(swath_value, mean_altitude_m)
        input_kind = "swath_width_km"
        input_value = swath_value
        method_note = (
            "Converted swath_width_km to field_of_regard_deg at the OMM "
            "mean altitude with TAT-C swath_width_to_field_of_regard."
        )
    else:
        assert for_value is not None
        resolved_for = for_value
        input_kind = "field_of_regard_deg"
        input_value = for_value
        method_note = "Used the provided field_of_regard_deg directly."
    sat_name = stage["satellite"]
    label = instrument_name.strip() if instrument_name else f"{sat_name} modeled scan"
    tasks = [
        (
            stage["omm_records"],
            label,
            resolved_for,
            int(cell["id"]),
            float(cell["center_lat"]),
            float(cell["center_lon"]),
            start,
            end,
        )
        for cell in cells
    ]
    if _max_workers == 1 or len(tasks) == 1:
        collected = [_collect_year_cell_observations(task) for task in tasks]
    else:
        worker_count = min(_max_workers, len(tasks))
        with ProcessPoolExecutor(
            max_workers=worker_count,
            mp_context=multiprocessing.get_context("spawn"),
        ) as pool:
            collected = list(pool.map(_collect_year_cell_observations, tasks))
    observations = gpd.GeoDataFrame(
        pd.concat(collected, ignore_index=True), geometry="geometry", crs="EPSG:4326"
    )
    aggregated = aggregate_observations(observations)
    summary = _summarize_coverage(cells, aggregated, start, end)
    summary["instrument_label"] = label
    full = _assemble_full_result(
        stage,
        cells,
        grid_rows,
        grid_columns,
        resolved_for,
        input_kind,
        input_value,
        reference,
        method_note,
        mean_altitude_m,
        start,
        end,
        summary,
        extra_limits=[
            "Long-horizon runs propagate one OMM epoch far beyond the 14-day short-window guardrail; timing of individual accesses drifts and only aggregate statistics should be quoted.",
            "Year grids are limited to 36 cells; each cell spans at most 10 degrees per axis.",
        ],
    )
    return _compact_summary(full, _save_result(full))


def get_access_daily(
    result_id: Any, offset: Any = 0, limit: Any = 31
) -> Dict[str, Any]:
    """Page through saved daily observed and gap areas."""
    # Validate paging before touching the store so broken args get an
    # arg error even when the ID is also unknown.
    _paginate([], offset, limit, "daily")
    payload = _get_saved(result_id)
    page = _paginate(payload["daily"], offset, limit, "daily")
    return {
        "result_id": result_id.strip() if isinstance(result_id, str) else result_id,
        "satellite": payload["satellite"],
        "window_start_utc": payload["window_start_utc"],
        "window_end_utc_exclusive": payload["window_end_utc_exclusive"],
        "offset": page["offset"],
        "limit": page["limit"],
        "total_days": page["total"],
        "daily": page["items"],
        "evidence": dict(EVIDENCE),
        "evidence_note": EVIDENCE_NOTE,
    }


def _cell_observed_ids(payload: Dict[str, Any]) -> set:
    seen: set = set()
    for row in payload["daily"]:
        seen.update(row["observed_cell_ids"])
    return seen


def get_access_cells(result_id: Any, include: Any = "all") -> Dict[str, Any]:
    """Return the saved grid as GeoJSON, optionally filtered by status."""
    # Validate the filter before touching the store so a bad value gets
    # a filter error even when the ID is also unknown.
    if not isinstance(include, str) or include.strip().lower() not in (
        "all", "observed", "gaps",
    ):
        raise ValueError(
            "include must be one of all, observed, or gaps, "
            f"got {include!r}"
        )
    payload = _get_saved(result_id)
    wanted = include.strip().lower()
    observed = _cell_observed_ids(payload)
    features = []
    for cell in payload["grid_cells"]:
        is_observed = cell["id"] in observed
        if wanted == "observed" and not is_observed:
            continue
        if wanted == "gaps" and is_observed:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "id": cell["id"],
                    "area_km2": cell["area_km2"],
                    "status": "observed" if is_observed else "gap",
                },
                "geometry": cell["geometry"],
            }
        )
    return {
        "result_id": result_id.strip() if isinstance(result_id, str) else result_id,
        "satellite": payload["satellite"],
        "window_start_utc": payload["window_start_utc"],
        "window_end_utc_exclusive": payload["window_end_utc_exclusive"],
        "include": wanted,
        "cell_count": len(features),
        "total_cells": len(payload["grid_cells"]),
        "geojson": {"type": "FeatureCollection", "features": features},
        "evidence": dict(EVIDENCE),
        "evidence_note": EVIDENCE_NOTE,
    }


def get_access_revisit(result_id: Any) -> Dict[str, Any]:
    """Return the saved revisit numbers with their definitions."""
    payload = _get_saved(result_id)
    metrics = payload["metrics"]
    revisit = {
        key: metrics.get(key)
        for key in (
            "grid_cell_count",
            "observed_cell_count",
            "never_observed_cell_count",
            "aggregated_observation_period_count",
            "mean_revisit_hours",
            "median_revisit_hours",
            "max_revisit_hours",
            "revisit_sample_count",
            "mean_access_minutes",
            "longest_unobserved_gap_hours",
            "revisit_definition",
            "longest_gap_definition",
        )
    }
    return {
        "result_id": result_id.strip() if isinstance(result_id, str) else result_id,
        "satellite": payload["satellite"],
        "instrument": payload["instrument"],
        "window_start_utc": payload["window_start_utc"],
        "window_end_utc_exclusive": payload["window_end_utc_exclusive"],
        "window_duration_hours": payload["window_duration_hours"],
        "revisit": revisit,
        "evidence": dict(EVIDENCE),
        "evidence_note": EVIDENCE_NOTE,
    }


# Re-export for tests that patch through this module.
__all__ = [
    "compute_satellite_coverage",
    "run_satellite_access",
    "begin_access_run",
    "set_access_region",
    "finish_access_run",
    "finish_year_access_run",
    "get_access_daily",
    "get_access_cells",
    "get_access_revisit",
]
