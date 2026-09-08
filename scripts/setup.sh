#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv || true
fi
if [[ ! -x .venv/bin/python ]]; then
  echo "无法创建隔离 Python；请确认安装 python3-venv 后重试。" >&2
  exit 1
fi
venv_python=".venv/bin/python"
if ! "$venv_python" -m pip --version >/dev/null 2>&1; then
  # Ubuntu 最小安装可能生成了 venv Python 但没有 ensurepip。仅向项目 venv 引导 pip。
  bootstrap_file="/tmp/oncall-agent-get-pip.py"
  curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$bootstrap_file"
  "$venv_python" "$bootstrap_file"
fi
"$venv_python" -m pip install --upgrade pip
"$venv_python" -m pip install -e '.[test]'

if [[ "${1:-}" == "--with-embedding" || "${1:-}" == "--with-models" || "${1:-}" == "--full" ]]; then
  # Force the official CPU wheel so pip never pulls multi-GB NVIDIA/CUDA runtime packages.
  "$venv_python" -m pip install --index-url https://download.pytorch.org/whl/cpu 'torch>=2.4,<3'
  "$venv_python" -m pip install -e '.[embedding]'
  echo "已安装 CPU 版 PyTorch 和 sentence-transformers。"
fi

if [[ "${1:-}" == "--with-evaluation" || "${1:-}" == "--full" ]]; then
  "$venv_python" -m pip install -e '.[evaluation]'
  echo "已安装锁定版本的 RAGAS 本地评测依赖。"
fi

if [[ "${1:-}" == "--with-models" || "${1:-}" == "--full" ]]; then
  "$venv_python" scripts/download_models.py
  echo "BGE Embedding 和 Reranker 已下载并完成样例推理。"
fi

[[ -f .env ]] || cp .env.example .env
mkdir -p data/uploads
"$venv_python" -c 'import asyncio; from backend.app.db import init_db; asyncio.run(init_db())'
echo "安装完成。运行 ./scripts/start.sh 启动。"
