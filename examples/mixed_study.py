#!/usr/bin/env python3
"""Run one mixed AMSR2 and Sentinel-1C study through the running MCP server.

This is a software demonstration, not a mission-accuracy validation. The
reviewed configurations have fixed source intervals, so the example asks for
an explicit applicability assumption when the current window is outside them.

Run with the server environment:

    apps/mcp-server/.venv312/bin/python examples/mixed_study.py \
        --base-url http://localhost:8000/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime, timedelta, timezone

from fastmcp import Client

AMSR2 = {
    "configuration_id": "amsr2-gcom-w1-conical-product-sector",
    "configuration_revision": "2026-10-27.1",
}
SENTINEL = {
    "configuration_id": "sentinel-1c-c-sar-iw-fixed-view",
    "configuration_revision": "2026-10-30.1",
}


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


async def call(client: Client, name: str, arguments: dict) -> dict:
    result = await client.call_tool(name, arguments, raise_on_error=False)
    text = "\n".join(getattr(part, "text", "") for part in result.content)
    if result.is_error:
        raise RuntimeError(f"{name}: {text}")
    return json.loads(text)


async def run(base_url: str, hours: int, grid: int) -> None:
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(hours=hours)
    assumptions = [
        {
            "configuration_id": item["configuration_id"],
            "start_utc": iso(start),
            "end_utc_exclusive": iso(end),
            "rationale": (
                "Runnable software example only. The reviewed source interval "
                "does not cover this window, and this assumption is not a "
                "mission-applicability claim."
            ),
        }
        for item in (AMSR2, SENTINEL)
    ]

    async with Client(base_url) as client:
        draft = await call(
            client,
            "begin_access_run",
            {
                "satellite_identifier": "GCOM-W1, SENTINEL-1C",
                "start_time": iso(start),
                "end_time": iso(end),
            },
        )
        await call(
            client,
            "set_access_region",
            {
                "draft_id": draft["draft_id"],
                "west_lon": -65.0,
                "south_lat": 0.0,
                "east_lon": -55.0,
                "north_lat": 10.0,
            },
        )

        prepare_args = {
            "draft_id": draft["draft_id"],
            "grid_rows": grid,
            "grid_columns": grid,
            "configurations": [AMSR2, SENTINEL],
            "applicability_assumptions": assumptions,
        }
        try:
            proposal = await call(client, "prepare_mixed_study", prepare_args)
        except RuntimeError as exc:
            if "adds no modeled time" not in str(exc):
                raise
            prepare_args.pop("applicability_assumptions")
            proposal = await call(client, "prepare_mixed_study", prepare_args)

        print("Proposed configurations:")
        for item in proposal["configurations"]:
            print(
                f"  {item['configuration_id']} "
                f"revision {item['configuration_revision']} "
                f"direction {item.get('orbit_direction_filter') or 'both'}"
            )
        print(f"Completeness: {proposal['completeness_status']}")
        print(f"Setup revision: {proposal['setup_revision']}")

        summary = await call(
            client,
            "finish_mixed_study",
            {
                "draft_id": draft["draft_id"],
                "accepted_setup_revision": proposal["setup_revision"],
                "accept_setup": True,
            },
        )
        result_id = summary["result_id"]
        print(f"\nResult ID: {result_id}")
        print(f"Combined events: {summary['summary_counts']['event_count']}")
        for item in summary["configuration_summaries"]:
            print(
                f"  {item['configuration_id']}: "
                f"{item['summary_counts']['event_count']} events"
            )

        daily = await call(
            client, "get_mixed_daily", {"result_id": result_id, "limit": 5}
        )
        print("\nFirst daily rows:")
        for row in daily["daily"][:5]:
            print(
                f"  {row['utc_date']} status={row['coverage_status']} "
                f"area_km2={row['observed_area_km2']}"
            )

        events = await call(
            client,
            "get_mixed_events",
            {"result_id": result_id, "limit": 5},
        )
        print("\nFirst combined events:")
        for event in events["events"]:
            print(
                f"  {event['epoch_utc']} cell={event['cell_id']} "
                f"direction={event['orbit_direction']}"
            )

        link = await call(client, "export_study", {"result_id": result_id})
        print("\nExport file:")
        print(f"  {link['file_name']}")
        print(f"  {link['download_url']}")
        print("Save it before the result expires.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-url",
        default="http://localhost:8000/mcp",
        help="MCP streamable-http endpoint",
    )
    parser.add_argument("--hours", type=int, default=1, help="study window length")
    parser.add_argument("--grid", type=int, default=2, help="square grid size")
    args = parser.parse_args()
    asyncio.run(run(args.base_url, args.hours, args.grid))


if __name__ == "__main__":
    main()
