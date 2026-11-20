"""UTC interval arithmetic for established and assumed applicability.

The requested study window is half-open ``[start, end)``.  A configuration's
established applicability intervals and any researcher-accepted assumption use
the same rule.  Calculations are clipped to the intersection of the window and
the union of those intervals, so unresolved time is never reported as a zero.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .conical_config import ConicalConfiguration, iso_utc
from .pointed_config import PointedConfiguration

Interval = Tuple[datetime, datetime]
Segment = Dict[str, Any]
UNMODELED_REASON = "no_modeled_interval"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("interval endpoints must include a UTC offset")
    return value.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    """Return an ISO-8601 UTC string with a Z suffix."""
    return iso_utc(_utc(value))


def parse_iso(value: Any) -> datetime:
    """Parse an ISO-8601 UTC time or return an aware datetime unchanged."""
    if isinstance(value, datetime):
        return _utc(value)
    if isinstance(value, str):
        return _utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
    raise ValueError(f"Unsupported time value {value!r}")


def iso_rows(intervals: Iterable[Interval]) -> List[Dict[str, str]]:
    return [
        {"start_utc": iso(start), "end_utc_exclusive": iso(end)}
        for start, end in intervals
    ]


def merge_intervals(intervals: Iterable[Interval]) -> List[Interval]:
    """Sort and merge overlapping or touching half-open intervals."""
    ordered = sorted(
        (_utc(start), _utc(end))
        for start, end in intervals
        if _utc(start) < _utc(end)
    )
    merged: List[Interval] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
        else:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def clip_intervals(
    intervals: Iterable[Interval], start: datetime, end: datetime
) -> List[Interval]:
    """Return the non-empty intersections with one half-open window."""
    start, end = _utc(start), _utc(end)
    if start >= end:
        return []
    clipped = []
    for left, right in intervals:
        low, high = max(_utc(left), start), min(_utc(right), end)
        if low < high:
            clipped.append((low, high))
    return clipped


def subtract_intervals(
    intervals: Iterable[Interval], cuts: Iterable[Interval]
) -> List[Interval]:
    """Return ``intervals`` minus ``cuts`` as sorted disjoint intervals."""
    remaining = list(intervals)
    for cut_start, cut_end in merge_intervals(cuts):
        next_remaining: List[Interval] = []
        for start, end in remaining:
            if cut_end <= start or cut_start >= end:
                next_remaining.append((start, end))
                continue
            if start < cut_start:
                next_remaining.append((start, cut_start))
            if cut_end < end:
                next_remaining.append((cut_end, end))
        remaining = next_remaining
    return sorted(remaining)


def intersect_intervals(
    left: Iterable[Interval], right: Iterable[Interval]
) -> List[Interval]:
    intersections = []
    for left_start, left_end in left:
        for right_start, right_end in right:
            start, end = max(_utc(left_start), _utc(right_start)), min(
                _utc(left_end), _utc(right_end)
            )
            if start < end:
                intersections.append((start, end))
    return merge_intervals(intersections)


class ApplicabilityAssumption(BaseModel):
    """A researcher-accepted extension of a configuration's applicability."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    configuration_id: str = Field(min_length=1)
    start_utc: AwareDatetime
    end_utc_exclusive: AwareDatetime
    rationale: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def _check_order(self) -> "ApplicabilityAssumption":
        if _utc(self.start_utc) >= _utc(self.end_utc_exclusive):
            raise ValueError(
                "applicability assumption start_utc must precede "
                "end_utc_exclusive"
            )
        return self

    def interval(self) -> Interval:
        return (_utc(self.start_utc), _utc(self.end_utc_exclusive))

    def public(self) -> Dict[str, Any]:
        return {
            "configuration_id": self.configuration_id,
            "start_utc": iso_utc(self.start_utc),
            "end_utc_exclusive": iso_utc(self.end_utc_exclusive),
            "rationale": self.rationale,
        }


def configuration_established_intervals(
    configuration: ConicalConfiguration | PointedConfiguration,
) -> List[Interval]:
    """Return every established interval recorded on a configuration."""
    intervals: List[Interval] = [
        (
            _utc(configuration.applicability.start_utc),
            _utc(configuration.applicability.end_utc_exclusive),
        )
    ]
    for interval in getattr(configuration, "additional_applicability", []):
        intervals.append(
            (_utc(interval.start_utc), _utc(interval.end_utc_exclusive))
        )
    return merge_intervals(intervals)


def compute_coverage(
    *,
    configuration_id: str,
    established: Sequence[Interval],
    assumptions: Sequence[ApplicabilityAssumption],
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    """Intersect one configuration's dates with the requested window."""
    start, end = _utc(start), _utc(end)
    if start >= end:
        raise ValueError("requested window start must precede its end")
    established_inside = clip_intervals(established, start, end)
    assumed_inside = clip_intervals(
        [assumption.interval() for assumption in assumptions], start, end
    )
    accepted = merge_intervals([*established_inside, *assumed_inside])
    unresolved = subtract_intervals([(start, end)], accepted)
    assumed_only = clip_intervals(
        subtract_intervals(assumed_inside, established_inside), start, end
    )
    established_modeled = intersect_intervals(established_inside, accepted)

    modeled: List[Segment] = []
    for left, right in established_modeled:
        modeled.append(
            {
                "start_utc": iso_utc(left),
                "end_utc_exclusive": iso_utc(right),
                "basis": "established",
            }
        )
    for left, right in assumed_only:
        modeled.append(
            {
                "start_utc": iso_utc(left),
                "end_utc_exclusive": iso_utc(right),
                "basis": "assumption",
            }
        )
    modeled.sort(key=lambda row: parse_iso(row["start_utc"]))
    if not modeled:
        status = "no_calculable_overlap"
    elif unresolved:
        status = "partial"
    elif assumed_only:
        status = "complete_under_assumptions"
    else:
        status = "complete"

    def hours(rows: Iterable[Interval]) -> float:
        return round(
            sum((_utc(right) - _utc(left)).total_seconds() for left, right in rows)
            / 3600,
            6,
        )

    return {
        "configuration_id": configuration_id,
        "requested_window": {
            "start_utc": iso_utc(start),
            "end_utc_exclusive": iso_utc(end),
        },
        "established_intervals": iso_rows(established),
        "assumed_intervals": iso_rows(assumed_only),
        "modeled_intervals": modeled,
        "unresolved_intervals": [
            {
                "start_utc": iso_utc(left),
                "end_utc_exclusive": iso_utc(right),
                "reason": UNMODELED_REASON,
            }
            for left, right in unresolved
        ],
        "assumptions": [assumption.public() for assumption in assumptions],
        "status": status,
        "established_hours": hours(established_inside),
        "assumed_hours": hours(assumed_only),
        "modeled_hours": hours(accepted),
        "unresolved_hours": hours(unresolved),
    }


def coverage_for_configuration(
    configuration: ConicalConfiguration | PointedConfiguration,
    *,
    assumptions: Sequence[ApplicabilityAssumption],
    start: datetime,
    end: datetime,
) -> Dict[str, Any]:
    """Compute coverage for a configuration and its accepted assumptions."""
    return compute_coverage(
        configuration_id=configuration.configuration_id,
        established=configuration_established_intervals(configuration),
        assumptions=assumptions,
        start=start,
        end=end,
    )


def combined_coverage_status(coverages: Sequence[Dict[str, Any]]) -> str:
    """Return the study-wide coverage status for several configurations."""
    statuses = [coverage.get("status") for coverage in coverages]
    if any(status == "no_calculable_overlap" for status in statuses):
        return "no_calculable_overlap"
    if any(status == "partial" for status in statuses):
        return "partial"
    if any(status == "complete_under_assumptions" for status in statuses):
        return "complete_under_assumptions"
    return "complete"


def result_completeness(coverage_status: str, has_omissions: bool) -> str:
    """Return the public completeness label for a proposal or result."""
    if has_omissions or coverage_status == "partial":
        return "partial"
    if coverage_status == "complete_under_assumptions":
        return "complete_under_assumptions"
    if coverage_status == "no_calculable_overlap":
        return "unresolved"
    return "complete"


def modeled_windows(coverage_payload: Dict[str, Any]) -> List[Interval]:
    """Return calculation intervals from a coverage payload."""
    return [
        (parse_iso(row["start_utc"]), parse_iso(row["end_utc_exclusive"]))
        for row in coverage_payload.get("modeled_intervals", [])
    ]


def interval_rows_for_day(
    rows: Sequence[dict], day_start: datetime, day_end: datetime
) -> List[dict]:
    """Clip interval rows to a UTC day, preserving basis or reason labels."""
    clipped = []
    for row in rows:
        start = parse_iso(row["start_utc"])
        end = parse_iso(row["end_utc_exclusive"])
        low = max(start, _utc(day_start))
        high = min(end, _utc(day_end))
        if low >= high:
            continue
        item = {
            "start_utc": iso_utc(low),
            "end_utc_exclusive": iso_utc(high),
        }
        if row.get("basis"):
            item["basis"] = row["basis"]
        if row.get("reason"):
            item["reason"] = row["reason"]
        clipped.append(item)
    return clipped


def day_segments(
    intervals: Sequence[dict], day_start: datetime, day_end: datetime
) -> List[dict]:
    """Alias for world-facing day clipping used by accounting."""
    return interval_rows_for_day(intervals, day_start, day_end)


def segments_duration_hours(segments: Sequence[dict]) -> float:
    return round(
        sum(
            (
                parse_iso(row["end_utc_exclusive"])
                - parse_iso(row["start_utc"])
            ).total_seconds()
            for row in segments
        )
        / 3600,
        6,
    )
