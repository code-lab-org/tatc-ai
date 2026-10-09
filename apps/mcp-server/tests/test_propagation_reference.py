"""Check SGP4 positions against published vectors and exercise real wrappers."""

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest
from skyfield.api import load, wgs84
from skyfield.framelib import itrs
from skyfield.positionlib import Geocentric
from skyfield.sgp4lib import TEME
from skyfield.units import Distance, Velocity

from src import tatc_integration as ti


# Source: CelesTrak AIAA 2006-6753 Rev. 2, Appendix E (NORAD 00005 TEME km).
# https://celestrak.org/publications/AIAA/2006-6753/AIAA-2006-6753-Rev2.pdf
VANGUARD_TLE = (
    "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753",
    "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667",
)
VANGUARD_EPOCH = datetime(2000, 6, 27, 18, 50, 19, 733568, tzinfo=timezone.utc)


def _lla_from_published_teme(time, position_km):
    # The Appendix C test below checks TEME-to-ITRS; this helper uses Skyfield for LLA.
    skyfield_time = load.timescale(builtin=True).from_datetime(time)
    reference = Geocentric.from_time_and_frame_vectors(
        skyfield_time, TEME, Distance(km=position_km), Velocity(km_per_s=(0, 0, 0))
    )
    point = wgs84.subpoint(reference)
    return point.latitude.degrees, point.longitude.degrees, point.elevation.m


@pytest.mark.parametrize(
    ("minutes", "expected_teme_km"),
    [
        pytest.param(0, (7022.46529266, -1400.08296755, 0.03995155), id="epoch"),
        pytest.param(
            360,
            (-7154.03120202, -3783.17682504, -3536.19412294),
            id="six-hours",
        ),
    ],
)
def test_vanguard_sgp4_matches_published_teme_vectors(minutes, expected_teme_km):
    satellite = ti.create_satellite_from_tle(*VANGUARD_TLE)
    time = VANGUARD_EPOCH + timedelta(minutes=minutes)
    expected_lla = _lla_from_published_teme(time, expected_teme_km)

    # One metre allows library-version rounding while detecting propagation errors.
    actual_teme_km = satellite.get_orbit_track(time).frame_xyz(TEME).km
    assert tuple(actual_teme_km) == pytest.approx(expected_teme_km, rel=0, abs=0.001)

    generated = ti.generate_ground_track(satellite, time, time)
    assert generated[0][0] == time.replace(tzinfo=None)
    for actual in (generated[0][1:], ti.propagate_satellite(satellite, time)):
        assert actual[:2] == pytest.approx(expected_lla[:2], rel=0, abs=1e-5)
        assert actual[2] == pytest.approx(expected_lla[2], rel=0, abs=1.0)


def test_propagate_satellite_converts_known_itrs_position_to_lla():
    latitude, longitude, altitude_m = 30.0, 20.0, 500_000.0
    semimajor_axis_m, flattening = 6_378_137.0, 1 / 298.257223563
    eccentricity_squared = flattening * (2 - flattening)
    lat_rad, lon_rad = map(math.radians, (latitude, longitude))
    prime_vertical_m = semimajor_axis_m / (
        1 - eccentricity_squared * math.sin(lat_rad) ** 2
    ) ** 0.5
    xyz_m = (
        (prime_vertical_m + altitude_m) * math.cos(lat_rad) * math.cos(lon_rad),
        (prime_vertical_m + altitude_m) * math.cos(lat_rad) * math.sin(lon_rad),
        (prime_vertical_m * (1 - eccentricity_squared) + altitude_m)
        * math.sin(lat_rad),
    )
    time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    skyfield_time = load.timescale(builtin=True).from_datetime(time)
    position = Geocentric.from_time_and_frame_vectors(
        skyfield_time,
        itrs,
        Distance(m=xyz_m),
        Velocity(km_per_s=(0, 0, 0)),
    )

    class FixedOrbit:
        def get_orbit_track(self, _time):
            return position

    actual = ti.propagate_satellite(FixedOrbit(), time)
    assert actual[0] == pytest.approx(latitude, rel=0, abs=1e-6)
    assert actual[1] == pytest.approx(longitude, rel=0, abs=1e-6)
    assert actual[2] == pytest.approx(altitude_m, rel=0, abs=0.01)


def test_teme_to_itrf_matches_vallado_appendix_c():
    # Rev. 2 Appendix C, p. 32: UTC 2004-04-06 07:51:28.386, DUT1 -0.439961 s,
    # TAI-UTC 32 s, and polar motion xp=-0.140682, yp=0.333309 arcsec.
    timescale = load.timescale(builtin=True)
    time = timescale.utc(2004, 4, 6, 7, 51, 28.386)
    time.delta_t = 64.623961  # TT - UT1 in seconds from the published values.
    timescale.polar_motion_table = (
        np.array([time.tt]), np.array([-0.140682]), np.array([0.333309])
    )
    position = Geocentric.from_time_and_frame_vectors(
        time,
        TEME,
        Distance(km=(5094.18016210, 6127.64465950, 6380.34453270)),
        Velocity(km_per_s=(0, 0, 0)),
    )

    actual_itrf_km = position.frame_xyz(itrs).km
    expected_itrf_km = (-1033.47938300, 7901.29527540, 6380.35659580)
    assert tuple(actual_itrf_km) == pytest.approx(expected_itrf_km, rel=0, abs=0.001)
