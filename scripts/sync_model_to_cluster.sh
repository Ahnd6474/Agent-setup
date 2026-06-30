#!/usr/bin/env bash
set -euo pipefail

cluster_config_file="${CLUSTER_CONFIG_FILE:-scripts/cluster.env}"
model_config_file="${MODEL_CONFIG_FILE:-scripts/model.env}"

if [[ -f "${cluster_config_file}" ]]; then
  # shellcheck disable=SC1090
  source "${cluster_config_file}"
fi

if [[ -f "${model_config_file}" ]]; then
  # shellcheck disable=SC1090
  source "${model_config_file}"
fi

# shellcheck disable=SC1091
source "$(dirname "$0")/cluster_connection.sh"

connect_type="${CONNECT_TYPE:-line}"
model_file="${MODEL_FILE:-Qwen3.6-35B-A3B-Q8_0.gguf}"
model_dir="${MODEL_DIR:-$HOME/llm-models/Qwen3.6-35B-A3B-GGUF}"
remote_model_dir="${REMOTE_MODEL_DIR:-~/llm-models/Qwen3.6-35B-A3B-GGUF}"

if [[ ! -f "${model_dir}/${model_file}" ]]; then
  echo "Missing local model file: ${model_dir}/${model_file}" >&2
  exit 1
fi

for role in worker1 worker2 worker3; do
  host="$(connection_host_for_role "${role}" "${connect_type}")"
  echo "Syncing ${model_file} to ${role} (${host}) via ${connect_type}"
  connection_run type="${connect_type}" host="${host}" command="mkdir -p '${remote_model_dir}'"
  rsync -avP \
    -e "ssh -o ConnectTimeout=${SSH_CONNECT_TIMEOUT:-5} -o StrictHostKeyChecking=accept-new" \
    "${model_dir}/" \
    "${host}:${remote_model_dir}/"
done

echo "model_sync=ok"
