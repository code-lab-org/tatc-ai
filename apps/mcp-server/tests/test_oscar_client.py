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
