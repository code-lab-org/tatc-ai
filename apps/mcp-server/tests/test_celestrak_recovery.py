import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier, Event, Lock

import pytest
import requests

from src import celestrak_client
from test_celestrak_client import ISS_OMM


def response(status, body):
    result = requests.Response()
    result.status_code = status
    result._content = body
    result._content_consumed = True
    return result


def test_cache_and_cooldown_survive_fresh_interpreter(monkeypatch, tmp_path):
    path = tmp_path / "restart-cache.sqlite3"
    monkeypatch.setenv("CELESTRAK_CACHE_PATH", str(path))
    monkeypatch.setattr("src.celestrak_cache.time.time", lambda: 100000.0)
    replies = iter(
        [response(200, json.dumps([ISS_OMM]).encode()), response(503, b"unavailable")]
    )
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: next(replies))
    assert celestrak_client.fetch_omm(25544) == [ISS_OMM]
    with pytest.raises(requests.RequestException, match="503"):
        celestrak_client.fetch_omm(20580)

    script = """
import json
import requests
from src import celestrak_client
import src.celestrak_cache
src.celestrak_cache.time.time = lambda: 100599.0
def no_network(*args, **kwargs):
    raise AssertionError("unexpected network request after restart")
requests.get = no_network
cached = celestrak_client.fetch_omm(25544)
try:
    celestrak_client.fetch_omm(20580)
except requests.RequestException as exc:
    print(json.dumps({"cached": cached, "error": str(exc)}))
else:
    raise AssertionError("cooldown did not survive restart")
"""
    env = os.environ.copy()
    env["CELESTRAK_CACHE_PATH"] = str(path)
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=True,
    )
    result = json.loads(completed.stdout)
    assert result["cached"] == [ISS_OMM]
    assert "503" in result["error"] and "retry" in result["error"]


@pytest.mark.parametrize(
    "recovery_succeeds", [False, True], ids=["failure-renews", "success-coalesces"]
)
def test_concurrent_recovery_at_cooldown_expiry(
    monkeypatch, tmp_path, recovery_succeeds
):
    monkeypatch.setenv("CELESTRAK_CACHE_PATH", str(tmp_path / "concurrent.sqlite3"))
    now = [100000.0]
    monkeypatch.setattr("src.celestrak_cache.time.time", lambda: now[0])
    calls = []
    calls_lock = Lock()
    entered, release = Event(), Event()
    gate = Barrier(8)

    def get(*args, **kwargs):
        with calls_lock:
            calls.append(1)
            attempt = len(calls)
        if attempt == 1:
            return response(503, b"unavailable")
        entered.set()
        if not release.wait(10):
            raise AssertionError("recovery request was not released")
        if recovery_succeeds:
            return response(200, json.dumps([ISS_OMM]).encode())
        return response(503, b"still unavailable")

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(requests.RequestException, match="503"):
        celestrak_client.fetch_omm(25544)
    now[0] += 600

    def lookup():
        gate.wait(timeout=10)
        try:
            return celestrak_client.fetch_omm(25544)
        except requests.RequestException as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(lookup) for _ in range(8)]
        try:
            assert entered.wait(10)
        finally:
            release.set()
        results = [future.result(timeout=15) for future in futures]
    assert len(calls) == 2
    if recovery_succeeds:
        assert results == [[ISS_OMM]] * 8
    else:
        assert all("503" in result and "retry" in result for result in results)
        now[0] += 599
        with pytest.raises(requests.RequestException, match="503.*retry"):
            celestrak_client.fetch_omm(20580)
        assert len(calls) == 2


def test_satcat_cache_expires_at_twelve_hours(monkeypatch, tmp_path):
    monkeypatch.setenv("CELESTRAK_CACHE_PATH", str(tmp_path / "satcat.sqlite3"))
    now = [100000.0]
    monkeypatch.setattr("src.celestrak_cache.time.time", lambda: now[0])
    calls = []

    def get(*args, **kwargs):
        calls.append(1)
        return response(200, b'[{"OBJECT_NAME":"TARGET","NORAD_CAT_ID":1}]')

    monkeypatch.setattr(requests, "get", get)
    assert celestrak_client.search_satellites_by_name("TARGET")[0]["norad_id"] == 1
    now[0] += 43199
    assert celestrak_client.search_satellites_by_name("TARGET")[0]["norad_id"] == 1
    assert len(calls) == 1
    now[0] += 1
    assert celestrak_client.search_satellites_by_name("TARGET")[0]["norad_id"] == 1
    assert len(calls) == 2


@pytest.mark.parametrize("failure", ["network", "invalid-json", "invalid-shape"])
def test_network_and_invalid_responses_pause_for_ten_minutes(
    monkeypatch, tmp_path, failure
):
    monkeypatch.setenv("CELESTRAK_CACHE_PATH", str(tmp_path / "failure.sqlite3"))
    now = [100000.0]
    monkeypatch.setattr("src.celestrak_cache.time.time", lambda: now[0])
    calls = []

    def get(*args, **kwargs):
        calls.append(1)
        if len(calls) > 1:
            return response(200, json.dumps([ISS_OMM]).encode())
        if failure == "network":
            raise requests.ConnectionError("offline")
        return response(200, b"not json" if failure == "invalid-json" else b"{}")

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(requests.RequestException, match="retry"):
        celestrak_client.fetch_omm(25544)
    now[0] += 599
    with pytest.raises(requests.RequestException, match="retry"):
        celestrak_client.fetch_omm(20580)
    assert len(calls) == 1
    now[0] += 1
    assert celestrak_client.fetch_omm(25544) == [ISS_OMM]
    assert len(calls) == 2
