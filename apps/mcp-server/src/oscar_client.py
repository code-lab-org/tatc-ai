"""OSCAR/Space client: the instrument registry backbone.

The OSCAR JSON API has no free-text search and can be slow per request,
so name lookups use the site's server-rendered capabilities search (one
request), then fetch details for the resolved record (one API request).
"""

import re
import time
from html import unescape
from html.parser import HTMLParser
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

# Instrument pages render specs as highlight-label rows, e.g.
# <td class="highlight">Resolution</td><td>4 km IFOV</td>.
_SPEC_ROW = re.compile(
    r'<td[^>]*class="highlight"[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>',
    re.DOTALL,
)

class _InstrumentMetaParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.description: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        attributes = dict(attrs)
        if (
            tag.lower() == "meta"
            and (attributes.get("name") or "").lower() == "description"
            and self.description is None
        ):
            self.description = attributes.get("content")


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


def _parse_links(page: str) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Parse satellite and instrument links from an OSCAR page."""
    satellites: List[Dict[str, str]] = []
    instruments: List[Dict[str, str]] = []
    seen = set()
    for match in _RESULT_ANCHOR.finditer(page):
        kind, slug, inner = match.groups()
        label = " ".join(re.sub(r"<[^>]+>", " ", inner).split())
        key = (kind, slug)
        if key in seen or not label:
            continue
        seen.add(key)
        entry = {"slug": slug, "label": label}
        (satellites if kind.lower() == "satellites" else instruments).append(entry)

    return satellites, instruments


def _search_links(term: str) -> Tuple[List[Dict[str, str]], List[Dict[str, str]]]:
    """Run the OSCAR capabilities search and parse satellite/instrument hits."""
    response = _get(SEARCH_URL, {"term": term})
    response.raise_for_status()
    return _parse_links(response.text)


def _link_score(query: str, link: Dict[str, str]) -> int:
    return max(
        score_match(query, link["label"]),
        score_match(query, link["slug"].replace("_", " ")),
    )


def _rank_links(query: str, links: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Lift exact and prefix matches; keep the site's order otherwise."""
    return sorted(links, key=lambda link: -_link_score(query, link))


def _search_terms(query: str) -> List[str]:
    """Search terms to try in order for one identifier.

    OSCAR's search misses its own slugs ("msi_sentinel_2a") and labels
    with a parenthetical ("MSI (Sentinel-2A)"), which the satellite tools
    return. The base term ("msi", "MSI") finds them, and _choose_link
    then picks the exact slug or label.
    """
    terms = [query]
    base = query.split("(")[0].strip()
    if "_" in base:
        base = base.split("_")[0]
    if base and base.lower() != query.lower():
        terms.append(base)
    return terms


def _find_links(query: str, kind: str) -> List[Dict[str, str]]:
    """Return search hits of one kind, retrying with the base term."""
    index = 0 if kind == "satellite" else 1
    lowered = query.lower()
    for term in _search_terms(query):
        links = _search_links(term)[index]
        exact = any(
            link["slug"] == lowered or link["label"].lower() == lowered
            for link in links
        )
        # A base-term search is only trusted when it finds the exact record.
        if links and (term == query or exact):
            return links
    return []


def _choose_link(
    query: str, links: List[Dict[str, str]], kind: str
) -> Dict[str, str]:
    """Pick exactly one search hit or raise."""
    lowered = query.lower()
    for field in ("slug", "label"):
        exact = [link for link in links if link[field].lower() == lowered]
        if len(exact) == 1:
            return exact[0]

    ranked = _rank_links(query, links)
    top_scores = [_link_score(query, link) for link in ranked[:2]]
    if len(top_scores) > 1 and top_scores[0] == top_scores[1]:
        raise ValueError(
            f"OSCAR {kind} '{query}' is ambiguous; use search results to pick "
            f"an exact name or slug"
        )
    return ranked[0]


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
        "description": record.get("description") or record.get("instrument_description"),
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
    satellites = _find_links(cleaned, "satellite")
    if not satellites:
        raise ValueError(f"No OSCAR satellite matched '{query}'")
    chosen = _choose_link(cleaned, satellites, "satellite")

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


def _clean_text(value: str) -> str:
    """Strip tags and entities, collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", value)
    return " ".join(unescape(text).replace("\xa0", " ").split())


def _parse_instrument_specs(page: str) -> Dict[str, str]:
    """Parse the labeled spec rows from an OSCAR instrument page."""
    specs: Dict[str, str] = {}
    for match in _SPEC_ROW.finditer(page):
        label = _clean_text(match.group(1)).rstrip(":").strip()
        value = _clean_text(match.group(2))
        if label and value and label not in specs:
            specs[label] = value
    return specs


def _parse_instrument_description(page: str) -> Optional[str]:
    """Read the instrument page description metadata when it is present."""
    parser = _InstrumentMetaParser()
    parser.feed(page)
    if parser.description is None:
        return None
    return _clean_text(parser.description) or None


def _instrument_satellite_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
    satellite = entry.get("satellites")
    satellite = satellite if isinstance(satellite, dict) else {}
    slug = str(satellite.get("slug") or "")
    return {
        "slug": slug,
        "name": satellite.get("name") or satellite.get("fullname"),
        "status": entry.get("status"),
        "start_date": entry.get("start-date"),
        "end_of_life": entry.get("EoL"),
        "data_latency": entry.get("data_latency"),
        "oscar_url": SATELLITE_URL.format(slug=slug) if slug else None,
    }


def get_instrument(instrument_identifier: str) -> Dict[str, Any]:
    """Resolve one OSCAR instrument with its spec table and carrying satellites.

    Spec values come from the OSCAR instrument page as source text; they
    are not parsed numbers, so downstream coverage work should treat them
    as hints rather than machine-readable geometry. satellites_source is
    "api" for dated API entries or "oscar_page" when only page links exist.
    """
    cleaned = _require_query(instrument_identifier, "instrument")
    instruments = _find_links(cleaned, "instrument")
    if not instruments:
        raise ValueError(f"No OSCAR instrument matched '{instrument_identifier}'")
    chosen = _choose_link(cleaned, instruments, "instrument")
    slug = chosen["slug"]

    payload = _fetch_json(f"instruments/{slug}")
    record = _detail_record(payload, "instrument")
    result = _instrument_summary(record)
    result["classification"] = record.get("classification")
    result["variables"] = record.get("variables")
    api_entries = [
        entry
        for entry in record.get("instrument-satellites") or []
        if isinstance(entry, dict)
    ]
    result["satellites"] = [
        _instrument_satellite_entry(entry) for entry in api_entries
    ]
    result["satellites_source"] = "api" if api_entries else None
    api_description = result.get("description")
    result["api_description"] = api_description
    result["page_description"] = None

    try:
        response = _get(INSTRUMENT_URL.format(slug=slug))
        response.raise_for_status()
        result["page_description"] = _parse_instrument_description(response.text)
        result["description"] = api_description or result["page_description"]
        result["specs"] = _parse_instrument_specs(response.text) or None
        # The API omits instrument-satellites for some records; the page links do not.
        if not result["satellites"]:
            page_satellites, _ = _parse_links(response.text)
            if page_satellites:
                result["satellites"] = [
                    {
                        "slug": link["slug"],
                        "name": link["label"],
                        "status": None,
                        "start_date": None,
                        "end_of_life": None,
                        "data_latency": None,
                        "oscar_url": SATELLITE_URL.format(slug=link["slug"]),
                    }
                    for link in page_satellites
                ]
                result["satellites_source"] = "oscar_page"
    except (requests.RequestException, ValueError) as exc:
        result["description"] = api_description
        result["specs"] = None
        result["specs_error"] = str(exc)

    return result
