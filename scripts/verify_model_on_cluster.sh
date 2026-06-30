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
remote_model_dir="${REMOTE_MODEL_DIR:-~/llm-models/Qwen3.6-35B-A3B-GGUF}"

for role in worker1 worker2 worker3; do
  echo "== ${role} =="
  connection_run type="${connect_type}" role="${role}" command="test -f '${remote_model_dir}/${model_file}' && ls -lh '${remote_model_dir}/${model_file}' && python3 - <<'PY'
from pathlib import Path
p = Path('${remote_model_dir}/${model_file}').expanduser()
with p.open('rb') as f:
    magic = f.read(4)
print('magic=' + magic.decode('ascii', errors='replace'))
if magic != b'GGUF':
    raise SystemExit(1)
PY"
done

echo "cluster_model_verify=ok"
