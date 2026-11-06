import json

import requests

from src import celestrak_client
from test_celestrak_client import ISS_OMM


def test_gp_cache_reuses_success(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps([ISS_OMM]).encode()
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests, "get", get)
    assert celestrak_client.fetch_omm(25544) == [ISS_OMM]
    assert celestrak_client.fetch_omm(25544) == [ISS_OMM]
    assert len(calls) == 1


def test_rate_limit_pauses_other_queries_but_serves_cached_data(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(kwargs)
        response = requests.Response()
        response.status_code = 200 if len(calls) == 1 else 429
        response._content = (
            json.dumps([ISS_OMM]).encode() if len(calls) == 1 else b"rate limited"
        )
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests, "get", get)
    celestrak_client.fetch_omm(25544)
    import pytest

    with pytest.raises(requests.RequestException, match="429"):
        celestrak_client.search_satellites_by_name("TARGET")
    assert celestrak_client.fetch_omm(25544) == [ISS_OMM]
    with pytest.raises(requests.RequestException, match="retry"):
        celestrak_client.fetch_omm(20580)
    assert len(calls) == 2


def test_expiry_and_satcat_limit_reuse(monkeypatch):
    now = [100000.0]
    monkeypatch.setattr("src.celestrak_cache.time.time", lambda: now[0])
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps(
            [ISS_OMM]
            if url == celestrak_client.GP_URL
            else [
                {"OBJECT_NAME": "TARGET", "NORAD_CAT_ID": 1},
                {"OBJECT_NAME": "TARGET 2", "NORAD_CAT_ID": 2},
            ]
        ).encode()
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests, "get", get)
    celestrak_client.fetch_omm(25544)
    assert len(celestrak_client.search_satellites_by_name("TARGET", 1)) == 1
    assert len(celestrak_client.search_satellites_by_name("TARGET", 2)) == 2
    now[0] += 7199
    celestrak_client.fetch_omm(25544)
    assert len(calls) == 2
    now[0] += 1
    celestrak_client.fetch_omm(25544)
    assert len(calls) == 3
    assert all(
        call[1]["timeout"] == (10, 30) and call[1]["allow_redirects"] is False
        for call in calls
    )


def test_no_matches_are_cached_and_do_not_pause_gp(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 200
        response._content = (
            b"No SATCAT records found"
            if url == celestrak_client.SATCAT_URL
            else json.dumps([ISS_OMM]).encode()
        )
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests, "get", get)
    assert celestrak_client.search_satellites_by_name("UNKNOWN") == []
    assert celestrak_client.search_satellites_by_name("UNKNOWN") == []
    assert celestrak_client.fetch_omm(25544) == [ISS_OMM]
    assert len(calls) == 2


def test_concurrent_same_key_misses_are_coalesced(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    calls = []
    gate = Barrier(8)

    def get(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps([ISS_OMM]).encode()
        response._content_consumed = True
        return response

    def lookup():
        gate.wait()
        return celestrak_client.fetch_omm(25544)

    monkeypatch.setattr(requests, "get", get)
    with ThreadPoolExecutor(max_workers=8) as executor:
        assert all(
            result == [ISS_OMM] for result in executor.map(lambda _: lookup(), range(8))
        )
    assert len(calls) == 1


def test_cache_storage_failure_prevents_request(monkeypatch, tmp_path):
    import pytest

    monkeypatch.setenv("CELESTRAK_CACHE_PATH", str(tmp_path / "missing" / "cache"))
    (tmp_path / "missing").write_text("not a directory")
    monkeypatch.setattr(
        requests, "get", lambda *args, **kwargs: pytest.fail("network called")
    )
    with pytest.raises(requests.RequestException, match="cache storage unavailable"):
        celestrak_client.fetch_omm(25544)


def test_cooldown_expires_and_renews_after_another_failure(monkeypatch, caplog):
    import pytest

    now = [100000.0]
    calls = []
    monkeypatch.setattr("src.celestrak_cache.time.time", lambda: now[0])

    def get(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 503
        response._content = b"maintenance"
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(requests.RequestException, match="503.*UTC"):
        celestrak_client.fetch_omm(25544)
    now[0] += 599
    with pytest.raises(requests.RequestException, match="503.*UTC"):
        celestrak_client.search_satellites_by_name("TARGET")
    assert len(calls) == 1
    assert len(caplog.records) == 1
    now[0] += 1
    with pytest.raises(requests.RequestException, match="503.*UTC"):
        celestrak_client.fetch_omm(20580)
    now[0] += 599
    with pytest.raises(requests.RequestException, match="503.*UTC"):
        celestrak_client.fetch_omm(20580)
    assert len(calls) == 2
    assert len(caplog.records) == 2


def test_gp_no_data_is_cached_without_service_pause(monkeypatch):
    import pytest

    calls = []

    def get(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 200
        response._content = (
            b"No GP data found" if url == celestrak_client.GP_URL else b"[]"
        )
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests, "get", get)
    for _ in range(2):
        with pytest.raises(ValueError, match="GP data not available"):
            celestrak_client.fetch_omm(25544)
    assert celestrak_client.search_satellites_by_name("TARGET") == []
    assert len(calls) == 2


def test_cache_and_pause_survive_another_process(monkeypatch):
    import multiprocessing
    import pytest

    now = [100000.0]
    calls = []
    monkeypatch.setattr("src.celestrak_cache.time.time", lambda: now[0])

    def get(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 200 if len(calls) == 1 else 429
        response._content = (
            json.dumps([ISS_OMM]).encode() if len(calls) == 1 else b"limited"
        )
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests, "get", get)
    celestrak_client.fetch_omm(25544)
    with pytest.raises(requests.RequestException):
        celestrak_client.fetch_omm(20580)

    context = multiprocessing.get_context("fork")
    queue = context.Queue()

    def child():
        try:
            cached = celestrak_client.fetch_omm(25544)
            try:
                celestrak_client.fetch_omm(12345)
            except requests.RequestException as exc:
                queue.put((cached, str(exc)))
        except Exception as exc:
            queue.put((None, str(exc)))

    process = context.Process(target=child)
    process.start()
    process.join(10)
    assert process.exitcode == 0
    cached, error = queue.get(timeout=2)
    assert cached == [ISS_OMM]
    assert "429" in error and "retry" in error
    assert len(calls) == 2


def test_generic_403_stops_alternate_search_without_global_pause(monkeypatch):
    import pytest

    calls = []

    def get(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 403 if len(calls) == 1 else 200
        response._content = (
            b"firewall blocked" if len(calls) == 1 else json.dumps([ISS_OMM]).encode()
        )
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(requests.RequestException, match="403"):
        celestrak_client.search_satellites_by_name("TARGET-1")
    assert celestrak_client.fetch_omm(25544) == [ISS_OMM]
    assert len(calls) == 2


def test_full_cache_stops_downloads_until_expired_entries_are_reclaimed(monkeypatch):
    import pytest

    now = [100000.0]
    calls = []
    monkeypatch.setattr("src.celestrak_cache.time.time", lambda: now[0])
    # A valid SATCAT response with a large ignored field fills most of the cache.
    large_body = json.dumps(
        [
            {
                "OBJECT_NAME": "TARGET",
                "NORAD_CAT_ID": 1,
                "padding": "x" * (26 * 1024 * 1024),
            }
        ]
    ).encode()

    def get(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 200
        response._content = large_body
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests, "get", get)
    assert celestrak_client.search_satellites_by_name("TARGET")[0]["norad_id"] == 1
    with pytest.raises(requests.RequestException, match="cache is full"):
        celestrak_client.search_satellites_by_name("OTHER")
    with pytest.raises(requests.RequestException, match="cache is full"):
        celestrak_client.search_satellites_by_name("THIRD")
    assert len(calls) == 2
    assert celestrak_client.search_satellites_by_name("TARGET")[0]["norad_id"] == 1
    now[0] += 43200
    assert celestrak_client.search_satellites_by_name("OTHER")[0]["norad_id"] == 1
    assert len(calls) == 3


def test_temporary_403_locks_for_two_hours_and_redacts_logs(monkeypatch, caplog):
    import pytest

    now = [100000.0]
    calls = []
    monkeypatch.setattr("src.celestrak_cache.time.time", lambda: now[0])

    def get(url, **kwargs):
        calls.append(url)
        response = requests.Response()
        response.status_code = 403
        response._content = (
            b"Excessive downloads temporarily blocked for 2 hours. token=secretvalue"
        )
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(requests.RequestException, match="403"):
        celestrak_client.fetch_omm(25544)
    now[0] += 7199
    with pytest.raises(requests.RequestException, match="retry"):
        celestrak_client.fetch_omm(20580)
    assert len(calls) == 1
    assert "secretvalue" not in caplog.text
    assert len(caplog.records) == 1
    now[0] += 1
    with pytest.raises(requests.RequestException, match="403"):
        celestrak_client.fetch_omm(20580)
    assert len(calls) == 2


def test_processes_coalesce_concurrent_misses(monkeypatch):
    import multiprocessing

    context = multiprocessing.get_context("fork")
    count = context.Value("i", 0)
    gate = context.Barrier(4)
    queue = context.Queue()

    def get(url, **kwargs):
        with count.get_lock():
            count.value += 1
        response = requests.Response()
        response.status_code = 200
        response._content = json.dumps([ISS_OMM]).encode()
        response._content_consumed = True
        return response

    monkeypatch.setattr(requests, "get", get)

    def child():
        try:
            gate.wait(timeout=5)
            queue.put(celestrak_client.fetch_omm(25544))
        except Exception as exc:
            queue.put(str(exc))

    processes = [context.Process(target=child) for _ in range(4)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert [queue.get(timeout=2) for _ in processes] == [[ISS_OMM]] * 4
    assert count.value == 1
