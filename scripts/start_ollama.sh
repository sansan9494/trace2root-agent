#!/usr/bin/env bash
set -euo pipefail

ollama_bin="${OLLAMA_BIN:-$HOME/.local/bin/ollama}"
if [[ ! -x "$ollama_bin" ]]; then
  echo "未找到 Ollama：$ollama_bin" >&2
  exit 1
fi

exec "$ollama_bin" serve
