"""Viewer HTTP contract: isolated assets, deployment URLs, private fragments."""

import base64
import gzip
import json
from urllib.parse import urlsplit

import pytest
from fastmcp import FastMCP
from starlette.testclient import TestClient

from src.coverage_view import build_viewer_url, register_viewer_routes


def test_viewer_url_keeps_scene_out_of_http_requests(monkeypatch):
    monkeypatch.setenv("MCP_BASE_URL", "https://mcp.example.org")
    monkeypatch.delenv("TATC_VIEWER_BASE_URL", raising=False)
    data = {"name": '</script><script>alert("x")</script>', "tracks": []}
    url = build_viewer_url(data)
    parts = urlsplit(url)
    assert parts.scheme == "https"
    assert parts.netloc == "mcp.example.org"
    assert parts.path == "/visualization/v1/launch"
    assert parts.query == ""
    encoded = parts.fragment.removeprefix("v1.")
    assert (
        json.loads(
            gzip.decompress(
                base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            )
        )
        == data
    )
    assert url == build_viewer_url(data)
    monkeypatch.setenv("TATC_VIEWER_BASE_URL", "https://viewer.example.org/tatc/")
    assert build_viewer_url(data).startswith(
        "https://viewer.example.org/tatc/visualization/v1/launch#v1."
    )


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "https://user:pass@example.org",
        "https://example.org/?x=1",
        "https://example.org/#foo",
        "relative",
    ],
)
def test_invalid_viewer_base_url_is_rejected(monkeypatch, url):
    monkeypatch.setenv("TATC_VIEWER_BASE_URL", url)
    with pytest.raises(ValueError, match="browser-accessible"):
        build_viewer_url({})


def test_viewer_rejects_oversized_and_nonfinite_data():
    with pytest.raises(ValueError):
        build_viewer_url({"value": float("nan")})
    with pytest.raises(ValueError, match="size"):
        build_viewer_url({"value": "x" * 8_000_000})


def test_public_routes_serve_only_viewer_and_packaged_assets(monkeypatch, tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "Workers").mkdir()
    (assets / "Workers" / "test.js").write_text("postMessage('ready')")
    (tmp_path / "secret.txt").write_text("must not be served")
    monkeypatch.setenv("TATC_CESIUM_ASSET_DIR", str(assets))
    app = FastMCP("test")
    register_viewer_routes(app)
    with TestClient(app.http_app()) as client:
        shell = client.get("/visualization/v1/")
        assert shell.status_code == 200
        assert 'src="./viewer.js"' in shell.text
        assert shell.headers["referrer-policy"] == "no-referrer"
        assert "worker-src 'self' blob:" in shell.headers["content-security-policy"]
        assert (
            "script-src 'self' blob: 'unsafe-eval'"
            in shell.headers["content-security-policy"]
        )
        js = client.get("/visualization/v1/viewer.js")
        assert js.status_code == 200
        assert "javascript" in js.headers["content-type"]
        assert "prepareEmbeddedWorkers" not in js.text
        assert "cesium.com" not in js.text
        worker = client.get("/visualization/cesium/1.146/Workers/test.js")
        assert worker.status_code == 200
        assert "javascript" in worker.headers["content-type"]
        assert "immutable" in worker.headers["cache-control"]
        assert worker.headers["x-content-type-options"] == "nosniff"
        for path in [
            "/visualization/v1/server.py",
            "/visualization/v1/nope",
            "/visualization/cesium/1.146/%2e%2e/secret.txt",
            "/visualization/cesium/1.146/nope.js",
        ]:
            response = client.get(path)
            assert response.status_code == 404
            assert "must not be served" not in response.text
