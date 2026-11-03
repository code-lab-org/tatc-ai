"""Map a validated conical configuration onto TAT-C 3.6 public APIs.

TAT-C performs orbit propagation, conical viewing-event calculation, and
footprint geometry.  This module only constructs the TAT-C objects, records
the derived values TAT-C returns, and extracts event rows.  It contains no
mission-specific defaults.
"""

from __future__ import annotations

import importlib.metadata
import platform
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Sequence

from tatc.analysis import collect_observations
from tatc.schemas import (
    Attitude,
    ConicalInstrument,
    Point,
    Satellite,
)
from tatc.utils import NadirReference, VelocityFrame

from .conical_config import ConicalConfiguration
from .tatc_integration import create_orbit_from_omm


@dataclass(frozen=True)
class MappedConical:
    """TAT-C objects and derived values for one configuration."""

    satellite: Satellite
    instrument: ConicalInstrument
    mean_altitude_m: float
    field_of_regard_deg: float
    swath_width_km: float


def tatc_version() -> str:
    return importlib.metadata.version("tatc")


def build_conical_satellite(
    configuration: ConicalConfiguration, omm_records: Sequence[Dict[str, Any]]
) -> MappedConical:
    """Build the TAT-C satellite, attitude, and conical instrument.

    The source velocity frame is represented by the satellite attitude, as in
    the TAT-C 3.6 AMSR2 validation, rather than by setting the instrument's
    own frame.  TAT-C rejects a satellite that sets both.
    """
    geometry = configuration.geometry
    orbit = create_orbit_from_omm(list(omm_records))
    attitude = Attitude(velocity_frame=VelocityFrame(geometry.velocity_frame))
    instrument_options: Dict[str, Any] = {
        "name": configuration.instrument,
        "cone_angle": geometry.cone_angle.value,
        "scan_center_azimuth": geometry.scan_center_azimuth.value,
        "scan_half_width": geometry.scan_half_width.value,
        "along_track_field_of_view": geometry.along_track_field_of_view.value,
        "nadir_reference": NadirReference(geometry.nadir_reference),
    }
    if geometry.illumination_requirement == "target_sunlit":
        instrument_options["req_target_sunlit"] = True
    if geometry.beam_width is not None:
        instrument_options["beam_width"] = geometry.beam_width.value
    if geometry.scan_sample_spacing is not None:
        instrument_options["scan_sample_spacing"] = geometry.scan_sample_spacing.value
    instrument = ConicalInstrument(**instrument_options)
    satellite = Satellite(
        name=configuration.spacecraft.name,
        orbit=orbit,
        instruments=[instrument],
        attitude=attitude,
    )
    mean_altitude_m = float(orbit.get_mean_altitude())
    swath_width_km = float(instrument.get_swath_width(mean_altitude_m)) / 1000.0
    return MappedConical(
        satellite=satellite,
        instrument=instrument,
        mean_altitude_m=mean_altitude_m,
        field_of_regard_deg=float(instrument.field_of_regard),
        swath_width_km=swath_width_km,
    )


def resolved_snapshot(
    configuration: ConicalConfiguration, omm_records: Sequence[Dict[str, Any]]
) -> Dict[str, Any]:
    """Return the configuration plus the values and mapping TAT-C derived.

    The snapshot is what a study stores and returns on every fetch.  It is a
    resolved copy, not a pointer to a mutable configuration source.
    """
    mapped = build_conical_satellite(configuration, omm_records)
    snapshot = configuration.portable()
    altitude_km = round(mapped.mean_altitude_m / 1000.0, 3)
    snapshot["tatc_mapping"] = {
        "tatc_version": tatc_version(),
        "instrument_class": "tatc.schemas.ConicalInstrument",
        "velocity_frame_source": (
            "tatc.schemas.Attitude(velocity_frame=...) on the satellite; the "
            "instrument inherits the body frame and does not set its own."
        ),
        "nadir_reference_source": "tatc.schemas.ConicalInstrument.nadir_reference",
        "field_of_regard_deg": round(mapped.field_of_regard_deg, 6),
        "field_of_regard_source": (
            "TAT-C ConicalInstrument derives field_of_regard from cone_angle and "
            "along_track_field_of_view, capped at 180 degrees."
        ),
        "modeled_swath_width_km_at_omm_mean_altitude": round(mapped.swath_width_km, 3),
        "omm_mean_altitude_km": altitude_km,
        "conversion_notes": [
            "All angles were supplied in degrees and passed to TAT-C without "
            "changing their meaning.",
            "The cone angle is measured from the configured nadir reference, "
            "not a ground incidence angle.",
            "The along-track field of view is the configured approximation for "
            "integration-time motion; it is not an integration-time input.",
            "Beam width and scan sample spacing, when present, affect TAT-C "
            "sample-size and sample-spacing helpers, not cone-crossing epochs.",
        ],
    }
    snapshot["software"] = {
        "tatc": tatc_version(),
        "python": platform.python_version(),
        "server": "tatc-ai-mcp-server",
    }
    return snapshot


def collect_cell_events(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Collect every valid conical viewing epoch for one grid cell.

    TAT-C reports one row per valid epoch and retains the broader
    field-of-regard period as each row's start/end.  Two cone crossings in
    one period therefore produce two rows here; the rows are not merged.

    The task is a plain dictionary so a spawned worker can pickle it.
    """
    configuration = ConicalConfiguration.model_validate(task["configuration"])
    mapped = build_conical_satellite(configuration, task["omm_records"])
    point = Point(
        id=int(task["cell_id"]),
        latitude=float(task["latitude"]),
        longitude=float(task["longitude"]),
    )
    try:
        observations = collect_observations(
            point,
            mapped.satellite,
            task["start"],
            task["end"],
            instrument_index=0,
            omit_solar=True,
        )
    except Exception as exc:  # TAT-C and Skyfield raise several types.
        raise ValueError(
            f"TAT-C conical analysis failed for cell {task['cell_id']}: {exc}"
        ) from exc
    return event_rows_from_observations(observations, configuration, int(task["cell_id"]))


def event_rows_from_observations(
    observations: Any, configuration: Any, cell_id: int
) -> List[Dict[str, Any]]:
    """Normalize TAT-C observation rows shared by pointed and conical adapters."""
    events: List[Dict[str, Any]] = []
    for row in observations.itertuples(index=False):
        epoch = row.epoch
        if epoch is None or epoch != epoch:  # pandas NaT
            continue
        events.append(
            {
                "cell_id": cell_id,
                "target_longitude_deg": float(row.geometry.x),
                "target_latitude_deg": float(row.geometry.y),
                "epoch": epoch.to_pydatetime(warn=False),
                "envelope_start": row.start.to_pydatetime(warn=False),
                "envelope_end": row.end.to_pydatetime(warn=False),
                "satellite": str(row.satellite),
                "instrument": str(row.instrument),
                "configuration_id": configuration.configuration_id,
                "configuration_revision": configuration.revision,
            }
        )
    return events
