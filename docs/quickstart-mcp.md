# MCP quickstart

The server speaks MCP over streamable HTTP. In development it is unauthenticated at `http://localhost:8000/mcp`. In deployment it is at `https://mcp.<DOMAIN>/mcp` behind OIDC.

## Connect a client

Most MCP clients accept a URL entry. For example:

```json
{
  "mcpServers": {
    "tatc-ai": {
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

Use a client that supports streamable HTTP and, for the deployed server, OAuth. The development server needs no token.

A minimal Python client:

```python
import asyncio
from fastmcp import Client

async def main():
    async with Client("http://localhost:8000/mcp") as client:
        tools = await client.list_tools()
        print([tool.name for tool in tools])
        result = await client.call_tool(
            "search_satellites", {"query": "GCOM-W1"}
        )
        print(result.content[0].text)

asyncio.run(main())
```

Run it with the server environment:

```sh
apps/mcp-server/.venv312/bin/python your_client.py
```

## Run a mixed study

The study is staged. Each step returns the ID the next step needs.

1. `begin_access_run` with `satellite_identifier: "GCOM-W1, SENTINEL-1C"`, an explicit UTC `start_time`, and an exclusive `end_time`.
2. `set_access_region` with `west_lon`, `south_lat`, `east_lon`, `north_lat` in degrees.
3. `prepare_mixed_study` with the grid and exact reviewed configurations. Add `orbit_direction` to a selection to keep only ascending or descending passes.
4. Show the proposal to the researcher. Then `finish_mixed_study` with the exact `setup_revision` and `accept_setup: true`.
5. Fetch with `get_mixed_events`, `get_mixed_daily`, `get_mixed_cells`, `get_mixed_revisit`, or `get_mixed_event_spacing`.
6. Call `export_study` with the result ID for a downloadable JSON file.

A mixed preparation body:

```json
{
  "draft_id": "draft_…",
  "grid_rows": 2,
  "grid_columns": 2,
  "configurations": [
    {
      "configuration_id": "amsr2-gcom-w1-conical-product-sector",
      "configuration_revision": "2026-10-27.1",
      "orbit_direction": "descending"
    },
    {
      "configuration_id": "sentinel-1c-c-sar-iw-fixed-view",
      "configuration_revision": "2026-10-30.1"
    }
  ]
}
```

## Download the export

`export_study` returns a short-lived signed link:

```json
{
  "result_id": "…",
  "file_name": "tatc-study-….json",
  "download_url": "http://localhost:8000/study-export/…",
  "expires_at_utc": "…"
}
```

Download it without an extra login:

```sh
curl -L -o study.json "http://localhost:8000/study-export/…"
```

The link is valid for 15 minutes and works only for that result. Anyone with the link can download the file. The server keeps no archive after the result expires.

## Runnable example

The repository includes a complete client script:

```sh
apps/mcp-server/.venv312/bin/python examples/mixed_study.py \
  --base-url http://localhost:8000/mcp
```

It stages both sensors, prepares and accepts a one-hour study, fetches a small result page, and prints the export link. It uses an explicit applicability assumption because the reviewed configurations have fixed source intervals. Read the printed warnings. The example is a software demonstration, not a mission claim. It needs a reachable CelesTrak service for the current orbit elements. A rate-limit or read timeout stops before any calculation.
