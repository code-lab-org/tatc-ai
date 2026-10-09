"""Coverage-core accounting and TAT-C integration tests."""

from datetime import datetime, timedelta, timezone

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point as ShapelyPoint
from tatc.analysis.coverage import aggregate_observations, collect_observations, reduce_observations
from tatc.schemas import Instrument, Point, Satellite

from src import coverage_core as mc
from src.tatc_integration import create_orbit_from_omm, propagate_satellite


ISS_OMM = {
    "OBJECT_NAME": "ISS (ZARYA)",
    "OBJECT_ID": "1998-067A",
    "EPOCH": "2026-09-18T03:25:38.782272",
    "MEAN_MOTION": 15.49160218,
    "ECCENTRICITY": 0.00048228,
    "INCLINATION": 51.6307,
    "RA_OF_ASC_NODE": 200.0361,
    "ARG_OF_PERICENTER": 152.4527,
    "MEAN_ANOMALY": 207.6718,
    "EPHEMERIS_TYPE": 0,
    "CLASSIFICATION_TYPE": "U",
    "NORAD_CAT_ID": 25544,
    "ELEMENT_SET_NO": 999,
    "REV_AT_EPOCH": 58616,
    "BSTAR": 0.00011125122,
    "MEAN_MOTION_DOT": 5.718e-5,
    "MEAN_MOTION_DDOT": 0,
}


def _raw_observations(rows):
    if not rows:
        return gpd.GeoDataFrame(
            {
                "point_id": pd.Series([], dtype="int64"),
                "geometry": gpd.GeoSeries([], crs="EPSG:4326"),
                "satellite": pd.Series([], dtype="str"),
                "instrument": pd.Series([], dtype="str"),
                "start": pd.Series([], dtype="datetime64[ns, UTC]"),
                "end": pd.Series([], dtype="datetime64[ns, UTC]"),
                "epoch": pd.Series([], dtype="datetime64[ns, UTC]"),
            },
            geometry="geometry",
            crs="EPSG:4326",
        )
    frame = gpd.GeoDataFrame(
        [
            {
                "point_id": row[0],
                "geometry": ShapelyPoint(float(row[1]), float(row[2])),
                "satellite": "FixtureSat",
                "instrument": "FixtureScan",
                "start": pd.Timestamp(row[3]),
                "end": pd.Timestamp(row[4]),
                "epoch": pd.Timestamp(row[3]) + (pd.Timestamp(row[4]) - pd.Timestamp(row[3])) / 2,
            }
            for row in rows
        ],
        crs="EPSG:4326",
    )
    return frame


def _summarize(cells, raw, start, end):
    aggregated = aggregate_observations(raw)
    return mc._summarize_coverage(cells, aggregated, reduce_observations(aggregated), start, end)


def _two_cells():
    return [
        {"id": 0, "area_km2": 100.0},
        {"id": 1, "area_km2": 200.0},
    ]


def test_grid_uses_geodesic_cell_areas_and_returns_ids_in_row_major_order():
    cells = mc._build_grid([-112.0, 33.0, -110.0, 35.0], 2, 2)
    assert [cell["id"] for cell in cells] == [0, 1, 2, 3]
    assert all(cell["area_km2"] > 3000 for cell in cells)
    assert cells[0]["bounds"] == [-112.0, 33.0, -111.0, 34.0]


@pytest.mark.parametrize(
    "bbox, rows, columns, message",
    [
        ([170, -10, -170, 10], 6, 6, "antimeridian"),
        ([-180, -10, 0, 10], 6, 6, "less than 180"),
        ([-50, -5, 50, 5], 6, 6, "no wider than 10"),
        ([-5, -50, 5, 50], 6, 6, "no taller than 10"),
        ([-5, 5, 5, -5], 6, 6, "north_lat must exceed"),
        ([-181, -5, 5, 5], 6, 6, "longitudes must be within"),
    ],
)
def test_grid_rejects_invalid_or_unbounded_region(bbox, rows, columns, message):
    with pytest.raises(ValueError, match=message):
        mc._build_grid(bbox, rows, columns)


def test_grid_rejects_an_axis_larger_than_12_partitions():
    with pytest.raises(ValueError, match="grid_rows must be an integer between 1 and 12"):
        mc._build_grid([-10, -10, 10, 10], 13, 12)


def test_omm_epoch_must_be_fresh_for_now_and_requested_window():
    now = datetime(2026, 9, 25, tzinfo=timezone.utc)
    epoch, _ = mc._validate_omm_freshness(
        [{"NORAD_CAT_ID": 25994, "EPOCH": "2026-09-18T03:25:38.782272"}],
        25994,
        now,
    )
    assert epoch == datetime(2026, 9, 18, 3, 25, 38, 782272, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="freshness limit"):
        mc._validate_omm_freshness(
            [{"NORAD_CAT_ID": 25994, "EPOCH": "2026-09-01T00:00:00Z"}],
            25994,
            now,
        )
    with pytest.raises(ValueError, match="did not match"):
        mc._validate_omm_freshness(
            [{"NORAD_CAT_ID": 25544, "EPOCH": "2026-09-18T00:00:00Z"}],
            25994,
            now,
        )
    with pytest.raises(ValueError, match="within 14 days"):
        mc._validate_window_against_omm(
            datetime(2020, 1, 1, tzinfo=timezone.utc),
            datetime(2020, 1, 2, tzinfo=timezone.utc),
            epoch,
        )


def test_utc_day_accounting_excludes_an_interval_ending_at_midnight():
    start = datetime(2026, 1, 1, 23, tzinfo=timezone.utc)
    end = datetime(2026, 1, 2, 1, tzinfo=timezone.utc)
    raw = _raw_observations(
        [
            (0, -111.5, 33.5, "2026-01-01T23:15:00Z", "2026-01-02T00:00:00Z"),
            (1, -110.5, 33.5, "2026-01-02T00:00:00Z", "2026-01-02T00:30:00Z"),
        ]
    )
    summary = _summarize(_two_cells(), raw, start, end)
    first, second = summary["daily"]
    assert first["observed_cell_ids"] == [0]
    assert second["observed_cell_ids"] == [1]
    assert first["observed_area_km2"] == 100.0
    assert second["observed_area_km2"] == 200.0
    assert first["gap_area_km2"] == 200.0
    assert second["gap_area_km2"] == 100.0


def test_tatc_merges_overlaps_before_area_and_revisit():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 2, tzinfo=timezone.utc)
    raw = _raw_observations(
        [
            (0, -111.5, 33.5, "2026-01-01T00:00:00Z", "2026-01-01T01:00:00Z"),
            (0, -111.5, 33.5, "2026-01-01T00:30:00Z", "2026-01-01T02:00:00Z"),
            (0, -111.5, 33.5, "2026-01-01T03:00:00Z", "2026-01-01T04:00:00Z"),
        ]
    )
    summary = _summarize(_two_cells(), raw, start, end)
    assert summary["metrics"]["aggregated_observation_period_count"] == 2
    assert summary["metrics"]["mean_revisit_hours"] == 1.0
    assert summary["daily"][0]["observed_cell_ids"] == [0]
    assert summary["daily"][0]["observed_area_km2"] == 100.0
    # Per-cell stats come straight from TAT-C reduce_observations.
    assert summary["per_cell"][0] == {
        "access_count": 2,
        "mean_access_minutes": 90.0,
        "mean_revisit_hours": 1.0,
    }
    assert 1 not in summary["per_cell"]


def test_no_observations_report_all_gap_area_and_full_window_gap_with_null_revisit():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = start + timedelta(hours=3)
    summary = _summarize(_two_cells(), _raw_observations([]), start, end)
    assert summary["daily"][0]["observed_area_km2"] == 0.0
    assert summary["daily"][0]["observed_fraction"] == 0.0
    assert summary["daily"][0]["gap_area_km2"] == 300.0
    assert summary["daily"][0]["gap_fraction"] == 1.0
    assert summary["metrics"]["mean_revisit_hours"] is None
    assert summary["metrics"]["max_revisit_hours"] is None
    assert summary["metrics"]["longest_unobserved_gap_hours"] == 3.0


def test_zero_daily_gap_area_can_coexist_with_positive_temporal_gap():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    end = datetime(2026, 1, 2, tzinfo=timezone.utc)
    raw = _raw_observations(
        [
            (0, -111.5, 33.5, "2026-01-01T04:00:00Z", "2026-01-01T05:00:00Z"),
            (0, -111.5, 33.5, "2026-01-01T07:00:00Z", "2026-01-01T08:00:00Z"),
            (1, -110.5, 33.5, "2026-01-01T12:00:00Z", "2026-01-01T13:00:00Z"),
        ]
    )

    summary = _summarize(_two_cells(), raw, start, end)

    daily = summary["daily"][0]
    metrics = summary["metrics"]
    assert daily["observed_cell_ids"] == [0, 1]
    assert daily["observed_area_km2"] == 300.0
    assert daily["gap_area_km2"] == 0.0
    assert metrics["mean_revisit_hours"] == 2.0
    assert metrics["longest_unobserved_gap_hours"] == 16.0
    assert "not continuous observation" in metrics["longest_gap_definition"]
    assert "next aggregated access start minus the preceding access end" in metrics[
        "revisit_definition"
    ]


def test_real_tatc_collect_and_aggregate_use_fixture_omm_without_network():
    orbit = create_orbit_from_omm([ISS_OMM])
    midpoint = orbit.get_epoch() + timedelta(minutes=30)
    latitude, longitude, _ = propagate_satellite(orbit, midpoint)
    satellite = Satellite(
        name="Fixture satellite",
        orbit=orbit,
        instruments=[Instrument(name="Fixture scan", field_of_regard=110.0)],
    )
    observations = collect_observations(
        Point(id=0, latitude=latitude, longitude=longitude),
        satellite,
        midpoint - timedelta(minutes=20),
        midpoint + timedelta(minutes=20),
        omit_solar=True,
    )
    aggregated = aggregate_observations(observations)
    assert not observations.empty
    assert len(aggregated) == 1
    assert "revisit" in aggregated.columns
    assert pd.isna(aggregated.iloc[0]["revisit"])


