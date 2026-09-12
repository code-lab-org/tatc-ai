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

    assert celestrak_client._fetch_satcat_records("STARLINK", limit=1) == [
        {
            "norad_id": 44714,
            "name": "STARLINK-1008",
            "object_type": "PAY",
            "country": "US",
            "launch_date": "2019-11-11",
        }
    ]
