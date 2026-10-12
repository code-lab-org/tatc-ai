# tatc-ai

Stub monorepo for an application stack with:
- a FastMCP-based MCP server
- a LibreChat client
- Docker-based development and deployment orchestration

## Repository layout

- `apps/mcp-server` - FastMCP server stub and Dockerfile
- `apps/librechat` - LibreChat environment stub
- `docker-compose.dev.yml` - local development stack with direct ports
- `docker-compose.deploy.yml` - deployment stack using image-based services, Traefik, and Dex
- `config/dex/start-dex.sh` - Dex startup script that renders config from environment variables

## MCP server

`.github/workflows/build-mcp-server.yml` builds and pushes the MCP server
image to `ghcr.io/code-lab-org/tatc-ai-mcp-server` on every push to `main`
that touches `apps/mcp-server/**`, tagged with both `latest` and the commit
SHA it was built from.

`docker-compose.deploy.yml` pulls `ghcr.io/code-lab-org/tatc-ai-mcp-server:${MCP_SERVER_IMAGE_TAG:-latest}`.
Leave `MCP_SERVER_IMAGE_TAG` unset (or `latest`) so [Auto-deploy](#auto-deploy)
picks up new images automatically. For a reproducible, rollback-able deploy
instead, set it in `.env` to a specific commit SHA - auto-deploy will then
keep redeploying that pinned image rather than moving off it.

To build the image locally instead (e.g. to test before pushing):

```bash
docker build -f apps/mcp-server/Dockerfile . -t ghcr.io/code-lab-org/tatc-ai-mcp-server:local
```

### Ground-track map

`generate_ground_track` returns telemetry (UTC time, latitude, longitude,
altitude in meters) and, with `include_footprint`, a TAT-C footprint per
point: the ground area inside a 60 degree cone under the satellite, as a
GeoJSON Polygon or a MultiPolygon split at the antimeridian.
`ground_track_map` takes the satellite, start time, duration, and step
interval, always includes footprints, and returns a short text summary plus
an interactive globe page (MapLibre) with a static Cartopy picture, which
LibreChat renders inline from a `ui://` resource. Both are drawn in
LibreChat's dark theme. On the globe, the Footprint box is checked by
default, Follow keeps the satellite centered while playing or scrubbing
until you move the map by hand, the drawn track follows great circles between
samples, and footprints are cut at 85 degrees latitude, the limit of the
globe's map tiles.

### Coverage estimates

Coverage tools estimate when one satellite, or a constellation of up to
six, could view a region. TAT-C does the modeling; OSCAR is used to find
instruments and is never silently used as geometry.

A run takes three calls. `begin_access_run` locks one satellite, or
several separated by commas, plus an explicit UTC window of up to 366
days, and returns a draft ID with each orbit's mean altitude.
`set_access_region` sets four separate numeric bounds on the draft.
`finish_access_run` takes grid size, exactly one of `field_of_regard_deg`
or `swath_width_km`, a required `geometry_reference` source, and an
optional `daylight_only` flag for optical imagers, then returns a result ID
with a compact summary. Windows of 3 days or less allow 144 cells; longer
windows allow 36 cells and 72 cells times satellites, and take minutes.

`resolve_instrument_geometry` parses an instrument's OSCAR spec text into
numeric candidates with provenance and a status of conclusive, ambiguous,
needs_review, or unknown. When it is not conclusive, the model asks the
researcher for a number with a source instead of guessing.
`convert_swath_to_field_of_regard` turns a swath width at an altitude into
an angle with the same TAT-C math the run uses.

`get_access_daily` pages through daily observed and gap areas,
`get_access_cells` returns the grid as GeoJSON with per-cell access counts
and revisit, filtered to all, observed, or gaps, and `get_access_revisit`
returns revisit numbers. Results and drafts expire after 24 hours. All
coverage is modeled access only, not actual acquisitions or clouds.

### Tool-calling eval

`apps/mcp-server/eval` checks how well Voyager models drive these tools.
It sends each model the server's real tools and instructions, runs their
calls against the real server with recorded CelesTrak and OSCAR
responses, and scores what the server computed:

```bash
cd apps/mcp-server
.venv/bin/python -m eval.run_eval --label mychange
.venv/bin/python -m eval.run_eval --compare eval/results/*.jsonl
```

By default it runs the models LibreChat offers; pass `--models` to try
others. It reads the proxy key from `apps/librechat/.env`, sends prompts
and tool schemas to the ASU proxy, and takes about half an hour.

The conversations are not streamed, but LibreChat streams. So each run
first sends every model one short streamed request and reports `stream:
ok`, `missing role`, or `no tool call`. LibreChat drops tool calls from a
stream whose first delta has no `role`. Models marked `disableStreaming` in
`librechat.yaml` (GLM-5.3 and DeepSeek) are noted rather than failed. `--stream-only`
runs just this check.

## Development stack

Runs with localhost/direct port access:
- MCP server: `http://localhost:8000`
- LibreChat: `http://localhost:3080`
- MongoDB: `localhost:27017`

Before starting the development stack, create the private LibreChat env file
and set `VOYAGER_API_KEY` to your ASU Research Computing key and
`CREATEAI_API_KEY` to your ASU CreateAI key. Keep the base URLs as given:

```bash
cp apps/librechat/.env.example apps/librechat/.env
chmod 600 apps/librechat/.env
# edit apps/librechat/.env and set VOYAGER_API_KEY and CREATEAI_API_KEY
```

```bash
docker compose -f docker-compose.dev.yml up --build
```

Ground-track requests are capped at 2,000 output points. For example, a
1-day request at 1-minute intervals produces 1,441 points.

Local UI is branded as TATC AI (browser tab, welcome message, footer).
All chat models come from ASU Voyager. TATC AI offers five of them: the
top Voyager models on the Artificial Analysis Intelligence Index that
passed the tool-calling eval. They are GLM-5.3, GLM-5.3-Flash, DeepSeek
V4.1 Flash, MiMo-V2.6-Flash, and Qwen 3.8 27B. New chats default to Qwen 3.8 27B
with TATC satellite tools pinned.
The ASU CreateAI endpoint is still configured alongside them.

The proxy serves more models, and any of them can be added. Add its ID to
the endpoint's model list and a `modelSpecs` entry in both
`apps/librechat/librechat.yaml` and `apps/librechat/librechat.deploy.yaml.template`,
then run the tool-calling eval with `--models` before offering it.

```bash
python3 apps/librechat/check_voyager_models.py
```

This lists offered models the proxy no longer serves, and proxy models
that are not offered. A weekly workflow runs it and fails, which emails
the repo owners, when an offered model disappears. It needs one repo
secret: VOYAGER_API_KEY holding an ASU proxy key.

## Deployment stack

Runs behind Traefik with automatic Let's Encrypt TLS and an OAuth IdP:
- `https://chat.<DOMAIN>` -> LibreChat
- `https://mcp.<DOMAIN>` -> MCP server
- `https://auth.<DOMAIN>` -> Dex

Both LibreChat and the MCP server authenticate end users against Dex via
OIDC. The MCP server enables this only when `MCP_OIDC_ISSUER_URL` (and the
related `MCP_OIDC_CLIENT_*`/`MCP_BASE_URL` vars) are set, which
`docker-compose.deploy.yml` does and `docker-compose.dev.yml` does not — so
the dev server stays unauthenticated. The `mcp-server` static client and its
redirect URI are pre-provisioned in `config/dex/start-dex.sh`.

All host names and secrets are read from environment files that are **not**
committed to the repository:

1. Point DNS for `<DOMAIN>`, `chat.<DOMAIN>`, `mcp.<DOMAIN>`, and
   `auth.<DOMAIN>` at the host's public IP, and open inbound 80/443 in its
   security group (80 is required for the Let's Encrypt HTTP challenge).
2. `cp .env.example .env` at the repo root and fill in `DOMAIN`, `ACME_EMAIL`,
   and freshly generated `DEX_*` secrets.
3. `cp apps/librechat/.env.example apps/librechat/.env` and replace the
   `JWT_SECRET`, `JWT_REFRESH_SECRET`, `CREDS_KEY`, and `CREDS_IV` placeholders
   with your own generated values (see the comments in that file), and set
   `VOYAGER_API_KEY` and `CREATEAI_API_KEY` to enable the ASU Voyager and
   ASU CreateAI model endpoints. Existing `.env` files that still use
   `OPENAI_API_KEY` / `OPENAI_REVERSE_PROXY` must rename them to
   `VOYAGER_API_KEY` / `VOYAGER_BASE_URL`.
4. `ghcr.io/code-lab-org/tatc-ai-mcp-server` is a private package. Log the
   host in once with a GitHub PAT (`read:packages` scope, from an account
   with read access to this repo):

   ```bash
   echo '<PAT>' | docker login ghcr.io -u <github-username> --password-stdin
   ```

5. Run:

   ```bash
   docker compose -f docker-compose.deploy.yml up -d
   ```

docker compose reads `.env` from the repo root automatically to fill in
`${DOMAIN}` etc. throughout `docker-compose.deploy.yml`. The compose file also
aliases `auth.<DOMAIN>` to the Traefik container on the internal Docker
network, so OIDC clients inside the stack and browsers outside it resolve the
same issuer URL without depending on outbound internet DNS.

## Auto-deploy

`.github/workflows/deploy.yml` redeploys the EC2 instance over SSH:
- on every push to `main` (picks up config/compose/Dex changes), and
- after `build-mcp-server.yml` finishes pushing a new image (picks up mcp-server
  code changes, after the image is actually available in GHCR rather than
  racing it).

Both triggers run the same thing: `config/deploy/deploy.sh` on the instance,
which does `git merge --ff-only` then `docker compose -f
docker-compose.deploy.yml pull && ... up -d`. It fails loudly rather than
overwriting anything if the checkout has diverged from `main`.

Because GitHub-hosted runners don't have a small, stable IP range, this means
opening inbound port 22 on the instance broadly rather than allowlisting
GitHub. To keep that safe, the deploy key is restricted with a forced
command so it can never be used for anything but running that one script,
even though the workflow only ever asks it to:

1. On the EC2 instance, in the deploy user's `~/.ssh/authorized_keys`
   (that user needs passwordless `docker`/`docker compose` access and a
   checkout of this repo with `.env` and `apps/librechat/.env` already in
   place):

   ```
   command="/path/to/repo/config/deploy/deploy.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA...
   ```

2. Generate a dedicated deploy keypair (don't reuse a personal key) and add
   the public half as shown above:

   ```bash
   ssh-keygen -t ed25519 -f deploy_key -N "" -C "tatc-ai-deploy"
   ```

3. Capture the instance's host key from a trusted channel (e.g. the EC2
   console's system log, or a `ssh-keyscan` run over a connection you already
   trust) - don't just accept whatever a first connection presents, since
   that defeats host verification entirely:

   ```bash
   ssh-keyscan -H <host> > known_hosts
   ```

4. In the repo's GitHub Actions settings, add:
   - Repository variables: `DEPLOY_SSH_HOST`, `DEPLOY_SSH_USER`
   - Repository secrets: `DEPLOY_SSH_KEY` (private half of `deploy_key`),
     `DEPLOY_SSH_KNOWN_HOSTS` (contents of `known_hosts`)
