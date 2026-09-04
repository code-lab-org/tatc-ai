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
docker build -f apps/mcp-server/Dockerfile .
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

Runs with reverse proxy and OAuth IdP:
- Traefik routes:
  - `http://chat.<BASE_DOMAIN>` -> LibreChat
  - `http://mcp.<BASE_DOMAIN>` -> MCP server
  - `http://auth.<BASE_DOMAIN>` -> Dex

Set `BASE_DOMAIN` (default: `localtest.me`) and run:

```bash
BASE_DOMAIN=localtest.me docker compose -f docker-compose.deploy.yml up -d
```
