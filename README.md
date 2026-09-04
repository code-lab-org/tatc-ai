# tat-ai

Stub monorepo for an application stack with:
- a FastMCP-based MCP server
- a LibreChat client
- Docker-based development and deployment orchestration

## Repository layout

- `apps/mcp-server` - FastMCP server stub and Dockerfile
- `apps/librechat` - LibreChat environment stub
- `docker-compose.dev.yml` - local development stack with direct ports
- `docker-compose.deploy.yml` - deployment stack using image-based services, Traefik, and Dex
- `config/dex/config.yaml` - Dex IdP config

## MCP server

Build the MCP server image:

```bash
docker build -f apps/mcp-server/Dockerfile . -t ghcr.io/code-lab-org/tat-ai-mcp-server:latest
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
  - `http://chat.localtest.me` -> LibreChat
  - `http://mcp.localtest.me` -> MCP server
  - `http://auth.localtest.me` -> Dex

Run:

```bash
docker compose -f docker-compose.deploy.yml up -d
```
