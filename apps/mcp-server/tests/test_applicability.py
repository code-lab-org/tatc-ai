"""UTC applicability interval and partial-day accounting tests."""

from datetime import datetime, timedelta, timezone

import pytest

from src.applicability import (
    ApplicabilityAssumption,
    compute_coverage,
    configuration_established_intervals,
)
from src.conical_accounting import summarize_events

UTC = timezone.utc


def _dt(day=4, hour=0, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=UTC)


def _assumption(configuration_id, start, end, rationale="test assumption"):
    return ApplicabilityAssumption(
        configuration_id=configuration_id,
        start_utc=start,
        end_utc_exclusive=end,
        rationale=rationale,
    )


def test_assumption_extends_established_coverage_without_claiming_it_is_source_established():
    coverage = compute_coverage(
        configuration_id="instrument-a",
        established=[(_dt(hour=0), _dt(hour=6))],
        assumptions=[_assumption("instrument-a", _dt(hour=6), _dt(hour=9))],
        start=_dt(hour=0),
        end=_dt(hour=9),
    )
    assert coverage["status"] == "complete_under_assumptions"
    assert coverage["unresolved_intervals"] == []
    assert [row["basis"] for row in coverage["modeled_intervals"]] == [
        "established",
        "assumption",
    ]
    assert coverage["assumed_hours"] == 3
    assert coverage["modeled_hours"] == 9


def test_disjoint_established_intervals_leave_the_gap_unresolved():
    coverage = compute_coverage(
        configuration_id="instrument-a",
        established=[
            (_dt(hour=0), _dt(hour=2)),
            (_dt(hour=4), _dt(hour=6)),
        ],
        assumptions=[],
        start=_dt(hour=0),
        end=_dt(hour=6),
    )
    assert coverage["status"] == "partial"
    assert coverage["unresolved_intervals"] == [
        {
            "start_utc": "2026-10-04T02:00:00Z",
            "end_utc_exclusive": "2026-10-04T04:00:00Z",
            "reason": "no_modeled_interval",
        }
    ]
    assert coverage["modeled_hours"] == 4


def test_exact_half_open_boundaries_are_not_double_counted():
    coverage = compute_coverage(
        configuration_id="instrument-a",
        established=[(_dt(hour=0), _dt(hour=6))],
        assumptions=[_assumption("instrument-a", _dt(hour=6), _dt(hour=9))],
        start=_dt(hour=0),
        end=_dt(hour=9),
    )
    assert coverage["modeled_hours"] == 9
    assert coverage["unresolved_hours"] == 0
    # The assumption starts exactly where the established interval ends.
    assert coverage["modeled_intervals"][0]["end_utc_exclusive"] == (
        coverage["modeled_intervals"][1]["start_utc"]
    )


def test_configuration_accepts_disjoint_additional_intervals():
    from src.conical_config import ConicalConfiguration
    import json
    from pathlib import Path

    data = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "data"
            / "configurations"
            / "amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json"
        ).read_text(encoding="utf-8")
    )
    data["additional_applicability"] = [
        {
            "start_utc": "2027-01-01T00:00:00Z",
            "end_utc_exclusive": "2027-01-02T00:00:00Z",
            "evidence_ids": data["applicability"]["evidence_ids"],
            "basis": "Test-only second established interval.",
        }
    ]
    configuration = ConicalConfiguration.model_validate(data)
    intervals = configuration_established_intervals(configuration)
    assert len(intervals) == 2
    assert intervals[1][0] == _dt(day=4, hour=0).replace(
        year=2027, month=1, day=1
    )


def test_overlapping_additional_intervals_are_rejected():
    from src.conical_config import ConicalConfiguration
    import json
    from pathlib import Path

    data = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "data"
            / "configurations"
            / "amsr2-gcom-w1-conical-product-sector-2026-10-27.1.json"
        ).read_text(encoding="utf-8")
    )
    data["additional_applicability"] = [
        {
            "start_utc": "2026-10-01T00:00:00Z",
            "end_utc_exclusive": "2026-10-02T00:00:00Z",
            "evidence_ids": data["applicability"]["evidence_ids"],
            "basis": "Overlaps the primary interval.",
        }
    ]
    with pytest.raises(ValueError, match="must not overlap"):
        ConicalConfiguration.model_validate(data)


def _cell(cell_id=0):
    return {
        "id": cell_id,
        "area_km2": 100.0,
        "center_lat": 0.0,
        "center_lon": 0.0,
        "bounds": [-0.5, -0.5, 0.5, 0.5],
    }


def test_unmodeled_day_has_null_area_and_is_not_a_gap():
    start = _dt(hour=0)
    end = _dt(day=6, hour=0)
    coverage = compute_coverage(
        configuration_id="instrument-a",
        established=[(start, _dt(day=4, hour=11))],
        assumptions=[],
        start=start,
        end=end,
    )
    summary = summarize_events(
        [_cell()],
        [],
        start,
        end,
        modeled_intervals=coverage["modeled_intervals"],
        unresolved_intervals=coverage["unresolved_intervals"],
    )
    first, second = summary["daily"]
    assert first["coverage_status"] == "partial_day"
    assert first["event_count"] == 0
    assert first["modeled_intervals"]
    assert first["unresolved_intervals"] == [
        {
            "start_utc": "2026-10-04T11:00:00Z",
            "end_utc_exclusive": "2026-10-05T00:00:00Z",
            "reason": "no_modeled_interval",
        }
    ]
    assert second["coverage_status"] == "unmodeled"
    assert second["event_count"] is None
    assert second["observed_area_km2"] is None
    assert second["gap_area_km2"] is None
    assert second["observed_cell_ids"] == []
    assert second["gap_cell_ids"] == []
    assert summary["metrics"]["unmodeled_day_count"] == 1
    assert summary["metrics"]["partial_day_count"] == 1


def test_overlapping_assumptions_are_merged_before_counting_hours():
    coverage = compute_coverage(
        configuration_id="instrument-a",
        established=[(_dt(hour=0), _dt(hour=2))],
        assumptions=[
            _assumption("instrument-a", _dt(hour=2), _dt(hour=4)),
            _assumption("instrument-a", _dt(hour=3), _dt(hour=5)),
        ],
        start=_dt(hour=0),
        end=_dt(hour=5),
    )
    assert coverage["status"] == "complete_under_assumptions"
    assert coverage["assumed_hours"] == 3
    assert coverage["modeled_hours"] == 5
    assert coverage["modeled_intervals"][-1]["end_utc_exclusive"] == (
        "2026-10-04T05:00:00Z"
    )


def test_summary_counts_ignore_unmodeled_days_and_partial_day_gaps():
    from src.conical_study import _event_summary_counts

    accounting = {
        "daily": [
            {"coverage_status": "full_day", "event_count": 0},
            {"coverage_status": "partial_day", "event_count": 0},
            {"coverage_status": "unmodeled", "event_count": None},
        ],
        "metrics": {
            "grid_cell_count": 1,
            "observed_cell_count": 0,
            "never_observed_cell_count": 1,
            "event_count": 0,
            "envelope_period_count": 0,
            "modeled_hours": 24,
            "unresolved_hours": 24,
        },
    }
    counts = _event_summary_counts(accounting)
    assert counts["gap_days"] == 1
    assert counts["partial_days"] == 1
    assert counts["unmodeled_days"] == 1
    assert counts["days_with_events"] == 0


def test_combined_day_is_partial_when_another_configuration_is_unmodeled():
    start = _dt(hour=0)
    end = _dt(day=6, hour=0)
    summary = summarize_events(
        [_cell()],
        [],
        start,
        end,
        modeled_intervals_by_configuration={
            "instrument-a": [
                {"start_utc": "2026-10-04T00:00:00Z", "end_utc_exclusive": "2026-10-05T00:00:00Z"}
            ],
            "instrument-b": [],
        },
        unresolved_intervals_by_configuration={
            "instrument-a": [],
            "instrument-b": [
                {"start_utc": "2026-10-04T00:00:00Z", "end_utc_exclusive": "2026-10-05T00:00:00Z"}
            ],
        },
    )
    row = summary["daily"][0]
    assert row["coverage_status"] == "partial_day"
    assert row["partial_coverage_configuration_ids"] == ["instrument-b"]
    assert row["modeled_hours"] == 24
    assert summary["daily"][1]["coverage_status"] == "unmodeled"


def test_spacing_across_unresolved_time_is_not_reported_as_observed():
    start = _dt(hour=0)
    end = _dt(hour=14)
    events = [
        {
            "cell_id": 0,
            "target_longitude_deg": 0.0,
            "target_latitude_deg": 0.0,
            "_epoch": _dt(hour=0),
            "envelope_start_utc": "2026-10-04T00:00:00Z",
            "envelope_end_utc": "2026-10-04T00:00:00Z",
            "contributors": set(),
        },
        {
            "cell_id": 0,
            "target_longitude_deg": 0.0,
            "target_latitude_deg": 0.0,
            "_epoch": _dt(hour=12),
            "envelope_start_utc": "2026-10-04T12:00:00Z",
            "envelope_end_utc": "2026-10-04T12:00:00Z",
            "contributors": set(),
        },
    ]
    summary = summarize_events(
        [_cell()],
        events,
        start,
        end,
        modeled_intervals=[
            {"start_utc": "2026-10-04T00:00:00Z", "end_utc_exclusive": "2026-10-04T06:00:00Z"},
            {"start_utc": "2026-10-04T12:00:00Z", "end_utc_exclusive": "2026-10-04T14:00:00Z"},
        ],
        unresolved_intervals=[
            {"start_utc": "2026-10-04T06:00:00Z", "end_utc_exclusive": "2026-10-04T12:00:00Z"}
        ],
    )
    row = summary["event_spacing"][0]
    assert row["event_spacing_sample_count"] == 0
    assert row["unresolved_spacing_sample_count"] == 1
    assert row["spacing_status"] == "null_spacing_crosses_unresolved_applicability"
    assert summary["metrics"]["max_event_spacing_hours"] is None
