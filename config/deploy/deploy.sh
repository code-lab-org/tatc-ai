#!/bin/sh
set -eu

# Invoked on the EC2 instance via a forced SSH command (see README) so the
# deploy key can only ever run this script. Assumes it lives at
# <repo>/config/deploy/deploy.sh in a checkout that already has a working
# `.env` and `apps/librechat/.env` in place.
cd "$(dirname "$0")/../.."

git fetch origin main
git merge --ff-only origin/main

docker compose -f docker-compose.deploy.yml pull
docker compose -f docker-compose.deploy.yml up -d
# The chat config is rendered from its template by the container's own
# startup command, so `up -d` alone leaves a stale config running when only
# the template changed (compose can't see bind-mounted file changes).
# Bounce it so every deploy serves the merged config.
docker compose -f docker-compose.deploy.yml restart librechat
docker image prune -f
