"""Geometry candidates from OSCAR source text.

OSCAR describes instruments in words, not numbers. This module pulls
numeric candidates out of those words without guessing.

It never picks quietly. It returns every candidate with its source
field and text, plus a status of conclusive, ambiguous, needs_review,
or unknown. The coverage tool only accepts an explicit number plus a
source, so a wrong guess cannot look like a fact.
"""

import math
import re
from typing import Any, Dict, List, Optional, Tuple

# Words that mean the simple nadir cone model may misstate access.
NON_NADIR_HINTS = (
    "limb",
    "conical",
    "oblique",
    "off-nadir",
    "off nadir",
    "steerable",
    "steering",
    "pointing",
    "tilt",
    "phase angle",
)

_SWATH_KM = re.compile(
    r"swath(?:\s+width)?[^0-9]{0,25}(\d+(?:\.\d+)?)\s*km",
    re.IGNORECASE,
)
_SWATH_KM_REVERSED = re.compile(
    r"(\d+(?:\.\d+)?)\s*km\s+swath",
    re.IGNORECASE,
)
_FOR_DEG = re.compile(
    r"field of regard[^0-9\xb1]{0,30}(\d+(?:\.\d+)?)\s*(?:degrees?|deg)",
    re.IGNORECASE,
)
_FOR_KM = re.compile(
    r"field of regard[^0-9]{0,30}(\d+(?:\.\d+)?)\s*km",
    re.IGNORECASE,
)
_FOV_DEG = re.compile(
    r"(?:f\.?\s?o\.?\s?v\.?|field of view)[^0-9\xb1]{0,30}"
    r"(\d+(?:\.\d+)?)\s*(?:degrees?|deg)",
    re.IGNORECASE,
)
_HALF_ANGLE_DEG = re.compile(
    r"\xb1\s*(\d+(?:\.\d+)?)\s*-?\s*(?:degrees?|deg)"
)
_STEER_DEG = re.compile(
    r"steer(?:able|ing)?[^0-9\xb1]{0,30}\xb1\s*"
    r"(\d+(?:\.\d+)?)\s*-?\s*(?:degrees?|deg)",
    re.IGNORECASE,
)

MAX_CANDIDATES = 10


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _has_non_nadir_hint(text: str) -> bool:
    lowered = text.lower()
    return any(hint in lowered for hint in NON_NADIR_HINTS)


def _add(
    candidates: List[Dict[str, Any]],
    kind: str,
    value: float,
    unit: str,
    source_field: str,
    source_text: str,
    confidence: str,
    usable: bool,
    derived: bool = False,
) -> None:
    if len(candidates) >= MAX_CANDIDATES:
        return
    if not math.isfinite(value) or value <= 0:
        return
    for existing in candidates:
        if (
            existing["kind"] == kind
            and existing["value"] == round(float(value), 6)
            and existing["source_field"] == source_field
        ):
            return
    candidates.append(
        {
            "kind": kind,
            "value": round(float(value), 6),
            "unit": unit,
            "source_field": source_field,
            "source_text": _clean(source_text)[:280],
            "confidence": confidence,
            "usable_for_coverage": usable,
            "derived": derived,
            "needs_review": _has_non_nadir_hint(source_text),
        }
    )


def extract_geometry_candidates(
    specs: Optional[Dict[str, Any]],
    oscar_url: Optional[str] = None,  # noqa: ARG001 - kept for provenance symmetry
) -> List[Dict[str, Any]]:
    """Pull numeric geometry candidates from OSCAR spec text.

    Args:
        specs: OSCAR spec table as field name to source text.
        oscar_url: Kept so callers pass provenance through; the
            candidates themselves cite the spec field and text.

    Returns:
        Candidate dicts with kind, value, unit, source field and
        text, confidence, and whether the simple cone model can use
        them without review.
    """
    candidates: List[Dict[str, Any]] = []
    if not isinstance(specs, dict):
        return candidates
    for field, raw in specs.items():
        text = _clean(raw)
        if not text:
            continue
        flagged = _has_non_nadir_hint(text)
        for match in _SWATH_KM.finditer(text):
            _add(
                candidates, "swath_width_km", float(match.group(1)), "km",
                str(field), text,
                "medium" if flagged else "high", usable=not flagged,
            )
        for match in _SWATH_KM_REVERSED.finditer(text):
            _add(
                candidates, "swath_width_km", float(match.group(1)), "km",
                str(field), text,
                "medium" if flagged else "high", usable=not flagged,
            )
        for match in _FOR_DEG.finditer(text):
            _add(
                candidates, "field_of_regard_deg", float(match.group(1)), "deg",
                str(field), text,
                "medium" if flagged else "high", usable=not flagged,
            )
        for match in _FOR_KM.finditer(text):
            _add(
                candidates, "field_of_regard_km", float(match.group(1)), "km",
                str(field), text, "low", usable=False,
            )
        for match in _FOV_DEG.finditer(text):
            _add(
                candidates, "field_of_view_deg", float(match.group(1)), "deg",
                str(field), text, "medium", usable=False,
            )
        for match in _STEER_DEG.finditer(text):
            _add(
                candidates, "off_nadir_steer_deg", float(match.group(1)), "deg",
                str(field), text, "low", usable=False,
            )
        # A bare half angle like "+-55 degrees" means a full width
        # of twice that for a cross-track scanner. Record it as
        # derived so the model sees the doubling step.
        if "field of regard" not in text.lower():
            for match in _HALF_ANGLE_DEG.finditer(text):
                half = float(match.group(1))
                _add(
                    candidates, "field_of_regard_deg", half * 2.0, "deg",
                    str(field), text, "low", usable=False, derived=True,
                )
    return candidates


def resolve_geometry_status(
    candidates: List[Dict[str, Any]],
) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """Decide whether candidates give a conclusive geometry.

    Returns:
        Tuple of status, reason, and recommended candidate or None.
        Status is conclusive, ambiguous, needs_review, or unknown.
    """
    usable = [c for c in candidates if c.get("usable_for_coverage")]
    if not candidates:
        return (
            "unknown",
            "No numeric swath or angle found in OSCAR specs. "
            "Provide swath_width_km or field_of_regard_deg with a source.",
            None,
        )
    if not usable:
        return (
            "needs_review",
            "Candidates exist but all need review: pointing, steering, "
            "limb, or angle wording that the simple cone model cannot "
            "use directly. Provide an explicit number with a source.",
            None,
        )
    first_kind = usable[0]["kind"]
    same_kind = [c for c in usable if c["kind"] == first_kind]
    values = [c["value"] for c in same_kind]
    if max(values) - min(values) <= max(1e-6, 0.001 * max(values)):
        return (
            "conclusive",
            f"Single consistent {first_kind} across {len(same_kind)} "
            "candidate(s). Confirm it before running coverage.",
            same_kind[0],
        )
    return (
        "ambiguous",
        f"Found {len(usable)} usable candidates with different values. "
        "Pick one with its source or provide your own number.",
        None,
    )


def validate_geometry_inputs(
    field_of_regard_deg: Optional[Any] = None,
    swath_width_km: Optional[Any] = None,
    geometry_reference: Optional[Any] = None,
) -> Tuple[Optional[float], Optional[float], str]:
    """Validate explicit geometry inputs for the coverage tool.

    Requires exactly one of field_of_regard_deg or swath_width_km,
    plus a non-empty source string. Returns cleaned values.
    """
    if not isinstance(geometry_reference, str) or not geometry_reference.strip():
        raise ValueError(
            "geometry_reference is required: cite where the angle or swath "
            "came from, for example an OSCAR URL or a mission page"
        )
    reference = geometry_reference.strip()
    has_for = field_of_regard_deg is not None
    has_swath = swath_width_km is not None
    if has_for and has_swath:
        raise ValueError(
            "Provide exactly one of field_of_regard_deg or swath_width_km, "
            "not both"
        )
    if not has_for and not has_swath:
        raise ValueError(
            "Missing geometry: provide field_of_regard_deg or swath_width_km "
            "with geometry_reference. Use the geometry resolver tool first "
            "when the value is unknown"
        )
    if has_for:
        if isinstance(field_of_regard_deg, bool):
            raise ValueError("field_of_regard_deg must be a number")
        try:
            for_value = float(field_of_regard_deg)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "field_of_regard_deg must be a number of degrees"
            ) from exc
        if not math.isfinite(for_value) or not 0 < for_value <= 360:
            raise ValueError(
                "field_of_regard_deg must be between 0 and 360 degrees, "
                f"got {field_of_regard_deg!r}"
            )
        return for_value, None, reference
    if isinstance(swath_width_km, bool):
        raise ValueError("swath_width_km must be a number")
    try:
        swath_value = float(swath_width_km)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("swath_width_km must be a number of km") from exc
    if not math.isfinite(swath_value) or swath_value <= 0:
        raise ValueError(
            f"swath_width_km must be a positive finite number, got {swath_width_km!r}"
        )
    if swath_value > 20000:
        raise ValueError(
            f"swath_width_km looks wrong: got {swath_width_km!r} km"
        )
    return None, swath_value, reference


def convert_swath_to_for(swath_width_km: Any, altitude_m: Any) -> float:
    """Convert a ground swath width to a field of regard in degrees.

    Uses TAT-C's own converter so the math matches the coverage run.
    Falls back to a spherical Earth formula when TAT-C is missing.
    """
    if isinstance(swath_width_km, bool):
        raise ValueError("swath_width_km must be a number")
    if isinstance(altitude_m, bool):
        raise ValueError("altitude_m must be a number")
    try:
        swath_km = float(swath_width_km)  # type: ignore[arg-type]
        altitude = float(altitude_m)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "swath_width_km and altitude_m must be numbers"
        ) from exc
    if not math.isfinite(swath_km) or swath_km <= 0:
        raise ValueError(f"swath_width_km must be positive, got {swath_width_km!r}")
    if not math.isfinite(altitude) or altitude <= 0:
        raise ValueError(f"altitude_m must be positive, got {altitude_m!r}")
    try:
        from tatc.utils import swath_width_to_field_of_regard

        result = float(
            swath_width_to_field_of_regard(altitude, swath_km * 1000.0)
        )
    except ImportError:
        # Spherical fallback: central angle from swath, then Snell-like
        # geometry to the satellite nadir angle. Matches TAT-C closely
        # for LEO swaths; TAT-C remains the reference when installed.
        earth_r = 6371000.0
        central = (swath_km * 1000.0 / 2.0) / earth_r
        sin_nadir = earth_r / (earth_r + altitude) * math.sin(central)
        sin_nadir = min(1.0, max(-1.0, sin_nadir))
        result = math.degrees(2.0 * (central + math.asin(sin_nadir)))
    if not math.isfinite(result) or not 0 < result <= 360:
        raise ValueError(f"Conversion gave an invalid angle: {result!r}")
    return round(result, 6)


def convert_for_to_swath_km(field_of_regard_deg: Any, altitude_m: Any) -> float:
    """Convert a field of regard to a ground swath width in km."""
    if isinstance(field_of_regard_deg, bool):
        raise ValueError("field_of_regard_deg must be a number")
    try:
        for_deg = float(field_of_regard_deg)  # type: ignore[arg-type]
        altitude = float(altitude_m)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "field_of_regard_deg and altitude_m must be numbers"
        ) from exc
    if not math.isfinite(for_deg) or not 0 < for_deg <= 360:
        raise ValueError(
            f"field_of_regard_deg must be between 0 and 360, got {field_of_regard_deg!r}"
        )
    if not math.isfinite(altitude) or altitude <= 0:
        raise ValueError(f"altitude_m must be positive, got {altitude_m!r}")
    try:
        from tatc.utils import field_of_regard_to_swath_width

        return round(float(field_of_regard_to_swath_width(altitude, for_deg)) / 1000.0, 6)
    except ImportError:
        earth_r = 6371000.0
        half = math.radians(for_deg / 2.0)
        sin_term = (earth_r + altitude) * math.sin(half) / earth_r
        if sin_term >= 1.0:
            width_m = 2.0 * earth_r * math.acos(earth_r / (earth_r + altitude))
        else:
            width_m = 2.0 * earth_r * (math.asin(sin_term) - half)
        return round(width_m / 1000.0, 6)
