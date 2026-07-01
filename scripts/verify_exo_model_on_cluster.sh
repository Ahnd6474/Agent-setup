#!/usr/bin/env bash
set -euo pipefail

config_file="${CONFIG_FILE:-scripts/cluster.env}"
if [[ -f "${config_file}" ]]; then
  # shellcheck disable=SC1090
  source "${config_file}"
fi

# shellcheck disable=SC1091
source "$(dirname "$0")/cluster_connection.sh"

model_id="${MODEL_ID:-mlx-community/Qwen3.6-35B-A3B-4bit}"
models_dir="${MODELS_DIR:-$HOME/models}"
connect_type="${CONNECT_TYPE:-line}"
normalized_model_id="${model_id//\//--}"
model_dir="${models_dir}/${normalized_model_id}"
python_bin="${REPO_DIR:-$HOME/exo}/.venv/bin/python"

verify_command="'${python_bin}' - '${model_dir}' <<'PY'
import json
import pathlib
import sys
root = pathlib.Path(sys.argv[1])
index = json.loads((root / 'model.safetensors.index.json').read_text())
files = sorted(set(index['weight_map'].values()))
missing = [name for name in files if not (root / name).is_file()]
size = sum((root / name).stat().st_size for name in files if (root / name).is_file())
print(f'weight_files={len(files)} missing={len(missing)} size_bytes={size}')
if missing:
    raise SystemExit(1)
PY"

echo "== node1 =="
bash -lc "${verify_command}"
for role in worker1 worker2 worker3; do
  echo "== ${role} =="
  if ! connection_run type="${connect_type}" role="${role}" command="${verify_command}"; then
    echo "${connect_type} verification failed; retrying ${role} via management network" >&2
    connection_run type=net role="${role}" command="${verify_command}"
  fi
done
