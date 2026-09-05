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
docker image prune -f
