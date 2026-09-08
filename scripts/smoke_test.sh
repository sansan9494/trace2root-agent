#!/usr/bin/env bash
set -euo pipefail
base_url="${TRACE2ROOT_BASE_URL:-http://127.0.0.1:8000}"
curl -fsS "$base_url/api/v1/health"
curl -fsS "$base_url/api/v1/ready"
curl -fsS -H 'Content-Type: application/json' -d '{"message":"支付服务高延迟如何排查？","service_name":"payment-service","environment":"production","enable_tools":true}' "$base_url/api/v1/chat"
echo
