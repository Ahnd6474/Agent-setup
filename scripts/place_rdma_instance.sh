#!/usr/bin/env bash
set -euo pipefail

config_file="${CONFIG_FILE:-scripts/cluster.env}"
if [[ -f "${config_file}" ]]; then
  # shellcheck disable=SC1090
  source "${config_file}"
fi

api_host="${API_HOST:-${MASTER_HOST:-127.0.0.1}}"
api_port="${API_PORT:-52415}"
model_id="${MODEL_ID:-pipenetwork/MiniMax-M3-MLX-4bit}"
min_nodes="${MIN_NODES:-4}"
sharding="${SHARDING:-Tensor}"
instance_meta="${INSTANCE_META:-MlxJaccl}"

curl -fsS -X POST "http://${api_host}:${api_port}/place_instance" \
  -H 'Content-Type: application/json' \
  -d "{
    \"model_id\": \"${model_id}\",
    \"sharding\": \"${sharding}\",
    \"instance_meta\": \"${instance_meta}\",
    \"min_nodes\": ${min_nodes}
  }" | python3 -m json.tool

