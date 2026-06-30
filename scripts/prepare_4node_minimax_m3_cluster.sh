#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 4 ]]; then
  cat <<'EOF'
Usage:
  scripts/prepare_4node_minimax_m3_cluster.sh MASTER WORKER1 WORKER2 WORKER3 [NAMESPACE]

Example:
  scripts/prepare_4node_minimax_m3_cluster.sh 10.80.118.167 10.80.118.168 10.80.118.169 10.80.118.170 my-dev-cluster

This script prints the exact commands to run on each Mac mini. Use
start_4node_exo_cluster.sh when you want to start all four nodes.

For RDMA/JACCL mode, first copy and edit:
  cp scripts/cluster.env.example scripts/cluster.env
EOF
  exit 1
fi

master_host="$1"
worker1_host="$2"
worker2_host="$3"
worker3_host="$4"
namespace="${5:-my-dev-cluster}"

repo_dir="${REPO_DIR:-$HOME/exo}"
api_port="${API_PORT:-52415}"
libp2p_port="${LIBP2P_PORT:-0}"

common_env="EXO_LIBP2P_NAMESPACE=${namespace} EXO_OFFLINE=true"
common_cmd="cd ${repo_dir} && ${common_env} uv run exo --fast-synch --api-port ${api_port} --libp2p-port ${libp2p_port}"

echo "=== Worker commands ==="
echo "Before running the cluster, enable SSH on each Mac mini so it stays on after reboot:"
echo "  scripts/enable_sshd_on_boot.sh"
echo
echo "Worker 1 (${worker1_host}):"
echo "  ssh ${worker1_host} '${common_cmd}'"
echo
echo "Worker 2 (${worker2_host}):"
echo "  ssh ${worker2_host} '${common_cmd}'"
echo
echo "Worker 3 (${worker3_host}):"
echo "  ssh ${worker3_host} '${common_cmd}'"
echo
echo "=== Master command ==="
echo "Master (${master_host}):"
echo "  ssh ${master_host} 'cd ${repo_dir} && ${common_env} uv run exo --force-master --api-port ${api_port} --libp2p-port ${libp2p_port}'"
echo
echo "=== Claude Code ==="
echo "Use the master/dashboard URL as ANTHROPIC_BASE_URL, for example:"
echo "  export ANTHROPIC_BASE_URL=http://${master_host}:${api_port}"
echo "  export ANTHROPIC_API_KEY=x"
echo "  export CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1"
echo "  claude"
echo
echo "=== Download model to cluster ==="
echo "When the model download is ready, run from the repo:"
echo "  uv run python scripts/download_model_to_cluster.py pipenetwork/MiniMax-M3-MLX-4bit --host ${master_host}"
echo
echo "=== Boot-time SSH note ==="
echo "On each Mac mini, run:"
echo "  sudo /usr/sbin/systemsetup -setremotelogin on"
echo "This keeps sshd enabled after reboot."
echo
echo "=== Start all four nodes ==="
echo "  scripts/start_4node_exo_cluster.sh ${master_host} ${worker1_host} ${worker2_host} ${worker3_host} ${namespace}"
echo
echo "=== RDMA/JACCL placement ==="
echo "After all nodes are visible in topology:"
echo "  MODEL_ID=pipenetwork/MiniMax-M3-MLX-4bit MIN_NODES=4 scripts/place_rdma_instance.sh"
