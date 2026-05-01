#!/usr/bin/env bash
# Boot backend + frontend in parallel for the walking-skeleton demo.
# Forwards termination signals to both children.

set -euo pipefail

cd "$(dirname "$0")"

cleanup() {
  trap - INT TERM EXIT
  jobs -p | xargs -r kill 2>/dev/null || true
}
trap cleanup INT TERM EXIT

./dev-backend.sh &
./dev-frontend.sh &

wait
