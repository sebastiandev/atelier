#!/usr/bin/env bash
# Boot backend + frontend in parallel for the walking-skeleton demo.
# Forwards termination signals to both children.

set -euo pipefail

cd "$(dirname "$0")"

usage() {
  cat <<'EOF'
Usage: ./scripts/dev.sh [--fe PORT] [--be PORT]

Aliases: --frontend-port, --backend-port

Defaults: frontend 4173, backend 8001. ATELIER_FRONTEND_PORT and
ATELIER_BACKEND_PORT remain supported as environment-variable defaults.
EOF
}

frontend_port="${ATELIER_FRONTEND_PORT:-4173}"
backend_port="${ATELIER_BACKEND_PORT:-8001}"

while (( $# )); do
  case "$1" in
    --fe|--frontend-port)
      if (( $# < 2 )); then
        echo "Missing value for $1" >&2
        exit 2
      fi
      frontend_port="${2:-}"
      shift 2
      ;;
    --be|--backend-port)
      if (( $# < 2 )); then
        echo "Missing value for $1" >&2
        exit 2
      fi
      backend_port="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

for port in "$frontend_port" "$backend_port"; do
  if [[ ! "$port" =~ ^[0-9]+$ ]] || (( 10#$port < 1 || 10#$port > 65535 )); then
    echo "Invalid port: $port (expected 1-65535)" >&2
    exit 2
  fi
done

export ATELIER_FRONTEND_PORT="$frontend_port"
export ATELIER_BACKEND_PORT="$backend_port"

cleanup() {
  trap - INT TERM EXIT
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup INT TERM EXIT

./dev-backend.sh &
./dev-frontend.sh &

wait
