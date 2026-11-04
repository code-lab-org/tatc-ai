"""Serve a same-origin Cesium viewer; private scene data travels in URL fragments."""

import base64
import gzip
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import FileResponse, Response
from starlette.staticfiles import StaticFiles

SOURCE = Path(__file__).parent
VIEWER_PATH = "/visualization/v1/"
CESIUM_PATH = "/visualization/cesium/1.146/"


def build_viewer_url(result: dict) -> str:
    base = os.environ.get("TATC_VIEWER_BASE_URL") or os.environ.get(
        "MCP_BASE_URL", "http://localhost:8000"
    )
    parts = urlsplit(base)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError(
            "TATC_VIEWER_BASE_URL must be a browser-accessible HTTP(S) server URL"
        )
    payload = json.dumps(result, allow_nan=False, separators=(",", ":")).encode()
    if len(payload) > 8_000_000:
        raise ValueError("Visualization scene exceeds the supported size")
    encoded = (
        base64.urlsafe_b64encode(gzip.compress(payload, mtime=0)).decode().rstrip("=")
    )
    if len(encoded) > 2_000_000:
        raise ValueError("Visualization scene exceeds the supported size")
    return base.rstrip("/") + VIEWER_PATH + "launch#v1." + encoded


def register_viewer_routes(mcp):
    # Public routes contain only library assets and a generic viewer.
    # Scenario data is supplied by the authenticated MCP response, never these routes.
    assets = StaticFiles(
        directory=os.environ.get(
            "TATC_CESIUM_ASSET_DIR",
            str(SOURCE.parent / "viewer-assets/node_modules/cesium/Build/Cesium"),
        ),
        check_dir=False,
    )

    @mcp.custom_route(VIEWER_PATH + "{filename:path}", methods=["GET", "HEAD"])
    async def viewer(request: Request):
        files = {
            "": ("coverage_view.html", "text/html"),
            "viewer.js": ("coverage_view.js", "text/javascript"),
            "viewer.css": ("coverage_view.css", "text/css"),
            "launch": ("coverage_launch.html", "text/html"),
            "launch.js": ("coverage_launch.js", "text/javascript"),
            "scene-data.js": ("scene_data.js", "text/javascript"),
            "research.js": ("research_view.js", "text/javascript"),
        }
        entry = files.get(request.path_params["filename"])
        if entry is None:
            return Response(status_code=404)
        # Cesium 1.146 embeds workers that import another local blob script.
        # Its expression evaluator and WebAssembly also require unsafe-eval.
        # No external script hosts are permitted.
        return FileResponse(
            SOURCE / entry[0],
            media_type=entry[1],
            headers={
                "Cache-Control": "no-cache",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": (
                    "default-src 'none'; script-src 'self' blob: 'unsafe-eval'; worker-src 'self' blob:; "
                    "style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; "
                    "connect-src 'self'; base-uri 'none'; form-action 'none'"
                ),
            },
        )

    @mcp.custom_route(CESIUM_PATH + "{filename:path}", methods=["GET", "HEAD"])
    async def cesium_asset(request: Request):
        try:
            response = await assets.get_response(
                request.path_params["filename"], request.scope
            )
        except RuntimeError:
            return Response(
                "Cesium assets are not installed. Build the server image or run npm ci in viewer-assets.",
                status_code=503,
            )
        response.headers["X-Content-Type-Options"] = "nosniff"
        if response.status_code == 200:
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
