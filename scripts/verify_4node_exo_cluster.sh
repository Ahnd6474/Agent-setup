#!/usr/bin/env bash
set -euo pipefail

host="${1:-10.80.118.167}"
port="${2:-52415}"
url="http://${host}:${port}/state/topology"

python3 - "$url" <<'PY'
import json
import sys
import urllib.request

url = sys.argv[1]
with urllib.request.urlopen(url, timeout=10) as response:
    topology = json.load(response)

nodes = topology.get("nodes") or []
if isinstance(nodes, dict):
    node_ids = list(nodes)
else:
    node_ids = [
        n if isinstance(n, str) else str(n.get("nodeId") or n.get("node_id") or "")
        for n in nodes
    ]
    node_ids = [node_id for node_id in node_ids if node_id]

print(f"topology_nodes={len(node_ids)}")
for node_id in node_ids:
    print(f"- {node_id}")

if len(node_ids) < 4:
    raise SystemExit("Cluster is not using all 4 nodes yet")
PY

