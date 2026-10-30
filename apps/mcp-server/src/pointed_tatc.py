"""Map one validated pointed configuration onto TAT-C 3.6 public APIs."""

from __future__ import annotations

import platform
from datetime import datetime
from typing import Any, Dict, Sequence

from tatc.analysis import collect_observations
from tatc.schemas import Point, PointedInstrument, Satellite
from tatc.utils import NadirReference, VelocityFrame

from .conical_tatc import event_rows_from_observations, tatc_version
from .pointed_config import PointedConfiguration
from .tatc_integration import create_orbit_from_omm


def build_pointed_satellite(
    configuration: PointedConfiguration, omm_records: Sequence[Dict[str, Any]]
) -> tuple[Satellite, PointedInstrument, float]:
    """Construct the TAT-C satellite and exactly one sourced PointedInstrument."""
    geometry = configuration.geometry
    orbit = create_orbit_from_omm(list(omm_records))
    instrument = PointedInstrument(
        name=configuration.instrument,
        field_of_regard=geometry.field_of_regard.value,
        cross_track_field_of_view=geometry.cross_track_field_of_view.value,
        along_track_field_of_view=geometry.along_track_field_of_view.value,
        roll_angle=geometry.roll_angle.value,
        pitch_angle=geometry.pitch_angle.value,
        nadir_reference=NadirReference(geometry.nadir_reference),
        velocity_frame=VelocityFrame(geometry.velocity_frame),
        is_rectangular=geometry.is_rectangular,
        view_geometry=geometry.view_geometry,
    )
    satellite = Satellite(
        name=configuration.spacecraft.name,
        orbit=orbit,
        instruments=[instrument],
    )
    return satellite, instrument, float(orbit.get_mean_altitude())


def resolved_pointed_snapshot(
    configuration: PointedConfiguration, omm_records: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    """Store the exact TAT-C mapping and software versions with the configuration."""
    _satellite, instrument, mean_altitude_m = build_pointed_satellite(
        configuration, omm_records
    )
    snapshot = configuration.portable()
    geometry = configuration.geometry
    snapshot["tatc_mapping"] = {
        "tatc_version": tatc_version(),
        "instrument_class": "tatc.schemas.PointedInstrument",
        "velocity_frame_source": "tatc.schemas.PointedInstrument.velocity_frame",
        "nadir_reference_source": "tatc.schemas.PointedInstrument.nadir_reference",
        "view_geometry": instrument.view_geometry,
        "is_rectangular": instrument.is_rectangular,
        "field_of_regard_deg": float(instrument.field_of_regard),
        "cross_track_field_of_view_deg": float(instrument.cross_track_field_of_view),
        "along_track_field_of_view_deg": float(instrument.along_track_field_of_view),
        "roll_angle_deg": float(instrument.roll_angle),
        "pitch_angle_deg": float(instrument.pitch_angle),
        "off_nadir_reference": geometry.off_nadir_reference,
        "incidence_reference": geometry.incidence_reference,
        "omm_mean_altitude_km": round(mean_altitude_m / 1000.0, 3),
        "conversion_notes": [
            "TAT-C PointedInstrument roll is negative for right-looking geometry.",
            "The instrument uses an Earth-fixed velocity frame for zero-Doppler viewing.",
            "TAT-C uses one fixed rectangular frame; source subswaths and orbit-dependent roll steering remain explicit approximations.",
            "Incidence angles are ground-normal references and are not passed as off-nadir view angles.",
        ],
    }
    snapshot["software"] = {
        "tatc": tatc_version(),
        "python": platform.python_version(),
        "server": "tatc-ai-mcp-server",
    }
    return snapshot


def collect_pointed_cell_events(task: Dict[str, Any]) -> list[Dict[str, Any]]:
    """Collect every valid pointed viewing epoch for one grid-cell center."""
    configuration = PointedConfiguration.model_validate(task["configuration"])
    satellite, _instrument, _altitude = build_pointed_satellite(
        configuration, task["omm_records"]
    )
    point = Point(
        id=int(task["cell_id"]),
        latitude=float(task["latitude"]),
        longitude=float(task["longitude"]),
    )
    try:
        observations = collect_observations(
            point,
            satellite,
            task["start"],
            task["end"],
            instrument_index=0,
            omit_solar=True,
        )
    except Exception as exc:  # TAT-C and Skyfield raise several types.
        raise ValueError(
            f"TAT-C pointed analysis failed for cell {task['cell_id']}: {exc}"
        ) from exc
    return event_rows_from_observations(observations, configuration, int(task["cell_id"]))
