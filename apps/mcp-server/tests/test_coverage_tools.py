"""MCP contracts, inline resource payloads, and HTML data isolation."""

import asyncio
import json
import base64
import gzip
from urllib.parse import urlsplit
from copy import deepcopy
from pathlib import Path
from unittest import mock

import pytest
from fastmcp import Client

from src import server
from src.coverage_analysis import CoverageRequest, analyze
from src.coverage_view import build_viewer_url
from test_coverage_analysis import scenario


def scene(url):
    encoded = urlsplit(url).fragment.removeprefix("v1.")
    return json.loads(
        gzip.decompress(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    )


def call(name, arguments):
    async def run():
        async with Client(server.mcp) as client:
            return await client.call_tool(name, arguments, raise_on_error=False)

    return asyncio.run(run())


def test_numerical_tool_is_usable_without_viewer():
    response = call("analyze_coverage", {"scenario": scenario()})
    assert not response.is_error
    result = response.structured_content
    assert result["points"][0]["metrics"]["longest_gap_seconds"] == 3600
    assert "tracks" not in result
    assert not any(c.type == "resource" for c in response.content)


def test_explorer_keeps_orbit_samples_in_ui_resource_only():
    response = call("visualize", {"view": "coverage", "scenario": scenario()})
    assert not response.is_error
    assert response.structured_content["points"][0]["longest_gap_seconds"] == 3600
    assert "tracks" not in response.structured_content
    resource = next(c.resource for c in response.content if c.type == "resource")
    assert str(resource.uri).startswith("ui://tatc/coverage/")
    assert resource.mimeType == "text/uri-list"
    data = scene(resource.text)
    assert len(data["tracks"]) == 1
    assert data["scenario"]["satellites"][0]["instruments"][0]["field_of_regard"] == 120


def test_tool_rejects_missing_sensor_before_computation():
    data = scenario()
    data["satellites"][0].pop("instruments")
    with mock.patch("tatc.analysis.collect_observations") as collect:
        response = call("analyze_coverage", {"scenario": data})
    assert response.is_error
    collect.assert_not_called()


def test_script_breaking_names_are_data_not_markup():
    data = scenario()
    data["points"][0]["name"] = '</script><script>alert("bad")</script>'
    result = analyze(CoverageRequest.model_validate(data))
    url = build_viewer_url(result)
    assert data["points"][0]["name"] not in url
    assert scene(url)["points"][0]["name"] == data["points"][0]["name"]
    assert urlsplit(url).query == ""


def test_example_view_needs_no_scenario_and_preserves_documented_inputs():
    async def inspect():
        async with Client(server.mcp) as client:
            tools = await client.list_tools()
            return next(t for t in tools if t.name == "visualize")

    tool = asyncio.run(inspect())
    assert tool.inputSchema["required"] == ["view"]
    assert tool.inputSchema["properties"]["view"]["enum"] == [
        "orbit",
        "coverage",
        "example",
        "data",
    ]
    response = call("visualize", {"view": "example"})
    assert not response.is_error
    assert response.structured_content["example"] is True
    assert [p["observation_count"] for p in response.structured_content["points"]] == [
        3,
        14,
        1,
    ]
    resource = next(c.resource for c in response.content if c.type == "resource")
    result = scene(resource.text)
    source = Path(server.__file__).with_name("coverage-scenario.json")
    expected = CoverageRequest.model_validate_json(source.read_text())
    from src.coverage_analysis import build_satellites

    assert result["scenario"]["satellites"] == [
        s.model_dump(mode="json") for s in build_satellites(expected)
    ]
    assert result["scenario"]["points"] == [p.model_dump() for p in expected.points]
    assert (
        result["scenario"]["start_time"]
        == expected.model_dump(mode="json")["start_time"]
    )
    assert (
        result["scenario"]["end_time"] == expected.model_dump(mode="json")["end_time"]
    )


def test_incomplete_custom_scenario_is_rejected_without_substituting_demo():
    # Actual failed Scout call: dates and longitude were omitted.
    data = scenario()
    data.pop("start_time")
    data.pop("end_time")
    data["points"][0].pop("longitude")
    with mock.patch.object(server, "compute_coverage_analysis") as compute:
        response = call("visualize", {"view": "coverage", "scenario": data})
    assert response.is_error
    compute.assert_not_called()


CATALOG_SATELLITE = {
    "norad_id": "25544",
    "name": "ISS",
    "omm": [
        {
            "OBJECT_NAME": "ISS (ZARYA)",
            "OBJECT_ID": "1998-067A",
            "EPOCH": "2021-06-05T07:19:36.128928",
            "MEAN_MOTION": 15.48957534,
            "ECCENTRICITY": 0.0003508,
            "INCLINATION": 51.6455,
            "RA_OF_ASC_NODE": 41.4969,
            "ARG_OF_PERICENTER": 68.0432,
            "MEAN_ANOMALY": 78.3395,
            "EPHEMERIS_TYPE": 0,
            "CLASSIFICATION_TYPE": "U",
            "NORAD_CAT_ID": 25544,
            "ELEMENT_SET_NO": 999,
            "REV_AT_EPOCH": 28675,
            "BSTAR": 7.0541e-5,
            "MEAN_MOTION_DOT": 3.432e-5,
            "MEAN_MOTION_DDOT": 0,
        }
    ],
}


def test_orbit_tool_uses_catalog_elements_without_inventing_a_sensor():
    with mock.patch.object(
        server.celestrak_client, "get_satellite_info", return_value=CATALOG_SATELLITE
    ):
        response = call(
            "visualize",
            {
                "view": "orbit",
                "satellite_identifiers": ["25544"],
                "start_time": "2021-06-05T07:00:00Z",
                "duration": "1 hour",
            },
        )
    assert not response.is_error
    summary = response.structured_content
    assert summary["start_time"] == "2021-06-05T07:00:00Z"
    assert summary["end_time"] == "2021-06-05T08:00:00Z"
    assert summary["display_step_seconds"] == 60
    assert summary["satellites"][0]["orbit_epoch"].startswith("2021-06-05")
    assert summary["satellites"][0]["omm"] == CATALOG_SATELLITE["omm"]
    assert "tracks" not in summary
    assert "ISS (NORAD 25544), element epoch 2021-06-05" in response.content[0].text
    assert (
        "view='orbit', satellite_identifiers=[\"25544\"], "
        "start_time='2021-06-05T07:00:00Z', duration='1 hour'"
    ) in response.content[0].text
    resource = next(c.resource for c in response.content if c.type == "resource")
    assert str(resource.uri).startswith("ui://tatc/orbits/")
    data = scene(resource.text)
    assert data["points"] == []
    assert "instruments" not in resource.text
    samples = data["tracks"][0]["samples"]
    assert len(samples) == 61
    assert samples[-1][0] == "2021-06-05T08:00:00Z"
    assert all(300000 < p[3] < 500000 for p in samples)


def test_orbit_tool_checks_limits_before_catalog_access():
    with mock.patch.object(server.celestrak_client, "get_satellite_info") as lookup:
        for extra in [
            {"satellite_identifiers": []},
            {"satellite_identifiers": ["1"] * 5},
            {"duration": "25 hours"},
            {"duration": "0 seconds"},
            {"display_step_seconds": 1},
            {
                "display_step_seconds": 30,
                "duration": "24 hours",
                "satellite_identifiers": ["1", "2", "3"],
            },
        ]:
            response = call(
                "visualize",
                {"view": "orbit", "satellite_identifiers": ["25544"], **extra},
            )
            assert response.is_error
        lookup.assert_not_called()


def test_orbit_tool_does_not_substitute_a_satellite_or_partial_trajectory():
    arguments = {
        "view": "orbit",
        "satellite_identifiers": ["ISS"],
        "start_time": "2021-06-05T07:00:00Z",
    }
    with mock.patch.object(
        server.celestrak_client,
        "get_satellite_info",
        side_effect=ValueError("Ambiguous satellite"),
    ):
        response = call("visualize", arguments)
    assert response.is_error
    assert not any(c.type == "resource" for c in response.content)
    with mock.patch.object(
        server.celestrak_client, "get_satellite_info", return_value=CATALOG_SATELLITE
    ):
        with mock.patch.object(
            server, "sample_orbit", side_effect=ValueError("Propagation failed")
        ):
            response = call("visualize", arguments)
        duplicate = call(
            "visualize", {**arguments, "satellite_identifiers": ["ISS", "25544"]}
        )
    assert response.is_error
    assert duplicate.is_error
    assert not any(c.type == "resource" for c in response.content)


def test_visualize_rejects_missing_or_mixed_view_inputs_before_computation():
    with mock.patch.object(
        server, "compute_coverage_analysis"
    ) as compute, mock.patch.object(
        server.celestrak_client, "get_satellite_info"
    ) as lookup:
        for arguments in [
            {"view": "coverage"},
            {"view": "orbit"},
            {"view": "example", "scenario": scenario()},
            {"view": "coverage", "scenario": scenario(), "duration": "1 hour"},
            {"view": "orbit", "scenario": scenario(), "satellite_identifiers": ["ISS"]},
        ]:
            response = call("visualize", arguments)
            assert response.is_error
            assert not any(c.type == "resource" for c in response.content)
        compute.assert_not_called()
        lookup.assert_not_called()


def test_unsupported_duration_returns_requested_value_and_stops_before_catalog_lookup():
    with mock.patch.object(server.celestrak_client, "get_satellite_info") as lookup:
        response = call(
            "visualize",
            {
                "view": "orbit",
                "satellite_identifiers": ["ISS"],
                "duration": "7 days",
            },
        )
        lookup.assert_not_called()
    assert response.is_error
    message = " ".join(c.text for c in response.content if c.type == "text")
    assert "7 days" in message
    assert "24 hours" in message
    assert "Do not shorten or retry" in message


def test_data_workspace_preserves_values_and_does_not_compute_orbits():
    dataset = {
        "title": "Comparison",
        "source": "User supplied test values",
        "labels": ["A", "B"],
        "series": [{"name": "Revisit", "values": [3.5, None]}],
        "unit": "hours",
        "kind": "bar",
        "provenance": {"kind": "user"},
    }
    with mock.patch.object(
        server, "compute_coverage_analysis"
    ) as compute, mock.patch.object(
        server.celestrak_client, "get_satellite_info"
    ) as lookup:
        response = call(
            "visualize", {"view": "data", "dataset": dataset, "presentation": "table"}
        )
    assert not response.is_error
    compute.assert_not_called()
    lookup.assert_not_called()
    data = scene(
        next(c.resource.text for c in response.content if c.type == "resource")
    )
    assert data["dataset"]["series"][0]["values"] == [3.5, None]
    assert data["ui"]["presentation"] == "table"
    assert "tracks" not in data


def test_workspace_rerun_retains_identity_and_editable_request():
    from uuid import uuid4

    workspace_id = str(uuid4())
    response = call(
        "visualize",
        {
            "view": "coverage",
            "scenario": scenario(),
            "presentation": "timeline",
            "workspace_id": workspace_id,
        },
    )
    assert not response.is_error
    data = scene(
        next(c.resource.text for c in response.content if c.type == "resource")
    )
    assert data["ui"]["workspace_id"] == workspace_id
    assert data["ui"]["presentation"] == "timeline"
    assert data["request"]["view"] == "coverage"
    assert CoverageRequest.model_validate(
        data["request"]["scenario"]
    ) == CoverageRequest.model_validate(scenario())


@pytest.mark.parametrize(
    "change",
    [
        {"series": [{"name": "A", "values": [1]}]},
        {"series": [{"name": "A", "values": [1, float("inf")]}]},
        {"series": [{"name": "A", "values": [1, 2]}, {"name": "A", "values": [3, 4]}]},
        {"labels": ["a"] * 2001},
        {"source": "x" * 501},
    ],
)
def test_research_dataset_rejects_inconsistent_or_unsupported_values(change):
    from src.research_data import ResearchData
    from pydantic import ValidationError

    data = {
        "title": "Study",
        "source": "Supplied values",
        "kind": "bar",
        "provenance": {"kind": "user"},
        "labels": ["a", "b"],
        "series": [{"name": "A", "values": [1, None]}],
    }
    with pytest.raises(ValidationError):
        ResearchData.model_validate({**data, **change})


def test_incompatible_presentations_are_errors_not_substituted_scenes():
    for arguments in [
        {"view": "orbit", "satellite_identifiers": ["ISS"], "presentation": "timeline"},
        {
            "view": "data",
            "dataset": {
                "title": "Study",
                "source": "Supplied",
                "kind": "bar",
                "provenance": {"kind": "user"},
                "labels": ["a"],
                "series": [{"name": "A", "values": [1]}],
            },
            "presentation": "simulation3d",
        },
    ]:
        with mock.patch.object(server.celestrak_client, "get_satellite_info") as lookup:
            result = call("visualize", arguments)
        lookup.assert_not_called()
        assert result.is_error
        assert not any(c.type == "resource" for c in result.content)


@pytest.mark.parametrize("attribution", [{}, {"source": ""}])
def test_prompt_supplied_plot_values_need_no_source_label(attribution):
    # Reported LibreChat calls, plus the now-required kind and provenance.
    dataset = {
        "title": "Revisit Times",
        "unit": "hours",
        "x_label": "Design",
        "labels": ["Design A", "Design B"],
        "series": [{"name": "Revisit Time", "values": [3.5, 1.2]}],
        **attribution,
        "kind": "bar",
        "provenance": {"kind": "user"},
    }
    with mock.patch.object(
        server, "compute_coverage_analysis"
    ) as compute, mock.patch.object(
        server.celestrak_client, "get_satellite_info"
    ) as lookup:
        response = call("visualize", {"view": "data", "dataset": dataset})
    assert not response.is_error
    compute.assert_not_called()
    lookup.assert_not_called()
    data = scene(
        next(c.resource.text for c in response.content if c.type == "resource")
    )
    assert data["dataset"]["series"][0]["values"] == [3.5, 1.2]
    assert data["dataset"]["kind"] == "bar"
    assert data["dataset"]["source"] == ""


def text(response):
    return " ".join(c.text for c in response.content if c.type == "text")


def test_example_text_carries_exact_results_and_reshow_call():
    # LibreChat keeps only text content; the model must see the study itself.
    response = call("visualize", {"view": "example"})
    summary = text(response)
    analysis_id = response.structured_content["analysis_id"]
    assert f"analysis_id={analysis_id}" in summary
    assert "2026-10-07T00:00:00Z to 2026-10-08T00:00:00Z UTC" in summary
    assert '"right_ascension_ascending_node": 45' in summary
    assert 'Study sensor {"field_of_regard": 100}' in summary
    assert "Svalbard (lat 78.2232, lon 15.6469" in summary
    for count, gap in [(3, "791.327"), (14, "694.104"), (1, "858.983")]:
        assert f"{count} observations" in summary
        assert f"longest gap {gap} min" in summary
    assert "mean complete revisit none (no complete revisit)" in summary
    assert "Scope: access opportunities at the listed study points only" in summary
    assert "area heatmap, swath, illumination mask" in summary
    assert "footprint and boresight of pointed instruments only" in summary
    assert "Satellites (2; name each in your reply):" in summary
    assert "only if the user later asks for another view" in summary
    assert "call visualize(view='example', presentation=<one>)" in summary
    assert "Do not describe this to the user" in summary
    assert "table, map2d" not in summary
    assert "coverage_intervals" not in summary and '"gaps"' not in summary
    assert len(summary) < 3000


def pointed_scenario():
    study = scenario()
    study["satellites"][0]["instruments"] = [
        {
            "name": "Imager",
            "field_of_regard": 120,
            "cross_track_field_of_view": 20,
            "along_track_field_of_view": 5,
            "roll_angle": 15,
            "is_rectangular": True,
            "req_target_sunlit": True,
        },
        {"name": "Sounder", "field_of_regard": 120},
    ]
    return study


def test_pointed_instruments_are_described_and_drawn_from_tatc_footprints():
    response = call("visualize", {"view": "coverage", "scenario": pointed_scenario()})
    assert not response.is_error
    summary = text(response)
    assert '"roll_angle": 15' in summary and '"req_target_sunlit": true' in summary
    data = scene(
        next(c.resource.text for c in response.content if c.type == "resource")
    )
    track = data["tracks"][0]
    # Only the pointed instrument has a footprint, one per display sample.
    [footprint] = track["footprints"]
    assert footprint["instrument"] == "Imager"
    assert len(footprint["parts"]) == len(footprint["centers"]) == len(track["samples"])
    ring = footprint["parts"][0][0]
    assert len(ring) == 9 and ring[0] == ring[-1]
    # A 15° roll moves the footprint off the equatorial ground track.
    assert all(abs(lat) > 0.5 for _, lat in footprint["centers"])


def test_pointing_is_part_of_the_scenario_and_its_analysis_id():
    study = pointed_scenario()
    first = analyze(CoverageRequest.model_validate(study))
    assert first["scenario"]["satellites"][0]["instruments"][0]["roll_angle"] == 15
    study["satellites"][0]["instruments"][0]["roll_angle"] = -15
    second = analyze(CoverageRequest.model_validate(study))
    assert first["analysis_id"] != second["analysis_id"]


def test_footprint_samples_are_limited_with_an_actionable_message():
    study = pointed_scenario()
    study["satellites"] = [
        {**deepcopy(study["satellites"][0]), "name": f"S{i}"} for i in range(4)
    ]
    for sat in study["satellites"]:
        sat["instruments"][1] = {**sat["instruments"][0], "name": "Second"}
    study["end_time"] = study["start_time"].replace("T00", "T22")
    study["display_step_seconds"] = 30
    response = call("visualize", {"view": "coverage", "scenario": study})
    assert response.is_error
    assert "increase display_step_seconds" in text(response)


def test_earlier_study_is_reshown_by_analysis_id_without_scenario():
    first = call("visualize", {"view": "coverage", "scenario": scenario()})
    analysis_id = first.structured_content["analysis_id"]
    assert (
        f"visualize(view='coverage', analysis_id='{analysis_id}', presentation=<one>)"
        in text(first)
    )
    response = call(
        "visualize",
        {"view": "coverage", "analysis_id": analysis_id, "presentation": "table"},
    )
    assert not response.is_error
    assert response.structured_content["analysis_id"] == analysis_id
    data = scene(
        next(c.resource.text for c in response.content if c.type == "resource")
    )
    assert data["ui"]["presentation"] == "table"
    assert CoverageRequest.model_validate(
        data["request"]["scenario"]
    ) == CoverageRequest.model_validate(scenario())
    numerical = call("analyze_coverage", {"scenario": scenario()})
    assert numerical.structured_content["analysis_id"] == analysis_id


def test_unknown_or_mixed_study_references_are_actionable_errors():
    with mock.patch.object(server, "compute_coverage_analysis") as compute:
        for arguments, message in [
            ({"view": "coverage", "analysis_id": "0ec194da93"}, "full scenario"),
            ({"view": "example", "analysis_id": "0ec194da93"}, "view='example'"),
            (
                {"view": "coverage", "analysis_id": "x", "scenario": scenario()},
                "not both",
            ),
            # Observed Scout loop: the UI marker ID sent as workspace_id.
            (
                {
                    "view": "example",
                    "presentation": "map2d",
                    "workspace_id": "0ec194da93",
                },
                "without workspace_id",
            ),
        ]:
            response = call("visualize", arguments)
            assert response.is_error
            assert message in text(response)
        compute.assert_not_called()


def test_visualize_schema_has_no_opaque_id_or_range_constraints():
    async def inspect():
        async with Client(server.mcp) as client:
            tools = await client.list_tools()
            return next(t for t in tools if t.name == "visualize")

    properties = asyncio.run(inspect()).inputSchema["properties"]
    for name in ("workspace_id", "analysis_id", "display_step_seconds"):
        assert "format" not in json.dumps(properties[name])
        assert "minimum" not in json.dumps(properties[name])


def test_study_limits_are_actionable_errors_not_schema_constraints():
    # Observed G04: an oversized constellation failed LibreChat schema checks opaquely.
    async def inspect():
        async with Client(server.mcp) as client:
            return {t.name: t.inputSchema for t in await client.list_tools()}

    schemas = json.dumps(asyncio.run(inspect()))
    assert "maxItems" not in json.dumps(
        CoverageRequest.model_json_schema()["properties"]
    )
    assert '"format": "uuid"' not in schemas
    data = scenario()
    data["satellites"] = [
        dict(data["satellites"][0], name=f"Sat {i}") for i in range(5)
    ]
    for name in ("analyze_coverage", "visualize"):
        arguments = {"scenario": data}
        if name == "visualize":
            arguments["view"] = "coverage"
        response = call(name, arguments)
        assert response.is_error
        assert "1–4 satellites" in text(response)
        assert "ask the user" in text(response)


def test_descriptions_name_unsupported_studies_and_exact_orbit_fields():
    async def inspect():
        async with Client(server.mcp) as client:
            return {t.name: t.description for t in await client.list_tools()}

    descriptions = asyncio.run(inspect())
    for name in ("visualize", "analyze_coverage"):
        assert "Not supported, so explain instead of calling" in descriptions[name]
        assert "right_ascension_ascending_node" in descriptions[name]


def example_chart(series, labels=("Phoenix", "Svalbard", "Equatorial study point")):
    analysis_id = call("visualize", {"view": "example"}).structured_content[
        "analysis_id"
    ]
    return call(
        "visualize",
        {
            "view": "data",
            "dataset": {
                "title": "Example study",
                "kind": "bar",
                "provenance": {"kind": "study", "analysis_id": analysis_id},
                "labels": list(labels),
                "series": series,
            },
        },
    )


def test_study_charts_must_copy_the_studys_metrics():
    # R06: observation counts and (rounded) longest gaps of the example.
    response = example_chart(
        [
            {"name": "Observations", "values": [3, 14, 1]},
            {"name": "Longest gap (min)", "values": [791.32, 694.11, 858.98]},
            {"name": "Mean revisit (min)", "values": [466.297, 51.797, None]},
        ]
    )
    assert not response.is_error, text(response)
    # G04: invented values presented as study results.
    for series, labels, message in [
        ([{"name": "Revisit", "values": [10, 1000000, 5]}], None, "does not match"),
        (
            [{"name": "Observations", "values": [3, 14, 1]}],
            ["A", "B", "C"],
            "point names",
        ),
    ]:
        response = example_chart(series, *([labels] if labels else []))
        assert response.is_error
        assert message in text(response)


def test_data_view_requires_kind_and_provenance_with_actionable_errors():
    dataset = {
        "title": "Plot",
        "labels": ["a"],
        "series": [{"name": "A", "values": [1]}],
    }
    for change, message in [
        ({"provenance": {"kind": "user"}}, "kind is required: 'line' for a line chart"),
        ({"kind": "line"}, "provenance is required"),
        ({"kind": "line", "provenance": {"kind": "study"}}, "provenance is required"),
        (
            {"kind": "line", "provenance": {"kind": "study", "analysis_id": "nope"}},
            "No study with analysis_id 'nope'",
        ),
    ]:
        response = call("visualize", {"view": "data", "dataset": {**dataset, **change}})
        assert response.is_error
        assert message in text(response)


def test_scenario_may_arrive_as_a_json_string():
    # Observed G04: the model JSON-encoded the scenario.
    for name, extra in [("analyze_coverage", {}), ("visualize", {"view": "coverage"})]:
        response = call(name, {"scenario": json.dumps(scenario()), **extra})
        assert not response.is_error, text(response)
    response = call("analyze_coverage", {"scenario": "{not json"})
    assert response.is_error
    assert "not valid JSON" in text(response)


def test_malformed_scenarios_reach_the_server_and_get_actionable_errors():
    nested = scenario()
    nested["satellites"][0]["points"] = nested.pop("points")
    nested["satellites"][0]["start_time"] = nested.pop("start_time")
    with mock.patch.object(server, "compute_coverage_analysis") as compute:
        for change, message in [
            # Observed N03: a relative date and no satellites.
            ({"start_time": "tomorrow"}, "resolve relative dates such as 'tomorrow'"),
            ({"satellites": []}, "No satellites or sensors were supplied"),
            (None, "top-level keys are satellites, points, start_time, end_time"),
            (
                {"points": [{"name": "P", "latitude": 91, "longitude": 0}]},
                "latitude must be from -90 to 90",
            ),
        ]:
            data = nested if change is None else {**scenario(), **change}
            response = call("analyze_coverage", {"scenario": data})
            assert response.is_error
            assert message in text(response)
        compute.assert_not_called()


def test_tool_schemas_leave_constraints_to_server_validation():
    async def inspect():
        async with Client(server.mcp) as client:
            return {t.name: t.inputSchema for t in await client.list_tools()}

    schemas = asyncio.run(inspect())

    def keys(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key != "properties":
                    yield key
                yield from keys(item)
        elif isinstance(value, list):
            for item in value:
                yield from keys(item)

    for name in ("visualize", "analyze_coverage"):
        found = set(keys(schemas[name]))
        assert not found & {
            "format",
            "minItems",
            "maxItems",
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "pattern",
        }, name
