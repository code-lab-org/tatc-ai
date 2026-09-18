"""OSCAR/Space client: the instrument registry backbone.

The OSCAR JSON API has no free-text search and can be slow per request,
so name lookups use the site's server-rendered capabilities search (one
request), then fetch details for the resolved record (one API request).
"""

import re
import time
from typing import Any, Dict, List, Optional, Tuple

import requests

from .search_utils import score_match

BASE_URL = "https://space.oscar.wmo.int/api/v1"
SEARCH_URL = "https://space.oscar.wmo.int/spacecapabilities/search"
SATELLITE_URL = "https://space.oscar.wmo.int/satellites/view/{slug}"
INSTRUMENT_URL = "https://space.oscar.wmo.int/instruments/view/{slug}"

REQUEST_TIMEOUT_SECONDS = 30
MAX_ATTEMPTS = 2
RETRY_DELAY_SECONDS = 1.0

_RESULT_ANCHOR = re.compile(
    r'<a[^>]+href="/(satellites|instruments)/view/([a-z0-9_]+)"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)


def _get(url: str, params: Optional[Dict[str, Any]] = None) -> requests.Response:
    """GET with one retry on timeout or connection errors."""
    last_error: Optional[Exception] = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            return requests.get(url, params=params or {}, timeout=REQUEST_TIMEOUT_SECONDS)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt + 1 < MAX_ATTEMPTS:
                time.sleep(RETRY_DELAY_SECONDS)
    raise last_error  # type: ignore[misc]


def _fetch_json(path: str) -> Dict[str, Any]:
    """Fetch one OSCAR API object. Transport errors stay as requests errors."""
    response = _get(f"{BASE_URL}/{path}")
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise ValueError(f"Invalid JSON from OSCAR {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"Unexpected OSCAR {path} payload: expected an object")
    return data


def _search_links(term: str) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Run the OSCAR capabilities search and parse satellite/instrument hits."""
    response = _get(SEARCH_URL, {"term": term})
    response.raise_for_status()

    satellites: List[Dict[str, str]] = []
    instruments: List[Dict[str, str]] = []
    seen = set()
    for match in _RESULT_ANCHOR.finditer(response.text):
        kind, slug, inner = match.groups()
        label = " ".join(re.sub(r"<[^>]+>", " ", inner).split())
        key = (kind, slug)
        if key in seen or not label:
            continue
        seen.add(key)
        entry = {"slug": slug, "label": label}
        (satellites if kind.lower() == "satellites" else instruments).append(entry)

    return satellites, instruments


def _rank_links(query: str, links: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Lift exact and prefix matches; keep the site's order otherwise."""
    return sorted(
        links,
        key=lambda link: -max(
            score_match(query, link["label"]),
            score_match(query, link["slug"].replace("_", " ")),
        ),
    )


def _summary(link: Dict[str, str], kind: str) -> Dict[str, Any]:
    template = SATELLITE_URL if kind == "satellites" else INSTRUMENT_URL
    return {
        "slug": link["slug"],
        "label": link["label"],
        "oscar_url": template.format(slug=link["slug"]),
    }


def _require_limit(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
        raise ValueError("limit must be an integer between 1 and 50")
    return limit


def _require_query(query: str, kind: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError(f"{kind} query must be a non-empty string")
    return query.strip()


def search_satellites(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search OSCAR satellites by acronym, name, or slug."""
    _require_limit(limit)
    satellites, _ = _search_links(_require_query(query, "satellite"))
    ranked = _rank_links(query.strip(), satellites)
    return [_summary(link, "satellites") for link in ranked[:limit]]


def search_instruments(query: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Search OSCAR instruments by acronym, name, or slug."""
    _require_limit(limit)
    _, instruments = _search_links(_require_query(query, "instrument"))
    ranked = _rank_links(query.strip(), instruments)
    return [_summary(link, "instruments") for link in ranked[:limit]]


def _detail_record(payload: Dict[str, Any], key: str) -> Dict[str, Any]:
    records = payload.get(key)
    if isinstance(records, list) and records and isinstance(records[0], dict):
        return records[0]
    if isinstance(records, dict):
        return records
    raise ValueError(f"OSCAR payload is missing {key} details")


def _satellite_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    slug = str(record.get("slug") or "")
    return {
        "slug": slug,
        "acronym": record.get("acronym"),
        "name": record.get("fullname"),
        "agency": record.get("space_agency"),
        "status": record.get("status"),
        "orbit": record.get("orbit"),
        "mean_altitude_km": record.get("Altitude"),
        "equator_crossing_time": record.get("ECT"),
        "launch_date": record.get("launch_date"),
        "end_of_life": record.get("EoL"),
        "oscar_url": SATELLITE_URL.format(slug=slug) if slug else None,
    }


def _instrument_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    slug = str(record.get("slug") or "")
    return {
        "slug": slug,
        "acronym": record.get("acronym") or record.get("name"),
        "name": record.get("fullname"),
        "agency": record.get("providing-agency"),
        "type": record.get("instrumenttype"),
        "oscar_url": INSTRUMENT_URL.format(slug=slug) if slug else None,
    }


def _satellite_instrument_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    instrument = entry.get("instrument")
    instrument = instrument if isinstance(instrument, dict) else {}
    summary = _instrument_summary(instrument)
    summary.update(
        {
            "status": entry.get("status"),
            "start_date": entry.get("start-date"),
            "end_of_life": entry.get("EoL"),
            "data_latency": entry.get("data_latency"),
        }
    )
    return summary


def resolve_satellite(query: str) -> Dict[str, Any]:
    """Resolve one OSCAR satellite and return its metadata plus instruments.

    Search hits are ranked; an equal top score raises instead of guessing.
    """
    cleaned = _require_query(query, "satellite")
    satellites, _ = _search_links(cleaned)
    if not satellites:
        raise ValueError(f"No OSCAR satellite matched '{query}'")

    exact = [link for link in satellites if link["slug"] == cleaned.lower()]
    if len(exact) == 1:
        chosen = exact[0]
    else:
        ranked = _rank_links(cleaned, satellites)
        top_scores = [
            max(
                score_match(cleaned, link["label"]),
                score_match(cleaned, link["slug"].replace("_", " ")),
            )
            for link in ranked[:2]
        ]
        if len(top_scores) > 1 and top_scores[0] == top_scores[1]:
            raise ValueError(
                f"OSCAR satellite '{query}' is ambiguous; use search results "
                f"to pick an exact name or slug"
            )
        chosen = ranked[0]

    payload = _fetch_json(f"satellites/{chosen['slug']}")
    record = _detail_record(payload, "satellite")
    summary = _satellite_summary(record)

    # OSCAR repeats an instrument once per WIGOS subcomponent; keep the first.
    instruments: List[Dict[str, Any]] = []
    seen = set()
    for entry in record.get("satellite-instruments") or []:
        if not isinstance(entry, dict):
            continue
        instrument = entry.get("instrument")
        slug = instrument.get("slug") if isinstance(instrument, dict) else None
        if slug and slug in seen:
            continue
        if slug:
            seen.add(slug)
        instruments.append(_satellite_instrument_entry(entry))
    summary["instruments"] = instruments
    return summary
