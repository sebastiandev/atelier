#!/usr/bin/env bash
# Thin wrapper around scripts/wipe.py — runs it inside the backend uv venv.
#
# Examples:
#   ./scripts/wipe.sh all
#   ./scripts/wipe.sh work WRK-001
#   ./scripts/wipe.sh project PRJ-001 -y

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR/../backend"
exec uv run python "$SCRIPT_DIR/wipe.py" "$@"
