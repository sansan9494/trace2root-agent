#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

# RAGAS installs unrelated pytest plugins. Keeping discovery explicit makes the
# suite deterministic and avoids cold-start network/plugin delays in WSL.
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
exec .venv/bin/python -m pytest -p pytest_asyncio.plugin "$@"
