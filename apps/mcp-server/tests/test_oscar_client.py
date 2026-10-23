import pytest
import requests

from src import oscar_client

SEARCH_HTML = """
<html><body>
Total search results 29 from 2 categories
Satellites (2)
<a href="/satellites/view/alos_2">ALOS-2</a>
<a href="/satellites/view/alos_4_daichi_4">ALOS-4 (DAICHI-4)</a>
Instruments (3)
<a href="/instruments/view/abi">ABI</a>
<a href="/instruments/view/abi_test">ABI Test</a>
<a href="/instruments/view/magic">MAGIC</a>
<a href="/pages/disclaimer">Disclaimer</a>
</body></html>
"""

INSTRUMENT_HTML = """
<html><head><meta name="description" content="Advanced Baseline Imager | Purpose: Multi-spectral VIS/IR imaging" /></head><body><table>
<tr><td class="highlight">Acronym</td><td>ABI</td></tr>
<tr><td class="highlight">Resolution</td><td>0.5&nbsp;km <b>IFOV</b></td></tr>
<tr><td class="highlight">Coverage / Cycle</td><td>Full disk every 15 min</td></tr>
</table></body></html>
"""

INSTRUMENT_DETAIL = {
    "instrument": [
        {
            "slug": "abi",
            "acronym": "ABI",
            "fullname": "Advanced Baseline Imager",
            "providing-agency": "NOAA",
            "instrumenttype": "Imaging multi-spectral radiometers (vis/IR)",
            "classification": [
                "Earth observation instrument",
                "Passive optical radiometer or spectrometer",
            ],
            "variables": [{"name": "Snow cover"}],
            "instrument-satellites": [
                {
                    "start-date": "Dec 2016",
                    "EoL": "\u22652037",
                    "data_latency": "PT5M",
                    "satellites": {"slug": "goes_16", "name": "GOES-16"},
                }
            ],
        }
    ]
}

INSTRUMENT_DETAIL_EMPTY = {
    "instrument": [
        {
            "slug": "abi",
            "acronym": "ABI",
            "fullname": "Advanced Baseline Imager",
            "providing-agency": "NOAA",
            "instrumenttype": "Imaging multi-spectral radiometers (vis/IR)",
            "classification": [],
            "variables": [],
            "instrument-satellites": [],
        }
    ]
}

INSTRUMENT_HTML_WITH_SATELLITES = """
<html><body>
<a href="/satellites/view/goes_16">GOES-16</a>
<a href="/satellites/view/goes_18">GOES-18</a>
</body></html>
"""

SATELLITE_DETAIL = {
    "satellite": [
        {
            "slug": "alos_2",
            "acronym": "ALOS-2",
            "fullname": "Advanced Land Observing Satellite - 2",
            "space_agency": "JAXA",
            "status": "Operational",
            "orbit": "Sunsynchronous orbit",
            "Altitude": "640",
            "ECT": "12:00 desc",
            "launch_date": "24 May 2014",
            "EoL": "\u22652026",
            "satellite-instruments": [
                {
                    "start-date": "25 Aug 2014",
                    "EoL": "\u22652026",
                    "status": "active",
                    "data_latency": None,
                    "instrument": {
                        "slug": "palsar_2",
                        "name": "PALSAR-2",
                        "fullname": "Phased-Array L-band Synthetic Aperture Radar - 2",
                        "instrumenttype": "Imaging radar (SAR)",
                        "providing-agency": "JAXA",
                    },
                },
                {
                    "start-date": "25 Aug 2014",
                    "EoL": "\u22652026",
                    "status": "active",
                    "data_latency": None,
                    "instrument": {
                        "slug": "palsar_2",
                        "name": "PALSAR-2",
                        "fullname": "Phased-Array L-band Synthetic Aperture Radar - 2",
                        "instrumenttype": "Imaging radar (SAR)",
                        "providing-agency": "JAXA",
                    },
                },
            ],
        }
    ]
}


class FakeResponse:
    def __init__(self, text="", json_data=None, json_error=None):
        self.text = text
        self._json = json_data
        self._json_error = json_error

    def raise_for_status(self):
        return None

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._json


def _search_response(html=SEARCH_HTML):
    def fake_get(url, params=None, timeout=None, **kwargs):
        assert url == oscar_client.SEARCH_URL
        return FakeResponse(text=html)

    return fake_get


def test_search_parses_ranks_and_limits(monkeypatch):
    monkeypatch.setattr(oscar_client.requests, "get", _search_response())

    results = oscar_client.search_instruments("ABI")
    # The site's search can match fields we cannot see, so score-0 hits
    # stay in the list behind the exact and prefix matches.
    assert [r["slug"] for r in results] == ["abi", "abi_test", "magic"]
    assert results[0]["label"] == "ABI"
    assert results[0]["oscar_url"] == "https://space.oscar.wmo.int/instruments/view/abi"

    satellites = oscar_client.search_satellites("ALOS")
    assert [s["slug"] for s in satellites] == ["alos_2", "alos_4_daichi_4"]

    assert oscar_client.search_satellites("ALOS", limit=1) == [
        satellites[0]
    ]


def test_search_validates_inputs():
    with pytest.raises(ValueError, match="between 1 and 50"):
        oscar_client.search_satellites("ALOS", limit=0)
    with pytest.raises(ValueError, match="non-empty"):
        oscar_client.search_instruments("  ")


def test_resolve_satellite_fetches_detail(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None, **kwargs):
        calls.append((url, params))
        if url == oscar_client.SEARCH_URL:
            return FakeResponse(text=SEARCH_HTML)
        assert url == f"{oscar_client.BASE_URL}/satellites/alos_2"
        return FakeResponse(json_data=SATELLITE_DETAIL)

    monkeypatch.setattr(oscar_client.requests, "get", fake_get)

    out = oscar_client.resolve_satellite("ALOS-2")
    assert out["slug"] == "alos_2"
    assert out["agency"] == "JAXA"
    assert out["mean_altitude_km"] == "640"
    # OSCAR repeats instruments per WIGOS subcomponent; the client dedupes.
    [instrument] = out["instruments"]
    assert instrument["slug"] == "palsar_2"
    assert instrument["acronym"] == "PALSAR-2"
    assert instrument["type"] == "Imaging radar (SAR)"
    assert instrument["status"] == "active"
    assert instrument["data_latency"] is None
    assert [url for url, _ in calls] == [
        oscar_client.SEARCH_URL,
        f"{oscar_client.BASE_URL}/satellites/alos_2",
    ]


def test_resolve_satellite_prefers_exact_slug(monkeypatch):
    detail = SATELLITE_DETAIL

    def fake_get(url, params=None, timeout=None, **kwargs):
        if url == oscar_client.SEARCH_URL:
            return FakeResponse(text=SEARCH_HTML)
        return FakeResponse(json_data=detail)

    monkeypatch.setattr(oscar_client.requests, "get", fake_get)
    assert oscar_client.resolve_satellite("alos_2")["slug"] == "alos_2"


def test_resolve_satellite_rejects_unknown_and_ambiguous(monkeypatch):
    monkeypatch.setattr(
        oscar_client.requests,
        "get",
        _search_response("<html>Total search results 0</html>"),
    )
    with pytest.raises(ValueError, match="No OSCAR satellite"):
        oscar_client.resolve_satellite("nothing")

    twins = (
        '<html><a href="/satellites/view/iss_hisui">ISS-HISUI</a>'
        '<a href="/satellites/view/iss_raincube">ISS-RAINCUBE</a></html>'
    )
    monkeypatch.setattr(
        oscar_client.requests, "get", _search_response(twins)
    )
    with pytest.raises(ValueError, match="ambiguous"):
        oscar_client.resolve_satellite("ISS")


def test_get_retries_once_then_raises(monkeypatch):
    calls = []

    def flaky_get(url, params=None, timeout=None, **kwargs):
        calls.append(url)
        if len(calls) == 1:
            raise requests.Timeout("slow")
        return FakeResponse(json_data={"satellite": [{"slug": "x"}]})

    monkeypatch.setattr(oscar_client.requests, "get", flaky_get)
    monkeypatch.setattr(oscar_client.time, "sleep", lambda seconds: None)

    assert oscar_client._fetch_json("satellites/x") == {"satellite": [{"slug": "x"}]}
    assert len(calls) == 2

    def always_timeout(url, params=None, timeout=None, **kwargs):
        calls.append(url)
        raise requests.Timeout("slow")

    monkeypatch.setattr(oscar_client.requests, "get", always_timeout)
    calls.clear()
    with pytest.raises(requests.Timeout):
        oscar_client._fetch_json("satellites/x")
    assert len(calls) == oscar_client.MAX_ATTEMPTS


def test_get_instrument_merges_api_and_page_specs(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None, **kwargs):
        calls.append(url)
        if url == oscar_client.SEARCH_URL:
            return FakeResponse(text=SEARCH_HTML)
        if url == f"{oscar_client.BASE_URL}/instruments/abi":
            return FakeResponse(json_data=INSTRUMENT_DETAIL)
        if url == "https://space.oscar.wmo.int/instruments/view/abi":
            return FakeResponse(text=INSTRUMENT_HTML)
        raise AssertionError(url)

    monkeypatch.setattr(oscar_client.requests, "get", fake_get)

    out = oscar_client.get_instrument("ABI")
    assert out["slug"] == "abi"
    assert out["agency"] == "NOAA"
    assert out["classification"][0] == "Earth observation instrument"
    assert out["description"] == "Advanced Baseline Imager | Purpose: Multi-spectral VIS/IR imaging"
    assert out["variables"] == [{"name": "Snow cover"}]
    [satellite] = out["satellites"]
    assert satellite["slug"] == "goes_16"
    assert satellite["name"] == "GOES-16"
    assert satellite["data_latency"] == "PT5M"
    assert out["specs"]["Resolution"] == "0.5 km IFOV"
    assert out["specs"]["Coverage / Cycle"] == "Full disk every 15 min"
    assert out["satellites_source"] == "api"
    assert calls == [
        oscar_client.SEARCH_URL,
        f"{oscar_client.BASE_URL}/instruments/abi",
        "https://space.oscar.wmo.int/instruments/view/abi",
    ]


def test_get_instrument_preserves_api_and_page_descriptions(monkeypatch):
    api_description = "The instrument uses off-nadir pointing for some observations."
    detail = {
        "instrument": [
            dict(INSTRUMENT_DETAIL["instrument"][0], description=api_description)
        ]
    }

    def fake_get(url, params=None, timeout=None, **kwargs):
        if url == oscar_client.SEARCH_URL:
            return FakeResponse(text=SEARCH_HTML)
        if url == f"{oscar_client.BASE_URL}/instruments/abi":
            return FakeResponse(json_data=detail)
        if url == "https://space.oscar.wmo.int/instruments/view/abi":
            return FakeResponse(text=INSTRUMENT_HTML)
        raise AssertionError(url)

    monkeypatch.setattr(oscar_client.requests, "get", fake_get)
    out = oscar_client.get_instrument("ABI")
    assert out["api_description"] == api_description
    assert out["page_description"] == (
        "Advanced Baseline Imager | Purpose: Multi-spectral VIS/IR imaging"
    )
    assert out["description"] == api_description


def test_get_instrument_falls_back_to_page_satellites(monkeypatch):
    def fake_get(url, params=None, timeout=None, **kwargs):
        if url == oscar_client.SEARCH_URL:
            return FakeResponse(text=SEARCH_HTML)
        if url == f"{oscar_client.BASE_URL}/instruments/abi":
            return FakeResponse(json_data=INSTRUMENT_DETAIL_EMPTY)
        return FakeResponse(text=INSTRUMENT_HTML_WITH_SATELLITES)

    monkeypatch.setattr(oscar_client.requests, "get", fake_get)

    out = oscar_client.get_instrument("ABI")
    assert out["satellites_source"] == "oscar_page"
    assert [s["name"] for s in out["satellites"]] == ["GOES-16", "GOES-18"]
    assert out["satellites"][0]["status"] is None
    assert out["satellites"][0]["oscar_url"].endswith("/satellites/view/goes_16")


def test_get_instrument_reports_specs_failure(monkeypatch):
    class FailingPage(FakeResponse):
        def raise_for_status(self):
            raise requests.HTTPError("500 Server Error")

    def fake_get(url, params=None, timeout=None, **kwargs):
        if url == oscar_client.SEARCH_URL:
            return FakeResponse(text=SEARCH_HTML)
        if url == f"{oscar_client.BASE_URL}/instruments/abi":
            return FakeResponse(json_data=INSTRUMENT_DETAIL)
        return FailingPage()

    monkeypatch.setattr(oscar_client.requests, "get", fake_get)

    out = oscar_client.get_instrument("ABI")
    assert out["specs"] is None
    assert "500" in out["specs_error"]


def test_get_instrument_rejects_unknown_and_ambiguous(monkeypatch):
    monkeypatch.setattr(
        oscar_client.requests, "get", _search_response("<html>no hits</html>")
    )
    with pytest.raises(ValueError, match="No OSCAR instrument"):
        oscar_client.get_instrument("nope")

    twins = (
        '<html><a href="/instruments/view/magic_a">MAGIC-A</a>'
        '<a href="/instruments/view/magic_b">MAGIC-B</a></html>'
    )
    monkeypatch.setattr(oscar_client.requests, "get", _search_response(twins))
    with pytest.raises(ValueError, match="ambiguous"):
        oscar_client.get_instrument("MAGIC")


def test_fetch_json_rejects_non_json(monkeypatch):
    monkeypatch.setattr(
        oscar_client.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(
            json_error=ValueError("not json")
        ),
    )
    with pytest.raises(ValueError, match="Invalid JSON"):
        oscar_client._fetch_json("satellites/x")


MSI_SEARCH_HTML = (
    '<html><a href="/instruments/view/msi">MSI</a>'
    '<a href="/instruments/view/msi_sentinel_2a">MSI (Sentinel-2A)</a>'
    '<a href="/instruments/view/msi_sansa">MSI (Sansa)</a></html>'
)


@pytest.mark.parametrize("identifier", ["msi_sentinel_2a", "MSI (Sentinel-2A)"])
def test_get_instrument_finds_slugs_and_labels_the_satellite_tool_returns(
    monkeypatch, identifier
):
    # OSCAR's search misses both forms; only the base term "msi" hits.
    searched = []

    def fake_get(url, params=None, timeout=None, **kwargs):
        if url == oscar_client.SEARCH_URL:
            searched.append(params["term"])
            hit = params["term"].lower() == "msi"
            return FakeResponse(text=MSI_SEARCH_HTML if hit else "<html></html>")
        if url == f"{oscar_client.BASE_URL}/instruments/msi_sentinel_2a":
            detail = {"instrument": [dict(INSTRUMENT_DETAIL["instrument"][0], slug="msi_sentinel_2a")]}
            return FakeResponse(json_data=detail)
        return FakeResponse(text=INSTRUMENT_HTML)

    monkeypatch.setattr(oscar_client.requests, "get", fake_get)
    assert oscar_client.get_instrument(identifier)["slug"] == "msi_sentinel_2a"
    assert searched[-1].lower() == "msi"


def test_base_term_fallback_needs_an_exact_match(monkeypatch):
    # "MSI (Unknown)" must not silently become the first MSI hit.
    def fake_get(url, params=None, timeout=None, **kwargs):
        hit = params["term"].lower() == "msi"
        return FakeResponse(text=MSI_SEARCH_HTML if hit else "<html></html>")

    monkeypatch.setattr(oscar_client.requests, "get", fake_get)
    with pytest.raises(ValueError, match="No OSCAR instrument"):
        oscar_client.get_instrument("MSI (Unknown)")
