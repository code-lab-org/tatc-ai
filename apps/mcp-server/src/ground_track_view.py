"""Study-pane ground tracks and native TATC sampled-footprint dissolves."""

import json
from datetime import timedelta
from importlib.metadata import version

from shapely import orient_polygons
from shapely.geometry import mapping
from tatc.analysis import compute_ground_track
from tatc.schemas import ConicalInstrument, Instrument, PointedInstrument, Satellite

from .cesium_geometry import boundary_lines, cesium_parts, needs_partition, part_rings
from .coverage_analysis import _reject_unknown, check_element_epoch, timestamp
from .tatc_integration import _sample_times, create_orbit_from_omm


def build_ground_track_scene(
    info, messages, start, end, step, instrument=None, dissolve_orbits=True, ui=None
):
    """Serialize TATC polygons; no viewing geometry is inferred without a sensor."""
    name = f"{info['name']} ({info['norad_id']})"
    # Even a line-only result carries the exact element epoch used to propagate it.
    orbit = create_orbit_from_omm(info["omm"])
    epoch = timestamp(orbit.get_epoch())
    assumptions = [
        "CelesTrak GP elements propagated with TATC; positions are sampled and interpolated.",
        "A ground-track line is the satellite's subpoint path, not instrument coverage.",
    ]
    area = {"type": "FeatureCollection", "features": []}
    validated = None
    if instrument is not None:
        if not isinstance(instrument, dict):
            raise ValueError("instrument must be a TATC instrument object")
        conical = "cone_angle" in instrument
        required = (
            {"name", "cone_angle", "along_track_field_of_view"}
            if conical
            else {"name", "field_of_regard"}
        )
        if not required <= instrument.keys():
            raise ValueError(
                "Supply instrument name and field_of_regard, or name, cone_angle and along_track_field_of_view"
            )
        cls = (
            ConicalInstrument
            if conical
            else (
                PointedInstrument
                if "cross_track_field_of_view" in instrument
                else Instrument
            )
        )
        try:
            sensor = cls.model_validate(instrument)
        except TypeError:
            raise ValueError("Instrument angles must be numeric degrees") from None
        _reject_unknown(instrument, sensor)
        if not sensor.name.strip() or len(sensor.name) > 80:
            raise ValueError("Instrument names must contain 1–80 characters")
        if sensor.min_access_time < timedelta(0):
            raise ValueError("Minimum access time must not be negative")
        if sensor.access_time_fixed and sensor.min_access_time <= timedelta(0):
            raise ValueError("Fixed access time must be positive")
        validated = sensor.model_dump(mode="json")
        json.dumps(validated, allow_nan=False)
        check_element_epoch(orbit.get_epoch(), name, start, end)
        satellite = Satellite(name=name, orbit=orbit, instruments=[sensor])
        times = _sample_times(start, end, step)
        dissolved = compute_ground_track(
            satellite, times, dissolve_orbits=dissolve_orbits
        )
        for index, row in dissolved.iterrows():
            if row.geometry is None or row.geometry.is_empty:
                continue
            area["features"].append(
                {
                    "type": "Feature",
                    "geometry": mapping(orient_polygons(row.geometry)),
                    **(
                        {
                            "cesium_parts": part_rings(cesium_parts(row.geometry, max_width=90, max_height=90)),
                            "cesium_boundary": boundary_lines(row.geometry),
                        }
                        if needs_partition(row.geometry, max_width=90, max_height=90)
                        else {}
                    ),
                    "properties": {
                        "satellite": name,
                        "instrument": sensor.name,
                        "orbit_group": int(index),
                        "start_time": timestamp(start),
                        "end_time": timestamp(end),
                        "step_seconds": step,
                        "instrument_parameters": validated,
                        "element_epoch": epoch,
                        "source": "tatc.analysis.compute_ground_track",
                        "dissolve_orbits": dissolve_orbits,
                    },
                }
            )
        assumptions.extend(
            [
                "Dissolved footprint area uses native TATC compute_ground_track on valid sampled footprints; gaps between samples are not filled.",
                "Observation validity is checked at each footprint center, not every polygon location. This is modeled viewable geometry, not executed observations or continuous coverage.",
                "The dissolved-area layer covers the entire requested window; playback moves the satellite, not the dissolved-area time extent.",
                "Instrument: " + json.dumps(instrument),
            ]
        )
    else:
        assumptions.append(
            "No instrument was supplied; no assumed 60-degree cone or sensor coverage is drawn."
        )
    return {
        "scenario": {
            "start_time": timestamp(start),
            "end_time": timestamp(end),
            "display_step_seconds": step,
            "satellites": [
                {
                    "name": info["name"],
                    "norad_id": str(info["norad_id"]),
                    "orbit_epoch": epoch,
                }
            ],
            "source": "CelesTrak GP elements propagated with TATC",
        },
        "tracks": [
            {
                "name": name,
                "samples": [
                    [
                        m["time"],
                        m["position_lla"]["lon_deg"],
                        m["position_lla"]["lat_deg"],
                        m["position_lla"]["alt_m"],
                    ]
                    for m in messages
                ],
            }
        ],
        "points": [],
        "ground_area": area,
        "instrument": validated,
        "assumptions": assumptions,
        "software": {name: version(name) for name in ("tatc", "skyfield", "sgp4")},
        "ui": ui or {},
    }
