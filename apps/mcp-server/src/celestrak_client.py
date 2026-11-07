"""CelesTrak API client for fetching GP (OMM) element data."""

import json
import re
from typing import Any, Dict, List, Optional

import requests

from .celestrak_cache import cached
from .search_utils import normalize_name as _normalize_name
from .search_utils import score_match as _score_search_result
from .validation import validate_norad_id, validate_omm_records

SATCAT_URL = "https://celestrak.org/satcat/records.php"
GP_URL = "https://celestrak.org/NORAD/elements/gp.php"

# A few common colloquial names need explicit mapping because CelesTrak NAME search
# is substring-based and can otherwise resolve to unrelated historical objects.
_COMMON_NAME_ALIASES = {
    "ISS": 25544,
    "INTERNATIONAL SPACE STATION": 25544,
    "INTERNATIONAL SPACE STATION ISS": 25544,
    "ISS ZARYA": 25544,
    "HUBBLE": 20580,
    "HUBBLE SPACE TELESCOPE": 20580,
    "HST": 20580,
    "JAMES WEBB": 50463,
    "JAMES WEBB SPACE TELESCOPE": 50463,
    "JWST": 50463,
    "TIANGONG": 48274,
    "TIANGONG SPACE STATION": 48274,
    "TIANHE": 48274,
    "CSS": 48274,
    "CSS TIANHE": 48274,
    "CHINESE SPACE STATION": 48274,
}


def _parse_json_response(response: requests.Response) -> Any:
    """Parse a JSON response that may be empty."""
    if not response.text or not response.text.strip():
        return []
    return response.json()


def _format_satcat_record(sat: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Normalize a SATCAT record to the public search schema."""
    if not isinstance(sat, dict):
        return None
    norad_id = sat.get("CATNR") or sat.get("NORAD_CAT_ID")
    name = sat.get("OBJECT_NAME") or sat.get("NAME", "Unknown")

    # SATCAT includes historical objects. A populated decay date means the
    # object no longer has current GP data, so returning it would break the
    # documented search-then-resolve tool workflow.
    if not norad_id or str(sat.get("DECAY_DATE") or "").strip():
        return None

    try:
        return {
            "norad_id": int(norad_id),
            "name": str(name),
            "object_type": sat.get("OBJECT_TYPE", ""),
            "country": sat.get("COUNTRY") or sat.get("OWNER", ""),
            "launch_date": sat.get("LAUNCH_DATE", ""),
        }
    except (ValueError, TypeError):
        return None


def _satcat_query_variants(query: str) -> List[str]:
    """Return query variants to try against SATCAT in order.

    CelesTrak NAME search is literal: "NOAA-19" matches nothing while
    "NOAA 19" matches. The scoring layer already normalizes punctuation,
    so this only expands what we send over the wire.
    """
    variants = [query]
    spaced = re.sub(r"[-_]+", " ", query)
    spaced = " ".join(spaced.split())
    if spaced and spaced != query:
        variants.append(spaced)
    return variants


def _fetch_raw_variant(variant: str) -> List[Dict[str, Any]]:
    """Fetch one query, distinguishing a valid no-match response from a failure."""

    def validate(text):
        if text.strip().rstrip(".").lower() == "no satcat records found":
            return []
        data = json.loads(text)
        if not isinstance(data, list) or any(
            not isinstance(item, dict) for item in data
        ):
            raise ValueError("invalid SATCAT records")
        return data

    try:
        return cached(SATCAT_URL, {"NAME": variant, "FORMAT": "json"}, 43200, validate)
    except requests.RequestException as exc:
        raise requests.RequestException(
            f"CelesTrak satellite search is unavailable: {exc}"
        ) from exc


def _iter_satcat_variants(query: str):
    """Try alternate spellings until an upstream failure occurs."""
    for variant in _satcat_query_variants(query):
        data = _fetch_raw_variant(variant)
        if data:
            yield data


def _fetch_satcat_records(query: str) -> List[Dict[str, Any]]:
    """Collect all active candidates before ranking and applying an output limit."""
    records: Dict[int, Dict[str, Any]] = {}
    for data in _iter_satcat_variants(query):
        for sat in data:
            formatted = _format_satcat_record(sat)
            if formatted is not None:
                records.setdefault(formatted["norad_id"], formatted)
    return list(records.values())


def _decayed_match_hint(identifier: str) -> Optional[str]:
    """Describe catalog matches that all decayed, if that is why search is empty."""
    raw = next(_iter_satcat_variants(identifier), [])
    if not raw:
        return None
    decayed = []
    for sat in raw[:3]:
        if not isinstance(sat, dict):
            continue
        decayed.append(
            f"{sat.get('OBJECT_NAME', 'Unknown')} "
            f"(NORAD {sat.get('NORAD_CAT_ID', '?')}, "
            f"decayed {str(sat.get('DECAY_DATE') or 'date unknown').strip()})"
        )
    if not decayed:
        return None
    return (
        f"Satellite identifier '{identifier}' matches catalog objects that have "
        f"all decayed and no longer have GP data: {'; '.join(decayed)}. "
        f"The active object you want may be cataloged under a different name; "
        f"use search_satellites with alternate spellings or provide a NORAD ID."
    )


def _rank_search_results(
    query: str, results: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Return search results in descending relevance order."""
    return sorted(
        results,
        key=lambda item: (_score_search_result(query, item["name"]), -item["norad_id"]),
        reverse=True,
    )


def _resolve_alias(identifier: str) -> Optional[int]:
    """Resolve a curated common-name alias to a NORAD ID."""
    return _COMMON_NAME_ALIASES.get(_normalize_name(identifier))


def _resolve_search_result(identifier: str) -> Optional[Dict[str, Any]]:
    """Resolve a text identifier to a single search result or raise if ambiguous."""
    results = search_satellites_by_name(identifier, limit=10)
    if not results:
        hint = _decayed_match_hint(identifier)
        if hint is not None:
            raise ValueError(hint)
        return None

    top_score = _score_search_result(identifier, results[0]["name"])
    if top_score < 40:
        raise ValueError(
            f"Satellite identifier '{identifier}' is ambiguous. "
            f"Use search_satellites first, then provide an exact name or NORAD ID."
        )

    if len(results) > 1:
        second_score = _score_search_result(identifier, results[1]["name"])
        if second_score == top_score:
            raise ValueError(
                f"Satellite identifier '{identifier}' matches multiple satellites. "
                f"Use search_satellites first, then provide an exact name or NORAD ID."
            )

    return results[0]


def search_satellites_by_name(query: str, limit: int = 10) -> List[Dict]:
    """Search currently orbiting satellites by name. Decayed objects are excluded."""
    results = _fetch_satcat_records(query)
    return _rank_search_results(query, results)[:limit]


def get_norad_id(satellite_identifier: str) -> Optional[int]:
    """Resolve a name (case-insensitive) or NORAD ID string to an integer, or None."""
    # Try to parse as integer first
    try:
        return validate_norad_id(int(satellite_identifier))
    except (ValueError, TypeError):
        pass

    if not isinstance(satellite_identifier, str):
        return None

    alias_norad_id = _resolve_alias(satellite_identifier)
    if alias_norad_id is not None:
        return alias_norad_id

    resolved = _resolve_search_result(satellite_identifier)
    if resolved:
        return resolved["norad_id"]

    return None


def fetch_omm(norad_id: int) -> List[Dict[str, Any]]:
    """Fetch OMM (GP) element records from CelesTrak for a NORAD ID.

    OMM JSON carries six- and nine-digit catalog numbers that the legacy
    TLE format cannot represent, and TAT-C consumes it directly.
    """
    norad_id = validate_norad_id(norad_id)

    def validate(text):
        stripped = text.strip()
        if stripped.lower().startswith("no gp data found"):
            return None
        return validate_omm_records(json.loads(stripped))

    result = cached(GP_URL, {"CATNR": norad_id, "FORMAT": "json"}, 7200, validate)
    if result is None:
        raise ValueError(
            f"GP data not available for NORAD ID {norad_id}. The object may have decayed or have no Earth-orbit GP data."
        )
    return result


def get_satellite_info(satellite_identifier: str) -> Dict:
    """Get norad_id, name, and current OMM element records for a satellite."""
    norad_id = get_norad_id(satellite_identifier)
    if norad_id is None:
        raise ValueError(
            f"Could not resolve satellite identifier '{satellite_identifier}' to NORAD ID. "
            "No orbiting satellite matches; it may be a place or a non-catalog name. "
            "Ask the user which satellite they mean; do not invent one or its TLE."
        )

    omm = fetch_omm(norad_id)
    name = str(omm[0].get("OBJECT_NAME") or "").strip()

    if not name:
        # Fall back to the ranked search result name if OMM lacks a name.
        resolved = None
        if not str(satellite_identifier).strip().isdigit():
            try:
                resolved = _resolve_search_result(satellite_identifier)
            except ValueError:
                resolved = None
        name = resolved["name"] if resolved else f"NORAD {norad_id}"

    return {
        "norad_id": norad_id,
        "name": name,
        "omm": omm,
    }
