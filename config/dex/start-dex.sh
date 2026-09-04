#!/bin/sh
set -eu

: "${DEX_DEV_PASSWORD_HASH:?DEX_DEV_PASSWORD_HASH is required}"
: "${DEX_LIBRECHAT_CLIENT_SECRET:?DEX_LIBRECHAT_CLIENT_SECRET is required}"
: "${DEX_MCP_CLIENT_SECRET:?DEX_MCP_CLIENT_SECRET is required}"

cat > /tmp/dex-config.yaml <<CONFIG
issuer: https://auth.localtest.me
storage:
  type: sqlite3
  config:
    file: /var/dex/dex.db
web:
  http: 0.0.0.0:5556
oauth2:
  skipApprovalScreen: true
enablePasswordDB: true
staticPasswords:
  - email: dev@example.com
    hash: "${DEX_DEV_PASSWORD_HASH}"
    username: dev
    userID: "08a8684b-db88-4b73-90a9-3cd1661f5466"
staticClients:
  - id: librechat
    name: LibreChat
    redirectURIs:
      - "https://chat.localtest.me/oauth/callback"
    secret: "${DEX_LIBRECHAT_CLIENT_SECRET}"
  - id: mcp-server
    name: MCP Server
    redirectURIs:
      - "https://mcp.localtest.me/oauth/callback"
    secret: "${DEX_MCP_CLIENT_SECRET}"
CONFIG

exec dex serve /tmp/dex-config.yaml
