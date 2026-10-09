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

## Development and checks

- Match the Python minor version in `apps/mcp-server/Dockerfile` for local server work and checks.
- With `apps/mcp-server/requirements.txt` and `pytest` installed, run `python -m pytest apps/mcp-server/tests` from the repository root.
- Pull requests that change `apps/mcp-server/**` or `.github/workflows/test-mcp-server.yml` preload Cartopy's 110m coastline, then run tests with fixed reference cases.
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
- Each successful MCP image build on `main` triggers another deployment after publication.
