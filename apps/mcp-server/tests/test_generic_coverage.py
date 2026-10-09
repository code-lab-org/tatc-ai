"""Generic coverage tests: explicit geometry, provenance, model-friendly errors."""

from datetime import datetime, timedelta, timezone

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point as ShapelyPoint

from src import generic_coverage as gc

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


def _raw(rows):
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
    return gpd.GeoDataFrame(
        [
            {
                "point_id": row[0],
                "geometry": ShapelyPoint(float(row[1]), float(row[2])),
                "satellite": "ISS modeled",
                "instrument": "modeled scan",
                "start": pd.Timestamp(row[3]),
                "end": pd.Timestamp(row[4]),
                "epoch": pd.Timestamp(row[3])
                + (pd.Timestamp(row[4]) - pd.Timestamp(row[3])) / 2,
            }
            for row in rows
        ],
        crs="EPSG:4326",
    )


# A second fixture orbit: the ISS elements a quarter orbit later under
# another catalog number, so constellation runs have two distinct satellites.
TWIN_OMM = dict(ISS_OMM, OBJECT_NAME="ISS TWIN", NORAD_CAT_ID=99999, MEAN_ANOMALY=297.6718)
FIXTURES = {
    "ISS": {"norad_id": 25544, "name": "ISS (ZARYA)", "omm": [ISS_OMM]},
    "TWIN": {"norad_id": 99999, "name": "ISS TWIN", "omm": [TWIN_OMM]},
    "THIRD": {"norad_id": 25545, "name": "ISS THIRD", "omm": [dict(ISS_OMM, NORAD_CAT_ID=25545)]},
}


def _mock_sat(monkeypatch):
    monkeypatch.setattr(
        gc.celestrak_client,
        "get_satellite_info",
        lambda identifier: FIXTURES.get(identifier.upper(), FIXTURES["ISS"]),
    )


def _mock_collect(monkeypatch, present_ids=(0,)):
    def fake_collect(point, satellites, start, end, **kwargs):
        if point.id in present_ids:
            return _raw(
                [
                    (
                        point.id,
                        point.longitude,
                        point.latitude,
                        start.isoformat(),
                        (start + timedelta(minutes=5)).isoformat(),
                    )
                ]
            )
        return _raw([])

    monkeypatch.setattr(gc, "collect_multi_observations", fake_collect)


NOW = datetime(2026, 9, 18, 4, tzinfo=timezone.utc)


def _staged(monkeypatch, end_iso="2026-09-18T04:30:00Z", bbox=(-2.0, -1.0, 2.0, 1.0)):
    _mock_sat(monkeypatch)
    draft = gc.begin_access_run("ISS", "2026-09-18T04:00:00Z", end_iso, now=NOW)
    gc.set_access_region(draft["draft_id"], list(bbox))
    return draft["draft_id"]


def _full(summary):
    from src import result_store

    return result_store.get_result(summary["result_id"])


def test_for_path_reports_provenance_and_daily_accounting(monkeypatch):
    _mock_collect(monkeypatch, present_ids=(0,))
    result = _full(
        gc.finish_access_run(
            _staged(monkeypatch),
            grid_rows=1,
            grid_columns=2,
            field_of_regard_deg=50.0,
            geometry_reference="https://example.com/iss-payload",
        )
    )
    assert result["satellite"] == "ISS (ZARYA)"
    assert result["satellites"][0]["norad_id"] == 25544
    geometry = result["geometry_model"]
    assert geometry["per_satellite"][0]["field_of_regard_deg"] == 50.0
    assert geometry["geometry_input_kind"] == "field_of_regard_deg"
    assert geometry["geometry_reference"] == "https://example.com/iss-payload"
    assert geometry["per_satellite"][0]["omm_mean_altitude_km"] > 300
    assert result["daily"][0]["observed_cell_ids"] == [0]
    assert result["daily"][0]["gap_cell_ids"] == [1]
    assert result["evidence"]["actual_acquisitions_known"] is False
    assert result["evidence"]["illumination_known_or_filtered"] is False
    assert "does not establish" in result["interpretation"]


def test_swath_path_converts_at_omm_altitude(monkeypatch):
    _mock_collect(monkeypatch, present_ids=(0, 1))
    result = _full(
        gc.finish_access_run(
            _staged(monkeypatch),
            grid_rows=1,
            grid_columns=2,
            swath_width_km=300.0,
            geometry_reference="https://example.com/iss-swath",
        )
    )
    geometry = result["geometry_model"]
    assert geometry["geometry_input_kind"] == "swath_width_km"
    assert geometry["geometry_input_value"] == 300.0
    assert 0 < geometry["per_satellite"][0]["field_of_regard_deg"] < 60
    assert "Converted swath_width_km" in geometry["conversion_note"]
    assert result["daily"][0]["gap_cell_ids"] == []


def test_missing_or_doubled_geometry_fails_with_guidance(monkeypatch):
    _mock_collect(monkeypatch)
    draft_id = _staged(monkeypatch)
    with pytest.raises(ValueError, match="Missing geometry"):
        gc.finish_access_run(draft_id, geometry_reference="https://example.com/x")
    with pytest.raises(ValueError, match="exactly one"):
        gc.finish_access_run(
            draft_id,
            field_of_regard_deg=50.0,
            swath_width_km=300.0,
            geometry_reference="https://example.com/x",
        )
    with pytest.raises(ValueError, match="geometry_reference is required"):
        gc.finish_access_run(draft_id, field_of_regard_deg=50.0, geometry_reference="  ")


def test_empty_identifier_rejected(monkeypatch):
    _mock_sat(monkeypatch)
    with pytest.raises(ValueError, match="satellite_identifier"):
        gc.begin_access_run("  ", "2026-09-18T04:00:00Z", None, now=NOW)


def _run_one(monkeypatch, present_ids=(0,)):
    _mock_collect(monkeypatch, present_ids=present_ids)
    return gc.finish_access_run(
        _staged(monkeypatch, end_iso="2026-09-19T04:00:00Z"),
        grid_rows=1,
        grid_columns=2,
        field_of_regard_deg=50.0,
        geometry_reference="https://example.com/iss",
    )


@pytest.fixture(autouse=True)
def clean_access_store():
    from src import result_store

    result_store.clear()
    yield
    result_store.clear()


def test_run_saves_compact_summary_with_id(monkeypatch):
    summary = _run_one(monkeypatch)
    assert summary["result_id"]
    assert "daily" not in summary
    assert "grid_cells" not in summary
    assert summary["summary_counts"]["total_days"] == 2
    assert summary["summary_counts"]["observed_days"] == 1
    assert summary["summary_counts"]["gap_days"] == 1
    assert summary["evidence"]["modeled_access_only"] is True
    assert "could view" in summary.get("evidence_note", "")


def test_daily_fetch_pages_saved_rows(monkeypatch):
    summary = _run_one(monkeypatch)
    page = gc.get_access_daily(summary["result_id"], 0, 31)
    assert page["total_days"] == 2
    assert page["daily"][0]["observed_cell_ids"] == [0]
    assert page["daily"][1]["observed_cell_ids"] == []
    assert page["evidence"]["actual_acquisitions_known"] is False
    second = gc.get_access_daily(summary["result_id"], 1, 1)
    assert len(second["daily"]) == 1
    assert second["daily"][0]["utc_date"] == "2026-09-19"
    empty = gc.get_access_daily(summary["result_id"], 5, 10)
    assert empty["daily"] == []


def test_cells_fetch_filters_status(monkeypatch):
    summary = _run_one(monkeypatch, present_ids=(0,))
    everything = gc.get_access_cells(summary["result_id"])
    assert everything["total_cells"] == 2
    assert everything["cell_count"] == 2
    assert everything["geojson"]["type"] == "FeatureCollection"
    observed = gc.get_access_cells(summary["result_id"], "observed")
    assert [f["properties"]["id"] for f in observed["geojson"]["features"]] == [0]
    gaps = gc.get_access_cells(summary["result_id"], "gaps")
    assert [f["properties"]["id"] for f in gaps["geojson"]["features"]] == [1]
    with pytest.raises(ValueError, match="all, observed, or gaps"):
        gc.get_access_cells(summary["result_id"], "sometimes")


def test_revisit_fetch_reports_numbers(monkeypatch):
    summary = _run_one(monkeypatch)
    revisit = gc.get_access_revisit(summary["result_id"])
    assert revisit["revisit"]["grid_cell_count"] == 2
    assert revisit["revisit"]["observed_cell_count"] == 1
    assert "revisit_definition" in revisit["revisit"]
    assert "longest_gap_definition" in revisit["revisit"]
    assert revisit["evidence"]["modeled_access_only"] is True


def test_fetch_rejects_unknown_id():
    with pytest.raises(ValueError, match="Unknown result_id"):
        gc.get_access_daily("missing")
    with pytest.raises(ValueError, match="Unknown result_id"):
        gc.get_access_cells("missing")
    with pytest.raises(ValueError, match="Unknown result_id"):
        gc.get_access_revisit("missing")


def _begin_raw(monkeypatch, start_iso, end_iso):
    _mock_sat(monkeypatch)
    start = datetime(2026, 9, 18, 4, tzinfo=timezone.utc)
    return gc.begin_access_run("ISS", start_iso, end_iso, now=start)


def _begin_one(monkeypatch):
    draft = _begin_raw(
        monkeypatch, "2026-09-18T04:00:00Z", "2026-09-19T04:00:00Z"
    )
    gc.set_access_region(draft["draft_id"], [-2.0, -1.0, 2.0, 1.0])
    return draft


def test_begin_returns_draft_with_altitude(monkeypatch):
    draft = _begin_raw(
        monkeypatch, "2026-09-18T04:00:00Z", "2026-09-19T04:00:00Z"
    )
    assert draft["draft_id"].startswith("draft_")
    assert draft["satellite"] == "ISS (ZARYA)"
    assert draft["satellites"][0]["norad_id"] == 25544
    assert draft["satellites"][0]["omm_mean_altitude_km"] > 300
    assert "omm_records" not in draft["satellites"][0]
    assert draft["roi_bbox"] is None
    assert "region" in draft["next_step"]


def test_set_region_stores_bounds(monkeypatch):
    draft = _begin_raw(
        monkeypatch, "2026-09-18T04:00:00Z", "2026-09-19T04:00:00Z"
    )
    out = gc.set_access_region(draft["draft_id"], [-2.0, -1.0, 2.0, 1.0])
    assert out["draft_id"] == draft["draft_id"]
    assert out["roi_bbox"] == [-2.0, -1.0, 2.0, 1.0]
    assert "finish" in out["next_step"]
    with pytest.raises(ValueError, match="antimeridian"):
        gc.set_access_region(
            draft["draft_id"], [170.0, -10.0, -170.0, 10.0]
        )
    with pytest.raises(ValueError, match="Unknown draft_id"):
        gc.set_access_region("draft_missing", [-2.0, -1.0, 2.0, 1.0])


def test_begin_defaults_end_to_24h(monkeypatch):
    _mock_sat(monkeypatch)
    start = datetime(2026, 9, 18, 4, tzinfo=timezone.utc)
    draft = gc.begin_access_run(
        "ISS",
        "2026-09-18T04:00:00Z",
        None,
        now=start,
    )
    assert draft["window_start_utc"] == "2026-09-18T04:00:00Z"
    assert draft["window_end_utc_exclusive"] == "2026-09-19T04:00:00Z"
    with pytest.raises(ValueError, match="UTC offset"):
        gc.begin_access_run(
            "ISS", "2026-09-18T04:00:00", None, now=start
        )


def test_begin_rejects_overlong_and_far_from_epoch_windows(monkeypatch):
    _mock_sat(monkeypatch)
    start = datetime(2026, 9, 18, 4, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="no longer than 366 days"):
        gc.begin_access_run(
            "ISS",
            "2026-09-18T04:00:00Z",
            "2027-09-25T04:00:00Z",
            now=start,
        )
    with pytest.raises(ValueError, match="must start within 14 days"):
        gc.begin_access_run(
            "ISS",
            "2026-10-20T04:00:00Z",
            "2026-10-25T04:00:00Z",
            now=start,
        )


def test_finish_needs_region_first(monkeypatch):
    draft = _begin_raw(
        monkeypatch, "2026-09-18T04:00:00Z", "2026-09-19T04:00:00Z"
    )
    with pytest.raises(ValueError, match="no region yet"):
        gc.finish_access_run(
            draft["draft_id"],
            grid_rows=1,
            grid_columns=2,
            field_of_regard_deg=50.0,
            geometry_reference="https://example.com/x",
        )


def test_finish_swath_path_and_errors(monkeypatch):
    _mock_sat(monkeypatch)
    _mock_collect(monkeypatch, present_ids=(0, 1))
    draft = _begin_one(monkeypatch)
    finished = gc.finish_access_run(
        draft["draft_id"],
        grid_rows=1,
        grid_columns=2,
        swath_width_km=300.0,
        geometry_reference="https://example.com/iss-swath",
    )
    assert finished["geometry_model"]["geometry_input_kind"] == "swath_width_km"
    with pytest.raises(ValueError, match="Unknown draft_id"):
        gc.finish_access_run(
            "draft_missing",
            field_of_regard_deg=50.0,
            geometry_reference="https://example.com/x",
        )
    with pytest.raises(ValueError, match="Missing geometry"):
        gc.finish_access_run(
            draft["draft_id"], geometry_reference="https://example.com/x"
        )


def _begin_long(monkeypatch, days=10):
    _mock_sat(monkeypatch)
    start = datetime(2026, 9, 18, 4, tzinfo=timezone.utc)
    end = start + timedelta(days=days)
    draft = gc.begin_access_run(
        "ISS",
        "2026-09-18T04:00:00Z",
        end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        now=start,
    )
    gc.set_access_region(draft["draft_id"], [-2.0, -1.0, 2.0, 1.0])
    return draft


def _fake_year_collect(task):
    _, _, cell_id, lat, lon, start, end = task
    rows = []
    day = start
    while day < end:
        rows.append(
            (
                cell_id,
                lon,
                lat,
                day.isoformat(),
                (day + timedelta(minutes=5)).isoformat(),
            )
        )
        day += timedelta(days=1)
    return _raw(rows)


def test_finish_saves_10day_result(monkeypatch):
    monkeypatch.setattr(
        gc, "_collect_cell_observations", _fake_year_collect
    )
    draft = _begin_long(monkeypatch, days=10)
    summary = gc.finish_access_run(
        draft["draft_id"],
        grid_rows=1,
        grid_columns=1,
        field_of_regard_deg=50.0,
        geometry_reference="https://example.com/iss",
        _max_workers=1,
    )
    assert summary["result_id"]
    assert summary["summary_counts"]["total_days"] == 11
    assert summary["summary_counts"]["observed_days"] == 10
    assert summary["geometry_model"]["per_satellite"][0]["field_of_regard_deg"] == 50.0
    from src import result_store

    stored = result_store.get_result(summary["result_id"])
    assert any("drift" in limit for limit in stored["method"]["limits"])
    page = gc.get_access_daily(summary["result_id"], 0, 5)
    assert page["total_days"] == 11
    assert len(page["daily"]) == 5
    second = gc.get_access_daily(summary["result_id"], 5, 5)
    assert len(second["daily"]) == 5
    revisit = gc.get_access_revisit(summary["result_id"])
    assert revisit["revisit"]["grid_cell_count"] == 1


def test_finish_picks_grid_limit_and_workers_from_window(monkeypatch):
    monkeypatch.setattr(gc, "_collect_cell_observations", _fake_year_collect)

    class NoPool:
        def __init__(self, *args, **kwargs):
            raise AssertionError("small jobs must not start worker processes")

    monkeypatch.setattr(gc, "ProcessPoolExecutor", NoPool)
    # 49 cells: fine for a 1-day window; too little work to start workers.
    short = gc.finish_access_run(
        _staged(monkeypatch, end_iso="2026-09-19T04:00:00Z"),
        grid_rows=7,
        grid_columns=7,
        field_of_regard_deg=50.0,
        geometry_reference="https://example.com/x",
        _max_workers=4,
    )
    assert short["summary_counts"]["grid_cell_count"] == 49
    with pytest.raises(ValueError, match="36 cells for windows over 3 days"):
        gc.finish_access_run(
            _begin_long(monkeypatch, days=10)["draft_id"],
            grid_rows=7,
            grid_columns=7,
            field_of_regard_deg=50.0,
            geometry_reference="https://example.com/x",
            _max_workers=1,
        )


def test_finish_real_execution_10day(monkeypatch):
    _mock_sat(monkeypatch)
    draft = _begin_long(monkeypatch, days=10)
    summary = gc.finish_access_run(
        draft["draft_id"],
        grid_rows=1,
        grid_columns=1,
        field_of_regard_deg=50.0,
        geometry_reference="https://example.com/iss",
        _max_workers=1,
    )
    assert summary["summary_counts"]["total_days"] == 11
    assert summary["summary_counts"]["observed_days"] >= 1
    assert summary["metrics"]["observed_cell_count"] == 1
    cells = gc.get_access_cells(summary["result_id"])
    assert cells["total_cells"] == 1


def _real_run(monkeypatch, satellites, days=1, **finish):
    _mock_sat(monkeypatch)
    end = NOW + timedelta(days=days)
    draft = gc.begin_access_run(
        satellites, "2026-09-18T04:00:00Z", end.strftime("%Y-%m-%dT%H:%M:%SZ"), now=NOW
    )
    gc.set_access_region(draft["draft_id"], [-112.5, 33.0, -111.5, 34.0])
    return gc.finish_access_run(
        draft["draft_id"],
        grid_rows=1,
        grid_columns=1,
        field_of_regard_deg=110.0,
        geometry_reference="https://example.com/x",
        _max_workers=1,
        **finish,
    )


def test_constellation_merges_both_satellites_in_tatc(monkeypatch):
    solo = _real_run(monkeypatch, "ISS", days=2)
    pair = _real_run(monkeypatch, "ISS, TWIN", days=2)
    assert pair["satellite"] == "ISS (ZARYA) + ISS TWIN"
    assert [s["norad_id"] for s in pair["satellites"]] == [25544, 99999]
    by_satellite = pair["metrics"]["access_periods_by_satellite"]
    assert set(by_satellite) == {"ISS (ZARYA)", "ISS TWIN"}
    assert by_satellite["ISS (ZARYA)"] == solo["metrics"]["aggregated_observation_period_count"]
    assert pair["metrics"]["aggregated_observation_period_count"] > solo["metrics"]["aggregated_observation_period_count"]
    assert pair["metrics"]["longest_unobserved_gap_hours"] <= solo["metrics"]["longest_unobserved_gap_hours"]
    cells = gc.get_access_cells(pair["result_id"])
    assert cells["geojson"]["features"][0]["properties"]["access_count"] == (
        pair["metrics"]["aggregated_observation_period_count"]
    )


def test_constellation_rejects_repeats_and_too_many(monkeypatch):
    _mock_sat(monkeypatch)
    with pytest.raises(ValueError, match="more than once"):
        gc.begin_access_run("ISS, 25544", "2026-09-18T04:00:00Z", None, now=NOW)
    with pytest.raises(ValueError, match="at most 6 satellites"):
        gc.begin_access_run(",".join(["ISS"] * 7), "2026-09-18T04:00:00Z", None, now=NOW)


def test_long_window_caps_cells_times_satellites(monkeypatch):
    _mock_sat(monkeypatch)
    # 36 cells is fine for one satellite over 10 days, too many for three.
    draft = gc.begin_access_run(
        "ISS, TWIN, THIRD", "2026-09-18T04:00:00Z", "2026-09-28T04:00:00Z", now=NOW
    )
    gc.set_access_region(draft["draft_id"], [-2.0, -1.0, 2.0, 1.0])
    with pytest.raises(ValueError, match="36 cells x 3 satellites is too many"):
        gc.finish_access_run(
            draft["draft_id"], grid_rows=6, grid_columns=6,
            field_of_regard_deg=50.0, geometry_reference="https://example.com/x",
        )


def test_daylight_only_drops_night_accesses_with_tatc(monkeypatch):
    every = _real_run(monkeypatch, "ISS", days=3)
    daylight = _real_run(monkeypatch, "ISS", days=3, daylight_only=True)
    all_count = every["metrics"]["aggregated_observation_period_count"]
    day_count = daylight["metrics"]["aggregated_observation_period_count"]
    assert 0 < day_count < all_count
    assert daylight["daylight_only"] is True
    assert daylight["evidence"]["illumination_known_or_filtered"] is True
    from src import result_store

    limits = result_store.get_result(daylight["result_id"])["method"]["limits"]
    assert any("req_target_sunlit" in limit for limit in limits)
    with pytest.raises(ValueError, match="daylight_only must be true or false"):
        _real_run(monkeypatch, "ISS", daylight_only="yes")
