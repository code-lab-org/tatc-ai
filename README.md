# TATC AI

TATC AI provides satellite search, ground-track maps, and modeled coverage estimates through a FastMCP server and LibreChat. See the [tool guide](docs/tools.md) for details.

## Repository layout

- [`apps/mcp-server/`](apps/mcp-server/) contains the MCP server and its Dockerfile.
- [`apps/librechat/`](apps/librechat/) contains LibreChat configuration and environment templates.
- [`docker-compose.dev.yml`](docker-compose.dev.yml) runs the local stack.
- [`docker-compose.deploy.yml`](docker-compose.deploy.yml) runs the production stack.
- [`config/`](config/) contains Dex and EC2 deployment scripts.

## Configured models

LibreChat is configured with these model labels:

- **ASU Voyager:** GLM-5.3, GLM-5.3-Flash, DeepSeek V4.1 Flash, MiMo-V2.6-Flash, Qwen 3.8 27B.
- **ASU CreateAI:**
  - Nova 2 Lite, Nova Lite, Nova Micro, Nova Pro.
  - Claude Haiku 4.5, Claude Sonnet 4, Claude Sonnet 4.5, Claude Sonnet 5, Claude Opus 4.1, Claude Opus 4.5, Claude Opus 4.6, Claude Opus 4.7, Claude Opus 4.8, Claude Opus 5.
  - Gemini 2.5 Flash, Gemini 2.5 Flash-Lite, Gemini 3 Flash, Gemini 3.1 Flash-Lite, Gemini 3.1 Pro, Gemini 3.5 Flash, Gemini 3.7 Flash.
  - Gemma 4 31B IT, Llama 4 Maverick 17B, Llama 4 Scout 17B, Muse Glimmer 30B.
  - GPT-OSS 20B, GPT-OSS 120B, GPT-4o mini, GPT-4.1, GPT-4.1 mini, o3, GPT-5, GPT-5 mini, GPT-5 nano, GPT-5.1, GPT-5.1 Instant, GPT-5.1 Thinking, GPT-5.2, GPT-5.2 Pro, GPT-5.4 mini, GPT-5.4 nano, GPT-5.4 Thinking, GPT-5.4 Pro, GPT-5.5, GPT-5.5 Thinking, GPT-5.5 Pro, GPT-5.6 Luna, GPT-5.6 Sol, GPT-5.6 Terra, GPT-6 Astra.

## Tool-calling eval

The eval measures how well ASU Voyager models use the MCP tools. From the repository root:

```bash
cd apps/mcp-server
.venv/bin/python -m eval.run_eval --label mychange
```

See the [evaluation guide](docs/evaluation.md) for comparison runs, model selection, and API key details.

## Run locally

The development stack exposes the MCP server at `http://localhost:8000`, LibreChat at `http://localhost:3080`, and MongoDB at `localhost:27017`.

Create the private LibreChat environment file and set `VOYAGER_API_KEY` and `CREATEAI_API_KEY` to your ASU keys. Keep the base URLs from the template:

```bash
cp apps/librechat/.env.example apps/librechat/.env
chmod 600 apps/librechat/.env
# Edit apps/librechat/.env and set VOYAGER_API_KEY and CREATEAI_API_KEY.
docker compose -f docker-compose.dev.yml up --build
```

LibreChat uses TATC AI branding in the local UI.

## Deploy

Production serves LibreChat at `chat.<DOMAIN>`, the MCP server at `mcp.<DOMAIN>`, and Dex at `auth.<DOMAIN>`. See the [deployment guide](docs/deployment.md) for DNS, TLS, OIDC, host setup, and GitHub Actions configuration.

Every push to `main` deploys to EC2 and restarts LibreChat. When MCP server inputs change, GitHub Actions builds and publishes the MCP image with `latest` and commit-SHA tags before deploying. A failed image build prevents deployment. The deploy stack uses `latest` by default; set `MCP_SERVER_IMAGE_TAG` in the root `.env` to pin a commit SHA for a reproducible deploy or rollback.
