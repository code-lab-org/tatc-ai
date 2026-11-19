"""Native dissolved ground-track geometry and Study-pane tool contracts."""

from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from shapely.geometry import shape
from tatc.analysis import compute_ground_track
from tatc.schemas import ConicalInstrument, Instrument, PointedInstrument, Satellite

from src import ground_track_view, server
from src.tatc_integration import (
    _sample_times,
    create_orbit_from_omm,
    generate_ground_track,
)
from src.schema_formatter import format_ground_track_response
from test_coverage_tools import CATALOG_SATELLITE, call, scene, text

START = datetime(2021, 6, 5, 7, 20, tzinfo=timezone.utc)


def messages(end):
    orbit = create_orbit_from_omm(CATALOG_SATELLITE["omm"])
    return format_ground_track_response(
        "25544", generate_ground_track(orbit, START, end, 120)
    )


@pytest.mark.parametrize(
    "instrument, cls",
    [
        ({"name": "Basic", "field_of_regard": 60}, Instrument),
        (
            {
                "name": "Pointed",
                "field_of_regard": 100,
                "cross_track_field_of_view": 10,
                "along_track_field_of_view": 2,
                "roll_angle": 15,
            },
            PointedInstrument,
        ),
        (
            {"name": "Conical", "cone_angle": 47.5, "along_track_field_of_view": 1},
            ConicalInstrument,
        ),
    ],
)
@pytest.mark.parametrize("dissolve", [True, False])
def test_dissolved_scene_matches_native_tatc(instrument, cls, dissolve):
    end = START + timedelta(hours=2)
    result = ground_track_view.build_ground_track_scene(
        CATALOG_SATELLITE,
        messages(end),
        START,
        end,
        120,
        instrument,
        dissolve,
    )
    satellite = Satellite(
        name="ISS (25544)",
        orbit=create_orbit_from_omm(CATALOG_SATELLITE["omm"]),
        instruments=[cls(**instrument)],
    )
    expected = compute_ground_track(
        satellite, _sample_times(START, end, 120), dissolve_orbits=dissolve
    )
    geometries = [g for g in expected.geometry if not g.is_empty]
    actual = result["ground_area"]["features"]
    assert len(actual) == len(geometries)
    assert all(
        shape(feature["geometry"]).equals(geometry)
        for feature, geometry in zip(actual, geometries)
    )
    assert result["instrument"]["name"] == instrument["name"]
    assert result["points"] == []
    assert "continuous coverage" in " ".join(result["assumptions"])


def test_no_sensor_means_no_assumed_footprint_or_dissolve():
    with mock.patch.object(ground_track_view, "compute_ground_track") as compute:
        result = ground_track_view.build_ground_track_scene(
            CATALOG_SATELLITE,
            messages(START + timedelta(minutes=2)),
            START,
            START + timedelta(minutes=2),
            120,
        )
    compute.assert_not_called()
    assert result["ground_area"]["features"] == []
    assert result["instrument"] is None
    assert result["scenario"]["satellites"][0]["orbit_epoch"].endswith("Z")


@pytest.mark.parametrize(
    "instrument",
    [
        {"name": "Missing angle"},
        {"name": "Typo", "field_of_regard": 60, "typo": 1},
        {"name": "Nonfinite", "field_of_regard": float("nan")},
        {
            "name": "Invalid fixed time",
            "field_of_regard": 60,
            "access_time_fixed": True,
        },
    ],
)
def test_bad_geometry_is_not_silently_accepted(instrument):
    with pytest.raises(ValueError):
        ground_track_view.build_ground_track_scene(
            CATALOG_SATELLITE,
            messages(START + timedelta(minutes=2)),
            START,
            START + timedelta(minutes=2),
            120,
            instrument,
        )


def test_map_tool_and_visualize_rerun_share_exact_inputs():
    with mock.patch.object(
        server.celestrak_client, "get_satellite_info", return_value=CATALOG_SATELLITE
    ):
        response = call(
            "ground_track_map",
            {
                "satellite_identifier": "25544",
                "start_time": START.isoformat(),
                "duration": "2 hours",
                "step_interval": "2 minutes",
                "instrument": {
                    "name": "Scanner",
                    "cone_angle": 47.5,
                    "along_track_field_of_view": 1,
                },
                "dissolve_orbits": False,
            },
        )
        assert not response.is_error, text(response)
        result = scene(response.content[1].resource.text)
        assert result["ui"]["presentation"] == "map2d"
        assert result["ground_area"]["features"]
        rerun = call(
            "visualize",
            {**result["request"], "workspace_id": result["ui"]["workspace_id"]},
        )
        assert not rerun.is_error, text(rerun)
        next_result = scene(rerun.content[1].resource.text)
        assert next_result["ground_area"] == result["ground_area"]
        assert next_result["request"] == result["request"]
        assert next_result["ui"]["workspace_id"] == result["ui"]["workspace_id"]


def test_catalog_epoch_must_match_an_instrument_study_window():
    start = START + timedelta(days=60)
    with pytest.raises(ValueError, match="epoch"):
        ground_track_view.build_ground_track_scene(
            CATALOG_SATELLITE,
            [],
            start,
            start + timedelta(minutes=2),
            120,
            {"name": "Basic", "field_of_regard": 60},
        )


def test_empty_dissolved_area_does_not_claim_coverage():
    end = START + timedelta(minutes=2)
    result = ground_track_view.build_ground_track_scene(
        CATALOG_SATELLITE,
        messages(end),
        START,
        end,
        120,
        {
            "name": "No overhead sun",
            "field_of_regard": 60,
            "req_target_sunlit": True,
            "min_target_solar_elevation": 90,
        },
    )
    assert result["ground_area"]["features"] == []
    assert result["instrument"]["name"] == "No overhead sun"


def test_polar_dissolve_keeps_native_geometry_and_adds_display_patches():
    import geopandas as gpd
    from shapely.geometry import Polygon
    from shapely import union_all
    from tests.polar_fixtures import polar_cases

    native = shape(polar_cases()[0]["native"])
    end = START + timedelta(minutes=2)
    with mock.patch.object(ground_track_view, "compute_ground_track", return_value=gpd.GeoDataFrame(geometry=[native])):
        result = ground_track_view.build_ground_track_scene(
            CATALOG_SATELLITE, messages(end), START, end, 120,
            {"name": "Polar", "field_of_regard": 100},
        )
    feature = result["ground_area"]["features"][0]
    assert shape(feature["geometry"]).equals(native)
    assert feature["cesium_boundary"]
    parts = [Polygon(rings[0], rings[1:]) for rings in feature["cesium_parts"]]
    assert union_all(parts).symmetric_difference(native).area < 1e-8
