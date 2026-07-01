#!/usr/bin/env bash
set -euo pipefail

model_path="${LLAMA_MODEL_PATH:?Set LLAMA_MODEL_PATH to a GGUF file}"
model_alias="${LLAMA_MODEL_ALIAS:?Set LLAMA_MODEL_ALIAS to the routed model name}"
host="${LLAMA_HOST:-0.0.0.0}"
port="${LLAMA_PORT:-8080}"
context_size="${LLAMA_CONTEXT_SIZE:-32768}"
parallel="${LLAMA_PARALLEL:-1}"
gpu_layers="${LLAMA_GPU_LAYERS:-999}"
reasoning="${LLAMA_REASONING:-auto}"

if [[ ! -f "${model_path}" ]]; then
  echo "Missing GGUF model: ${model_path}" >&2
  exit 1
fi
if ! command -v llama-server >/dev/null 2>&1; then
  echo "llama-server is not installed" >&2
  exit 1
fi

exec llama-server \
  --model "${model_path}" \
  --alias "${model_alias}" \
  --host "${host}" \
  --port "${port}" \
  --ctx-size "${context_size}" \
  --parallel "${parallel}" \
  --n-gpu-layers "${gpu_layers}" \
  --reasoning "${reasoning}" \
  ${LLAMA_EXTRA_ARGS:-}
