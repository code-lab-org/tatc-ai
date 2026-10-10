import asyncio
import importlib
import json
import os
import unittest
from datetime import datetime
from unittest import mock

from fastmcp import Client
from shapely.geometry import Polygon


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
            {
                "generate_ground_track",
                "get_instrument_info",
                "get_satellite_info",
                "get_satellite_instruments",
                "resolve_instrument_geometry",
                "convert_swath_to_field_of_regard",
                "begin_access_run",
                "set_access_region",
                "finish_access_run",
                "get_access_daily",
                "get_access_cells",
                "get_access_revisit",
                "search_instruments",
                "search_satellites",
            },
        )

    def test_server_name_and_instructions(self):
        self.assertEqual(self.server.mcp.name, "tatc-ai-mcp-server")
        # Instructions must describe tools by role, not bare MCP tool names,
        # which chat clients register under prefixed/suffixed names.
        self.assertIn("satellite search tool", self.server.SERVER_INSTRUCTIONS)
        self.assertIn("instrument search tool", self.server.SERVER_INSTRUCTIONS)
        self.assertIn("satellite-instruments tool", self.server.SERVER_INSTRUCTIONS)
        self.assertIn("instrument info tool", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("search_satellites", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("get_satellite_info", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("generate_ground_track", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("search_instruments", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("get_satellite_instruments", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("get_instrument_info", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("resolve_instrument_geometry", self.server.SERVER_INSTRUCTIONS)

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
            "omm": [{"OBJECT_NAME": "ISS (ZARYA)", "NORAD_CAT_ID": 25544}],
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
                "omm": [{"OBJECT_NAME": "ISS (ZARYA)", "NORAD_CAT_ID": 25544}],
            },
        ), mock.patch.object(
            self.server, "create_orbit_from_omm", return_value=object()
        ), mock.patch.object(
            self.server,
            "compute_ground_track",
            return_value=[(datetime(2026, 1, 1, 0, 0, 0), 51.5, -0.12, 408000.0, None)],
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

    def test_generate_ground_track_opt_in_footprint(self):
        with mock.patch.object(
            self.server.celestrak_client,
            "get_satellite_info",
            return_value={
                "norad_id": 25544,
                "name": "ISS (ZARYA)",
                "omm": [{"OBJECT_NAME": "ISS (ZARYA)", "NORAD_CAT_ID": 25544}],
            },
        ), mock.patch.object(
            self.server, "create_orbit_from_omm", return_value=object()
        ), mock.patch.object(
            self.server,
            "compute_ground_track",
            return_value=[(
                datetime(2026, 1, 1, 0, 0, 0), 51.5, -0.12, 408000.0,
                Polygon([(0.0, 0.0), (1.0, 0.0), (1.0, 1.0)]),
            )],
        ):
            result = self.call_tool(
                "generate_ground_track",
                {
                    "satellite_identifier": "ISS",
                    "start_time": "2026-01-01T00:00:00Z",
                    "duration": "1 minute",
                    "step_interval": "1 minute",
                    "include_footprint": True,
                },
            )
        self.assertFalse(result.is_error)
        point = json.loads(result.content[0].text)[0]
        self.assertIn("footprint_geojson", point)
        self.assertEqual(point["footprint_geojson"]["type"], "Feature")

    def test_search_instruments_returns_list(self):
        expected = [
            {
                "slug": "abi",
                "label": "ABI",
                "oscar_url": "https://space.oscar.wmo.int/instruments/view/abi",
            }
        ]
        with mock.patch.object(
            self.server.oscar_client,
            "search_instruments",
            return_value=expected,
        ):
            result = self.call_tool("search_instruments", {"query": "ABI"})
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content[0].text), expected)

    def test_search_instruments_rejects_bad_limit(self):
        result = self.call_tool(
            "search_instruments", {"query": "ABI", "limit": 0}
        )
        self.assertTrue(result.is_error)
        self.assertIn("between 1 and 50", result.content[0].text)

    def test_get_satellite_instruments_returns_dict(self):
        expected = {
            "slug": "iss",
            "acronym": "ISS",
            "instruments": [{"slug": "iss_lis", "acronym": "LIS"}],
        }
        with mock.patch.object(
            self.server.oscar_client,
            "resolve_satellite",
            return_value=expected,
        ):
            result = self.call_tool(
                "get_satellite_instruments", {"satellite_identifier": "ISS"}
            )
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content[0].text), expected)

    def test_get_instrument_info_returns_dict(self):
        expected = {
            "slug": "abi",
            "acronym": "ABI",
            "specs": {"Resolution": "0.5 km IFOV"},
        }
        with mock.patch.object(
            self.server.oscar_client,
            "get_instrument",
            return_value=expected,
        ):
            result = self.call_tool(
                "get_instrument_info", {"instrument_identifier": "ABI"}
            )
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content[0].text), expected)

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

    def test_resolve_geometry_schema_and_status(self):
        tool = next(
            tool for tool in self.list_tools() if tool.name == "resolve_instrument_geometry"
        )
        self.assertIn("instrument_identifier", tool.inputSchema["required"])
        description = " ".join(tool.description.lower().split())
        self.assertIn("provenance", description)
        self.assertIn("instead of guessing", description)
        record = {
            "slug": "msi",
            "acronym": "MSI",
            "name": "Multi-Spectral Instrument",
            "type": "High resolution optical imagers",
            "oscar_url": "https://space.oscar.wmo.int/instruments/view/msi",
            "specs": {"Scanning Technique": "Pushbroom. Swath 290 km"},
        }
        with mock.patch.object(
            self.server.oscar_client, "get_instrument", return_value=record
        ):
            result = self.call_tool(
                "resolve_instrument_geometry", {"instrument_identifier": "MSI"}
            )
        self.assertFalse(result.is_error)
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["geometry_status"], "conclusive")
        self.assertEqual(payload["recommended_candidate"]["value"], 290.0)
        self.assertIn("Confirm this swath", payload["next_step"])

    def test_resolve_geometry_unknown_when_no_numbers(self):
        record = {
            "slug": "x",
            "acronym": "X",
            "name": "X",
            "type": "t",
            "oscar_url": "https://space.oscar.wmo.int/instruments/view/x",
            "specs": {"Resolution": "10 m"},
        }
        with mock.patch.object(
            self.server.oscar_client, "get_instrument", return_value=record
        ):
            result = self.call_tool(
                "resolve_instrument_geometry", {"instrument_identifier": "X"}
            )
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["geometry_status"], "unknown")
        self.assertIsNone(payload["recommended_candidate"])

    def test_convert_swath_schema_and_math(self):
        tool = next(
            tool
            for tool in self.list_tools()
            if tool.name == "convert_swath_to_field_of_regard"
        )
        self.assertTrue(
            {"swath_width_km", "orbit_altitude_m"}.issubset(
                tool.inputSchema["required"]
            )
        )
        result = self.call_tool(
            "convert_swath_to_field_of_regard",
            {"swath_width_km": 290.0, "orbit_altitude_m": 786000.0},
        )
        self.assertFalse(result.is_error)
        payload = json.loads(result.content[0].text)
        self.assertGreater(payload["field_of_regard_deg"], 0)
        self.assertLess(payload["field_of_regard_deg"], 60)

    def test_begin_access_schema_and_forwards_flat_bounds(self):
        tool = next(
            tool for tool in self.list_tools() if tool.name == "begin_access_run"
        )
        required = set(tool.inputSchema["required"])
        self.assertEqual(required, {"satellite_identifier", "start_time"})
        expected = {"draft_id": "draft_abc", "satellite": "ISS (ZARYA)"}
        with mock.patch.object(
            self.server, "_begin_run", return_value=expected
        ) as begin:
            result = self.call_tool(
                "begin_access_run",
                {
                    "satellite_identifier": "ISS",
                    "start_time": "2026-09-18T04:00:00Z",
                },
            )
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content[0].text), expected)
        begin.assert_called_once_with(
            "ISS", "2026-09-18T04:00:00Z", None
        )

    def test_set_access_region_schema_and_forwards(self):
        tool = next(
            tool for tool in self.list_tools() if tool.name == "set_access_region"
        )
        properties = tool.inputSchema["properties"]
        bound_fields = ["west_lon", "south_lat", "east_lon", "north_lat"]
        self.assertTrue(set(bound_fields).issubset(tool.inputSchema["required"]))
        for name in bound_fields:
            self.assertEqual(properties[name]["type"], "number")
        self.assertNotIn("roi_bbox", properties)
        expected = {"draft_id": "draft_abc", "roi_bbox": [-2.0, -1.0, 2.0, 1.0]}
        with mock.patch.object(
            self.server, "_set_region", return_value=expected
        ) as set_region:
            result = self.call_tool(
                "set_access_region",
                {
                    "draft_id": "draft_abc",
                    "west_lon": -2.0,
                    "south_lat": -1.0,
                    "east_lon": 2.0,
                    "north_lat": 1.0,
                },
            )
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content[0].text), expected)
        set_region.assert_called_once_with(
            "draft_abc", [-2.0, -1.0, 2.0, 1.0]
        )

    def test_begin_access_rejects_bad_window_without_network(self):
        result = self.call_tool(
            "begin_access_run",
            {
                "satellite_identifier": "ISS",
                "start_time": "2026-09-18T04:00:00Z",
                "end_time": "2027-09-25T04:00:00Z",
            },
        )
        self.assertTrue(result.is_error)
        self.assertIn("no longer than 366 days", result.content[0].text)

    def test_finish_access_forwards_draft_and_geometry(self):
        expected = {"result_id": "abc123", "summary_counts": {}}
        with mock.patch.object(
            self.server, "_finish_run", return_value=expected
        ) as finish:
            result = self.call_tool(
                "finish_access_run",
                {
                    "draft_id": "draft_abc",
                    "grid_rows": 1,
                    "grid_columns": 2,
                    "field_of_regard_deg": 50.0,
                    "geometry_reference": "https://example.com/iss",
                },
            )
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content[0].text), expected)
        finish.assert_called_once_with(
            "draft_abc",
            grid_rows=1,
            grid_columns=2,
            field_of_regard_deg=50.0,
            swath_width_km=None,
            geometry_reference="https://example.com/iss",
            instrument_name=None,
            daylight_only=False,
        )

    def test_finish_access_rejects_unknown_draft(self):
        result = self.call_tool(
            "finish_access_run",
            {
                "draft_id": "draft_nope",
                "field_of_regard_deg": 50.0,
                "geometry_reference": "https://example.com/iss",
            },
        )
        self.assertTrue(result.is_error)
        self.assertIn("Unknown draft_id", result.content[0].text)

    def test_get_access_daily_forwards_pagination(self):
        expected = {"result_id": "abc123", "daily": []}
        with mock.patch.object(
            self.server, "_access_daily", return_value=expected
        ) as fetch:
            result = self.call_tool(
                "get_access_daily",
                {"result_id": "abc123", "offset": 1, "limit": 2},
            )
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content[0].text), expected)
        fetch.assert_called_once_with("abc123", 1, 2)

    def test_get_access_cells_schema_and_filter(self):
        tool = next(
            tool for tool in self.list_tools() if tool.name == "get_access_cells"
        )
        self.assertIn("result_id", tool.inputSchema["required"])
        expected = {"result_id": "abc123", "geojson": {}}
        with mock.patch.object(
            self.server, "_access_cells", return_value=expected
        ) as fetch:
            result = self.call_tool(
                "get_access_cells", {"result_id": "abc123", "include": "gaps"}
            )
        self.assertFalse(result.is_error)
        fetch.assert_called_once_with("abc123", "gaps")


if __name__ == "__main__":
    unittest.main()
