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

if [[ ! -f "${model_path}" ]]; then
  echo "Missing model file: ${model_path}" >&2
  exit 1
fi

python3 - "${model_path}" "${MODEL_SHA256:-}" <<'PY'
import hashlib
import pathlib
import sys

path = pathlib.Path(sys.argv[1])
expected_sha256 = sys.argv[2]
size = path.stat().st_size

with path.open("rb") as f:
    magic = f.read(4)

print(f"path={path}")
print(f"size_bytes={size}")
print(f"size_gib={size / (1024 ** 3):.2f}")

if magic != b"GGUF":
    raise SystemExit(f"invalid GGUF magic: {magic!r}")

if expected_sha256:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024 * 16), b""):
            h.update(chunk)
    digest = h.hexdigest()
    print(f"sha256={digest}")
    if digest.lower() != expected_sha256.lower():
        raise SystemExit("sha256 mismatch")

print("model_file=ok")
PY
