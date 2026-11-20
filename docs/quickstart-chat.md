# Chat quickstart

This walks through one mixed-instrument study in LibreChat, from request to exported file. The example uses the two reviewed sensors: AMSR2 on GCOM-W1 and Sentinel-1C C-SAR in IW mode.

## Before you start

Start the development stack:

```sh
docker compose -f docker-compose.dev.yml up -d --build mcp-server librechat
```

Open `http://localhost:3080`, start a new conversation, and select a model. Refresh the page or reconnect the MCP server after a server update so the current tool schema loads.

## Ask for the study

Paste this into chat:

> Run a mixed access study over west=-65, south=0, east=-55, north=10 with a 2x2 grid for 24 hours starting 2026-10-04T00:00:00Z. Use AMSR2 on GCOM-W1 and Sentinel-1C C-SAR IW. Count only descending passes for AMSR2 and both directions for Sentinel-1C. Show me the sources, geometry, warnings, and limits before you run it.

The assistant should look up the exact configuration revisions, stage both spacecraft, and call `prepare_mixed_study`. It must show the proposal and wait for your acceptance. It should not run a calculation during preparation.

## Accept the proposal

Read the returned sources, applicable dates, geometry, approximations, warnings, and limits. If they match what you want, reply:

> I accept this exact setup revision. Run it.

The assistant calls `finish_mixed_study` with that exact revision. A changed grid, region, window, configuration, pass direction, or assumption requires a new proposal and a new acceptance.

## Inspect the results

Ask follow-up questions in plain language:

> Show the combined daily area and the AMSR2-only event count.

> Which cells had access from both sensors on the same epoch?

> Show the first five Sentinel-1C events.

The assistant uses `get_mixed_daily`, `get_mixed_events`, and `get_mixed_cells`. Keep pages small in chat. The finish summary has the full counts.

## Download the study

Ask:

> Export this study so I can keep the exact inputs and results.

The assistant calls `export_study` with the result ID and gives you a short-lived download link. The JSON file includes:

- The exact satellite orbit records.
- Both configuration snapshots, source evidence, and source choices.
- Applicability intervals, assumptions, approximations, and warnings.
- Software versions.
- Combined and per-sensor results.
- A replay block with the setup inputs and recorded orbit hash.

The link is valid for 15 minutes. Save the file if you need it later. The server has no durable archive. The replay block records the setup choices; a new run fetches current orbit elements, so compare the orbit hash with the recorded one.

## Change one thing and run again

Ask:

> Change only the grid to 3x3 and run the updated study.

The assistant prepares a new setup revision, shows the change, and waits for acceptance. The old result stays unchanged.

## What to watch for

- An omitted sensor has unknown access, not zero access.
- A modeled sensor with no events has zero events over its modeled interval.
- Cell-center sampling does not mean the whole cell was viewed.
- A field-of-regard envelope is not continuous viewing time.
- The pass-direction filter is a study setting. It is recorded in the setup and result.
- The study is modeled geometric access. It is not an acquisition schedule, cloud-free imagery, or a scientific retrieval product.
