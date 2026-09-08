#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
  echo "缺少 .venv，请先运行 ./scripts/setup.sh" >&2
  exit 1
fi

exec .venv/bin/python -m uvicorn backend.app.main:app --host "${APP_HOST:-127.0.0.1}" --port "${APP_PORT:-8000}"
