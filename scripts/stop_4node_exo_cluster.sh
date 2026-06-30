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
api_port="${API_PORT:-52415}"
libp2p_port="${LIBP2P_PORT:-52416}"
worker1_host="${WORKER1_HOST:-$(connection_host_for_role worker1 "${connect_type}" 2>/dev/null || true)}"
worker2_host="${WORKER2_HOST:-$(connection_host_for_role worker2 "${connect_type}" 2>/dev/null || true)}"
worker3_host="${WORKER3_HOST:-$(connection_host_for_role worker3 "${connect_type}" 2>/dev/null || true)}"

screen_quit() {
  local session="$1"
  screen -S "${session}" -X quit >/dev/null 2>&1 || true
}

remote_stop() {
  local host="$1"
  [[ -z "${host}" ]] && return 0
  ssh -o ConnectTimeout="${SSH_CONNECT_TIMEOUT:-5}" -o StrictHostKeyChecking=accept-new "${host}" \
    "if test -f \"\$HOME/.exo/exo.pid\"; then kill -TERM \$(cat \"\$HOME/.exo/exo.pid\") 2>/dev/null || true; rm -f \"\$HOME/.exo/exo.pid\"; fi" \
    >/dev/null 2>&1 || true
}

screen_quit exo-worker1
screen_quit exo-worker2
screen_quit exo-worker3
screen_quit exo-master

remote_stop "${worker1_host}"
remote_stop "${worker2_host}"
remote_stop "${worker3_host}"

if [[ "${STOP_MASTER:-false}" == "true" ]]; then
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"${api_port}" -sTCP:LISTEN | xargs -r kill -TERM >/dev/null 2>&1 || true
    lsof -tiTCP:"${libp2p_port}" -sTCP:LISTEN | xargs -r kill -TERM >/dev/null 2>&1 || true
  fi
fi

echo "Stopped worker sessions. Set STOP_MASTER=true to stop the local master listener too."
