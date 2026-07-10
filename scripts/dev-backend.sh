#!/usr/bin/env bash
set -euo pipefail

HOST="${ATELIER_BACKEND_HOST:-127.0.0.1}"
PORT="${ATELIER_BACKEND_PORT:-8001}"

cd "$(dirname "$0")/../backend"

ACP_RUNTIME_DIR="$(pwd)/acp-runtime"
ACP_RUNTIME_INSTALLED_LOCK="$ACP_RUNTIME_DIR/node_modules/.package-lock.json"
if [ ! -f "$ACP_RUNTIME_INSTALLED_LOCK" ] \
  || [ "$ACP_RUNTIME_DIR/package-lock.json" -nt "$ACP_RUNTIME_INSTALLED_LOCK" ] \
  || [ "$ACP_RUNTIME_DIR/package.json" -nt "$ACP_RUNTIME_INSTALLED_LOCK" ]; then
  echo "==> installing backend ACP runtime deps"
  npm install --prefix "$ACP_RUNTIME_DIR"
fi

exec uv run uvicorn src.main:app --host "$HOST" --port "$PORT" --reload
