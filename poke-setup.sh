#!/usr/bin/env bash
#
# poke-setup.sh — Start the Piazza MCP server and tunnel it to Poke.
#
# Usage:
#   PIAZZA_EMAIL=you@school.ca PIAZZA_PASSWORD=yourpass ./poke-setup.sh
#
# Prerequisites:
#   - uv (brew install uv)
#   - Node.js 18+ (for npx poke)
#   - A Poke account (npx poke@latest login)

set -euo pipefail

PORT="${PORT:-8247}"

if [ -z "${PIAZZA_EMAIL:-}" ] || [ -z "${PIAZZA_PASSWORD:-}" ]; then
  echo "Error: PIAZZA_EMAIL and PIAZZA_PASSWORD must be set."
  echo "Usage: PIAZZA_EMAIL=you@school.ca PIAZZA_PASSWORD=pass $0"
  exit 1
fi

echo "==> Starting Piazza MCP server on port $PORT..."
PIAZZA_EMAIL="$PIAZZA_EMAIL" PIAZZA_PASSWORD="$PIAZZA_PASSWORD" PORT="$PORT" \
  uv run piazza-mcp-poke &
SERVER_PID=$!

# Wait for server to be ready
sleep 3

echo "==> Tunneling to Poke..."
npx poke@latest tunnel "http://localhost:${PORT}/sse" -n "Piazza"

# Clean up on exit
kill "$SERVER_PID" 2>/dev/null
