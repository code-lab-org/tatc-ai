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
For a reproducible, rollback-able deploy, set `MCP_SERVER_IMAGE_TAG` in `.env`
to the specific commit SHA you want running rather than leaving it on
`latest`.

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
4. Run:

   ```bash
   docker compose -f docker-compose.deploy.yml up -d
   ```

docker compose reads `.env` from the repo root automatically to fill in
`${DOMAIN}` etc. throughout `docker-compose.deploy.yml`. The compose file also
aliases `auth.<DOMAIN>` to the Traefik container on the internal Docker
network, so OIDC clients inside the stack and browsers outside it resolve the
same issuer URL without depending on outbound internet DNS.
