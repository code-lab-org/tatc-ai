"""Event-based accounting for conical viewing epochs.

TAT-C supplies each valid viewing epoch and the broader field-of-regard
period.  This module applies the study's UTC-day, cell-union, and spacing
definitions.  It never turns a field-of-regard period into continuous
viewing or uncovered-duration metrics.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from statistics import mean, median
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .coverage_core import _utc_days

ACCOUNTING_METHOD = "conical_events_v1"
POINTED_ACCOUNTING_METHOD = "pointed_events_v1"
MIXED_ACCOUNTING_METHOD = "mixed_events_union_v1"
MIXED_CONFIGURATION_ACCOUNTING_METHOD = "mixed_configuration_events_v1"

EVENT_DEFINITION = (
    "A valid TAT-C conical viewing epoch at a grid-cell center: the time the "
    "cell center crosses the scanned cone within the accepted scan sector."
)
POINTED_EVENT_DEFINITION = (
    "A valid TAT-C pointed-instrument viewing epoch at a grid-cell center under "
    "the accepted roll, frame, and field-of-view configuration."
)
DAILY_DEFINITION = (
    "A cell is visited on a UTC day when at least one valid event epoch falls "
    "in the half-open interval [day start, next day start). The cell's WGS84 "
    "geodesic area is counted once that day, regardless of how many events it has."
)
SPACING_DEFINITION = (
    "For one cell, the differences between consecutive distinct viewing-event "
    "epochs in the study window, in hours. Simultaneous duplicate events count "
    "once. A cell with fewer than two distinct events has null spacing and "
    "contributes no spacing samples."
)
ENVELOPE_DEFINITION = (
    "The TAT-C field-of-regard period (start/end) is retained only as envelope "
    "metadata for each event. It is not continuous viewing time, not an "
    "access duration, and not evidence that a cell was observed for the whole "
    "period. A period crossing midnight does not create access on both days."
)
UNAVAILABLE_DEFINITION = (
    "Continuous viewing duration and continuous uncovered duration are not "
    "available for this event-based result. The field-of-regard envelope is "
    "wider than the valid scan: its duration would overstate viewing time, and "
    "the gaps between discrete events are not instrument-uncovered intervals."
)
CELL_CENTER_DEFINITION = (
    "Each whole grid cell is classified from modeled events at its center only. "
    "An observed cell does not mean every point in the cell or region was viewed."
)


def _as_utc(value: Any) -> datetime:
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    elif isinstance(value, datetime):
        parsed = value
    else:
        parsed = value.to_pydatetime()  # pandas.Timestamp
    if parsed.tzinfo is None:
        raise ValueError("event times must include a UTC offset")
    return parsed.astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _stats(values: Sequence[float]) -> Dict[str, Optional[float]]:
    if not values:
        return {
            "mean_event_spacing_hours": None,
            "median_event_spacing_hours": None,
            "min_event_spacing_hours": None,
            "max_event_spacing_hours": None,
        }
    return {
        "mean_event_spacing_hours": round(mean(values), 6),
        "median_event_spacing_hours": round(median(values), 6),
        "min_event_spacing_hours": round(min(values), 6),
        "max_event_spacing_hours": round(max(values), 6),
    }


def _envelope_periods(events: Sequence[Dict[str, Any]]) -> set[Tuple[Any, ...]]:
    return {
        (event["cell_id"], *period)
        for event in events
        for period in (
            (
                (
                    contributor["configuration_id"],
                    contributor["envelope_start_utc"],
                    contributor["envelope_end_utc"],
                )
                for contributor in event["contributor_configurations"]
            )
            if event.get("envelope_semantics") == "contributor_hull"
            else ((event["envelope_start_utc"], event["envelope_end_utc"]),)
        )
    }


def _merge_event_epochs(
    raw_events: Iterable[Dict[str, Any]],
    start: datetime,
    end: datetime,
    *,
    applicability_start: Optional[datetime] = None,
    applicability_end: Optional[datetime] = None,
    mixed: bool = False,
) -> List[Dict[str, Any]]:
    """Share UTC filtering and exact cell/epoch grouping between result shapes."""
    start, end = _as_utc(start), _as_utc(end)
    app_start = _as_utc(applicability_start) if applicability_start else start
    app_end = _as_utc(applicability_end) if applicability_end else end
    merged: Dict[Tuple[int, datetime], Dict[str, Any]] = {}
    for raw in raw_events:
        cell_id = int(raw["cell_id"])
        epoch = _as_utc(raw["epoch"])
        if not (start <= epoch < end):
            continue
        if not (app_start <= epoch < app_end):
            continue
        configuration_id = str(
            raw["configuration_id"] if mixed else raw.get("configuration_id", "")
        )
        envelope_start = _as_utc(raw.get("envelope_start", epoch))
        envelope_end = _as_utc(raw.get("envelope_end", epoch))
        key = (cell_id, epoch)
        existing = merged.get(key)
        if existing is None:
            existing = {
                "cell_id": cell_id,
                "target_longitude_deg": raw.get("target_longitude_deg"),
                "target_latitude_deg": raw.get("target_latitude_deg"),
                "epoch": epoch,
                "envelope_start": envelope_start,
                "envelope_end": envelope_end,
                "satellite": str(raw.get("satellite", "")),
                "instrument": str(raw.get("instrument", "")),
                "configuration_id": configuration_id,
                "configuration_revision": str(raw.get("configuration_revision", "")),
                "contributors": {} if mixed else {configuration_id},
            }
            merged[key] = existing
        else:
            existing["envelope_start"] = min(existing["envelope_start"], envelope_start)
            existing["envelope_end"] = max(existing["envelope_end"], envelope_end)
            if not mixed:
                existing["contributors"].add(configuration_id)
        if mixed:
            contributor = existing["contributors"].get(configuration_id)
            if contributor is None:
                contributor = {
                    "configuration_id": configuration_id,
                    "configuration_revision": str(raw["configuration_revision"]),
                    "instrument": str(raw["instrument"]),
                    "satellite": str(raw["satellite"]),
                    "envelope_start": envelope_start,
                    "envelope_end": envelope_end,
                }
                existing["contributors"][configuration_id] = contributor
            else:
                contributor["envelope_start"] = min(
                    contributor["envelope_start"], envelope_start
                )
                contributor["envelope_end"] = max(
                    contributor["envelope_end"], envelope_end
                )
    return sorted(merged.values(), key=lambda event: (event["epoch"], event["cell_id"]))


def _envelope_fields(start: datetime, end: datetime) -> Dict[str, Any]:
    return {
        "envelope_start_utc": _iso(start),
        "envelope_end_utc": _iso(end),
        "envelope_duration_hours": round((end - start).total_seconds() / 3600, 6),
    }


def _normalized_event_rows(
    groups: Sequence[Dict[str, Any]], *, mixed: bool
) -> List[Dict[str, Any]]:
    events = []
    for index, event in enumerate(groups):
        row = {
            "event_id": f"E{index:06d}",
            "cell_id": event["cell_id"],
            "target_longitude_deg": event["target_longitude_deg"],
            "target_latitude_deg": event["target_latitude_deg"],
            "epoch_utc": _iso(event["epoch"]),
            "utc_date": event["epoch"].date().isoformat(),
            **_envelope_fields(event["envelope_start"], event["envelope_end"]),
        }
        if mixed:
            row.update(
                configuration_id=None,
                configuration_revision=None,
                envelope_semantics="contributor_hull",
                contributors=sorted(event["contributors"]),
                contributor_configurations=[
                    {
                        **{
                            field: contributor[field]
                            for field in (
                                "configuration_id",
                                "configuration_revision",
                                "instrument",
                                "satellite",
                            )
                        },
                        **_envelope_fields(
                            contributor["envelope_start"],
                            contributor["envelope_end"],
                        ),
                    }
                    for contributor in (
                        event["contributors"][key]
                        for key in sorted(event["contributors"])
                    )
                ],
            )
        else:
            row.update(
                satellite=event["satellite"],
                instrument=event["instrument"],
                configuration_id=event["configuration_id"],
                configuration_revision=event["configuration_revision"],
                contributors=sorted(event["contributors"]),
            )
        row["_epoch"] = event["epoch"]
        events.append(row)
    return events


def normalize_events(
    raw_events: Iterable[Dict[str, Any]],
    start: datetime,
    end: datetime,
    *,
    applicability_start: Optional[datetime] = None,
    applicability_end: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Filter and group by exact cell/epoch; nearby epochs remain distinct."""
    return _normalized_event_rows(
        _merge_event_epochs(
            raw_events,
            start,
            end,
            applicability_start=applicability_start,
            applicability_end=applicability_end,
        ),
        mixed=False,
    )


def normalize_mixed_events(
    raw_events: Iterable[Dict[str, Any]], start: datetime, end: datetime
) -> List[Dict[str, Any]]:
    """Group only exact simultaneous mixed-configuration events by cell."""
    return _normalized_event_rows(
        _merge_event_epochs(raw_events, start, end, mixed=True), mixed=True
    )


def summarize_events(
    cells: Sequence[Dict[str, Any]],
    events: Sequence[Dict[str, Any]],
    start: datetime,
    end: datetime,
    *,
    accounting_method: str = ACCOUNTING_METHOD,
    event_definition: str = EVENT_DEFINITION,
) -> Dict[str, Any]:
    """Build daily rows, cell rows, spacing rows, and compact metrics."""
    start, end = _as_utc(start), _as_utc(end)
    cells_by_id = {int(cell["id"]): cell for cell in cells}
    total_area = sum(cell["area_km2"] for cell in cells)
    by_cell: Dict[int, List[Dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_cell[event["cell_id"]].append(event)
    for rows in by_cell.values():
        rows.sort(key=lambda event: event["_epoch"])

    daily: List[Dict[str, Any]] = []
    for day, day_start, day_end in _utc_days(start, end):
        day_events = [
            event for event in events if day_start <= event["_epoch"] < day_end
        ]
        observed_ids = sorted({event["cell_id"] for event in day_events})
        observed_area = sum(cells_by_id[cell]["area_km2"] for cell in observed_ids)
        gap_ids = sorted(set(cells_by_id) - set(observed_ids))
        envelope_periods = _envelope_periods(day_events)
        daily.append(
            {
                "utc_date": day.isoformat(),
                "window_start_utc": _iso(day_start),
                "window_end_utc_exclusive": _iso(day_end),
                "observed_area_km2": round(observed_area, 3),
                "observed_fraction": (
                    round(observed_area / total_area, 6) if total_area else 0.0
                ),
                "gap_area_km2": round(max(0.0, total_area - observed_area), 3),
                "gap_fraction": (
                    round(max(0.0, total_area - observed_area) / total_area, 6)
                    if total_area
                    else 0.0
                ),
                "observed_cell_ids": observed_ids,
                "gap_cell_ids": gap_ids,
                "event_count": len(day_events),
                "envelope_period_count": len(envelope_periods),
                "membership": "epoch in half-open UTC day [start, end)",
            }
        )

    cell_rows: List[Dict[str, Any]] = []
    spacing_rows: List[Dict[str, Any]] = []
    all_spacings: List[float] = []
    daily_visit_count = sum(len(row["observed_cell_ids"]) for row in daily)
    for cell_id in sorted(cells_by_id):
        cell = cells_by_id[cell_id]
        cell_events = by_cell.get(cell_id, [])
        epochs = [event["_epoch"] for event in cell_events]
        spacings = [
            round((right - left).total_seconds() / 3600, 6)
            for left, right in zip(epochs[:-1], epochs[1:])
        ]
        all_spacings.extend(spacings)
        observed_days = sorted({event["_epoch"].date().isoformat() for event in cell_events})
        contributors = sorted(
            {
                contributor
                for event in cell_events
                for contributor in event["contributors"]
            }
        )
        west, south, east, north = cell["bounds"]
        stats = _stats(spacings)
        row = {
            "id": cell_id,
            "center": [round(cell["center_lon"], 7), round(cell["center_lat"], 7)],
            "area_km2": round(cell["area_km2"], 3),
            "status": "observed" if cell_events else "gap",
            "event_count": len(cell_events),
            "observed_day_count": len(observed_days),
            "observed_dates": observed_days,
            "first_event_utc": _iso(epochs[0]) if epochs else None,
            "last_event_utc": _iso(epochs[-1]) if epochs else None,
            "event_spacing_sample_count": len(spacings),
            **stats,
            "contributors": contributors,
            "geometry": {
                "type": "Polygon",
                "coordinates": [
                    [
                        [west, south],
                        [east, south],
                        [east, north],
                        [west, north],
                        [west, south],
                    ]
                ],
            },
        }
        cell_rows.append(row)
        spacing_rows.append(
            {
                "cell_id": cell_id,
                "center": row["center"],
                "area_km2": row["area_km2"],
                "status": row["status"],
                "event_count": len(cell_events),
                "distinct_event_count": len(epochs),
                "spacing_status": (
                    "available"
                    if spacings
                    else "null_fewer_than_two_distinct_events"
                ),
                "event_spacing_sample_count": len(spacings),
                "event_spacing_hours": spacings,
                **stats,
                "contributors": contributors,
            }
        )

    envelope_periods_all = _envelope_periods(events)
    spacing_stats = _stats(all_spacings)
    metrics = {
        "grid_cell_count": len(cells),
        "observed_cell_count": sum(1 for row in cell_rows if row["event_count"] > 0),
        "never_observed_cell_count": sum(
            1 for row in cell_rows if row["event_count"] == 0
        ),
        "event_count": len(events),
        "envelope_period_count": len(envelope_periods_all),
        "daily_cell_visit_count": daily_visit_count,
        "cells_with_event_spacing": sum(
            1 for row in spacing_rows if row["event_spacing_sample_count"] > 0
        ),
        "event_spacing_sample_count": len(all_spacings),
        **spacing_stats,
        "continuous_viewing_duration_available": False,
        "continuous_uncovered_duration_available": False,
        "accounting_method": accounting_method,
    }
    definitions = {
        "event": event_definition,
        "daily_cell": DAILY_DEFINITION,
        "event_spacing": SPACING_DEFINITION,
        "event_spacing_metrics": (
            "mean, median, min, and max over all per-cell spacing samples in "
            "the result; null when no cell has at least two distinct events."
        ),
        "envelope": ENVELOPE_DEFINITION,
        "envelope_period_count": (
            "Counts distinct per-cell envelope intervals. For a combined mixed "
            "result, contributor intervals are counted by configuration, cell, "
            "start, and end; a combined hull is not counted as a TAT-C period."
        ),
        "unavailable_metrics": UNAVAILABLE_DEFINITION,
        "cell_center_sampling": CELL_CENTER_DEFINITION,
        "time_semantics": "Half-open UTC intervals [start, end); an epoch exactly at a boundary belongs only to the interval starting there.",
    }
    return {
        "daily": daily,
        "cells": cell_rows,
        "event_spacing": spacing_rows,
        "metrics": metrics,
        "definitions": definitions,
        "total_roi_area_km2": round(total_area, 3),
    }
