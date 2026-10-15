from datetime import timedelta
from typing import Optional

import pandas as pd
from tatc.schemas import (
    Satellite,
    WalkerConstellation,
    TwoLineElements,
    Instrument,
    Point,
)
from tatc.utils import swath_width_to_field_of_regard
from tatc.utils.orbital import compute_ground_surface_velocity
from tatc.analysis import (
    collect_observations,
    collect_multi_observations,
    aggregate_observations,
    reduce_observations,
    compute_ground_track,
)
from tatc.generation import generate_points_uniform_spacing


def _build(tle, swath_width_m):
    orbit = TwoLineElements(tle=list(tle))
    altitude_m = orbit.get_altitude()
    instrument = Instrument(
        name="sensor",
        field_of_regard=swath_width_to_field_of_regard(altitude_m, swath_width_m),
    )
    return orbit, altitude_m, instrument


def _members(orbit, instrument, constellation):
    if constellation is None:
        return [Satellite(name="sat", orbit=orbit, instruments=[instrument])]
    return WalkerConstellation(
        name="sat",
        orbit=orbit,
        instruments=[instrument],
        configuration=constellation.get("configuration", "delta"),
        number_satellites=constellation["number_satellites"],
        number_planes=constellation["number_planes"],
    ).generate_members()


def revisit_over_point(
    latitude: float,
    longitude: float,
    tle: list[str],
    swath_width_m: float,
    window_days: int = 30,
    constellation: Optional[dict] = None,
) -> float:
    orbit, _, instrument = _build(tle, swath_width_m)
    point = Point(id=0, latitude=latitude, longitude=longitude)
    start = orbit.get_epoch()
    end = start + timedelta(days=window_days)
    members = _members(orbit, instrument, constellation)
    if constellation is None:
        raw = collect_observations(point, members[0], start, end)
    else:
        raw = collect_multi_observations(point, members, start, end)
    reduced = reduce_observations(aggregate_observations(raw))
    return float(reduced.iloc[0].revisit / timedelta(hours=1))


def ground_track(
    tle: list[str],
    swath_width_m: float,
    window_minutes: int = 30,
) -> str:
    orbit, altitude_m, instrument = _build(tle, swath_width_m)
    satellite = Satellite(name="sat", orbit=orbit, instruments=[instrument])
    start = orbit.get_epoch()
    end = start + timedelta(minutes=window_minutes)
    max_delta_t_s = swath_width_m / compute_ground_surface_velocity(
        altitude_m, orbit.get_inclination()
    )
    times = pd.date_range(start, end, freq=timedelta(seconds=max_delta_t_s / 10))
    return compute_ground_track(satellite, times).iloc[0].geometry.wkt


def global_coverage(
    tle: list[str],
    swath_width_m: float,
    spacing_km: float,
    window_days: int = 30,
    constellation: Optional[dict] = None,
) -> list[dict]:
    orbit, _, instrument = _build(tle, swath_width_m)
    members = _members(orbit, instrument, constellation)
    points_df = generate_points_uniform_spacing(spacing_km * 1e3)
    points = points_df.apply(
        lambda r: Point(id=r.point_id, latitude=r.geometry.y, longitude=r.geometry.x),
        axis=1,
    )
    start = orbit.get_epoch()
    end = start + timedelta(days=window_days)
    raw = pd.concat(
        [collect_multi_observations(p, members, start, end) for p in points]
    )
    reduced = reduce_observations(aggregate_observations(raw))
    reduced["mean_revisit_hr"] = reduced.apply(
        lambda r: r["revisit"] / timedelta(hours=1), axis=1
    )
    return [
        {
            "point_id": int(r.point_id),
            "lon": float(r.geometry.x),
            "lat": float(r.geometry.y),
            "mean_revisit_hr": float(r.mean_revisit_hr),
        }
        for r in reduced.itertuples()
    ]
