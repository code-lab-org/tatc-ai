"""Shared coverage math for modeled orbital access.

Satellite-neutral helpers used by every coverage path: UTC parsing, window
and bounding-box validation, geodesic grid building, OMM freshness checks,
and TAT-C summarization into daily rows plus revisit metrics. No
mission-specific assumptions live here.
"""

import math
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import geopandas as gpd
import pandas as pd
from dateutil import parser as date_parser
from pyproj import Geod
from tatc.utils import (
    field_of_regard_to_swath_width,
)

SHORT_WINDOW_MAX = timedelta(days=3)
MAX_YEAR_WINDOW = timedelta(days=366)
MAX_OMM_AGE = timedelta(days=14)
MAX_OMM_WINDOW_OFFSET = timedelta(days=14)
MAX_FUTURE_OMM_AGE = timedelta(days=1)
MAX_GRID_AXIS = 12
MAX_GRID_CELLS = MAX_GRID_AXIS * MAX_GRID_AXIS
MAX_YEAR_GRID_CELLS = 36
MAX_YEAR_CELL_CONFIGURATIONS = 72
MAX_YEAR_WORKERS = 4
MAX_CELL_AXIS_SPAN_DEG = 10.0
_WGS84_GEOD = Geod(ellps="WGS84")


def _public_satellites(stage: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Return staged satellite summaries without their OMM records."""
    return [
        {key: value for key, value in satellite.items() if key != "omm_records"}
        for satellite in stage["satellites"]
    ]


def _parse_utc(value: str, name: str) -> datetime:
    """Parse a timezone-qualified UTC ISO/date-time value."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be an explicit UTC date-time string")
    try:
        parsed = date_parser.parse(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"Could not parse {name} {value!r}: {exc}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must include a UTC offset, for example 2026-09-25T12:00:00Z")
    return parsed.astimezone(timezone.utc)


def _validate_bbox(roi_bbox: Sequence[float]) -> Tuple[float, float, float, float]:
    """Validate [west, south, east, north] in decimal degrees."""
    if not isinstance(roi_bbox, (list, tuple)) or len(roi_bbox) != 4:
        raise ValueError("roi_bbox must contain [west_lon, south_lat, east_lon, north_lat]")
    if any(isinstance(value, bool) for value in roi_bbox):
        raise ValueError("roi_bbox values must be finite numbers")
    try:
        west, south, east, north = (float(value) for value in roi_bbox)
    except (TypeError, ValueError) as exc:
        raise ValueError("roi_bbox values must be finite numbers") from exc
    if not all(math.isfinite(value) for value in (west, south, east, north)):
        raise ValueError("roi_bbox values must be finite numbers")
    if not -180 <= west < 180 or not -180 < east <= 180:
        raise ValueError("roi_bbox longitudes must be within [-180, 180]")
    if not -90 <= south < 90 or not -90 < north <= 90:
        raise ValueError("roi_bbox latitudes must be within [-90, 90]")
    if east <= west:
        raise ValueError("roi_bbox must not cross the antimeridian; east_lon must exceed west_lon")
    if east - west >= 180:
        raise ValueError("roi_bbox longitude span must be less than 180 degrees")
    if north <= south:
        raise ValueError("roi_bbox north_lat must exceed south_lat")
    return west, south, east, north


def _build_grid(
    roi_bbox: Sequence[float], grid_rows: int, grid_columns: int
) -> List[Dict[str, Any]]:
    west, south, east, north = _validate_bbox(roi_bbox)
    for name, value in (("grid_rows", grid_rows), ("grid_columns", grid_columns)):
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= MAX_GRID_AXIS:
            raise ValueError(f"{name} must be an integer between 1 and {MAX_GRID_AXIS}")
    if grid_rows * grid_columns > MAX_GRID_CELLS:
        raise ValueError(f"Grid must contain no more than {MAX_GRID_CELLS} cells")
    if (east - west) / grid_columns > MAX_CELL_AXIS_SPAN_DEG:
        raise ValueError(
            f"Each grid cell must be no wider than {MAX_CELL_AXIS_SPAN_DEG:g} degrees; "
            "increase grid_columns or use a smaller ROI"
        )
    if (north - south) / grid_rows > MAX_CELL_AXIS_SPAN_DEG:
        raise ValueError(
            f"Each grid cell must be no taller than {MAX_CELL_AXIS_SPAN_DEG:g} degrees; "
            "increase grid_rows or use a smaller ROI"
        )

    lon_step = (east - west) / grid_columns
    lat_step = (north - south) / grid_rows
    cells: List[Dict[str, Any]] = []
    for row in range(grid_rows):
        cell_south = south + row * lat_step
        cell_north = north if row == grid_rows - 1 else south + (row + 1) * lat_step
        for column in range(grid_columns):
            cell_west = west + column * lon_step
            cell_east = east if column == grid_columns - 1 else west + (column + 1) * lon_step
            area_m2, _ = _WGS84_GEOD.polygon_area_perimeter(
                [cell_west, cell_east, cell_east, cell_west],
                [cell_south, cell_south, cell_north, cell_north],
            )
            cell_id = row * grid_columns + column
            cells.append(
                {
                    "id": cell_id,
                    "center_lat": (cell_south + cell_north) / 2,
                    "center_lon": (cell_west + cell_east) / 2,
                    "bounds": [cell_west, cell_south, cell_east, cell_north],
                    "area_km2": abs(area_m2) / 1_000_000,
                }
            )
    return cells


def _parse_omm_epoch(value: Any) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("OMM record has a missing or invalid EPOCH")
    try:
        epoch = date_parser.parse(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"OMM record has an invalid EPOCH {value!r}") from exc
    if epoch.tzinfo is None:
        epoch = epoch.replace(tzinfo=timezone.utc)
    return epoch.astimezone(timezone.utc)


def _validate_omm_freshness(
    omm_records: Sequence[Dict[str, Any]],
    expected_norad_id: int,
    now: Optional[datetime] = None,
) -> Tuple[datetime, float]:
    if not isinstance(omm_records, (list, tuple)) or not omm_records:
        raise ValueError("CelesTrak returned no OMM element records")
    epochs = []
    for record in omm_records:
        try:
            norad_id = int(record["NORAD_CAT_ID"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("OMM record is missing a valid NORAD_CAT_ID") from exc
        if norad_id != expected_norad_id:
            raise ValueError(
                f"CelesTrak OMM NORAD_CAT_ID {norad_id} did not match requested "
                f"satellite {expected_norad_id}"
            )
        epochs.append(_parse_omm_epoch(record.get("EPOCH")))

    latest_epoch = max(epochs)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc)
    age = current - latest_epoch
    if age > MAX_OMM_AGE:
        raise ValueError(
            f"Latest CelesTrak OMM epoch {latest_epoch.isoformat()} is older than "
            f"the {MAX_OMM_AGE.days}-day freshness limit"
        )
    if age < -MAX_FUTURE_OMM_AGE:
        raise ValueError(
            f"Latest CelesTrak OMM epoch {latest_epoch.isoformat()} is more than "
            "one day in the future"
        )
    return latest_epoch, age.total_seconds() / 3600


def _validate_window_against_omm(
    start: datetime, end: datetime, omm_epoch: datetime
) -> None:
    if max(abs(start - omm_epoch), abs(end - omm_epoch)) > MAX_OMM_WINDOW_OFFSET:
        raise ValueError(
            "Coverage window must lie within 14 days of the latest OMM epoch "
            f"{omm_epoch.isoformat()} (our chosen request guardrail, not a "
            "guarantee of prediction accuracy)"
        )


def _as_utc_datetime(value: Any) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def _utc_days(start: datetime, end: datetime) -> Iterable[Tuple[date, datetime, datetime]]:
    day = start.date()
    while datetime.combine(day, datetime.min.time(), timezone.utc) < end:
        day_start = datetime.combine(day, datetime.min.time(), timezone.utc)
        yield day, max(start, day_start), min(end, day_start + timedelta(days=1))
        day += timedelta(days=1)


def _per_cell_stats(reduced: gpd.GeoDataFrame) -> Dict[int, Dict[str, Any]]:
    """TAT-C reduce_observations output keyed by cell ID."""
    stats: Dict[int, Dict[str, Any]] = {}
    for row in reduced.itertuples(index=False):
        revisit = getattr(row, "revisit", pd.NaT)
        stats[int(row.point_id)] = {
            "access_count": int(row.samples),
            "mean_access_minutes": round(pd.Timedelta(row.access).total_seconds() / 60, 3),
            "mean_revisit_hours": (
                None if pd.isna(revisit) else round(pd.Timedelta(revisit).total_seconds() / 3600, 3)
            ),
        }
    return stats


def _summarize_coverage(
    cells: Sequence[Dict[str, Any]],
    aggregated: gpd.GeoDataFrame,
    reduced: gpd.GeoDataFrame,
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    """Turn TAT-C's merged accesses into daily area rows and gap metrics.

    TAT-C's aggregate_observations has already merged overlapping accesses,
    across satellites too, and reports each access's revisit; this adds the
    per-UTC-day area accounting and window-edge gaps TAT-C does not compute.
    """
    by_cell: Dict[int, List[Tuple[datetime, datetime]]] = {cell["id"]: [] for cell in cells}
    revisits_hours: List[float] = []
    access_minutes: List[float] = []
    contributions: Counter = Counter()
    for row in aggregated.itertuples(index=False):
        cell_id = int(row.point_id)
        if cell_id not in by_cell:
            continue
        observation_start = max(start, _as_utc_datetime(row.start))
        observation_end = min(end, _as_utc_datetime(row.end))
        if observation_end <= observation_start:
            continue
        by_cell[cell_id].append((observation_start, observation_end))
        contributions.update({name.strip() for name in str(row.satellite).split(",")})
        access_minutes.append((observation_end - observation_start).total_seconds() / 60)
        revisit = getattr(row, "revisit", pd.NaT)
        if not pd.isna(revisit):
            revisit_hours = pd.Timedelta(revisit).total_seconds() / 3600
            if revisit_hours >= 0:
                revisits_hours.append(revisit_hours)

    for intervals in by_cell.values():
        intervals.sort()

    total_area = sum(cell["area_km2"] for cell in cells)
    daily = []
    for day, day_start, day_end in _utc_days(start, end):
        observed_ids = sorted(
            cell_id
            for cell_id, intervals in by_cell.items()
            if any(left < day_end and right > day_start for left, right in intervals)
        )
        observed_area = sum(cells[cell_id]["area_km2"] for cell_id in observed_ids)
        gap_ids = sorted(set(by_cell) - set(observed_ids))
        gap_area = max(0.0, total_area - observed_area)
        daily.append(
            {
                "utc_date": day.isoformat(),
                "window_start_utc": day_start.isoformat().replace("+00:00", "Z"),
                "window_end_utc_exclusive": day_end.isoformat().replace("+00:00", "Z"),
                "observed_area_km2": round(observed_area, 3),
                "observed_fraction": round(observed_area / total_area, 6) if total_area else 0.0,
                "gap_area_km2": round(gap_area, 3),
                "gap_fraction": round(gap_area / total_area, 6) if total_area else 0.0,
                "observed_cell_ids": observed_ids,
                "gap_cell_ids": gap_ids,
            }
        )

    all_gaps_hours: List[float] = []
    observed_cell_ids = [cell_id for cell_id, intervals in by_cell.items() if intervals]
    for intervals in by_cell.values():
        if not intervals:
            all_gaps_hours.append((end - start).total_seconds() / 3600)
            continue
        previous_end = start
        for left, right in intervals:
            if left > previous_end:
                all_gaps_hours.append((left - previous_end).total_seconds() / 3600)
            previous_end = max(previous_end, right)
        if previous_end < end:
            all_gaps_hours.append((end - previous_end).total_seconds() / 3600)

    def stat(values: Sequence[float], fn) -> Optional[float]:
        return round(fn(values), 3) if values else None

    return {
        "daily": daily,
        "metrics": {
            "grid_cell_count": len(cells),
            "observed_cell_count": len(observed_cell_ids),
            "never_observed_cell_count": len(cells) - len(observed_cell_ids),
            "aggregated_observation_period_count": sum(len(v) for v in by_cell.values()),
            "mean_revisit_hours": stat(revisits_hours, mean),
            "median_revisit_hours": stat(revisits_hours, median),
            "max_revisit_hours": stat(revisits_hours, max),
            "revisit_sample_count": len(revisits_hours),
            "mean_access_minutes": stat(access_minutes, mean),
            "longest_unobserved_gap_hours": round(max(all_gaps_hours), 3) if all_gaps_hours else 0.0,
            "access_periods_by_satellite": dict(sorted(contributions.items())),
            "revisit_definition": (
                "For each grid cell, the next aggregated access start minus the "
                "preceding access end; mean_revisit_hours is the arithmetic mean "
                "of available revisit intervals and is null when there are no "
                "paired visits in the window."
            ),
            "longest_gap_definition": (
                "Longest continuous period with no modeled access for any grid "
                "cell, including the window edges; a never-observed cell "
                "contributes the full window duration. A daily gap_area_km2 of "
                "0 means every cell center had at least one modeled access that "
                "UTC day; this is not continuous observation and can coexist "
                "with a positive longest_unobserved_gap_hours."
            ),
        },
        "per_cell": _per_cell_stats(reduced),
    }


def _geojson_cells(
    cells: Sequence[Dict[str, Any]], per_cell: Dict[int, Dict[str, Any]]
) -> List[Dict[str, Any]]:
    empty = {"access_count": 0, "mean_access_minutes": None, "mean_revisit_hours": None}
    result = []
    for cell in cells:
        west, south, east, north = cell["bounds"]
        result.append(
            {
                "id": cell["id"],
                "center": [round(cell["center_lon"], 7), round(cell["center_lat"], 7)],
                "area_km2": round(cell["area_km2"], 3),
                **per_cell.get(cell["id"], empty),
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [[[west, south], [east, south], [east, north], [west, north], [west, south]]],
                },
            }
        )
    return result


