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
master_host="${MASTER_HOST:-${1:-}}"
worker1_host="${WORKER1_HOST:-${2:-}}"
worker2_host="${WORKER2_HOST:-${3:-}}"
worker3_host="${WORKER3_HOST:-${4:-}}"

if [[ -z "${master_host}" ]]; then master_host="$(connection_host_for_role master "${connect_type}" 2>/dev/null || true)"; fi
if [[ -z "${worker1_host}" ]]; then worker1_host="$(connection_host_for_role worker1 "${connect_type}" 2>/dev/null || true)"; fi
if [[ -z "${worker2_host}" ]]; then worker2_host="$(connection_host_for_role worker2 "${connect_type}" 2>/dev/null || true)"; fi
if [[ -z "${worker3_host}" ]]; then worker3_host="$(connection_host_for_role worker3 "${connect_type}" 2>/dev/null || true)"; fi

if [[ -z "${master_host}" || -z "${worker1_host}" || -z "${worker2_host}" || -z "${worker3_host}" ]]; then
  cat <<'EOF'
Usage:
  cp scripts/cluster.env.example scripts/cluster.env
  scripts/configure_rdma_network_on_cluster.sh

or:
  scripts/configure_rdma_network_on_cluster.sh MASTER WORKER1 WORKER2 WORKER3
EOF
  exit 1
fi

repo_dir="${REPO_DIR:-$HOME/exo}"
hosts=("${master_host}" "${worker1_host}" "${worker2_host}" "${worker3_host}")

for host in "${hosts[@]}"; do
  if [[ "${host}" == "127.0.0.1" || "${host}" == "localhost" || "${host}" == "$(hostname)" || "${host}" == "$(hostname -s)" ]]; then
    echo "Configuring local node (${host})"
    scripts/configure_rdma_network.sh
  else
    echo "Configuring remote node (${host}) via ${connect_type}"
    connection_run type="${connect_type}" host="${host}" command="cd '${repo_dir}' && scripts/configure_rdma_network.sh"
  fi
done

echo "RDMA network configuration requested on all nodes."
