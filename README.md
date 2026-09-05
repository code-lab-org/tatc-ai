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

## Development stack

Runs with localhost/direct port access:
- MCP server: `http://localhost:8000`
- LibreChat: `http://localhost:3080`
- MongoDB: `localhost:27017`

```bash
docker compose -f docker-compose.dev.yml up --build
```

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
   with your own generated values (see the comments in that file).
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
