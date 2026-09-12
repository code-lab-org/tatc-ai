import asyncio
import importlib
import json
import os
import unittest
from datetime import datetime
from unittest import mock

from fastmcp import Client


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for name in (
            "MCP_OIDC_ISSUER_URL",
            "MCP_OIDC_CLIENT_ID",
            "MCP_OIDC_CLIENT_SECRET",
            "MCP_BASE_URL",
        ):
            os.environ.pop(name, None)
        import src.server as server_module

        self.server = importlib.reload(server_module)

    def tearDown(self):
        self.env_patcher.stop()

    def list_tools(self):
        async def run():
            async with Client(self.server.mcp) as client:
                return await client.list_tools()

        return asyncio.run(run())

    def call_tool(self, name, arguments):
        async def run():
            async with Client(self.server.mcp) as client:
                return await client.call_tool(
                    name, arguments, raise_on_error=False
                )

        return asyncio.run(run())

    def test_registers_tatc_tools_instead_of_echo(self):
        tools = {tool.name for tool in self.list_tools()}
        self.assertEqual(
            tools,
            {"generate_ground_track", "get_satellite_info", "search_satellites"},
        )

    def test_server_name_and_instructions(self):
        self.assertEqual(self.server.mcp.name, "tatc-ai-mcp-server")
        self.assertIn("search_satellites", self.server.SERVER_INSTRUCTIONS)

    def test_no_auth_when_oidc_not_configured(self):
        self.assertIsNone(self.server.mcp.auth)

    def test_builds_oidc_auth_when_configured(self):
        os.environ["MCP_OIDC_ISSUER_URL"] = "https://auth.example.com"
        os.environ["MCP_OIDC_CLIENT_ID"] = "mcp-server"
        os.environ["MCP_OIDC_CLIENT_SECRET"] = "test-secret"
        os.environ["MCP_BASE_URL"] = "https://mcp.example.com"

        captured = {}

        def fake_discovery(self_proxy, config_url, strict, timeout_seconds):
            captured["config_url"] = str(config_url)
            return mock.MagicMock(
                authorization_endpoint="https://auth.example.com/authorize",
                token_endpoint="https://auth.example.com/token",
                revocation_endpoint=None,
                service_documentation=None,
            )

        with mock.patch(
            "fastmcp.server.auth.oidc_proxy.OIDCProxy.get_oidc_configuration",
            autospec=True,
            side_effect=fake_discovery,
        ):
            self.server = importlib.reload(self.server)

        auth = self.server.mcp.auth
        self.assertIsNotNone(auth)
        self.assertEqual(
            captured["config_url"],
            "https://auth.example.com/.well-known/openid-configuration",
        )
        self.assertEqual(auth._upstream_client_id, "mcp-server")
        self.assertEqual(str(auth.base_url), "https://mcp.example.com/")
        self.assertEqual(auth._redirect_path, "/oauth/callback")

    def test_main_runs_streamable_http_server(self):
        self.server.mcp.run = mock.MagicMock()
        self.server.main()
        self.server.mcp.run.assert_called_once_with(
            transport="streamable-http",
            host="0.0.0.0",
            port=8000,
            uvicorn_config={"proxy_headers": True, "forwarded_allow_ips": "*"},
        )

    def test_search_satellites_returns_list(self):
        expected = [
            {"norad_id": 25544, "name": "ISS (ZARYA)", "object_type": "PAYLOAD"}
        ]
        with mock.patch.object(
            self.server.celestrak_client,
            "search_satellites_by_name",
            return_value=expected,
        ):
            result = self.call_tool("search_satellites", {"query": "ISS"})
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content[0].text), expected)

    def test_search_satellites_rejects_bad_limit(self):
        result = self.call_tool("search_satellites", {"query": "ISS", "limit": 0})
        self.assertTrue(result.is_error)
        self.assertIn("between 1 and 50", result.content[0].text)

    def test_get_satellite_info_returns_dict(self):
        expected = {
            "norad_id": 25544,
            "name": "ISS (ZARYA)",
            "tle_line1": "line 1",
            "tle_line2": "line 2",
        }
        with mock.patch.object(
            self.server.celestrak_client,
            "get_satellite_info",
            return_value=expected,
        ):
            result = self.call_tool(
                "get_satellite_info", {"satellite_identifier": "ISS"}
            )
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content[0].text), expected)

    def test_generate_ground_track_formats_telemetry(self):
        with mock.patch.object(
            self.server.celestrak_client,
            "get_satellite_info",
            return_value={
                "norad_id": 25544,
                "name": "ISS (ZARYA)",
                "tle_line1": "line 1",
                "tle_line2": "line 2",
            },
        ), mock.patch.object(
            self.server, "create_satellite_from_tle", return_value=object()
        ), mock.patch.object(
            self.server,
            "compute_ground_track",
            return_value=[(datetime(2026, 1, 1, 0, 0, 0), 51.5, -0.12, 408000.0)],
        ), mock.patch.object(
            self.server, "calculate_footprint_from_position", return_value=None
        ):
            result = self.call_tool(
                "generate_ground_track",
                {
                    "satellite_identifier": "ISS",
                    "start_time": "2026-01-01T00:00:00Z",
                    "duration": "1 minute",
                    "step_interval": "1 minute",
                },
            )
        self.assertFalse(result.is_error)
        self.assertEqual(
            json.loads(result.content[0].text),
            [
                {
                    "id": "25544",
                    "time": "2026-01-01T00:00:00Z",
                    "position_lla": {
                        "lat_deg": 51.5,
                        "lon_deg": -0.12,
                        "alt_m": 408000.0,
                    },
                }
            ],
        )

    def test_tool_errors_surface_to_the_model(self):
        with mock.patch.object(
            self.server.celestrak_client,
            "get_satellite_info",
            side_effect=ValueError("Could not resolve satellite identifier 'junk'"),
        ):
            result = self.call_tool(
                "get_satellite_info", {"satellite_identifier": "junk"}
            )
        self.assertTrue(result.is_error)
        self.assertIn("junk", result.content[0].text)


if __name__ == "__main__":
    unittest.main()
