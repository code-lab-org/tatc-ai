# Repository guidance

Read `README.md` before changing the stack or its deployment setup.

## Code map

- `apps/mcp-server/src/` contains MCP tools, input validation, TAT-C integration, data formatting, and map rendering.
- `apps/mcp-server/tests/` covers server behavior and offline unit checks.
- `apps/librechat/librechat.yaml` configures the development LibreChat upstream image.
- `apps/librechat/librechat.deploy.yaml.template` supplies deployment settings rendered into that upstream image at startup.
- Keep shared LibreChat model and tool settings consistent across both files. Preserve the URLs and authentication settings for each environment.
- `docker-compose.dev.yml` and `docker-compose.deploy.yml` define the development and deployment stacks.
- `config/` contains Dex and EC2 deployment scripts. `docs/` contains research notebooks.

## TAT-C first

This server is a model-facing interface to TAT-C, not a reimplementation of it.

- Before writing orbit, access, coverage, revisit, swath, illumination, or grid math, check the installed `tatc` package (`tatc.analysis`, `tatc.generation`, `tatc.utils`, `tatc.schemas`) and call it when it covers the need.
- Do not add fallback formulas for when TAT-C is missing. It is a hard dependency.
- Our code may stage inputs, validate them, store results, and reshape TAT-C output into what the tools return. When it must compute something TAT-C lacks, such as per-UTC-day area rows, say so in a docstring.
- Keep tools general: no satellite, instrument, or region defaults in tool code. Viewing geometry comes from the researcher or the OSCAR resolver with a source.
- When changing tool names, arguments, descriptions, or server instructions, run `python -m eval.run_eval` from `apps/mcp-server` and compare it with the last run using `--compare`. It needs the ASU key in `apps/librechat/.env`, sends prompts and tool schemas to the ASU proxy, and takes about half an hour. Unit tests do not show whether models can still use the tools.

## Development and checks

- Match the Python minor version in `apps/mcp-server/Dockerfile` for local server work and checks.
- With `apps/mcp-server/requirements.txt` and `pytest` installed, run `python -m pytest apps/mcp-server/tests` from the repository root.
- Pull requests that change `apps/mcp-server/**` or `.github/workflows/test-mcp-server.yml` preload world-map coastline data, then run tests with fixed reference cases.
- Docs-only pull requests skip Python setup and tests while the workflow stays successful.
- Report checks that you could not run and the reason.
- Preserve UTC timestamps, latitude and longitude in degrees, altitude in meters, and GeoJSON coordinates in `[longitude, latitude]` order.
- Keep credentials in private local environment files. Never commit API keys, OIDC secrets, or generated credentials.
- When adding an environment setting, update the matching `.env.example` with a placeholder and safe setup instructions.

## Changes and deployment

- Make changes on a branch and use a pull request.
- Before opening each pull request, create or identify an issue that explains why the change is needed.
- Link the issue from every pull request. Use `Closes #number` when the pull request resolves it.
- Every push to `main`, including documentation changes, triggers an EC2 deployment and restarts LibreChat.
- Pushes that change `apps/mcp-server/**` rebuild the MCP image first and deploy only after it is published.
