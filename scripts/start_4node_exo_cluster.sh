#!/usr/bin/env bash
set -euo pipefail

config_file="${CONFIG_FILE:-scripts/cluster.env}"
if [[ -f "${config_file}" ]]; then
  # shellcheck disable=SC1090
  source "${config_file}"
fi

# shellcheck disable=SC1091
source "$(dirname "$0")/cluster_connection.sh"

connect_type="${CONNECT_TYPE:-line}"
master_host="${MASTER_HOST:-127.0.0.1}"
master_api_host="${API_HOST:-127.0.0.1}"
bootstrap_host="${MASTER_BOOTSTRAP_HOST:-10.0.0.1}"
namespace="${NAMESPACE:-macmini-ai-server}"
repo_dir="${REPO_DIR:-$HOME/exo}"
models_dir="${MODELS_DIR:-$HOME/models}"
api_port="${API_PORT:-52415}"
libp2p_port="${LIBP2P_PORT:-52416}"
min_nodes="${MIN_NODES:-4}"
restart="${RESTART:-false}"
ssh_timeout="${SSH_CONNECT_TIMEOUT:-5}"

worker1_host="${WORKER1_HOST:-$(connection_host_for_role worker1 "${connect_type}" 2>/dev/null || true)}"
worker2_host="${WORKER2_HOST:-$(connection_host_for_role worker2 "${connect_type}" 2>/dev/null || true)}"
worker3_host="${WORKER3_HOST:-$(connection_host_for_role worker3 "${connect_type}" 2>/dev/null || true)}"

if [[ -z "${worker1_host}" || -z "${worker2_host}" || -z "${worker3_host}" ]]; then
  echo "Missing worker hosts. Set WORKER*_LINE_HOST or WORKER*_NET_HOST in ${config_file}." >&2
  exit 1
fi

if ! command -v screen >/dev/null 2>&1; then
  echo "screen is required to keep SSH TTY worker sessions alive." >&2
  exit 1
fi

api_url="http://${master_api_host}:${api_port}"
bootstrap_peer="/ip4/${bootstrap_host}/tcp/${libp2p_port}"
exo_bin="${repo_dir}/.venv/bin/exo"

screen_quit() {
  local session="$1"
  screen -S "${session}" -X quit >/dev/null 2>&1 || true
}

api_ready() {
  curl -fsS "${api_url}/state/topology" >/dev/null 2>&1
}

wait_for_api() {
  echo "Waiting for master API: ${api_url}"
  for _ in $(seq 1 60); do
    if api_ready; then
      echo "Master API is up"
      return 0
    fi
    sleep 1
  done
  echo "Master API did not come up in time" >&2
  return 1
}

wait_for_nodes() {
  local count
  echo "Waiting for ${min_nodes} cluster nodes"
  for _ in $(seq 1 90); do
    count="$(
      python3 - "${api_url}/state/topology" <<'PY' 2>/dev/null || true
import json
import sys
import urllib.request

with urllib.request.urlopen(sys.argv[1], timeout=5) as response:
    topology = json.load(response)

nodes = topology.get("nodes") or []
print(len(nodes if isinstance(nodes, list) else list(nodes)))
PY
    )"
    if [[ "${count:-0}" -ge "${min_nodes}" ]]; then
      echo "Cluster nodes: ${count}"
      return 0
    fi
    sleep 1
  done
  echo "Cluster did not reach ${min_nodes} nodes" >&2
  curl -fsS "${api_url}/state/topology" || true
  echo
  return 1
}

start_master() {
  local session="exo-master"
  local command

  if [[ "${restart}" == "true" ]]; then
    screen_quit "${session}"
  fi

  if api_ready; then
    echo "Master already running at ${api_url}"
    return 0
  fi

  command="mkdir -p \"\$HOME/.exo\" && cd '${repo_dir}' && EXO_LIBP2P_NAMESPACE='${namespace}' EXO_MODELS_DIRS='${models_dir}' EXO_OFFLINE=true PATH='/Users/dshs_llm/.local/bin':\"\$PATH\" '${exo_bin}' --force-master --offline --no-downloads --api-port '${api_port}' --libp2p-port '${libp2p_port}'"

  echo "Starting master in local screen: ${session}"
  screen -dmS "${session}" bash -lc "${command}"
  wait_for_api
}

start_worker() {
  local role="$1"
  local host="$2"
  local session="exo-${role}"
  local remote_command

  screen_quit "${session}"

  remote_command="if test -f \"\$HOME/.exo/exo.pid\"; then kill -TERM \$(cat \"\$HOME/.exo/exo.pid\") 2>/dev/null || true; rm -f \"\$HOME/.exo/exo.pid\"; fi; mkdir -p \"\$HOME/.exo\"; cd '${repo_dir}' && EXO_LIBP2P_NAMESPACE='${namespace}' EXO_MODELS_DIRS='${models_dir}' EXO_OFFLINE=true PATH='/Users/dshs_llm/.local/bin':\"\$PATH\" '${exo_bin}' -v --offline --no-downloads --api-port '${api_port}' --libp2p-port '${libp2p_port}' --bootstrap-peers '${bootstrap_peer}'"

  echo "Starting ${role} via ${host} in local screen: ${session}"
  screen -dmS "${session}" ssh -tt -o ConnectTimeout="${ssh_timeout}" -o StrictHostKeyChecking=accept-new "${host}" "${remote_command}"
}

echo "Cluster namespace: ${namespace}"
echo "Master API: ${api_url}"
echo "Bootstrap peer: ${bootstrap_peer}"

start_master
start_worker worker1 "${worker1_host}"
start_worker worker2 "${worker2_host}"
start_worker worker3 "${worker3_host}"
wait_for_nodes

echo
echo "Web UI:"
echo "  ${api_url}"
echo "Claude Code:"
echo "  ANTHROPIC_BASE_URL=${api_url} ANTHROPIC_API_KEY=x claude"
echo "Status:"
echo "  scripts/status_4node_exo_cluster.sh"
