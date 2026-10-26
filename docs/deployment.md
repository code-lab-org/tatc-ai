# Deployment

The production stack runs behind Traefik with automatic Let's Encrypt TLS and Dex as the OAuth identity provider:

- `https://chat.<DOMAIN>` serves LibreChat.
- `https://mcp.<DOMAIN>` serves the MCP server.
- `https://auth.<DOMAIN>` serves Dex.

LibreChat and the MCP server authenticate users against Dex with OIDC. The MCP server enables OIDC when `MCP_OIDC_ISSUER_URL` and the related `MCP_OIDC_CLIENT_*` and `MCP_BASE_URL` variables are set. [The deployment Compose file](../docker-compose.deploy.yml) sets them; the development Compose file does not, so the development server stays unauthenticated. The `mcp-server` static client and its redirect URI are pre-provisioned in [`config/dex/start-dex.sh`](../config/dex/start-dex.sh).

## Prepare the host

Keep host names and secrets in environment files that are not committed to the repository. Run the shell commands below from the repository root unless a step says otherwise.

1. Point DNS for `<DOMAIN>`, `chat.<DOMAIN>`, `mcp.<DOMAIN>`, and `auth.<DOMAIN>` at the host's public IP. Open inbound ports 80 and 443 in its security group. Port 80 is required for the Let's Encrypt HTTP challenge.
2. Copy the [root environment template](../.env.example) to `.env` and fill in `DOMAIN`, `ACME_EMAIL`, and newly generated `DEX_*` secrets:

   ```bash
   cp .env.example .env
   ```

3. Copy the [LibreChat environment template](../apps/librechat/.env.example) to `apps/librechat/.env`. Replace the `JWT_SECRET`, `JWT_REFRESH_SECRET`, `CREDS_KEY`, and `CREDS_IV` placeholders with generated values, following the comments in the template. Set `VOYAGER_API_KEY` and `CREATEAI_API_KEY` for the ASU Voyager and ASU CreateAI endpoints. Existing `.env` files that use `OPENAI_API_KEY` or `OPENAI_REVERSE_PROXY` must rename them to `VOYAGER_API_KEY` and `VOYAGER_BASE_URL`.

   ```bash
   cp apps/librechat/.env.example apps/librechat/.env
   ```

4. `ghcr.io/code-lab-org/tatc-ai-mcp-server` is a private package. Log in on the host with a GitHub PAT that has `read:packages` and belongs to an account with access to this repository:

   ```bash
   echo '<PAT>' | docker login ghcr.io -u <github-username> --password-stdin
   ```

5. Start the stack:

   ```bash
   docker compose -f docker-compose.deploy.yml up -d
   ```

Docker Compose reads `.env` from the repository root and uses it to fill `${DOMAIN}` and other variables in `docker-compose.deploy.yml`. The Compose file aliases `auth.<DOMAIN>` to the Traefik container on the internal Docker network, so OIDC clients inside the stack and browsers outside it resolve the same issuer URL without depending on outbound internet DNS.

## MCP image and auto-deploy

The [deployment workflow](../.github/workflows/deploy.yml) runs on every push to `main` and can also be started manually. It checks whether `apps/mcp-server/**` or the workflow changed. If so, it builds and pushes `ghcr.io/code-lab-org/tatc-ai-mcp-server` with both `latest` and the commit SHA as tags, then deploys after the build succeeds. If no image build is needed, it deploys right away. Manual runs and pushes that cannot be diffed, such as force pushes, always rebuild. A failed build does not deploy.

The [deployment Compose file](../docker-compose.deploy.yml) pulls `ghcr.io/code-lab-org/tatc-ai-mcp-server:${MCP_SERVER_IMAGE_TAG:-latest}`. Leave `MCP_SERVER_IMAGE_TAG` unset or set it to `latest` to follow new images. Set it in the root `.env` to a commit SHA for a reproducible deploy or rollback; auto-deploy keeps using that pinned image until the setting changes.

To build the image locally, for example to test before pushing:

```bash
docker build -f apps/mcp-server/Dockerfile . -t ghcr.io/code-lab-org/tatc-ai-mcp-server:local
```

The workflow runs [`config/deploy/deploy.sh`](../config/deploy/deploy.sh) on the instance. The script runs `git merge --ff-only`, then `docker compose -f docker-compose.deploy.yml pull` and `up -d`. It fails rather than overwriting a checkout that has diverged from `main`.

GitHub-hosted runners do not have a small, stable IP range, so the instance must accept inbound port 22 broadly instead of allowlisting GitHub. The deploy key has a forced command so it can only run the deployment script.

1. On the EC2 instance, add the public key to the deploy user's `~/.ssh/authorized_keys`. That user needs passwordless `docker` and `docker compose` access and a repository checkout with `.env` and `apps/librechat/.env` in place:

   ```text
   command="/path/to/repo/config/deploy/deploy.sh",no-port-forwarding,no-X11-forwarding,no-agent-forwarding,no-pty ssh-ed25519 AAAA...
   ```

2. Generate a dedicated deploy keypair. Do not reuse a personal key. Add the public half as shown above:

   ```bash
   ssh-keygen -t ed25519 -f deploy_key -N "" -C "tatc-ai-deploy"
   ```

3. Capture the instance host key from a trusted channel, such as the EC2 console's system log or an `ssh-keyscan` run over a connection you already trust. Do not accept an unverified first connection, which would defeat host verification:

   ```bash
   ssh-keyscan -H <host> > known_hosts
   ```

4. In the repository's GitHub Actions settings, add:
   - Repository variables: `DEPLOY_SSH_HOST`, `DEPLOY_SSH_USER`
   - Repository secrets: `DEPLOY_SSH_KEY` (the private half of `deploy_key`) and `DEPLOY_SSH_KNOWN_HOSTS` (the contents of `known_hosts`)
