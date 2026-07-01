#!/usr/bin/env bash

# Kill any existing llama-server
pkill -f llama-server || true

# Run llama-server with abliterated Q4_K_M model on port 52417
exec /opt/homebrew/bin/llama-server \
  -m /Users/dshs_llm/llm-models/Qwen3.6-35B-A3B-GGUF/Huihui-Qwen3.6-35B-A3B-abliterated-Q4_K_M.gguf \
  -ngl 99 \
  -c 4096 \
  --port 52417 \
  --host 0.0.0.0
