"""CelesTrak API client for fetching TLE data."""

import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from .validation import validate_norad_id, validate_tle_format

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


def _normalize_name(value: str) -> str:
    """Normalize a name for case-insensitive matching."""
    return " ".join(re.sub(r"[^A-Za-z0-9]+", " ", value.upper()).split())


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
    # object no longer has current GP/TLE data, so returning it would break the
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


def _fetch_raw_variant(variant: str) -> Optional[List[Dict[str, Any]]]:
    """Fetch one SATCAT query variant, returning None when unusable."""
    try:
        response = requests.get(
            SATCAT_URL,
            params={"NAME": variant, "FORMAT": "json"},
            timeout=15,
        )
        response.raise_for_status()
        data = _parse_json_response(response)
    except (requests.RequestException, ValueError):
        # "No SATCAT records found" is plain text, not JSON; a network
        # error here should not block the remaining variants.
        return None
    return data if isinstance(data, list) else None


def _iter_satcat_variants(query: str):
    """Yield parsed SATCAT record lists per query variant, skipping unusable ones."""
    for variant in _satcat_query_variants(query):
        data = _fetch_raw_variant(variant)
        if data:
            yield data


def _fetch_satcat_records(query: str, limit: int = 50) -> List[Dict[str, Any]]:
    """Fetch raw SATCAT records for a search query."""
    for data in _iter_satcat_variants(query):
        records: List[Dict[str, Any]] = []
        for sat in data:
            formatted = _format_satcat_record(sat)
            if formatted is not None:
                records.append(formatted)
            if len(records) >= limit:
                break
        if records:
            return records
    return []


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
        f"all decayed and no longer have TLE data: {'; '.join(decayed)}. "
        f"The active object you want may be cataloged under a different name; "
        f"use search_satellites with alternate spellings or provide a NORAD ID."
    )


def _score_search_result(query: str, candidate_name: str) -> int:
    """Score a search candidate for deterministic ranking."""
    normalized_query = _normalize_name(query)
    normalized_candidate = _normalize_name(candidate_name)
    if not normalized_query or not normalized_candidate:
        return 0

    query_tokens = normalized_query.split()
    candidate_tokens = normalized_candidate.split()
    score = 0

    if normalized_candidate == normalized_query:
        score += 100
    if normalized_candidate.startswith(normalized_query):
        score += 40
    if normalized_query in normalized_candidate:
        score += 20
    if all(token in candidate_tokens for token in query_tokens):
        score += 15
    if any(token == normalized_query for token in candidate_tokens):
        score += 10

    # Prefer tighter matches when the score would otherwise tie.
    score -= abs(len(candidate_tokens) - len(query_tokens))
    return score


def _rank_search_results(
    query: str, results: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Return search results in descending relevance order."""
    return sorted(
        results,
        key=lambda item: (_score_search_result(query, item["name"]), -item["norad_id"]),
        reverse=True,
    )


def _fetch_gp_metadata(norad_id: int) -> Optional[Dict[str, Any]]:
    """Fetch current GP metadata for a NORAD ID."""
    response = requests.get(
        GP_URL,
        params={"CATNR": validate_norad_id(norad_id), "FORMAT": "json"},
        timeout=15,
    )
    response.raise_for_status()

    data = _parse_json_response(response)
    if isinstance(data, list) and data:
        item = data[0]
        if isinstance(item, dict):
            try:
                return {
                    "norad_id": int(item.get("NORAD_CAT_ID", norad_id)),
                    "name": str(item.get("OBJECT_NAME") or "").strip(),
                    "object_id": str(item.get("OBJECT_ID") or "").strip(),
                }
            except (ValueError, TypeError):
                return None
    return None


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
    try:
        results = _fetch_satcat_records(query, limit=max(limit, 25))
        ranked_results = _rank_search_results(query, results)
        return ranked_results[:limit]

    except requests.RequestException:
        # Silently return empty list for network errors
        return []
    except Exception:  # pylint: disable=broad-exception-caught
        # Search is best-effort; resolution reports the failure instead.
        return []


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


def fetch_tle(norad_id: int) -> Tuple[str, str]:
    """Fetch (line1, line2) TLE strings from CelesTrak for a NORAD ID."""
    # Validate NORAD ID
    norad_id = validate_norad_id(norad_id)

    # CelesTrak defaults GP queries to CSV, so request the legacy three-line
    # TLE representation explicitly. The parser below also accepts 2LE data.
    try:
        response = requests.get(
            GP_URL,
            params={"CATNR": norad_id, "FORMAT": "TLE"},
            allow_redirects=True,
            timeout=10,
        )
        response.raise_for_status()

        # Check if response is empty or indicates no data
        response_text = response.text.strip()
        if not response_text:
            raise ValueError(
                f"No TLE data returned from CelesTrak for NORAD ID {norad_id}"
            )

        # Check for common error messages from CelesTrak
        if "No GP data found" in response_text or "not found" in response_text.lower():
            raise ValueError(
                f"TLE data not available for NORAD ID {norad_id}. "
                f"The satellite may have decayed, been decommissioned, or the ID may be incorrect. "
                f"Deep-space objects without Earth-orbit GP data (e.g. JWST at Sun-Earth L2) "
                f"also have no TLE. "
                f"Response: {response_text[:200]}"
            )

        lines = response_text.split("\n")

        # Filter out empty lines
        lines = [line.strip() for line in lines if line.strip()]

        if len(lines) < 2:
            raise ValueError(
                f"Invalid TLE format: expected at least 2 lines, got {len(lines)}. "
                f"Response: {response_text[:200]}"
            )

        # TLE format: first line is satellite name, second and third are TLE lines
        # Sometimes the name line is included, sometimes not
        if len(lines) >= 3:
            # Has name line, TLE lines are 2nd and 3rd
            line1 = lines[1]
            line2 = lines[2]
        else:
            # Just TLE lines
            line1 = lines[0]
            line2 = lines[1]

        # Validate TLE format
        line1, line2 = validate_tle_format(line1, line2)

        return line1, line2

    except requests.Timeout:
        raise requests.RequestException(
            f"Timeout while fetching TLE from CelesTrak for NORAD ID {norad_id}"
        )
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else "unknown"
        hint = (
            " The ID may be incorrect, or the object may have no Earth-orbit GP data "
            "(e.g. deep-space missions like JWST at Sun-Earth L2 have no TLE)."
            if status == 404
            else ""
        )
        raise requests.RequestException(
            f"HTTP error {status} while fetching TLE from CelesTrak: {e}.{hint}"
        ) from e
    except requests.RequestException as e:
        raise requests.RequestException(
            f"Failed to fetch TLE from CelesTrak: {e}"
        ) from e
    except ValueError:
        # Re-raise validation errors
        raise


def get_satellite_info(satellite_identifier: str) -> Dict:
    """Get norad_id, name, and both TLE lines for a satellite name or NORAD ID."""
    norad_id = get_norad_id(satellite_identifier)
    if norad_id is None:
        raise ValueError(
            f"Could not resolve satellite identifier '{satellite_identifier}' to NORAD ID"
        )

    line1, line2 = fetch_tle(norad_id)
    try:
        metadata = _fetch_gp_metadata(norad_id)
    except (requests.RequestException, ValueError):
        # Metadata is a nicety; TLE success is what matters.
        metadata = None

    if metadata and metadata.get("name"):
        name = metadata["name"]
    else:
        # Fall back to the ranked search result name if GP metadata is unavailable.
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
        "tle_line1": line1,
        "tle_line2": line2,
    }
