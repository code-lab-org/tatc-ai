"""Numeric geometry extraction and input validation for modeled coverage."""

import math
import re
from typing import Any, Dict, List, Literal, Optional, Tuple

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from tatc.utils import swath_width_to_field_of_regard


class GeometryAssessment(BaseModel):
    """The caller's interpretation of the supplied instrument evidence."""

    model_config = ConfigDict(extra="forbid")

    status: Literal["supported", "approximate", "needs_clarification"] = Field(
        description="Assistant assessment of this geometry for the coverage model."
    )
    source_text: str = Field(
        min_length=1,
        description="Relevant source evidence, including the selected geometry number.",
    )
    explanation: str = Field(
        min_length=1,
        description=(
            "Why the selected number is supported or how the model approximates it."
        ),
    )
    warning: Optional[str] = Field(
        default=None,
        description="Required for approximate geometry; explain the model mismatch.",
    )
    mode: Optional[str] = Field(
        default=None, description="Selected source mode, when the source names one."
    )

    @field_validator("source_text", "explanation", "warning", "mode")
    @classmethod
    def require_non_empty_text(cls, value: Optional[str]) -> Optional[str]:
        if value is not None and not value.strip():
            raise ValueError("must be non-empty when supplied")
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def check_warning_status(self) -> "GeometryAssessment":
        if self.status == "approximate" and not self.warning:
            raise ValueError("approximate geometry requires a non-empty warning")
        if self.status == "supported" and self.warning is not None:
            raise ValueError("supported geometry cannot include an approximation warning")
        return self


_NUMBER = re.compile(r"(?<![\w.,])(?P<value>\d+(?:\.\d+)?)(?![\d.,])")
_RANGE_BEFORE = re.compile(r"(?:[+\-−–—±]|\bto)\s*$", re.IGNORECASE)
_RANGE_AFTER = re.compile(r"^\s*(?:[\-−–—]|\bto\b)\s*\d", re.IGNORECASE)
_SWATH = re.compile(
    r"\bswath(?:\s+width)?\b[^0-9]{0,35}(\d+(?:\.\d+)?)\s*km\b",
    re.IGNORECASE,
)
_SWATH_REVERSED = re.compile(
    r"(?<![\w.,])([0-9]+(?:\.[0-9]+)?)\s*km\s+swath\b", re.IGNORECASE
)
_FOR_DEG = re.compile(
    r"\bfield[ -]of[ -]regard\b[^0-9±]{0,35}"
    r"(\d+(?:\.\d+)?)\s*(?:degrees?|deg)\b",
    re.IGNORECASE,
)
_FOR_KM = re.compile(
    r"\bfield[ -]of[ -]regard\b[^0-9±]{0,35}(\d+(?:\.\d+)?)\s*km\b",
    re.IGNORECASE,
)
_FOV_DEG = re.compile(
    r"\b(?:f\.?\s?o\.?\s?v\.?|field of view)\b[^0-9±]{0,35}"
    r"(\d+(?:\.\d+)?)\s*(?:degrees?|deg)\b",
    re.IGNORECASE,
)
_PATTERNS = (
    ("swath_width_km", _SWATH, "km"),
    ("swath_width_km", _SWATH_REVERSED, "km"),
    ("field_of_regard_deg", _FOR_DEG, "deg"),
    ("field_of_regard_km", _FOR_KM, "km"),
    ("field_of_view_deg", _FOV_DEG, "deg"),
)


def _clean(value: Any) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _scalar_match_is_valid(text: str, match: re.Match[str]) -> bool:
    prefix = text[: match.start(1)]
    suffix = text[match.end(1) :]
    return not _RANGE_BEFORE.search(prefix) and not _RANGE_AFTER.search(suffix)


def extract_geometry_candidates(
    sources: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Extract explicit numeric values with their source field and original text."""
    candidates: List[Dict[str, Any]] = []
    if not isinstance(sources, dict):
        return candidates

    for field, raw in sources.items():
        text = _clean(raw)
        if not text:
            continue
        for kind, pattern, unit in _PATTERNS:
            for match in pattern.finditer(text):
                if not _scalar_match_is_valid(text, match):
                    continue
                value = float(match.group(1))
                if not math.isfinite(value) or value <= 0:
                    continue
                candidate = {
                    "kind": kind,
                    "value": round(value, 6),
                    "unit": unit,
                    "confidence": "high",
                    "source_field": str(field),
                    "source_text": text,
                }
                if candidate not in candidates:
                    candidates.append(candidate)
    return candidates


def numeric_certainty(candidates: List[Dict[str, Any]]) -> str:
    """Describe number extraction only; this does not assess compatibility."""
    if not candidates:
        return "none"
    values = {(item["kind"], item["value"]) for item in candidates}
    return "high" if len(values) == 1 else "ambiguous"


def _source_contains_number(text: str, value: float) -> bool:
    for match in _NUMBER.finditer(text):
        if not _scalar_match_is_valid(text, match):
            continue
        if float(match.group("value")) == value:
            return True
    return False


def validate_geometry_inputs(
    field_of_regard_deg: Optional[Any],
    swath_width_km: Optional[Any],
    geometry_reference: Optional[Any],
    geometry_assessment: Any,
) -> Tuple[Optional[float], Optional[float], str, GeometryAssessment]:
    """Validate model inputs and the caller-supplied geometry assessment.

    Status and explanation come from the assistant's reading of the source.
    These checks validate structure and numeric consistency, not the science
    or authenticity of that interpretation.
    """
    if not isinstance(geometry_reference, str) or not geometry_reference.strip():
        raise ValueError("geometry_reference is required: cite the source")
    reference = geometry_reference.strip()
    if (field_of_regard_deg is None) == (swath_width_km is None):
        raise ValueError(
            "Provide exactly one of field_of_regard_deg or swath_width_km"
        )

    if field_of_regard_deg is not None:
        name, raw, maximum = "field_of_regard_deg", field_of_regard_deg, 360.0
    else:
        name, raw, maximum = "swath_width_km", swath_width_km, 20000.0
    if isinstance(raw, bool):
        raise ValueError(f"{name} must be a number")
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be a number") from exc
    if not math.isfinite(value) or value <= 0 or value > maximum:
        raise ValueError(
            f"{name} must be greater than 0 and at most {maximum}, got {raw!r}"
        )

    try:
        assessment = (
            geometry_assessment
            if isinstance(geometry_assessment, GeometryAssessment)
            else GeometryAssessment.model_validate(geometry_assessment)
        )
    except (ValidationError, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid geometry_assessment: {exc}") from exc
    if assessment.status == "needs_clarification":
        raise ValueError("Geometry requires clarification and cannot be run")
    if not _source_contains_number(assessment.source_text, value):
        raise ValueError(
            "geometry_assessment.source_text must include the selected numeric value"
        )
    if field_of_regard_deg is not None:
        return value, None, reference, assessment
    return None, value, reference, assessment


def convert_swath_to_for(swath_width_km: Any, altitude_m: Any) -> float:
    """Convert a ground swath width to a field of regard using TAT-C math."""
    if isinstance(swath_width_km, bool) or isinstance(altitude_m, bool):
        raise ValueError("swath_width_km and altitude_m must be numbers")
    try:
        swath_km = float(swath_width_km)
        altitude = float(altitude_m)
    except (TypeError, ValueError) as exc:
        raise ValueError("swath_width_km and altitude_m must be numbers") from exc
    if not math.isfinite(swath_km) or swath_km <= 0:
        raise ValueError(f"swath_width_km must be positive, got {swath_width_km!r}")
    if not math.isfinite(altitude) or altitude <= 0:
        raise ValueError(f"altitude_m must be positive, got {altitude_m!r}")
    result = float(swath_width_to_field_of_regard(altitude, swath_km * 1000.0))
    if not math.isfinite(result) or not 0 < result <= 360:
        raise ValueError(f"Conversion gave an invalid angle: {result!r}")
    return round(result, 6)
