#!/usr/bin/env bash
set -euo pipefail

config_file="${CONFIG_FILE:-scripts/model.env}"
if [[ -f "${config_file}" ]]; then
  # shellcheck disable=SC1090
  source "${config_file}"
fi

model_file="${MODEL_FILE:-Qwen3.6-35B-A3B-Q8_0.gguf}"
model_dir="${MODEL_DIR:-$HOME/llm-models/Qwen3.6-35B-A3B-GGUF}"
model_path="${MODEL_PATH:-${model_dir}/${model_file}}"
host="${LLAMA_HOST:-0.0.0.0}"
port="${LLAMA_PORT:-8080}"
ctx_size="${LLAMA_CTX_SIZE:-32768}"
gpu_layers="${LLAMA_GPU_LAYERS:-999}"
reasoning="${LLAMA_REASONING:-auto}"

if [[ ! -f "${model_path}" ]]; then
  echo "Missing model file: ${model_path}" >&2
  exit 1
fi

if ! command -v llama-server >/dev/null 2>&1; then
  cat >&2 <<'EOF'
llama-server is not installed.
Install it with:
  brew install llama.cpp
EOF
  exit 1
fi

exec llama-server \
  --model "${model_path}" \
  --host "${host}" \
  --port "${port}" \
  --ctx-size "${ctx_size}" \
  --n-gpu-layers "${gpu_layers}" \
  --reasoning "${reasoning}" \
  ${LLAMA_EXTRA_ARGS:-}
