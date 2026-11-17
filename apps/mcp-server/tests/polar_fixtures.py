"""Real native polar and antimeridian footprints, shared by Python/Cesium checks."""

from datetime import datetime, timezone
from functools import lru_cache

from pyproj import Geod
from shapely import orient_polygons
from shapely.geometry import mapping
from tatc.analysis import collect_ground_track, collect_orbit_track
from tatc.schemas import (
    CircularOrbit,
    ConicalInstrument,
    Instrument,
    PointedInstrument,
    Satellite,
)

from src.cesium_geometry import (
    boundary_lines,
    cesium_parts,
    needs_partition,
    part_rings,
)


@lru_cache
def polar_cases():
    start = datetime(2026, 10, 7, tzinfo=timezone.utc)
    instruments = [
        Instrument(name="basic", field_of_regard=100),
        PointedInstrument(
            name="pointed",
            field_of_regard=100,
            cross_track_field_of_view=60,
            along_track_field_of_view=60,
        ),
        ConicalInstrument(name="conical", cone_angle=47.5, along_track_field_of_view=1),
    ]
    cases = []
    for anomaly in (89, 90, 91, 269, 270, 271):
        for instrument in instruments:
            satellite = Satellite(
                name="Polar reference",
                orbit=CircularOrbit(
                    mean_altitude=550000,
                    inclination=90,
                    true_anomaly=anomaly,
                    epoch=start,
                ),
                instruments=[instrument],
            )
            geometry = collect_ground_track(satellite, [start]).geometry.iloc[0]
            cases.append(case(f"{instrument.name}-{anomaly}", geometry))
    satellite = Satellite(
        name="Dateline reference",
        orbit=CircularOrbit(
            mean_altitude=550000,
            inclination=0,
            epoch=start,
        ),
        instruments=[instruments[0]],
    )
    longitude = collect_orbit_track(satellite, [start]).geometry.iloc[0].x
    satellite.orbit = satellite.orbit.model_copy(
        update={
            "right_ascension_ascending_node": (180 - longitude) % 360,
        }
    )
    cases.append(
        case("antimeridian", collect_ground_track(satellite, [start]).geometry.iloc[0])
    )
    return cases


def case(name, geometry):
    return {
        "name": name,
        "native": mapping(geometry),
        "parts": part_rings(cesium_parts(geometry)),
        "area": abs(
            Geod(ellps="WGS84").geometry_area_perimeter(orient_polygons(geometry))[0]
        ),
        "bounds": geometry.bounds,
        "boundaries": boundary_lines(geometry) if needs_partition(geometry) else [],
    }


def polar_movie():
    """A native pointed footprint sequence crossing the North Pole."""
    from datetime import timedelta
    from src.coverage_analysis import sample_orbit, timestamp

    start = datetime(2026, 10, 7, tzinfo=timezone.utc)
    times = [start + timedelta(seconds=i) for i in (0, 30, 60)]
    instrument = PointedInstrument(
        name="Polar imager",
        field_of_regard=100,
        cross_track_field_of_view=60,
        along_track_field_of_view=60,
    )
    satellite = Satellite(
        name="Polar reference",
        orbit=CircularOrbit(
            mean_altitude=550000,
            inclination=90,
            true_anomaly=89,
            epoch=start,
        ),
        instruments=[instrument],
    )
    shapes = collect_ground_track(satellite, times).geometry
    polygons = [part_rings(cesium_parts(shape)) for shape in shapes]
    centers = instrument.compute_footprint_center(
        satellite.orbit.to_gp_orbit().get_orbit_track(times)
    )
    track = sample_orbit(satellite.name, satellite.orbit, start, times[-1], 30)
    track["footprints"] = [
        {
            "instrument": instrument.name,
            "parts": [[rings[0] for rings in parts] for parts in polygons],
            "holes": [[rings[1:] for rings in parts] for parts in polygons],
            "partitioned": [needs_partition(shape) for shape in shapes],
            "boundaries": [
                boundary_lines(shape) if needs_partition(shape) else []
                for shape in shapes
            ],
            "centers": [
                [float(x), float(y)]
                for x, y in zip(centers.longitude.degrees, centers.latitude.degrees)
            ],
        }
    ]
    return {
        "scenario": {
            "start_time": timestamp(start),
            "end_time": timestamp(times[-1]),
            "display_step_seconds": 30,
        },
        "tracks": [track],
        "points": [],
        "request": {
            "view": "orbit",
            "satellite_identifiers": [satellite.name],
            "start_time": timestamp(start),
            "duration": "1 minute",
            "display_step_seconds": 30,
        },
    }
