#!/usr/bin/env bash
set -euo pipefail

config_file="${CONFIG_FILE:-scripts/cluster.env}"
if [[ -f "${config_file}" ]]; then
  # shellcheck disable=SC1090
  source "${config_file}"
fi

api_host="${API_HOST:-127.0.0.1}"
api_port="${API_PORT:-52415}"
api_url="http://${api_host}:${api_port}"

echo "API: ${api_url}"
echo

python3 - "${api_url}" <<'PY'
import json
import sys
import urllib.request

base_url = sys.argv[1]
with urllib.request.urlopen(f"{base_url}/state/topology", timeout=10) as response:
    topology = json.load(response)
with urllib.request.urlopen(f"{base_url}/state", timeout=10) as response:
    state = json.load(response)

nodes = topology.get("nodes") or []
if isinstance(nodes, dict):
    node_ids = list(nodes)
else:
    node_ids = [
        n if isinstance(n, str) else str(n.get("nodeId") or n.get("node_id") or "")
        for n in nodes
    ]
    node_ids = [node_id for node_id in node_ids if node_id]

identities = state.get("nodeIdentities") or {}
print(f"topology_nodes={len(node_ids)}")
for node_id in node_ids:
    name = (identities.get(node_id) or {}).get("friendlyName") or "unnamed"
    print(f"- {name}: {node_id}")

connections = topology.get("connections") or {}
print(f"connection_groups={len(connections)}")
PY

echo
echo "Local screen sessions:"
screen -ls | grep -E 'exo-(master|worker[123])' || true
