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

echo "== Local Thunderbolt RDMA interfaces =="
python3 - <<'PY'
import subprocess
import sys

classes = {
    "AppleThunderboltRDMAInterface": "local RDMA interfaces",
    "AppleThunderboltRDMAPeerInterface": "RDMA peer interfaces",
    "AppleThunderboltIPConnection": "Thunderbolt IP connections",
}

for cls, label in classes.items():
    out = subprocess.run(
        ["ioreg", "-r", "-c", cls, "-l", "-w", "0"],
        check=False,
        text=True,
        capture_output=True,
    ).stdout
    count = out.count(f"<class {cls}")
    print(f"{label}: {count}")
    if count < 3:
        raise SystemExit(f"expected at least 3 {label}, got {count}")

ports = subprocess.run(
    ["ioreg", "-r", "-c", "AppleThunderboltIPPort", "-l", "-w", "0"],
    check=False,
    text=True,
    capture_output=True,
).stdout
if "IOLinkSpeed\" = 80000000000" not in ports:
    raise SystemExit("no 80 Gb/s Thunderbolt IP port found")
print("80 Gb/s Thunderbolt IP ports detected")
PY

echo
echo "== exo topology =="
curl -fsS "http://${api_host}:${api_port}/state/topology" | python3 -m json.tool | sed -n '1,220p'

echo
echo "== RDMA/JACCL placement check =="
curl -fsS \
  "http://${api_host}:${api_port}/instance/placement?model_id=${model_id}&sharding=Tensor&instance_meta=MlxJaccl&min_nodes=${min_nodes}" \
  | python3 -m json.tool | sed -n '1,220p'

echo
echo "RDMA cluster verification passed."

