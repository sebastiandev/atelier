#!/usr/bin/env bash
set -euo pipefail

HOST="${ATELIER_BACKEND_HOST:-127.0.0.1}"
PORT="${ATELIER_BACKEND_PORT:-8001}"

cd "$(dirname "$0")/../backend"

exec uv run uvicorn src.main:app --host "$HOST" --port "$PORT" --reload
