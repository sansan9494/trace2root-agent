#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

ollama_started=0
ollama_pid=""
cleanup() {
  if [[ "$ollama_started" == "1" && -n "$ollama_pid" ]]; then
    kill "$ollama_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
  # llama.cpp's default 8 GiB cross-request prompt cache can exceed this
  # project's typical WSL memory allocation. Disable that cache; model/KV
  # inference remains on GPU and no model files are removed.
  LLAMA_ARG_CACHE_RAM="${LLAMA_ARG_CACHE_RAM:-0}" OLLAMA_NO_CLOUD="${OLLAMA_NO_CLOUD:-1}" \
    "$HOME/.local/bin/ollama" serve >data/ollama.log 2>&1 &
  ollama_pid=$!
  ollama_started=1
  for _ in {1..30}; do
    curl -fsS http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
    sleep 0.5
  done
fi

./scripts/start.sh
