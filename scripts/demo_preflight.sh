#!/usr/bin/env bash
set -uo pipefail
cd "$(dirname "$0")/.."

failures=0
warnings=0
preflight_tmpdir="$(mktemp -d /tmp/trace2root-preflight.XXXXXX)"
trap 'rm -rf -- "$preflight_tmpdir"' EXIT

ok() { printf '[OK]   %s\n' "$1"; }
warn() { printf '[WARN] %s\n' "$1"; warnings=$((warnings + 1)); }
fail() { printf '[FAIL] %s\n' "$1"; failures=$((failures + 1)); }

printf 'Trace2Root demo preflight\n\n'

if [[ -x .venv/bin/python ]]; then
  ok 'Python virtual environment exists'
else
  fail 'Missing .venv; run ./scripts/setup.sh --full'
fi

if [[ -f .env ]]; then
  ok '.env exists'
else
  fail 'Missing .env; copy .env.example to .env'
fi

for path in data/trace2root.db data/milvus_bge.db; do
  if [[ -s "$path" ]]; then
    ok "$path exists"
  else
    fail "$path is missing; import samples and rebuild the vector index"
  fi
done

if command -v nvidia-smi >/dev/null 2>&1; then
  gpu_line="$(nvidia-smi --query-gpu=name,memory.total,memory.used --format=csv,noheader 2>/dev/null | head -n 1)"
  [[ -n "$gpu_line" ]] && ok "GPU visible: $gpu_line" || warn 'nvidia-smi returned no GPU row'
else
  warn 'nvidia-smi is unavailable; Ollama may run on CPU'
fi

ollama_bin="${OLLAMA_BIN:-$HOME/.local/bin/ollama}"
if [[ -x "$ollama_bin" ]]; then
  ok "Ollama binary exists: $ollama_bin"
else
  fail "Ollama binary not found: $ollama_bin"
fi

if curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >"$preflight_tmpdir/ollama-tags.json" 2>/dev/null; then
  if grep -q 'qwen3:4b' "$preflight_tmpdir/ollama-tags.json"; then
    ok 'Ollama is running and qwen3:4b is available'
  else
    fail 'Ollama is running, but qwen3:4b is not listed'
  fi
else
  warn 'Ollama is stopped; ./scripts/start_all.sh will start it'
fi

if curl -fsS --max-time 3 http://127.0.0.1:8000/api/v1/ready >"$preflight_tmpdir/ready.json" 2>/dev/null; then
  ok 'FastAPI is ready at http://127.0.0.1:8000'
else
  warn 'FastAPI is stopped; run ./scripts/start_all.sh and wait for startup complete'
fi

if [[ -x .venv/bin/python ]]; then
  .venv/bin/python - <<'PY'
import json
from pathlib import Path

checks = {
    "RAG": Path("evaluation/results/latest_public_rag.json"),
    "RCA": Path("evaluation/results/latest_public_rca.json"),
    "Robustness": Path("evaluation/results/latest_robustness.json"),
}
for name, path in checks.items():
    if not path.exists():
        print(f"[WARN] Missing {name} evaluation result: {path}")
        continue
    data = json.loads(path.read_text(encoding="utf-8"))
    if name == "RAG":
        stats = data["repeat_statistics"]["hybrid_rrf"]
        print(f"[OK]   RAG: {data['case_count']} queries x {data['repeat_count']}, "
              f"Recall@3={stats['recall@3']['mean']:.4f}, MRR={stats['mrr']['mean']:.4f}")
    elif name == "RCA":
        print(f"[OK]   RCA: {data['case_count']} cases x {data['repeat_count']}, "
              f"first-service accuracy={data['first_failing_service_accuracy']:.4f}")
    else:
        print(f"[OK]   Robustness: {data['case_count']} categories x {data['repeat_count']}, "
              f"pass rate={data['pass_rate_mean']:.4f}")
PY
fi

printf '\nResult: %d failure(s), %d warning(s)\n' "$failures" "$warnings"
if (( failures > 0 )); then
  exit 1
fi
printf 'Preflight passed. Warnings about stopped services are expected before startup.\n'
