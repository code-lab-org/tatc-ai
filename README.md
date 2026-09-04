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

Build the MCP server image:

```bash
docker build -f apps/mcp-server/Dockerfile . -t ghcr.io/code-lab-org/tatc-ai-mcp-server:latest
```

For deployment, push this image (or update `docker-compose.deploy.yml` with your own published image tag).

## Development stack

Runs with localhost/direct port access:
- MCP server: `http://localhost:8000`
- LibreChat: `http://localhost:3080`
- MongoDB: `localhost:27017`

```bash
docker compose -f docker-compose.dev.yml up --build
```

## Deployment stack

Runs with reverse proxy and OAuth IdP:
- Traefik routes:
  - `https://chat.localtest.me` -> LibreChat
  - `https://mcp.localtest.me` -> MCP server
  - `https://auth.localtest.me` -> Dex

Run:

```bash
export DEX_LIBRECHAT_CLIENT_SECRET=replace-me
export DEX_MCP_CLIENT_SECRET=replace-me
export DEX_DEV_PASSWORD_HASH=replace-me-with-bcrypt-hash
docker compose -f docker-compose.deploy.yml up -d
```

The deployment compose file aliases `auth.localtest.me` to the Traefik container on the internal network so OIDC clients and browser traffic use the same issuer URL.
