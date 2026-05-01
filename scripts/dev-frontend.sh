#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../frontend"

if [ ! -d node_modules ]; then
  echo "==> installing frontend deps"
  npm install
fi

exec npm run dev
