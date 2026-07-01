#!/usr/bin/env bash
set -euo pipefail

config_file="${CONFIG_FILE:-scripts/cluster.env}"
if [[ -f "${config_file}" ]]; then
  # shellcheck disable=SC1090
  source "${config_file}"
fi

# shellcheck disable=SC1091
source "$(dirname "$0")/cluster_connection.sh"

repo_dir="${REPO_DIR:-$HOME/exo}"
connect_type="${CONNECT_TYPE:-line}"

"${repo_dir}/.venv/bin/python" -c "import mlx.core, mlx_lm"

for role in worker1 worker2 worker3; do
  host="$(connection_host_for_role "${role}" "${connect_type}")"
  echo "Syncing validated runtime to ${role}: ${host}"
  rsync -a --partial --whole-file \
    --exclude '__pycache__/' \
    --exclude '.cache/' \
    -e "ssh -o ServerAliveInterval=5 -o ServerAliveCountMax=6 -o ConnectTimeout=${SSH_CONNECT_TIMEOUT:-10} -o StrictHostKeyChecking=accept-new" \
    "${repo_dir}/.venv/" "${host}:${repo_dir}/.venv/"
done

for role in worker1 worker2 worker3; do
  connection_run type=net role="${role}" command="'${repo_dir}/.venv/bin/python' -c 'import mlx.core as mx, mlx_lm; print(mx.__version__)'"
done
