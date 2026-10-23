"""Extract instrument geometry and assess its use in modeled coverage.

Numeric extraction and compatibility are separate conclusions. The parser
keeps candidate provenance, while the assessment requires positive support
evidence or a known approximation before coverage can run.
"""

import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple

from tatc.utils import swath_width_to_field_of_regard

_SWATH_KM = re.compile(
    r"swath(?:\s+width)?[^0-9]{0,35}(\d+(?:\.\d+)?)\s*km",
    re.IGNORECASE,
)
_SWATH_KM_REVERSED = re.compile(
    r"(?<![\d.+\-\u2212])([0-9]+(?:\.[0-9]+)?)\s*km\s+swath",
    re.IGNORECASE,
)
_TRACK_SPAN_KM = re.compile(
    r"\b(?:separated|discrete)\s+(?:laser\s+)?tracks?\b"
    r"[\s\S]{0,140}?\b(?:span(?:ning)?|overall\s+(?:swath\s+)?(?:span|width)|"
    r"total\s+(?:swath\s+)?(?:span|width))\b"
    r"[^0-9]{0,30}(\d+(?:\.\d+)?)\s*km",
    re.IGNORECASE,
)
_OVERALL_SWATH_BASIS = re.compile(
    r"\b(?:overall|total)\s+(?:swath\s+)?(?:span|width)\b",
    re.IGNORECASE,
)
_FOR_DEG = re.compile(
    r"field[ -]of[ -]regard[^0-9\xb1]{0,35}"
    r"(\d+(?:\.\d+)?)\s*(?:degrees?|deg)",
    re.IGNORECASE,
)
_FOR_KM = re.compile(
    r"field[ -]of[ -]regard[^0-9]{0,35}(\d+(?:\.\d+)?)\s*km",
    re.IGNORECASE,
)
_FOV_DEG = re.compile(
    r"(?:f\.?\s?o\.?\s?v\.?|field of view)[^0-9\xb1]{0,35}"
    r"(\d+(?:\.\d+)?)\s*(?:degrees?|deg)",
    re.IGNORECASE,
)
_HALF_ANGLE_DEG = re.compile(
    r"\xb1\s*(\d+(?:\.\d+)?)\s*-?\s*(?:degrees?|deg)",
    re.IGNORECASE,
)
_STEER_DEG = re.compile(
    r"steer(?:able|ing)?[^0-9\xb1]{0,35}\xb1\s*"
    r"(\d+(?:\.\d+)?)\s*-?\s*(?:degrees?|deg)",
    re.IGNORECASE,
)
_MODE = re.compile(
    r"\b(?P<left>[a-z][a-z0-9._-]{0,18})\s+mode\b|"
    r"\bmode\s+(?P<right>[a-z0-9][a-z0-9._-]{0,18})\b",
    re.IGNORECASE,
)

_PRIMARY_KINDS = {"swath_width_km", "field_of_regard_deg"}
_KNOWN_APPROXIMATION_HINTS = (
    "off-nadir",
    "off nadir",
    "side-looking",
    "side looking",
    "oblique",
    "non-nadir",
    "non nadir",
)
_UNCLEAR_GEOMETRY_HINTS = (
    "limb",
    "steerable",
    "tilt",
    "phase angle",
    "field of view",
    "fov",
)
_GLOBAL_CONTEXT_FIELDS = {
    "instrument_name",
    "instrument_type",
    "instrument_description",
    "api_description",
    "page_description",
    "description",
    "name",
    "type",
    "classification",
    "variables",
}


def _clean(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())


def _split_clauses(text: str) -> List[str]:
    return [
        clause.strip()
        for clause in re.split(r"(?<=[.!?;])\s+|\n+", text)
        if clause.strip()
    ]


def _clause_at(text: str, position: int) -> str:
    cursor = 0
    for clause in _split_clauses(text):
        start = text.find(clause, cursor)
        end = start + len(clause)
        if start <= position <= end:
            return clause
        cursor = end
    return _clean(text)


def _mode_scope(text: str) -> Tuple[Optional[str], List[str]]:
    modes = sorted(
        {
            " ".join((match.group("left") or match.group("right")).lower().split())
            for match in _MODE.finditer(text)
        }
    )
    return (modes[0], modes) if len(modes) == 1 else (None, modes)


def _scalar_match_is_unambiguous(text: str, match: re.Match[str]) -> bool:
    prefix = text[: match.start(1)]
    suffix = text[match.end(1) :]
    if re.search(r"(?:[-+\u2212\u2013\u2014]|\bto)\s*$", prefix, re.IGNORECASE):
        return False
    if re.match(r"\s*(?:[-\u2212\u2013\u2014]|\bto\b)\s*\d", suffix, re.IGNORECASE):
        return False
    return True


def _add(
    candidates: List[Dict[str, Any]],
    kind: str,
    value: float,
    unit: str,
    source_field: str,
    source_text: str,
    confidence: str,
    derived: bool = False,
    mode: Optional[str] = None,
    basis: Optional[str] = None,
) -> None:
    if not math.isfinite(value) or value <= 0:
        return
    normalized = round(float(value), 6)
    for existing in candidates:
        if (
            existing["kind"] == kind
            and existing["value"] == normalized
            and existing["source_field"] == source_field
            and existing["source_text"] == _clean(source_text)
            and existing.get("mode") == mode
            and existing.get("basis") == basis
        ):
            return
    candidates.append(
        {
            "kind": kind,
            "value": normalized,
            "unit": unit,
            "source_field": source_field,
            "source_text": _clean(source_text),
            "confidence": confidence,
            "derived": derived,
            "mode": mode,
            "basis": basis,
        }
    )


def extract_geometry_candidates(
    specs: Optional[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Extract numeric candidates while keeping their source field and text."""
    candidates: List[Dict[str, Any]] = []
    if not isinstance(specs, dict):
        return candidates
    for field, raw in specs.items():
        text = _clean(raw)
        if not text:
            continue
        for pattern, kind, unit, confidence in (
            (_SWATH_KM, "swath_width_km", "km", "high"),
            (_SWATH_KM_REVERSED, "swath_width_km", "km", "high"),
            (_FOR_DEG, "field_of_regard_deg", "deg", "high"),
            (_FOR_KM, "field_of_regard_km", "km", "low"),
            (_FOV_DEG, "field_of_view_deg", "deg", "medium"),
            (_STEER_DEG, "off_nadir_steer_deg", "deg", "low"),
        ):
            for match in pattern.finditer(text):
                if not _scalar_match_is_unambiguous(text, match):
                    continue
                clause = _clause_at(text, match.start())
                mode, _ = _mode_scope(clause)
                _add(
                    candidates,
                    kind,
                    float(match.group(1)),
                    unit,
                    str(field),
                    clause,
                    confidence,
                    mode=mode,
                    basis=(
                        "overall_track_span"
                        if kind == "swath_width_km"
                        and _OVERALL_SWATH_BASIS.search(clause)
                        else None
                    ),
                )

        for match in _TRACK_SPAN_KM.finditer(text):
            if not _scalar_match_is_unambiguous(text, match):
                continue
            clause = _clause_at(text, match.start())
            mode, _ = _mode_scope(clause)
            _add(
                candidates,
                "swath_width_km",
                float(match.group(1)),
                "km",
                str(field),
                clause,
                "high",
                mode=mode,
                basis="overall_track_span",
            )

        # A bare half angle is not a field of regard unless the source says
        # that it is one. Keep it as a derived, low-certainty candidate.
        if "field of regard" not in text.lower():
            for match in _HALF_ANGLE_DEG.finditer(text):
                if not _scalar_match_is_unambiguous(text, match):
                    continue
                clause = _clause_at(text, match.start())
                mode, _ = _mode_scope(clause)
                _add(
                    candidates,
                    "field_of_regard_deg",
                    float(match.group(1)) * 2,
                    "deg",
                    str(field),
                    clause,
                    "low",
                    derived=True,
                    mode=mode,
                )
    return candidates


def _context_records(
    context: Any, prefix: str = ""
) -> List[Tuple[str, str, bool]]:
    records: List[Tuple[str, str, bool]] = []
    if isinstance(context, str):
        if context.strip():
            records.append((prefix or "source_description", context.strip(), False))
    elif isinstance(context, dict):
        for key, value in context.items():
            field = f"{prefix}.{key}" if prefix else str(key)
            root_field = field.split(".", 1)[0].lower()
            is_global = root_field in _GLOBAL_CONTEXT_FIELDS
            if isinstance(value, str) and value.strip():
                records.append((field, value.strip(), is_global))
            elif isinstance(value, dict):
                records.extend(_context_records(value, field))
            elif isinstance(value, (list, tuple)):
                for index, item in enumerate(value):
                    item_field = f"{field}[{index}]"
                    if isinstance(item, str) and item.strip():
                        records.append((item_field, item.strip(), is_global))
                    elif isinstance(item, dict):
                        records.extend(_context_records(item, item_field))
                    elif item is not None:
                        records.append((item_field, str(item), is_global))
    elif isinstance(context, (list, tuple)):
        for index, value in enumerate(context):
            records.extend(_context_records(value, f"{prefix or 'source'}[{index}]"))
    return records


def _source_units(
    candidates: Sequence[Dict[str, Any]], context: Any = None
) -> List[Dict[str, Any]]:
    units: List[Dict[str, Any]] = []
    records = _context_records(context)
    context_clauses = {
        _clean(clause)
        for _, text, _ in records
        for clause in _split_clauses(text)
    }
    present = {
        (field.rsplit(".", 1)[-1].lower(), _clean(clause))
        for field, text, _ in records
        for clause in _split_clauses(text)
    }
    for candidate in candidates:
        field = str(candidate.get("source_field") or "candidate_source")
        text = _clean(candidate.get("source_text"))
        if text in context_clauses:
            continue
        if text and (field.lower(), text) not in present:
            records.append((field, text, False))
            present.add((field.lower(), text))

    for field, text, is_global in records:
        for clause in _split_clauses(text):
            mode, modes = _mode_scope(clause)
            units.append(
                {
                    "source_field": field,
                    "text": clause,
                    "mode": mode,
                    "mode_labels": modes,
                    "global": is_global,
                }
            )
    return units


def _source_context_text(units: Sequence[Dict[str, Any]]) -> str:
    return " ".join(
        f"{unit['source_field']}: {unit['text']}" for unit in units
    )


def _close(left: float, right: float) -> bool:
    # Candidate values are rounded to six decimal places in _add. Compare at
    # that precision rather than treating a source value as a broad tolerance.
    return round(left, 6) == round(right, 6)


def _describe_candidates(candidates: Sequence[Dict[str, Any]]) -> str:
    return "; ".join(
        f"{candidate.get('mode') + ' mode ' if candidate.get('mode') else ''}"
        f"{candidate.get('kind')}={candidate.get('value')} {candidate.get('unit')}"
        f" (field {candidate.get('source_field')})"
        for candidate in candidates
    )


def _numeric_certainty(
    candidates: Sequence[Dict[str, Any]], selected: Optional[Dict[str, Any]]
) -> str:
    if selected is not None:
        return "low" if selected.get("derived") else str(selected.get("confidence", "low"))
    if not candidates:
        return "none"
    kinds = {candidate.get("kind") for candidate in candidates}
    values_by_kind: Dict[Any, List[float]] = {}
    for candidate in candidates:
        try:
            values_by_kind.setdefault(candidate.get("kind"), []).append(
                float(candidate["value"])
            )
        except (KeyError, TypeError, ValueError):
            continue
    if len(kinds) > 1 or any(
        any(not _close(value, values[0]) for value in values[1:])
        for values in values_by_kind.values()
        if values
    ):
        return "ambiguous"
    rank = {"low": 0, "medium": 1, "high": 2}
    return max(
        (str(candidate.get("confidence", "low")) for candidate in candidates),
        key=lambda confidence: rank.get(confidence, 0),
    )


_QUALIFICATION = re.compile(
    r"\b(?:not\s+necessarily|may|might|could|possibly|perhaps|maybe|"
    r"potentially|apparently|probably|would|should|if|whether|"
    r"appears?\s+to(?:\s+be)?|seems?\s+to(?:\s+be)?|"
    r"assumed|believed|thought|uncertain|unclear|unknown|unconfirmed)\b",
    re.IGNORECASE,
)


def _match_is_qualified(text: str, match: re.Match[str]) -> bool:
    """Whether nearby wording qualifies this geometry assertion as uncertain."""
    clause = _clause_at(text, match.start())
    local_start = clause.find(match.group())
    if local_start < 0:
        return False
    window = clause[max(0, local_start - 70) : local_start + len(match.group()) + 70]
    return bool(_QUALIFICATION.search(window))


def _has_qualified(text: str, pattern: str) -> bool:
    return any(
        _match_is_qualified(text, match)
        for match in re.finditer(pattern, text, re.IGNORECASE)
    )


def _has_affirmative(text: str, pattern: str) -> bool:
    for match in re.finditer(pattern, text, re.IGNORECASE):
        if _match_is_qualified(text, match):
            continue
        clause = _clause_at(text, match.start())
        local_start = clause.find(match.group())
        if local_start < 0:
            continue
        prefix = clause[max(0, local_start - 60) : local_start]
        if re.search(
            r"\b(?:not|never|isn't|is not|cannot|can't|no|without|"
            r"does not use|doesn't use)\s+(?:(?:clearly|separate|additional|any|the|a|an)\s+)*$",
            prefix,
            re.IGNORECASE,
        ):
            continue
        return True
    return False


def _explicitly_global_mode_scope(text: str) -> bool:
    return bool(
        re.search(
            r"\b(?:all|both|every)\s+(?:operating\s+)?modes?\b|"
            r"\bregardless of (?:the )?mode\b|\bapplies across all modes\b",
            text,
            re.IGNORECASE,
        )
    )


def _unit_assertions(text: str) -> Dict[str, Any]:
    support_patterns = {
        "continuous": r"\bcontinuous\b",
        "nadir_centered": (
            r"\bnadir[ -]center(?:ed|ing)\b|"
            r"\bcenter(?:ed|ing) (?:directly )?(?:beneath|under) (?:the )?satellite\b|"
            r"\bdirectly beneath (?:the )?satellite\b"
        ),
        "gap_free": (
            r"\bno\s+gaps?\b|\bgap[- ]free\b|\bwithout\s+gaps?\b|"
            r"\bgaps?\s+(?:are\s+)?not\s+present\b"
        ),
        "no_pointing_or_steering": (
            r"\bno\s+(?:(?:separate|additional|off[ -]nadir)\s+)?"
            r"(?:pointing|steering)(?:\s+is)?\s+(?:needed|required|necessary)\b|"
            r"\bno\s+gaps?\s+(?:or|and)\s+(?:separate\s+)?pointing"
            r"(?:\s+is)?\s+(?:needed|required|necessary)\b|"
            r"\bwithout\s+(?:(?:separate|additional)\s+)?(?:pointing|steering)\b|"
            r"\b(?:pointing|steering)\s+(?:is\s+)?not\s+"
            r"(?:needed|required|necessary)\b"
        ),
    }
    positive = {
        key: _has_affirmative(text, pattern)
        for key, pattern in support_patterns.items()
    }
    negative = {
        "continuous": bool(
            re.search(
                r"\b(?:not|never|isn't|is not)\s+continuous\b|"
                r"\b(?:non[- ]continuous|discontinuous)\b",
                text,
                re.IGNORECASE,
            )
        ),
        "nadir_centered": bool(
            re.search(
                r"\b(?:not|never|isn't|is not)\s+(?:nadir[ -])?centered\b",
                text,
                re.IGNORECASE,
            )
        ),
        "gap_free": bool(
            re.search(
                r"\bnot\s+(?:gap[- ]free|no\s+gaps?)\b|"
                r"\b(?:with|has|have|contains?|containing)\s+(?:separated\s+)?gaps?\b|"
                r"\bgaps?\s+(?:remain|are\s+present|between)\b",
                text,
                re.IGNORECASE,
            )
        ),
        "no_pointing_or_steering": _has_affirmative(
            text,
            r"\b(?:requires?|needs?|must use)\b[^.!?;]{0,45}"
            r"\b(?:separate\s+)?(?:pointing|steering)\b|"
            r"\b(?:pointing|steering)\s+(?:is\s+)?(?:required|necessary)\b",
        ),
    }
    separated_tracks = _has_affirmative(
        text, r"\b(?:separated|discrete)\s+(?:laser\s+)?tracks?\b"
    )
    qualified_assertions = [
        key for key, pattern in support_patterns.items() if _has_qualified(text, pattern)
    ]
    if _has_qualified(text, r"\b(?:separated|discrete)\s+(?:laser\s+)?tracks?\b"):
        qualified_assertions.append("separated_tracks")
    for hint in (*_KNOWN_APPROXIMATION_HINTS, *_UNCLEAR_GEOMETRY_HINTS):
        if _has_qualified(text, re.escape(hint)):
            qualified_assertions.append(hint)
    gap_present = negative["gap_free"] or separated_tracks
    known_non_nadir = [
        hint
        for hint in _KNOWN_APPROXIMATION_HINTS
        if _has_affirmative(text, re.escape(hint))
    ]
    unclear = [
        hint
        for hint in _UNCLEAR_GEOMETRY_HINTS
        if _has_affirmative(text, re.escape(hint))
    ]
    if _has_affirmative(text, r"\bsteering\b|\bsteerable\b"):
        unclear.append("steering")
    conditional = bool(
        re.search(
            r"\b(?:may|might|can|could|sometimes|occasionally|depending on)\b"
            r"[^.!?;]{0,55}\b(?:off[ -]nadir|side[ -]looking|oblique|steer(?:ing|able)?)\b",
            text,
            re.IGNORECASE,
        )
    )
    return {
        "positive": positive,
        "negative": negative,
        "gap_present": gap_present,
        "separated_tracks": separated_tracks,
        "known_non_nadir": known_non_nadir,
        "unclear": sorted(set(unclear)),
        "qualified_assertions": sorted(set(qualified_assertions)),
        "conditional_geometry": conditional,
    }


def _has_geometry_assertion(assertions: Dict[str, Any]) -> bool:
    return bool(
        any(assertions["positive"].values())
        or any(assertions["negative"].values())
        or assertions["gap_present"]
        or assertions["separated_tracks"]
        or assertions["known_non_nadir"]
        or assertions["unclear"]
        or assertions["qualified_assertions"]
        or assertions["conditional_geometry"]
    )


def _all_modes(
    candidates: Sequence[Dict[str, Any]], units: Sequence[Dict[str, Any]]
) -> List[str]:
    modes = {str(candidate["mode"]).lower() for candidate in candidates if candidate.get("mode")}
    for unit in units:
        modes.update(str(mode).lower() for mode in unit.get("mode_labels", []))
    return sorted(modes)


def _candidate_conflict(
    candidates: Sequence[Dict[str, Any]],
    geometry_mode: Optional[str],
    source_modes: Sequence[str],
) -> Optional[str]:
    primary = [candidate for candidate in candidates if candidate.get("kind") in _PRIMARY_KINDS]
    other = [candidate for candidate in candidates if candidate.get("kind") not in _PRIMARY_KINDS]
    if other:
        return "Other geometry measurements are also present: " + _describe_candidates(other)
    if not primary:
        return "No swath width or full field-of-regard candidate was extracted."

    modes = sorted(
        set(source_modes)
        | {str(candidate["mode"]).lower() for candidate in primary if candidate.get("mode")}
    )
    if geometry_mode:
        requested_mode = _clean(geometry_mode).lower()
        if not modes:
            return f"The requested mode {geometry_mode!r} is not named in the source evidence."
        if requested_mode not in modes:
            return (
                f"The requested mode {geometry_mode!r} does not match the source modes: "
                + ", ".join(modes)
                + "."
            )
        if len(modes) > 1 and any(candidate.get("mode") is None for candidate in primary):
            return (
                "A geometry value has no mode scope while the source describes several modes. "
                "Clarify which mode the unscoped value applies to: "
                + _describe_candidates(primary)
            )
        primary = [candidate for candidate in primary if candidate.get("mode") == requested_mode]
    elif len(modes) > 1:
        return (
            "Several operating modes are named in the source. Choose a mode: "
            + _describe_candidates(primary)
        )

    kinds = {candidate["kind"] for candidate in primary}
    if len(kinds) > 1:
        return "Different geometry kinds appear in the source: " + _describe_candidates(primary)

    values = [float(candidate["value"]) for candidate in primary]
    if values and any(not _close(value, values[0]) for value in values[1:]):
        return "Conflicting values appear in the source: " + _describe_candidates(primary)
    return None


def _scope_evidence_units(
    units: Sequence[Dict[str, Any]], selected_mode: Optional[str], source_modes: Sequence[str]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    relevant: List[Dict[str, Any]] = []
    unresolved: List[Dict[str, Any]] = []
    multiple_modes = len(source_modes) > 1
    for unit in units:
        assertions = _unit_assertions(unit["text"])
        if len(unit.get("mode_labels", [])) > 1:
            if _has_geometry_assertion(assertions):
                unresolved.append(unit)
            continue
        mode = unit.get("mode")
        if mode:
            if selected_mode and mode == selected_mode:
                relevant.append(unit)
            continue
        if unit.get("global") or _explicitly_global_mode_scope(unit["text"]) or not multiple_modes:
            relevant.append(unit)
        elif _has_geometry_assertion(assertions):
            unresolved.append(unit)
    return relevant, unresolved


def _combined_assertions(units: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    states = {
        "positive": {key: False for key in (
            "continuous", "nadir_centered", "gap_free", "no_pointing_or_steering"
        )},
        "negative": {key: False for key in (
            "continuous", "nadir_centered", "gap_free", "no_pointing_or_steering"
        )},
        "gap_present": False,
        "separated_tracks": False,
        "known_non_nadir": [],
        "unclear": [],
        "qualified_assertions": [],
        "conditional_geometry": False,
        "observations": [],
    }
    for unit in units:
        assertions = _unit_assertions(unit["text"])
        states["observations"].append(
            {"source_field": unit["source_field"], "text": unit["text"], "assertions": assertions}
        )
        for group in ("positive", "negative"):
            for key, value in assertions[group].items():
                states[group][key] = states[group][key] or value
        states["gap_present"] |= assertions["gap_present"]
        states["separated_tracks"] |= assertions["separated_tracks"]
        states["known_non_nadir"].extend(assertions["known_non_nadir"])
        states["unclear"].extend(assertions["unclear"])
        states["qualified_assertions"].extend(assertions["qualified_assertions"])
        states["conditional_geometry"] |= assertions["conditional_geometry"]
    states["known_non_nadir"] = sorted(set(states["known_non_nadir"]))
    states["unclear"] = sorted(set(states["unclear"]))
    states["qualified_assertions"] = sorted(set(states["qualified_assertions"]))
    return states


def _positive_support(assertions: Dict[str, Any]) -> bool:
    return all(assertions["positive"].values()) and not any(
        assertions["negative"].values()
    ) and not assertions["gap_present"] and not assertions["qualified_assertions"]


def _classification(
    candidates: Sequence[Dict[str, Any]],
    assertions: Dict[str, Any],
    selected: Optional[Dict[str, Any]],
    conflict: Optional[str],
    unresolved_mode_evidence: Sequence[Dict[str, Any]],
    selected_mode: Optional[str],
) -> Tuple[str, str, List[str]]:
    if conflict:
        return "needs_clarification", conflict, []
    if selected is None:
        return (
            "needs_clarification",
            "No single numeric geometry candidate is established by the source evidence.",
            [],
        )
    if selected.get("derived") or selected.get("kind") not in _PRIMARY_KINDS:
        return (
            "needs_clarification",
            "The extracted number does not establish a ground swath width or full field of regard.",
            [],
        )
    if unresolved_mode_evidence:
        fields = sorted({str(unit["source_field"]) for unit in unresolved_mode_evidence})
        return (
            "needs_clarification",
            "Relevant geometry evidence has no clear mode scope in: " + ", ".join(fields) + ".",
            [],
        )
    if (
        assertions["unclear"]
        or assertions["conditional_geometry"]
        or assertions["qualified_assertions"]
    ):
        details = (
            assertions["unclear"]
            + assertions["qualified_assertions"]
            + (["conditional mode or pointing applicability"] if assertions["conditional_geometry"] else [])
        )
        return (
            "needs_clarification",
            "The selected geometry has qualified or unclear source wording: "
            + ", ".join(sorted(set(details)))
            + ".",
            [],
        )

    assertion_conflicts = [
        name
        for name in assertions["positive"]
        if assertions["positive"][name] and assertions["negative"][name]
    ]
    if assertions["positive"]["gap_free"] and assertions["gap_present"]:
        assertion_conflicts.append("gap-free coverage and separated gaps")
    if assertions["positive"]["no_pointing_or_steering"] and assertions["negative"]["no_pointing_or_steering"]:
        assertion_conflicts.append("pointing or steering requirements")
    if assertion_conflicts:
        return (
            "needs_clarification",
            "The selected source evidence contains conflicting geometry assertions: "
            + ", ".join(assertion_conflicts)
            + ".",
            [],
        )

    if assertions["known_non_nadir"] and _positive_support(assertions):
        return (
            "needs_clarification",
            "The selected source evidence describes both supported nadir geometry and non-nadir viewing. Clarify which applies.",
            [],
        )

    track_span = next(
        (
            candidate
            for candidate in candidates
            if candidate.get("basis") == "overall_track_span"
            and candidate.get("kind") == selected.get("kind")
            and _close(float(candidate["value"]), float(selected["value"]))
            and (
                candidate.get("mode") == selected_mode
                or (candidate.get("mode") is None and selected_mode is None)
            )
        ),
        None,
    )
    warnings = []
    if assertions["separated_tracks"]:
        if track_span is None:
            return (
                "needs_clarification",
                "The source describes separated tracks but does not establish their overall span. "
                "A per-track width cannot be used as the envelope width.",
                [],
            )
        if selected.get("kind") != "swath_width_km":
            return (
                "needs_clarification",
                "The selected value is not a ground swath width for the separated-track envelope.",
                [],
            )
        warnings.append(
            f"The source describes separated tracks with an overall span of {selected['value']:g} km. "
            "The model represents the overall envelope as one filled swath and counts gaps between "
            "tracks as accessible. Coverage and revisit estimates describe this approximation and may overstate access."
        )

    if assertions["gap_present"] and not assertions["separated_tracks"]:
        return (
            "needs_clarification",
            "The source describes gaps but does not establish a supported continuous swath or a documented overall envelope.",
            [],
        )

    if assertions["known_non_nadir"]:
        if selected.get("kind") != "swath_width_km":
            return (
                "needs_clarification",
                "The source describes non-nadir viewing, but the selected value is not an explicitly stated ground swath width.",
                [],
            )
        hints = ", ".join(assertions["known_non_nadir"])
        warnings.append(
            f"The source describes {hints} viewing. The model treats the stated ground swath as a "
            "continuous nadir-centered swath; the coverage and revisit estimates describe that "
            "simplification. The direction and size of any difference are not inferred."
        )

    if warnings:
        return "approximate", " ".join(warnings), warnings

    if any(assertions["negative"].values()):
        return (
            "needs_clarification",
            "The source contains negative geometry evidence that does not establish a supported swath or a known approximation.",
            [],
        )

    if _positive_support(assertions):
        return (
            "supported",
            "The selected source evidence affirmatively describes a continuous nadir-centered swath without gaps or separate pointing.",
            [],
        )

    return (
        "needs_clarification",
        "The source does not provide positive evidence affirmatively establishing a continuous nadir-centered swath without gaps or separate pointing, and it does not describe a specific understood approximation.",
        [],
    )


def assess_geometry_candidates(
    candidates: List[Dict[str, Any]],
    *,
    context: Any = None,
    geometry_kind: Optional[str] = None,
    geometry_value: Optional[float] = None,
    geometry_mode: Optional[str] = None,
    source_reference: Optional[str] = None,
    source_description: Optional[str] = None,
) -> Dict[str, Any]:
    """Classify extracted values as supported, approximate, or unclear."""
    source_units = _source_units(candidates, context)
    source_modes = _all_modes(candidates, source_units)
    context_text = _source_context_text(source_units)
    conflict = _candidate_conflict(candidates, geometry_mode, source_modes)
    selected: Optional[Dict[str, Any]] = None

    if geometry_kind is not None and geometry_value is not None:
        requested_mode = _clean(geometry_mode).lower() if geometry_mode else None
        pool = [
            candidate
            for candidate in candidates
            if requested_mode is None or candidate.get("mode") == requested_mode
        ]
        matching = [
            candidate
            for candidate in pool
            if candidate.get("kind") == geometry_kind
            and _close(float(candidate.get("value", math.nan)), float(geometry_value))
        ]
        if not matching:
            chosen_in_other_mode = next(
                (
                    candidate
                    for candidate in candidates
                    if candidate.get("kind") == geometry_kind
                    and _close(float(candidate.get("value", math.nan)), float(geometry_value))
                ),
                None,
            )
            if chosen_in_other_mode is not None and geometry_mode:
                conflict = (
                    f"The supplied {geometry_kind} value {geometry_value:g} is listed for "
                    f"mode {chosen_in_other_mode.get('mode')!r}, not requested mode "
                    f"{geometry_mode!r}."
                )
                selected = None
            else:
                conflict = (
                    f"The supplied {geometry_kind} value {geometry_value:g} does not match a "
                    "candidate in the source description. Extracted candidates: "
                    + (_describe_candidates(candidates) or "none")
                )
                selected = None
        else:
            selected = next(
                (candidate for candidate in matching if candidate.get("basis") == "overall_track_span"),
                matching[0],
            )
    elif conflict is None:
        primary = [candidate for candidate in candidates if candidate.get("kind") in _PRIMARY_KINDS]
        if geometry_mode:
            requested_mode = _clean(geometry_mode).lower()
            primary = [candidate for candidate in primary if candidate.get("mode") == requested_mode]
        if primary:
            selected = next(
                (candidate for candidate in primary if candidate.get("basis") == "overall_track_span"),
                primary[0],
            )

    selected_mode = (
        _clean(geometry_mode).lower()
        if geometry_mode
        else (str(selected.get("mode")).lower() if selected and selected.get("mode") else None)
    )
    scoped_units, unresolved_mode_evidence = _scope_evidence_units(
        source_units, selected_mode, source_modes
    )
    assertions = _combined_assertions(scoped_units)
    status, reason, warnings = _classification(
        candidates,
        assertions,
        selected,
        conflict,
        unresolved_mode_evidence,
        selected_mode,
    )
    numeric_certainty = _numeric_certainty(candidates, selected)
    return {
        "geometry_status": status,
        "numeric_certainty": numeric_certainty,
        "geometry_input_kind": geometry_kind or (selected or {}).get("kind"),
        "geometry_input_value": geometry_value if geometry_value is not None else (selected or {}).get("value"),
        "geometry_mode": selected_mode,
        "source_reference": source_reference,
        "source_description": source_description,
        "source_candidates": [dict(candidate) for candidate in candidates],
        "selected_candidate": dict(selected) if selected is not None else None,
        "evidence": {
            "positive_continuous_nadir_support": _positive_support(assertions),
            "source_context": context_text,
            "source_units": source_units,
            "scoped_source_units": scoped_units,
            "unresolved_mode_evidence": unresolved_mode_evidence,
            "assertions": assertions,
        },
        "warnings": warnings,
        "interpretation": reason,
    }


def resolve_geometry_status(
    candidates: List[Dict[str, Any]],
    context: Any = None,
) -> Tuple[str, str, Optional[Dict[str, Any]]]:
    """Return compatibility, explanation, and a candidate when unambiguous."""
    assessment = assess_geometry_candidates(candidates, context=context)
    if assessment["geometry_status"] in {"supported", "approximate"}:
        selected = assessment["selected_candidate"]
        return assessment["geometry_status"], assessment["interpretation"], selected
    return assessment["geometry_status"], assessment["interpretation"], None


def validate_geometry_inputs(
    field_of_regard_deg: Optional[Any] = None,
    swath_width_km: Optional[Any] = None,
    geometry_reference: Optional[Any] = None,
) -> Tuple[Optional[float], Optional[float], str]:
    """Validate the numeric shape and citation, without assessing compatibility."""
    if not isinstance(geometry_reference, str) or not geometry_reference.strip():
        raise ValueError(
            "geometry_reference is required: cite where the angle or swath came from"
        )
    reference = geometry_reference.strip()
    has_for = field_of_regard_deg is not None
    has_swath = swath_width_km is not None
    if has_for and has_swath:
        raise ValueError(
            "Provide exactly one of field_of_regard_deg or swath_width_km, not both"
        )
    if not has_for and not has_swath:
        raise ValueError(
            "Missing geometry: provide field_of_regard_deg or swath_width_km"
        )
    if has_for:
        if isinstance(field_of_regard_deg, bool):
            raise ValueError("field_of_regard_deg must be a number")
        try:
            for_value = float(field_of_regard_deg)  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise ValueError("field_of_regard_deg must be a number of degrees") from exc
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
        raise ValueError(f"swath_width_km looks wrong: got {swath_width_km!r} km")
    return None, swath_value, reference


def assess_manual_geometry(
    field_of_regard_deg: Optional[Any],
    swath_width_km: Optional[Any],
    geometry_reference: Optional[Any],
    geometry_source_description: Optional[Any],
    geometry_mode: Optional[Any] = None,
) -> Tuple[Optional[float], Optional[float], str, Dict[str, Any]]:
    """Validate and classify researcher-supplied geometry from source evidence."""
    for_value, swath_value, reference = validate_geometry_inputs(
        field_of_regard_deg, swath_width_km, geometry_reference
    )
    if not isinstance(geometry_source_description, str) or not geometry_source_description.strip():
        raise ValueError(
            "geometry_source_description is required. Include the relevant source text "
            "that explains what the selected angle or swath represents."
        )
    if geometry_mode is not None and (
        not isinstance(geometry_mode, str) or not geometry_mode.strip()
    ):
        raise ValueError("geometry_mode must be a non-empty string when given")

    kind = "swath_width_km" if swath_value is not None else "field_of_regard_deg"
    value = swath_value if swath_value is not None else for_value
    assert value is not None
    description = geometry_source_description.strip()
    candidates = extract_geometry_candidates({"researcher_source_description": description})
    assessment = assess_geometry_candidates(
        candidates,
        context=description,
        geometry_kind=kind,
        geometry_value=value,
        geometry_mode=geometry_mode.strip() if isinstance(geometry_mode, str) else None,
        source_reference=reference,
        source_description=description,
    )
    if assessment["geometry_status"] == "needs_clarification":
        raise ValueError("Geometry requires clarification: " + assessment["interpretation"])
    return for_value, swath_value, reference, assessment


def convert_swath_to_for(swath_width_km: Any, altitude_m: Any) -> float:
    """Convert a ground swath width to a field of regard using TAT-C math."""
    if isinstance(swath_width_km, bool):
        raise ValueError("swath_width_km must be a number")
    if isinstance(altitude_m, bool):
        raise ValueError("altitude_m must be a number")
    try:
        swath_km = float(swath_width_km)  # type: ignore[arg-type]
        altitude = float(altitude_m)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise ValueError("swath_width_km and altitude_m must be numbers") from exc
    if not math.isfinite(swath_km) or swath_km <= 0:
        raise ValueError(f"swath_width_km must be positive, got {swath_width_km!r}")
    if not math.isfinite(altitude) or altitude <= 0:
        raise ValueError(f"altitude_m must be positive, got {altitude!r}")
    result = float(swath_width_to_field_of_regard(altitude, swath_km * 1000.0))
    if not math.isfinite(result) or not 0 < result <= 360:
        raise ValueError(f"Conversion gave an invalid angle: {result!r}")
    return round(result, 6)
