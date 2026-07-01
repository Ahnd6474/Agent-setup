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
source_dir="${models_dir}/${normalized_model_id}"
remote_dir="${models_dir}/${normalized_model_id}"

if [[ ! -f "${source_dir}/config.json" || ! -f "${source_dir}/model.safetensors.index.json" ]]; then
  echo "Incomplete local model directory: ${source_dir}" >&2
  exit 1
fi

python3 - "${source_dir}" <<'PY'
import json
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
index = json.loads((root / "model.safetensors.index.json").read_text())
missing = sorted({name for name in index["weight_map"].values() if not (root / name).is_file()})
if missing:
    raise SystemExit(f"missing weight files: {missing[:5]}")
print(f"local_weight_files={len(set(index['weight_map'].values()))}")
PY

for role in worker1 worker2 worker3; do
  host="$(connection_host_for_role "${role}" "${connect_type}")"
  echo "Syncing ${model_id} to ${role} via ${connect_type}: ${host}"
  connection_run type="${connect_type}" host="${host}" command="mkdir -p '${remote_dir}'"
  rsync_args=(
    -a
    --delete
    --partial
    --whole-file
    --progress
    --exclude '.cache/' \
    -e "ssh -o ConnectTimeout=${SSH_CONNECT_TIMEOUT:-10} -o StrictHostKeyChecking=accept-new" \
    "${source_dir}/"
  )
  if ! rsync "${rsync_args[@]}" "${host}:${remote_dir}/"; then
    fallback_host="$(connection_host_for_role "${role}" net)"
    if [[ "${fallback_host}" == "${host}" ]]; then
      exit 1
    fi
    echo "Line sync failed; retrying ${role} via management network: ${fallback_host}" >&2
    connection_run type=net host="${fallback_host}" command="mkdir -p '${remote_dir}'"
    rsync "${rsync_args[@]}" "${fallback_host}:${remote_dir}/"
  fi
done

echo "model_sync=ok"
