#!/usr/bin/env bash
set -euo pipefail

config_file="${CONFIG_FILE:-scripts/cluster.env}"
if [[ -f "${config_file}" ]]; then
  # shellcheck disable=SC1090
  source "${config_file}"
fi

# shellcheck disable=SC1091
source "$(dirname "$0")/cluster_connection.sh"

requested_connect_type="${CONNECT_TYPE:-line}"
connect_type="${requested_connect_type}"
master_host="${MASTER_HOST:-127.0.0.1}"
master_api_host="${API_HOST:-127.0.0.1}"
# Discovery must not depend on the optional high-speed link. A worker that
# falls back from line control to the management LAN still needs a reachable
# bootstrap address or it will run indefinitely without joining the master.
master_net_address="${MASTER_NET_ADDRESS:-}"
master_line_address="${MASTER_LINE_ADDRESS:-10.0.0.1}"
master_bootstrap_host="${MASTER_BOOTSTRAP_HOST:-}"
namespace="${NAMESPACE:-macmini-ai-server}"
master_node_name="${MASTER_NODE_NAME:-node1}"
repo_dir="${REPO_DIR:-$HOME/exo}"
models_dir="${MODELS_DIR:-$HOME/models}"
api_port="${API_PORT:-52415}"
libp2p_port="${LIBP2P_PORT:-52416}"
min_nodes="${MIN_NODES:-4}"
restart="${RESTART:-false}"
ssh_timeout="${SSH_CONNECT_TIMEOUT:-5}"
fast_synch="${FAST_SYNCH:-true}"
skip_warmup="${SKIP_WARMUP:-false}"
debug_pipeline="${EXO_DEBUG_PIPELINE:-false}"
node_timeout_seconds="${EXO_NODE_TIMEOUT_SECONDS:-30}"
llama_replicas_file="${LLAMA_REPLICAS_FILE:-}"
lock_dir="${TMPDIR:-/tmp}/exo-cluster-start.lock"

acquire_start_lock() {
  local owner_pid=""
  if mkdir "${lock_dir}" 2>/dev/null; then
    echo "$$" >"${lock_dir}/pid"
    trap 'rm -rf "${lock_dir}"' EXIT INT TERM
    return 0
  fi

  owner_pid="$(cat "${lock_dir}/pid" 2>/dev/null || true)"
  if [[ -n "${owner_pid}" ]] && kill -0 "${owner_pid}" 2>/dev/null; then
    echo "Another cluster start is already running (pid=${owner_pid})" >&2
    return 1
  fi

  rm -rf "${lock_dir}"
  mkdir "${lock_dir}"
  echo "$$" >"${lock_dir}/pid"
  trap 'rm -rf "${lock_dir}"' EXIT INT TERM
}

acquire_start_lock

select_worker_connection() {
  local role="$1"
  local requested_type="$2"
  local line_host=""
  local net_host=""

  line_host="$(connection_host_for_role "${role}" line 2>/dev/null || true)"
  net_host="$(connection_host_for_role "${role}" net 2>/dev/null || true)"

  if [[ "${requested_type}" == "line" && -n "${line_host}" ]]; then
    if SSH_CONNECTION_ATTEMPTS=1 SSH_CONNECT_TIMEOUT="${SSH_PREFLIGHT_TIMEOUT:-${ssh_timeout}}" \
      connection_run type=line host="${line_host}" command="true" >/dev/null 2>&1; then
      printf 'line %s\n' "${line_host}"
      return 0
    fi
    echo "Line preflight failed for ${line_host}" >&2
  fi

  if [[ -n "${net_host}" ]]; then
    printf 'net %s\n' "${net_host}"
    return 0
  fi

  if [[ -n "${line_host}" ]]; then
    printf 'line %s\n' "${line_host}"
    return 0
  fi

  echo "No host configured for ${role}" >&2
  return 1
}

read -r worker1_type worker1_host < <(select_worker_connection worker1 "${connect_type}")
read -r worker2_type worker2_host < <(select_worker_connection worker2 "${connect_type}")
read -r worker3_type worker3_host < <(select_worker_connection worker3 "${connect_type}")

if [[ -z "${worker1_host}" || -z "${worker2_host}" || -z "${worker3_host}" ]]; then
  echo "Missing worker hosts. Set WORKER*_LINE_HOST or WORKER*_NET_HOST in ${config_file}." >&2
  exit 1
fi

if [[ ( "${worker1_type}" == "net" || "${worker2_type}" == "net" || "${worker3_type}" == "net" ) && -z "${master_net_address}" && -z "${master_bootstrap_host}" ]]; then
  echo "Net control requires MASTER_NET_ADDRESS or MASTER_BOOTSTRAP_HOST" >&2
  exit 1
fi

if ! command -v screen >/dev/null 2>&1; then
  echo "screen is required to keep SSH TTY worker sessions alive." >&2
  exit 1
fi

api_url="http://${master_api_host}:${api_port}"
exo_bin="${repo_dir}/.venv/bin/exo"

screen_quit() {
  local session="$1"
  screen -S "${session}" -X quit >/dev/null 2>&1 || true
}

fast_synch_flag() {
  if [[ "${fast_synch}" == "true" ]]; then
    printf '%s' "--fast-synch"
  else
    printf '%s' "--no-fast-synch"
  fi
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

stop_master_process() {
  local pid_file="$HOME/.exo/exo.pid"
  local pid=""
  local listener_pids=""

  if [[ -f "${pid_file}" ]]; then
    pid="$(cat "${pid_file}")"
    kill -TERM "${pid}" >/dev/null 2>&1 || true
    rm -f "${pid_file}"
  fi

  # exo does not always create ~/.exo/exo.pid. Fall back to the process that
  # actually owns the configured API listener so a detached screen child
  # cannot survive a requested restart.
  if command -v lsof >/dev/null 2>&1; then
    listener_pids="$(lsof -tiTCP:"${api_port}" -sTCP:LISTEN 2>/dev/null || true)"
    for pid in ${listener_pids}; do
      kill -TERM "${pid}" >/dev/null 2>&1 || true
    done
  fi

  for _ in $(seq 1 30); do
    if ! api_ready; then
      return 0
    fi
    sleep 1
  done
  echo "Existing master did not stop cleanly" >&2
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

topology_node_count() {
  python3 - "${api_url}/state/topology" <<'PY' 2>/dev/null || printf '0\n'
import json
import sys
import urllib.request

with urllib.request.urlopen(sys.argv[1], timeout=5) as response:
    topology = json.load(response)

nodes = topology.get("nodes") or []
print(len(nodes if isinstance(nodes, list) else list(nodes)))
PY
}

get_master_node_id() {
  curl -fsS "${api_url}/node_id" 2>/dev/null || true
}

worker_api_host() {
  local host="$1"
  printf '%s\n' "${host#*@}"
}

bootstrap_peer_for_type() {
  local type="$1"
  local host=""

  if [[ -n "${master_bootstrap_host}" ]]; then
    host="${master_bootstrap_host}"
  elif [[ "${type}" == "line" ]]; then
    host="${master_line_address}"
  else
    host="${master_net_address}"
  fi

  printf '/ip4/%s/tcp/%s\n' "${host}" "${libp2p_port}"
}

start_master() {
  local session="exo-master"
  local command
  local llama_router_environment=""

  if [[ "${restart}" == "true" ]]; then
    screen_quit "${session}"
    stop_master_process
  fi

  if api_ready; then
    echo "Master already running at ${api_url}"
    return 0
  fi

  if [[ -n "${llama_replicas_file}" ]]; then
    if [[ ! -f "${llama_replicas_file}" ]]; then
      echo "Missing llama replica config: ${llama_replicas_file}" >&2
      return 1
    fi
    llama_router_environment="EXO_LLAMA_REPLICAS_FILE='${llama_replicas_file}'"
  fi

  command="mkdir -p \"\$HOME/.exo\" && cd '${repo_dir}' && ${llama_router_environment} EXO_NODE_NAME='${master_node_name}' EXO_LIBP2P_NAMESPACE='${namespace}' EXO_MODELS_DIRS='${models_dir}' EXO_OFFLINE=true EXO_SKIP_WARMUP='${skip_warmup}' EXO_DEBUG_PIPELINE='${debug_pipeline}' EXO_NODE_TIMEOUT_SECONDS='${node_timeout_seconds}' PATH=\"\$HOME/.local/bin:\$PATH\" caffeinate -ims '${exo_bin}' --force-master --offline --no-downloads --api-port '${api_port}' --libp2p-port '${libp2p_port}' '$(fast_synch_flag)'"

  echo "Starting master in local screen: ${session}"
  screen -dmS "${session}" bash -lc "${command}"
  wait_for_api
}

start_worker() {
  local role="$1"
  local host="$2"
  local worker_connect_type="$3"
  local session="exo-${role}"
  local fallback_host=""
  local node_name=""
  local master_id=""
  local remote_id=""
  local worker_bootstrap_peer=""
  local remote_cleanup_command
  local remote_node_id_command
  local remote_identity_reset_command
  local remote_command

  screen_quit "${session}"

  case "${role}" in
    worker1) node_name="${WORKER1_NODE_NAME:-node2}" ;;
    worker2) node_name="${WORKER2_NODE_NAME:-node3}" ;;
    worker3) node_name="${WORKER3_NODE_NAME:-node4}" ;;
    *) echo "Unknown worker role: ${role}" >&2; return 2 ;;
  esac

  remote_cleanup_command="if command -v pkill >/dev/null 2>&1; then pkill -TERM -f '${repo_dir}/[.]venv/bin/exo' 2>/dev/null || true; for _ in \$(seq 1 15); do pgrep -f '${repo_dir}/[.]venv/bin/exo' >/dev/null 2>&1 || break; sleep 1; done; pkill -KILL -f '${repo_dir}/[.]venv/bin/exo' 2>/dev/null || true; fi; rm -f \"\$HOME/.exo/exo.pid\""
  remote_node_id_command="'${repo_dir}/.venv/bin/python' -c \"from exo.routing.router import get_node_id_keypair; print(get_node_id_keypair().to_node_id())\""
  remote_identity_reset_command="mkdir -p \"\$HOME/.exo\"; key=\"\$HOME/.exo/node_id.keypair\"; stamp=\$(date +%Y%m%d%H%M%S); if test -f \"\$key\"; then mv \"\$key\" \"\$key.duplicate-${node_name}-\$stamp\"; fi; if test -f \"\$key.lock\"; then mv \"\$key.lock\" \"\$key.lock.duplicate-${node_name}-\$stamp\"; fi; ${remote_node_id_command}"
  worker_bootstrap_peer="$(bootstrap_peer_for_type "${worker_connect_type}")"
  remote_command="mkdir -p \"\$HOME/.exo\"; cd '${repo_dir}' && EXO_NODE_NAME='${node_name}' EXO_LIBP2P_NAMESPACE='${namespace}' EXO_MODELS_DIRS='${models_dir}' EXO_OFFLINE=true EXO_SKIP_WARMUP='${skip_warmup}' EXO_DEBUG_PIPELINE='${debug_pipeline}' EXO_NODE_TIMEOUT_SECONDS='${node_timeout_seconds}' PATH=\"\$HOME/.local/bin:\$PATH\" caffeinate -ims '${exo_bin}' -v --offline --no-downloads --api-port '${api_port}' --libp2p-port '${libp2p_port}' --bootstrap-peers '${worker_bootstrap_peer}' '$(fast_synch_flag)'"

  echo "Starting ${role} via ${worker_connect_type}:${host} in local screen: ${session}"
  echo "${role} bootstrap peer: ${worker_bootstrap_peer}"
  # Run cleanup synchronously so failures are visible and a stale worker
  # cannot make the detached replacement exit immediately on a port conflict.
  if ! connection_run type="${worker_connect_type}" host="${host}" command="${remote_cleanup_command}"; then
    if [[ "${worker_connect_type}" != "line" ]]; then
      return 1
    fi
    fallback_host="$(connection_host_for_role "${role}" net 2>/dev/null || true)"
    if [[ -z "${fallback_host}" || "${fallback_host}" == "${host}" ]]; then
      return 1
    fi
    echo "${role} line control unavailable; falling back to ${fallback_host}"
    host="${fallback_host}"
    worker_connect_type="net"
    worker_bootstrap_peer="$(bootstrap_peer_for_type "${worker_connect_type}")"
    remote_command="mkdir -p \"\$HOME/.exo\"; cd '${repo_dir}' && EXO_NODE_NAME='${node_name}' EXO_LIBP2P_NAMESPACE='${namespace}' EXO_MODELS_DIRS='${models_dir}' EXO_OFFLINE=true EXO_SKIP_WARMUP='${skip_warmup}' EXO_DEBUG_PIPELINE='${debug_pipeline}' EXO_NODE_TIMEOUT_SECONDS='${node_timeout_seconds}' PATH=\"\$HOME/.local/bin:\$PATH\" caffeinate -ims '${exo_bin}' -v --offline --no-downloads --api-port '${api_port}' --libp2p-port '${libp2p_port}' --bootstrap-peers '${worker_bootstrap_peer}' '$(fast_synch_flag)'"
    echo "${role} fallback bootstrap peer: ${worker_bootstrap_peer}"
    connection_run type=net host="${host}" command="${remote_cleanup_command}"
  fi

  master_id="$(get_master_node_id)"
  if [[ -n "${master_id}" ]]; then
    remote_id="$(connection_run type="${worker_connect_type}" host="${host}" command="${remote_node_id_command}" 2>/dev/null | tail -n 1 || true)"
    if [[ "${remote_id}" == "${master_id}" ]]; then
      echo "${role} has the same node id as master (${master_id}); rotating worker keypair"
      remote_id="$(connection_run type="${worker_connect_type}" host="${host}" command="${remote_identity_reset_command}" 2>/dev/null | tail -n 1 || true)"
      if [[ -z "${remote_id}" || "${remote_id}" == "${master_id}" ]]; then
        echo "${role} failed to obtain a unique node id" >&2
        return 1
      fi
      echo "${role} new node id: ${remote_id}"
    elif [[ -n "${remote_id}" ]]; then
      echo "${role} node id: ${remote_id}"
    else
      echo "${role} node id could not be read before launch; continuing with startup"
    fi
  fi

  screen -dmS "${session}" ssh -tt \
    -o BatchMode=yes \
    -o ConnectTimeout="${ssh_timeout}" \
    -o ConnectionAttempts=1 \
    -o ServerAliveInterval=2 \
    -o ServerAliveCountMax=2 \
    -o StrictHostKeyChecking=accept-new \
    "${host}" "${remote_command}"

  for _ in $(seq 1 10); do
    if curl -fsS "http://$(worker_api_host "${host}"):${api_port}/node_id" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  echo "${role} SSH session started but worker API did not respond yet; waiting for topology join"
}

echo "Cluster namespace: ${namespace}"
echo "Control path: requested=${requested_connect_type}"
echo "Worker paths: worker1=${worker1_type}:${worker1_host}, worker2=${worker2_type}:${worker2_host}, worker3=${worker3_type}:${worker3_host}"
echo "Master API: ${api_url}"
echo "Bootstrap peers: line=$(bootstrap_peer_for_type line), net=$(bootstrap_peer_for_type net)"

start_master
if [[ "${restart}" != "true" ]] && [[ "$(topology_node_count)" -ge "${min_nodes}" ]]; then
  echo "Cluster already has ${min_nodes} nodes; leaving workers untouched"
else
  worker_start_failures=()
  start_worker worker1 "${worker1_host}" "${worker1_type}" || worker_start_failures+=("worker1")
  start_worker worker2 "${worker2_host}" "${worker2_type}" || worker_start_failures+=("worker2")
  start_worker worker3 "${worker3_host}" "${worker3_type}" || worker_start_failures+=("worker3")
  if [[ "${#worker_start_failures[@]}" -gt 0 ]]; then
    echo "Workers failed to start: ${worker_start_failures[*]}" >&2
  fi
fi
wait_for_nodes

echo
echo "Web UI:"
echo "  ${api_url}"
echo "Claude Code:"
echo "  ANTHROPIC_BASE_URL=${api_url} ANTHROPIC_API_KEY=x claude"
echo "Status:"
echo "  scripts/status_4node_exo_cluster.sh"
