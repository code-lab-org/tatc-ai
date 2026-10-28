import asyncio
import importlib
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path
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
                "ground_track_map",
                "get_satellite_info",
                "get_satellite_instruments",
                "resolve_instrument_geometry",
                "convert_swath_to_field_of_regard",
                "begin_access_run",
                "set_access_region",
                "finish_access_run",
                "prepare_conical_study",
                "finish_conical_study",
                "get_access_events",
                "get_access_event_spacing",
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
        self.assertIn("complete instrument context", self.server.SERVER_INSTRUCTIONS)
        self.assertIn("without confirmation", self.server.SERVER_INSTRUCTIONS)
        self.assertIn("conical preparation", self.server.SERVER_INSTRUCTIONS)
        self.assertIn("conical finish", self.server.SERVER_INSTRUCTIONS)
        self.assertIn("viewing-event fetch", self.server.SERVER_INSTRUCTIONS)
        self.assertIn("event-spacing fetch", self.server.SERVER_INSTRUCTIONS)
        self.assertIn("field-of-regard period", self.server.SERVER_INSTRUCTIONS)
        self.assertIn("24 hours", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("prepare_conical_study", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("finish_conical_study", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("get_access_events", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("get_access_event_spacing", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("search_satellites", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("get_satellite_info", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("generate_ground_track", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("search_instruments", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("get_satellite_instruments", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("get_instrument_info", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("resolve_instrument_geometry", self.server.SERVER_INSTRUCTIONS)
        self.assertNotIn("ground_track_map", self.server.SERVER_INSTRUCTIONS)

    def test_map_tool_role_phrase_snake_cases_to_its_name(self):
        # Models guess a tool's name by snake-casing the role phrase in the
        # instructions ("satellite search tool" -> satellite_search), so the
        # map tool's phrase must snake-case to its real name.
        self.assertIn("ground track map tool", self.server.SERVER_INSTRUCTIONS)
        tools = {tool.name: tool for tool in self.list_tools()}
        description = tools["ground_track_map"].description.lower()
        for word in ("visualize", "map", "plot"):
            self.assertIn(word, description)
        self.assertIn(
            "ground track map tool", tools["generate_ground_track"].description
        )

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

    def test_search_upstream_outage_is_a_tool_error(self):
        with mock.patch.object(
            self.server.celestrak_client.requests,
            "get",
            side_effect=self.server.celestrak_client.requests.Timeout("offline"),
        ):
            result = self.call_tool("search_satellites", {"query": "TARGET"})
        self.assertTrue(result.is_error)
        self.assertIn("CelesTrak satellite search is unavailable", result.content[0].text)

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

    def test_resolve_geometry_returns_numeric_candidates_and_source_context(self):
        tool = next(
            tool for tool in self.list_tools() if tool.name == "resolve_instrument_geometry"
        )
        self.assertIn("instrument_identifier", tool.inputSchema["required"])
        description = " ".join(tool.description.lower().split())
        self.assertIn("provenance", description)
        self.assertIn("does not assess compatibility", description)
        record = {
            "slug": "msi",
            "acronym": "MSI",
            "name": "Multi-Spectral Instrument",
            "type": "High resolution optical imagers",
            "oscar_url": "https://space.oscar.wmo.int/instruments/view/msi",
            "description": "Continuous nadir-centered ground coverage without gaps.",
            "specs": {
                "Scanning Technique": (
                    "Continuous nadir-centered swath 290 km with no gaps and no "
                    "separate pointing needed."
                )
            },
        }
        with mock.patch.object(
            self.server.oscar_client, "get_instrument", return_value=record
        ):
            result = self.call_tool(
                "resolve_instrument_geometry", {"instrument_identifier": "MSI"}
            )
        self.assertFalse(result.is_error)
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["numeric_certainty"], "high")
        self.assertEqual(payload["geometry_candidates"][0]["value"], 290.0)
        self.assertEqual(payload["source_context"]["description"], record["description"])
        self.assertNotIn("geometry_status", payload)
        self.assertNotIn("recommended_candidate", payload)
        self.assertIn("geometry_assessment", payload["next_step"])

    def test_geometry_resolution_leaves_compatibility_to_the_assistant(self):
        finish_tool = next(
            tool for tool in self.list_tools() if tool.name == "finish_access_run"
        )
        finish_properties = finish_tool.inputSchema["properties"]
        self.assertIn("geometry_assessment", finish_tool.inputSchema["required"])
        self.assertIn("geometry_assessment", finish_properties)
        self.assertNotIn("geometry_source_description", finish_properties)
        self.assertNotIn("geometry_mode", finish_properties)

        record = {
            "slug": "x",
            "acronym": "X",
            "name": "X imager",
            "type": "Optical imager",
            "description": "Instrument record description.",
            "oscar_url": "https://space.oscar.wmo.int/instruments/view/x",
            "specs": {"Swath": "Swath 290 km."},
        }
        with mock.patch.object(
            self.server.oscar_client, "get_instrument", return_value=record
        ):
            result = self.call_tool(
                "resolve_instrument_geometry", {"instrument_identifier": "X"}
            )
        self.assertFalse(result.is_error)
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["numeric_certainty"], "high")
        self.assertEqual(payload["geometry_candidates"][0]["value"], 290.0)
        self.assertEqual(payload["source_context"]["specs"], record["specs"])
        self.assertNotIn("geometry_status", payload)
        self.assertNotIn("recommended_candidate", payload)
        self.assertIn("geometry_assessment", payload["next_step"])

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
        self.assertEqual(payload["numeric_certainty"], "none")
        self.assertEqual(payload["geometry_candidates"], [])
        self.assertNotIn("geometry_status", payload)

    def test_resolver_does_not_extract_geometry_from_metadata_or_urls(self):
        record = {
            "slug": "sensor_290",
            "name": "Sensor 290",
            "type": "Imager with 110 degree field of view",
            "oscar_url": "https://example.com/instruments/swath_900km",
            "classification": ["swath width 500 km"],
            "variables": [{"name": "Coverage 700 km"}],
            "specs": {"Resolution": "10 m"},
        }
        with mock.patch.object(
            self.server.oscar_client, "get_instrument", return_value=record
        ):
            result = self.call_tool(
                "resolve_instrument_geometry", {"instrument_identifier": "X"}
            )
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["geometry_candidates"], [])
        self.assertEqual(payload["numeric_certainty"], "none")
        self.assertEqual(payload["source_context"], record)

    def test_resolve_geometry_uses_warning_from_another_spec_field(self):
        record = {
            "slug": "x",
            "acronym": "X",
            "name": "X imager",
            "type": "Optical imager",
            "description": "Pushbroom optical imager.",
            "oscar_url": "https://space.oscar.wmo.int/instruments/view/x",
            "specs": {
                "Swath": "Continuous swath 290 km",
                "Pointing": "Instrument uses off-nadir pointing.",
            },
        }
        with mock.patch.object(
            self.server.oscar_client, "get_instrument", return_value=record
        ):
            result = self.call_tool(
                "resolve_instrument_geometry", {"instrument_identifier": "X"}
            )
        payload = json.loads(result.content[0].text)
        self.assertNotIn("geometry_status", payload)
        self.assertEqual(payload["geometry_candidates"][0]["value"], 290.0)
        self.assertEqual(payload["source_context"]["specs"], record["specs"])

    def test_resolve_geometry_uses_api_warning_and_keeps_page_description(self):
        record = {
            "slug": "x",
            "acronym": "X",
            "name": "X imager",
            "type": "Optical imager",
            "api_description": "The instrument uses off-nadir pointing.",
            "description": "The instrument uses off-nadir pointing.",
            "page_description": "An Earth-observation instrument.",
            "oscar_url": "https://space.oscar.wmo.int/instruments/view/x",
            "specs": {"Swath": "Swath 290 km."},
        }
        with mock.patch.object(
            self.server.oscar_client, "get_instrument", return_value=record
        ):
            result = self.call_tool(
                "resolve_instrument_geometry", {"instrument_identifier": "X"}
            )
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["source_context"]["api_description"], record["api_description"])
        self.assertEqual(payload["source_context"]["page_description"], record["page_description"])
        self.assertEqual(payload["geometry_candidates"][0]["value"], 290.0)

    def test_resolve_geometry_keeps_distinct_descriptions_without_alias_duplicates(self):
        api_description = "Swath 290 km from the instrument API."
        page_description = "Swath 300 km from the instrument page."
        record = {
            "slug": "x",
            "acronym": "X",
            "name": "X imager",
            "type": "Optical imager",
            "api_description": api_description,
            "page_description": page_description,
            "description": api_description,
            "oscar_url": "https://space.oscar.wmo.int/instruments/view/x",
            "specs": {},
        }
        with mock.patch.object(
            self.server.oscar_client, "get_instrument", return_value=record
        ):
            result = self.call_tool(
                "resolve_instrument_geometry", {"instrument_identifier": "X"}
            )
        self.assertFalse(result.is_error)
        payload = json.loads(result.content[0].text)
        self.assertEqual(payload["source_context"], record)
        self.assertEqual(
            [
                (candidate["value"], candidate["source_field"])
                for candidate in payload["geometry_candidates"]
            ],
            [(290.0, "API description"), (300.0, "Instrument page description")],
        )

        independent = dict(record, description="Swath 310 km from a distinct source.")
        with mock.patch.object(
            self.server.oscar_client, "get_instrument", return_value=independent
        ):
            result = self.call_tool(
                "resolve_instrument_geometry", {"instrument_identifier": "X"}
            )
        payload = json.loads(result.content[0].text)
        self.assertEqual(
            [
                (candidate["value"], candidate["source_field"])
                for candidate in payload["geometry_candidates"]
            ],
            [
                (290.0, "API description"),
                (300.0, "Instrument page description"),
                (310.0, "Instrument description"),
            ],
        )

    def test_resolve_geometry_extracts_track_span_from_description(self):
        description = (
            "Four separated laser tracks with gaps span an overall swath width "
            "of 7.0 km."
        )
        record = {
            "slug": "x",
            "acronym": "X",
            "name": "Sensor 7",
            "type": "Three-channel imager",
            "description": description,
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
        self.assertEqual(payload["numeric_certainty"], "high")
        self.assertEqual(payload["geometry_candidates"][0]["value"], 7.0)
        self.assertEqual(
            payload["geometry_candidates"][0]["source_field"],
            "Instrument description",
        )

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
        tool = next(tool for tool in self.list_tools() if tool.name == "finish_access_run")
        properties = tool.inputSchema["properties"]
        self.assertIn("geometry_assessment", tool.inputSchema["required"])
        self.assertIn("geometry_assessment", properties)
        self.assertNotIn("geometry_source_description", properties)
        self.assertNotIn("geometry_mode", properties)
        self.assertNotIn("configuration", properties)
        self.assertNotIn("accepted_setup_revision", properties)
        assessment_schema = properties["geometry_assessment"]
        if "$ref" in assessment_schema:
            definitions = tool.inputSchema.get("$defs", tool.inputSchema.get("definitions", {}))
            assessment_schema = definitions[assessment_schema["$ref"].split("/")[-1]]
        self.assertEqual(assessment_schema["type"], "object")
        self.assertEqual(
            set(assessment_schema["properties"]),
            {"status", "source_text", "explanation", "warning", "mode"},
        )
        self.assertEqual(
            set(assessment_schema["required"]), {"status", "source_text", "explanation"}
        )
        self.assertEqual(
            set(assessment_schema["properties"]["status"]["enum"]),
            {"supported", "approximate", "needs_clarification"},
        )
        assessment = {
            "status": "supported",
            "source_text": "Wide mode: full field of regard 50 degrees.",
            "explanation": "The selected wide mode is represented by this angle.",
            "mode": "wide",
        }
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
                    "geometry_assessment": assessment,
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
            geometry_assessment=mock.ANY,
        )

    def test_prepare_conical_forwards_config_without_legacy_geometry(self):
        tool = next(
            tool
            for tool in self.list_tools()
            if tool.name == "prepare_conical_study"
        )
        required = set(tool.inputSchema["required"])
        self.assertEqual(
            required, {"draft_id", "grid_rows", "grid_columns", "configuration"}
        )
        properties = tool.inputSchema["properties"]
        self.assertNotIn("field_of_regard_deg", properties)
        self.assertNotIn("swath_width_km", properties)
        configuration = properties["configuration"]
        self.assertEqual(configuration["type"], "object")
        self.assertIn("geometry", configuration["properties"])
        self.assertIn("evidence", configuration["properties"])
        self.assertIn("source_conflicts", configuration["properties"])
        expected = {"status": "awaiting_acceptance", "setup_revision": "sha256:abc"}
        configuration = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "data"
                / "amsr2_conical_configuration.json"
            ).read_text()
        )
        with mock.patch.object(
            self.server, "_prepare_conical", return_value=expected
        ) as prepare:
            result = self.call_tool(
                "prepare_conical_study",
                {
                    "draft_id": "draft_abc",
                    "grid_rows": 2,
                    "grid_columns": 2,
                    "configuration": configuration,
                },
            )
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content[0].text), expected)
        prepare.assert_called_once_with("draft_abc", 2, 2, mock.ANY)

    def test_finish_conical_requires_revision_and_acceptance_only(self):
        tool = next(
            tool for tool in self.list_tools() if tool.name == "finish_conical_study"
        )
        self.assertEqual(
            set(tool.inputSchema["required"]),
            {"draft_id", "accepted_setup_revision", "accept_setup"},
        )
        properties = tool.inputSchema["properties"]
        for legacy in ("grid_rows", "grid_columns", "field_of_regard_deg", "swath_width_km"):
            self.assertNotIn(legacy, properties)
        expected = {"result_id": "abc123", "result_kind": "conical_study"}
        with mock.patch.object(
            self.server, "_finish_conical", return_value=expected
        ) as finish:
            result = self.call_tool(
                "finish_conical_study",
                {
                    "draft_id": "draft_abc",
                    "accepted_setup_revision": "sha256:abc",
                    "accept_setup": True,
                },
            )
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content[0].text), expected)
        finish.assert_called_once_with("draft_abc", "sha256:abc", True)

    def test_event_fetch_tools_forward_paging(self):
        events_tool = next(
            tool for tool in self.list_tools() if tool.name == "get_access_events"
        )
        self.assertEqual(
            set(events_tool.inputSchema["required"]), {"result_id"}
        )
        self.assertEqual(
            events_tool.inputSchema["properties"]["limit"].get("default"), 100
        )
        expected = {"result_id": "abc123", "events": []}
        with mock.patch.object(
            self.server, "_conical_events", return_value=expected
        ) as fetch:
            result = self.call_tool(
                "get_access_events",
                {"result_id": "abc123", "offset": 2, "limit": 5},
            )
        self.assertFalse(result.is_error)
        self.assertEqual(json.loads(result.content[0].text), expected)
        fetch.assert_called_once_with("abc123", 2, 5)

        spacing_tool = next(
            tool
            for tool in self.list_tools()
            if tool.name == "get_access_event_spacing"
        )
        self.assertEqual(
            spacing_tool.inputSchema["properties"]["limit"].get("default"), 50
        )
        with mock.patch.object(
            self.server, "_conical_event_spacing", return_value=expected
        ) as fetch:
            result = self.call_tool(
                "get_access_event_spacing",
                {"result_id": "abc123", "offset": 1, "limit": 3},
            )
        self.assertFalse(result.is_error)
        fetch.assert_called_once_with("abc123", 1, 3)

    def test_finish_access_rejects_unknown_draft(self):
        result = self.call_tool(
            "finish_access_run",
            {
                "draft_id": "draft_nope",
                "field_of_regard_deg": 50.0,
                "geometry_reference": "https://example.com/iss",
                "geometry_assessment": {
                    "status": "supported",
                    "source_text": "Field of regard 50 degrees.",
                    "explanation": "The selected geometry is supported for this study.",
                },
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
        fetch.assert_called_once_with("abc123", "gaps", 0, 144)

    def test_staged_access_returns_correct_cell_results(self):
        # GeographicLib areas and direct TAT-C accesses were derived without
        # the server grid, cell-ID adapter, or summary code. The fixture keeps
        # their exact inputs, unrounded areas, observation periods, and method.
        # This checks server behavior across supported TAT-C upgrades.
        # Investigate calculation changes before updating reference values.
        reference = json.loads(
            (Path(__file__).parent / "fixtures" / "staged_access_reference.json")
            .read_text()
        )
        omm = reference["input"]["omm"]
        start = reference["input"]["start_utc"]
        end = reference["input"]["end_utc_exclusive"]
        west, south, east, north = (
            -70.259424,
            -9.086291,
            -70.219424,
            -9.046291,
        )
        geometry_assessment = {
            "status": "supported",
            "source_text": "Fixture scan has a full field of regard of 4 degrees.",
            "explanation": "The fixed fixture supplies the geometry for this access run.",
        }
        from src import coverage_core

        fixture_now = datetime(2026, 10, 25, 4, 30, tzinfo=timezone.utc)

        class FixtureClock(datetime):
            @classmethod
            def now(cls, tz=None):
                return fixture_now if tz is None else fixture_now.astimezone(tz)

        with mock.patch.object(coverage_core, "datetime", FixtureClock), mock.patch.object(
            self.server.celestrak_client,
            "get_satellite_info",
            return_value={"norad_id": 25544, "name": "ISS (ZARYA)", "omm": [omm]},
        ):
            begin = self.call_tool(
                "begin_access_run",
                {
                    "satellite_identifier": "ISS",
                    "start_time": start,
                    "end_time": end,
                },
            )
            self.assertFalse(begin.is_error, begin.content[0].text)
            draft = json.loads(begin.content[0].text)

            region = self.call_tool(
                "set_access_region",
                {
                    "draft_id": draft["draft_id"],
                    "west_lon": west,
                    "south_lat": south,
                    "east_lon": east,
                    "north_lat": north,
                },
            )
            self.assertFalse(region.is_error, region.content[0].text)

            finish = self.call_tool(
                "finish_access_run",
                {
                    "draft_id": draft["draft_id"],
                    "grid_rows": 1,
                    "grid_columns": 2,
                    "field_of_regard_deg": 4.0,
                    "geometry_reference": "https://example.test/fixture-geometry",
                    "instrument_name": "Fixture scan",
                    "geometry_assessment": geometry_assessment,
                },
            )
            self.assertFalse(finish.is_error, finish.content[0].text)
            summary = json.loads(finish.content[0].text)

            result_id = summary["result_id"]
            daily_result = self.call_tool(
                "get_access_daily", {"result_id": result_id}
            )
            cells_result = self.call_tool(
                "get_access_cells", {"result_id": result_id}
            )
            revisit_result = self.call_tool(
                "get_access_revisit", {"result_id": result_id}
            )

        self.assertFalse(daily_result.is_error, daily_result.content[0].text)
        self.assertFalse(cells_result.is_error, cells_result.content[0].text)
        self.assertFalse(revisit_result.is_error, revisit_result.content[0].text)
        daily = json.loads(daily_result.content[0].text)
        cells = json.loads(cells_result.content[0].text)
        revisit = json.loads(revisit_result.content[0].text)

        self.assertEqual(summary["satellite"], "ISS (ZARYA)")
        self.assertEqual(summary["accounting_method"], "legacy_intervals_v1")
        self.assertEqual(daily["accounting_method"], "legacy_intervals_v1")
        self.assertEqual(summary["window_start_utc"], start)
        self.assertEqual(summary["window_end_utc_exclusive"], end)
        self.assertEqual(
            summary["summary_counts"],
            {
                "total_days": 1,
                "observed_days": 1,
                "gap_days": 0,
                "grid_cell_count": 2,
                "observed_cell_count": 2,
                "never_observed_cell_count": 0,
            },
        )
        self.assertEqual(
            daily["daily"],
            [
                {
                    "utc_date": "2026-10-25",
                    "window_start_utc": start,
                    "window_end_utc_exclusive": end,
                    "observed_area_km2": reference["expected_observed_area_km2"],
                    "observed_fraction": 1.0,
                    "gap_area_km2": 0.0,
                    "gap_fraction": 0.0,
                    "observed_cell_ids": reference["expected_observed_cell_ids"],
                    "gap_cell_ids": [],
                }
            ],
        )
        features = cells["geojson"]["features"]
        self.assertEqual([feature["properties"]["id"] for feature in features], [0, 1])
        self.assertEqual(
            [feature["properties"]["area_km2"] for feature in features],
            reference["expected_cell_areas_km2"],
        )
        self.assertEqual(
            [feature["properties"]["status"] for feature in features],
            ["observed", "observed"],
        )
        self.assertEqual(
            [feature["properties"]["access_count"] for feature in features],
            reference["expected_access_counts"],
        )
        self.assertEqual(
            features[0]["geometry"]["coordinates"][0][0], [west, south]
        )
        self.assertIsNone(revisit["revisit"]["mean_revisit_hours"])
        self.assertIsNone(revisit["revisit"]["max_revisit_hours"])
        self.assertEqual(revisit["revisit"]["revisit_sample_count"], 0)
        self.assertIn(
            "next aggregated access start minus the preceding access end",
            revisit["revisit"]["revisit_definition"],
        )


if __name__ == "__main__":
    unittest.main()
