#!/usr/bin/env bash
set -euo pipefail

config_file="${CONFIG_FILE:-scripts/model.env}"
if [[ -f "${config_file}" ]]; then
  # shellcheck disable=SC1090
  source "${config_file}"
fi

model_repo="${MODEL_REPO:-unsloth/Qwen3.6-35B-A3B-GGUF}"
model_revision="${MODEL_REVISION:-main}"
model_file="${MODEL_FILE:-Qwen3.6-35B-A3B-Q8_0.gguf}"
model_dir="${MODEL_DIR:-$HOME/llm-models/Qwen3.6-35B-A3B-GGUF}"
connections="${ARIA2_CONNECTIONS:-16}"
split="${ARIA2_SPLIT:-16}"
min_split_size="${ARIA2_MIN_SPLIT_SIZE:-16M}"
url="${MODEL_URL:-https://huggingface.co/${model_repo}/resolve/${model_revision}/${model_file}?download=true}"

if ! command -v aria2c >/dev/null 2>&1; then
  echo "aria2c is required. Install it with: brew install aria2" >&2
  exit 1
fi

mkdir -p "${model_dir}"

headers=()
if [[ -n "${HF_TOKEN:-}" ]]; then
  headers+=(--header="Authorization: Bearer ${HF_TOKEN}")
fi

echo "Downloading ${model_repo}/${model_file}"
echo "Target: ${model_dir}/${model_file}"

aria2_args=(
  --continue=true
  --max-connection-per-server="${connections}"
  --split="${split}"
  --min-split-size="${min_split_size}"
  --file-allocation=none
  --allow-overwrite=true
  --auto-file-renaming=false
  --summary-interval=10
  --dir="${model_dir}"
  --out="${model_file}"
)

if [[ ${#headers[@]} -gt 0 ]]; then
  aria2_args+=("${headers[@]}")
fi

aria2c "${aria2_args[@]}" "${url}"

"$(dirname "$0")/verify_model_file.sh"
