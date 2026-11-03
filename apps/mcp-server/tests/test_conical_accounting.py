"""Conical event accounting: UTC days, cell unions, spacing, envelopes."""

from datetime import datetime, timedelta, timezone

import pytest

from src.conical_accounting import normalize_events, summarize_events


def _cell(cell_id, lon, lat, area):
    return {
        "id": cell_id,
        "center_lon": lon,
        "center_lat": lat,
        "bounds": [lon - 0.5, lat - 0.5, lon + 0.5, lat + 0.5],
        "area_km2": area,
    }


def _event(cell_id, epoch, envelope_start=None, envelope_end=None, config="cfg"):
    return {
        "cell_id": cell_id,
        "epoch": epoch,
        "envelope_start": envelope_start or epoch - timedelta(minutes=2),
        "envelope_end": envelope_end or epoch + timedelta(minutes=2),
        "satellite": "GCOM-W1",
        "instrument": "AMSR2",
        "configuration_id": config,
        "configuration_revision": "1",
    }


def _summary(cells, raw, start, end):
    events = normalize_events(raw, start, end)
    return events, summarize_events(cells, events, start, end)


def test_two_crossings_same_cell_count_area_once_per_day():
    cells = [_cell(0, 0, 0, 100.0)]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    first = datetime(2026, 1, 1, 4, tzinfo=timezone.utc)
    second = first + timedelta(hours=4)
    events, summary = _summary(
        cells,
        [_event(0, first), _event(0, second)],
        start,
        end,
    )
    assert len(events) == 2
    assert summary["daily"][0]["event_count"] == 2
    assert summary["daily"][0]["observed_cell_ids"] == [0]
    assert summary["daily"][0]["observed_area_km2"] == 100.0
    assert summary["metrics"]["event_count"] == 2
    assert summary["metrics"]["daily_cell_visit_count"] == 1
    assert summary["cells"][0]["event_count"] == 2
    assert summary["event_spacing"][0]["event_spacing_hours"] == [4.0]
    assert summary["metrics"]["mean_event_spacing_hours"] == 4.0


def test_midnight_and_exclusive_end_boundaries():
    cells = [_cell(0, 0, 0, 100.0), _cell(1, 1, 0, 200.0), _cell(2, 2, 0, 300.0)]
    start = datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 2, 1, 0, tzinfo=timezone.utc)
    before = datetime(2026, 1, 1, 23, 59, 59, tzinfo=timezone.utc)
    at_midnight = datetime(2026, 1, 2, 0, 0, 0, tzinfo=timezone.utc)
    at_end = end
    events, summary = _summary(
        cells,
        [
            _event(
                0,
                before,
                envelope_start=before - timedelta(minutes=1),
                envelope_end=at_midnight + timedelta(minutes=2),
            ),
            _event(1, at_midnight),
            _event(2, at_end),
        ],
        start,
        end,
    )
    assert [event["cell_id"] for event in events] == [0, 1]
    first_day, second_day = summary["daily"]
    assert first_day["utc_date"] == "2026-01-01"
    assert first_day["observed_cell_ids"] == [0]
    assert first_day["observed_area_km2"] == 100.0
    assert second_day["utc_date"] == "2026-01-02"
    assert second_day["observed_cell_ids"] == [1]
    assert second_day["observed_area_km2"] == 200.0
    # The envelope crossed midnight, but its epoch did not: no cell on both days.
    assert set(first_day["observed_cell_ids"]).isdisjoint(second_day["observed_cell_ids"])
    # The event exactly at the exclusive end belongs to no modeled day.
    assert 2 not in first_day["observed_cell_ids"]
    assert 2 not in second_day["observed_cell_ids"]


def test_envelope_is_not_continuous_access_or_duration():
    cells = [_cell(0, 0, 0, 100.0)]
    start = datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)
    end = datetime(2026, 1, 2, 1, 0, tzinfo=timezone.utc)
    epoch = datetime(2026, 1, 1, 23, 58, tzinfo=timezone.utc)
    _, summary = _summary(
        cells,
        [
            _event(
                0,
                epoch,
                envelope_start=epoch - timedelta(minutes=1),
                envelope_end=datetime(2026, 1, 2, 0, 3, tzinfo=timezone.utc),
            )
        ],
        start,
        end,
    )
    assert summary["daily"][0]["observed_cell_ids"] == [0]
    assert summary["daily"][1]["observed_cell_ids"] == []
    assert summary["daily"][0]["envelope_period_count"] == 1
    assert summary["metrics"]["continuous_viewing_duration_available"] is False
    assert summary["metrics"]["continuous_uncovered_duration_available"] is False
    assert "mean_access_minutes" not in summary["metrics"]
    assert "longest_unobserved_gap_hours" not in summary["metrics"]
    assert "envelope" in summary["definitions"]


def test_duplicate_visits_do_not_inflate_daily_area():
    cells = [_cell(0, 0, 0, 100.0), _cell(1, 1, 0, 200.0)]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    epoch = datetime(2026, 1, 1, 6, tzinfo=timezone.utc)
    # Repeated exact event, plus an event for the same cell at another time.
    events, summary = _summary(
        cells,
        [_event(0, epoch), _event(0, epoch), _event(0, epoch + timedelta(hours=1))],
        start,
        end,
    )
    assert len(events) == 2
    assert summary["daily"][0]["observed_cell_ids"] == [0]
    assert summary["daily"][0]["observed_area_km2"] == 100.0
    assert summary["daily"][0]["event_count"] == 2
    assert summary["metrics"]["daily_cell_visit_count"] == 1


def test_spacing_is_null_with_fewer_than_two_distinct_events():
    cells = [_cell(0, 0, 0, 100.0), _cell(1, 1, 0, 100.0)]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    only = datetime(2026, 1, 1, 4, tzinfo=timezone.utc)
    paired = datetime(2026, 1, 1, 6, tzinfo=timezone.utc)
    _, summary = _summary(
        cells,
        [_event(0, only), _event(1, paired), _event(1, paired + timedelta(hours=3))],
        start,
        end,
    )
    one, two = summary["event_spacing"]
    assert one["event_spacing_hours"] == []
    assert one["mean_event_spacing_hours"] is None
    assert one["spacing_status"] == "null_fewer_than_two_distinct_events"
    assert two["event_spacing_hours"] == [3.0]
    assert two["mean_event_spacing_hours"] == 3.0
    assert summary["metrics"]["cells_with_event_spacing"] == 1
    assert summary["metrics"]["event_spacing_sample_count"] == 1


def test_simultaneous_duplicate_is_one_spacing_opportunity():
    cells = [_cell(0, 0, 0, 100.0)]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    epoch = datetime(2026, 1, 1, 4, tzinfo=timezone.utc)
    _, summary = _summary(
        cells,
        [
            _event(0, epoch, config="a"),
            _event(0, epoch, config="b"),
            _event(0, epoch + timedelta(hours=2), config="a"),
        ],
        start,
        end,
    )
    assert summary["metrics"]["event_spacing_sample_count"] == 1
    assert summary["event_spacing"][0]["event_spacing_hours"] == [2.0]
    assert summary["event_spacing"][0]["contributors"] == ["a", "b"]


def test_area_sum_and_rounding_are_within_one_output_unit():
    cells = [_cell(0, 0, 0, 100.1234), _cell(1, 1, 0, 200.2345)]
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    epoch = datetime(2026, 1, 1, 4, tzinfo=timezone.utc)
    _, summary = _summary(cells, [_event(1, epoch)], start, end)
    expected = 200.2345
    assert summary["daily"][0]["observed_area_km2"] == pytest.approx(expected, abs=0.001)
    assert summary["daily"][0]["gap_area_km2"] == pytest.approx(100.1234, abs=0.001)
    assert summary["total_roi_area_km2"] == pytest.approx(300.3579, abs=0.001)
