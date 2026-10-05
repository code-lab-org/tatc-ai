import json

import pytest
import requests

from src import celestrak_client
from src.validation import _tle_checksum


def _padded_tle(line_number: str) -> str:
    """Build a 69-char TLE line with a correct checksum digit."""
    body = (line_number + " " + "0" * 67)[:68]
    return body + str(_tle_checksum(body))


def test_fetch_tle_requests_tle_format_and_parses_three_lines(monkeypatch):
    line1 = _padded_tle("1")
    line2 = _padded_tle("2")

    class FakeResponse:
        text = f"ISS (ZARYA)\n{line1}\n{line2}\n"

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        assert url == celestrak_client.GP_URL
        assert kwargs["params"] == {"CATNR": 25544, "FORMAT": "TLE"}
        assert kwargs["allow_redirects"] is True
        assert kwargs["timeout"] == 10
        return FakeResponse()

    monkeypatch.setattr(celestrak_client.requests, "get", fake_get)

    assert celestrak_client.fetch_tle(25544) == (line1, line2)


def test_satcat_search_skips_decayed_objects_before_applying_limit(monkeypatch):
    records = [
        {
            "OBJECT_NAME": "STARLINK-31",
            "NORAD_CAT_ID": 44235,
            "OBJECT_TYPE": "PAY",
            "OWNER": "US",
            "LAUNCH_DATE": "2019-05-24",
            "DECAY_DATE": "2020-10-01",
        },
        {
            "OBJECT_NAME": "STARLINK-1008",
            "NORAD_CAT_ID": 44714,
            "OBJECT_TYPE": "PAY",
            "OWNER": "US",
            "LAUNCH_DATE": "2019-11-11",
            "DECAY_DATE": "",
        },
    ]

    class FakeResponse:
        text = "not empty"

        def raise_for_status(self):
            return None

        def json(self):
            return records

    monkeypatch.setattr(
        celestrak_client.requests, "get", lambda *args, **kwargs: FakeResponse()
    )

    assert celestrak_client.search_satellites_by_name("STARLINK", limit=1) == [
        {
            "norad_id": 44714,
            "name": "STARLINK-1008",
            "object_type": "PAY",
            "country": "US",
            "launch_date": "2019-11-11",
        }
    ]


def _response(data=None, *, text=None, status=200):
    response = requests.Response()
    response.status_code = status
    response._content = (json.dumps(data) if text is None else text).encode()
    return response


def _record(name, norad_id):
    return {"OBJECT_NAME": name, "NORAD_CAT_ID": norad_id, "DECAY_DATE": ""}


@pytest.mark.parametrize("limit", [1, 10, 50])
def test_search_ranks_exact_match_after_all_candidates(monkeypatch, limit):
    records = [_record(f"TARGET {i}", i + 1) for i in range(75)]
    records.append(_record("TARGET", 100))
    monkeypatch.setattr(celestrak_client.requests, "get", lambda *a, **k: _response(records))

    result = celestrak_client.search_satellites_by_name("TARGET", limit)

    assert len(result) == limit
    assert result[0]["norad_id"] == 100
    assert celestrak_client.get_norad_id("TARGET") == 100


def test_search_ranks_and_deduplicates_across_spellings(monkeypatch):
    partial = _record("TARGET-1 BACKUP", 1)
    exact = _record("TARGET 1", 2)

    def get(*args, **kwargs):
        return _response([partial] if kwargs["params"]["NAME"] == "TARGET-1" else [partial, exact])

    monkeypatch.setattr(celestrak_client.requests, "get", get)
    assert [r["norad_id"] for r in celestrak_client.search_satellites_by_name("TARGET-1")] == [2, 1]


@pytest.mark.parametrize("text", ["[]", "No SATCAT records found", "No SATCAT records found.\n"])
def test_search_preserves_genuine_no_matches(monkeypatch, text):
    monkeypatch.setattr(celestrak_client.requests, "get", lambda *a, **k: _response(text=text))
    assert celestrak_client.search_satellites_by_name("UNKNOWN") == []


@pytest.mark.parametrize("error", [requests.Timeout("timeout"), requests.ConnectionError("offline")])
def test_search_and_resolution_report_upstream_outage(monkeypatch, error):
    def get(*args, **kwargs):
        raise error

    monkeypatch.setattr(celestrak_client.requests, "get", get)
    for lookup in (celestrak_client.search_satellites_by_name, celestrak_client.get_norad_id):
        with pytest.raises(requests.RequestException, match="CelesTrak.*unavailable"):
            lookup("TARGET")


@pytest.mark.parametrize("status", [429, 500, 503])
def test_search_reports_http_failure(monkeypatch, status):
    monkeypatch.setattr(celestrak_client.requests, "get", lambda *a, **k: _response([], status=status))
    with pytest.raises(requests.RequestException, match="CelesTrak.*unavailable"):
        celestrak_client.search_satellites_by_name("TARGET")


@pytest.mark.parametrize("text", ["", "<html>Unavailable</html>", "{broken", "{}", '["bad record"]'])
def test_search_reports_invalid_response(monkeypatch, text):
    monkeypatch.setattr(celestrak_client.requests, "get", lambda *a, **k: _response(text=text))
    with pytest.raises(requests.RequestException, match="CelesTrak.*invalid response"):
        celestrak_client.search_satellites_by_name("TARGET")


@pytest.mark.parametrize("failed_first", [True, False])
@pytest.mark.parametrize("has_match", [True, False])
def test_alternate_spelling_recovers_only_with_results(monkeypatch, failed_first, has_match):
    calls = []

    def get(*args, **kwargs):
        calls.append(kwargs["params"]["NAME"])
        if (calls[-1] == "TARGET-1") == failed_first:
            raise requests.Timeout("timeout")
        return _response([_record("TARGET 1", 1)] if has_match else [])

    monkeypatch.setattr(celestrak_client.requests, "get", get)
    if has_match:
        assert celestrak_client.search_satellites_by_name("TARGET-1")[0]["norad_id"] == 1
    else:
        with pytest.raises(requests.RequestException, match="unavailable"):
            celestrak_client.search_satellites_by_name("TARGET-1")
    assert calls == ["TARGET-1", "TARGET 1"]


def test_optional_name_lookup_outage_does_not_discard_valid_tle(monkeypatch):
    monkeypatch.setattr(celestrak_client, "fetch_tle", lambda norad: ("line1", "line2"))

    def unavailable(*args, **kwargs):
        raise requests.RequestException("unavailable")

    monkeypatch.setattr(celestrak_client, "_fetch_gp_metadata", unavailable)
    monkeypatch.setattr(celestrak_client, "_resolve_search_result", unavailable)
    result = celestrak_client.get_satellite_info("ISS")
    assert result["tle_line1"] == "line1"
    assert result["name"] == "NORAD 25544"
