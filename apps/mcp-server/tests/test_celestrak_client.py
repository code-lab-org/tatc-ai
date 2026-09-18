import json

import pytest

from src import celestrak_client

ISS_OMM = {
    "OBJECT_NAME": "ISS (ZARYA)",
    "OBJECT_ID": "1998-067A",
    "EPOCH": "2026-09-18T03:25:38.782272",
    "MEAN_MOTION": 15.49160218,
    "ECCENTRICITY": 0.00048228,
    "INCLINATION": 51.6307,
    "RA_OF_ASC_NODE": 200.0361,
    "ARG_OF_PERICENTER": 152.4527,
    "MEAN_ANOMALY": 207.6718,
    "NORAD_CAT_ID": 25544,
    "BSTAR": 0.00011125122,
    "MEAN_MOTION_DOT": 5.718e-5,
    "MEAN_MOTION_DDOT": 0,
}


def _fake_response(records, text=None):
    class FakeResponse:
        def __init__(self):
            self.text = text if text is not None else json.dumps(records)

        def raise_for_status(self):
            return None

        def json(self):
            return records

    return FakeResponse()


def test_fetch_omm_requests_json_and_validates_records(monkeypatch):
    def fake_get(url, **kwargs):
        assert url == celestrak_client.GP_URL
        assert kwargs["params"] == {"CATNR": 25544, "FORMAT": "json"}
        assert kwargs["allow_redirects"] is True
        assert kwargs["timeout"] == 10
        return _fake_response([ISS_OMM])

    monkeypatch.setattr(celestrak_client.requests, "get", fake_get)

    assert celestrak_client.fetch_omm(25544) == [ISS_OMM]


def test_fetch_omm_rejects_celestrak_errors_and_bad_json(monkeypatch):
    responses = iter(
        [
            _fake_response(None, text="No GP data found"),
            _fake_response(None, text="<html>not json</html>"),
        ]
    )
    monkeypatch.setattr(
        celestrak_client.requests, "get", lambda *args, **kwargs: next(responses)
    )

    with pytest.raises(ValueError, match="not available"):
        celestrak_client.fetch_omm(25544)
    with pytest.raises(ValueError, match="Invalid OMM JSON"):
        celestrak_client.fetch_omm(25544)


def test_get_satellite_info_uses_omm_name(monkeypatch):
    monkeypatch.setattr(celestrak_client, "get_norad_id", lambda identifier: 25544)
    monkeypatch.setattr(celestrak_client, "fetch_omm", lambda norad_id: [ISS_OMM])

    assert celestrak_client.get_satellite_info("ISS") == {
        "norad_id": 25544,
        "name": "ISS (ZARYA)",
        "omm": [ISS_OMM],
    }


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
