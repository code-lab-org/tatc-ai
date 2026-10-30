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

`.github/workflows/deploy.yml` builds and pushes the MCP server image to
`ghcr.io/code-lab-org/tatc-ai-mcp-server` on every push to `main` that
touches `apps/mcp-server/**`, tagged with both `latest` and the commit SHA
it was built from, then deploys it (see [Auto-deploy](#auto-deploy)).

`docker-compose.deploy.yml` pulls `ghcr.io/code-lab-org/tatc-ai-mcp-server:${MCP_SERVER_IMAGE_TAG:-latest}`.
Leave `MCP_SERVER_IMAGE_TAG` unset (or `latest`) so [Auto-deploy](#auto-deploy)
picks up new images automatically. For a reproducible, rollback-able deploy
instead, set it in `.env` to a specific commit SHA - auto-deploy will then
keep redeploying that pinned image rather than moving off it.

To build the image locally instead (e.g. to test before pushing):

```bash
docker build -f apps/mcp-server/Dockerfile . -t ghcr.io/code-lab-org/tatc-ai-mcp-server:local
```

See the [tool guide](docs/tools.md) for ground-track maps, legacy coverage estimates, and event-based conical and pointed-instrument studies.

### Tool-calling eval

The eval checks how well Voyager models use the MCP tools. The server image
uses Python 3.12; run tests and evals with a matching environment.

```bash
cd apps/mcp-server
.venv/bin/python -m eval.run_eval --label mychange
```

See the [evaluation guide](docs/evaluation.md) for API key setup, comparison runs, and streaming checks.

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

### Configured models

LibreChat is configured with these model labels:

- **ASU Voyager:** GLM-5.3, GLM-5.3-Flash, DeepSeek V4.1 Flash, MiMo-V2.6-Flash, Qwen 3.8 27B.
- **ASU CreateAI:**
  - Nova 2 Lite, Nova Lite, Nova Micro, Nova Pro.
  - Claude Haiku 4.5, Claude Sonnet 4, Claude Sonnet 4.5, Claude Sonnet 5, Claude Opus 4.1, Claude Opus 4.5, Claude Opus 4.6, Claude Opus 4.7, Claude Opus 4.8, Claude Opus 5.
  - Gemini 2.5 Flash, Gemini 2.5 Flash-Lite, Gemini 3 Flash, Gemini 3.1 Flash-Lite, Gemini 3.1 Pro, Gemini 3.5 Flash, Gemini 3.7 Flash.
  - Gemma 4 31B IT, Llama 4 Maverick 17B, Llama 4 Scout 17B, Muse Glimmer 30B.
  - GPT-OSS 20B, GPT-OSS 120B, GPT-4o mini, GPT-4.1, GPT-4.1 mini, o3, GPT-5, GPT-5 mini, GPT-5 nano, GPT-5.1, GPT-5.1 Instant, GPT-5.1 Thinking, GPT-5.2, GPT-5.2 Pro, GPT-5.4 mini, GPT-5.4 nano, GPT-5.4 Thinking, GPT-5.4 Pro, GPT-5.5, GPT-5.5 Thinking, GPT-5.5 Pro, GPT-5.6 Luna, GPT-5.6 Sol, GPT-5.6 Terra, GPT-6 Astra.

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

Every push to `main`, including documentation changes, deploys to EC2 and restarts LibreChat.
When MCP server inputs change, the workflow builds and publishes the image before deploying.
A failed build prevents deployment. Manual runs and pushes that cannot be diffed always rebuild.

The deploy runs `config/deploy/deploy.sh` on the instance,
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
