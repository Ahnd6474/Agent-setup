#!/usr/bin/env bash
set -euo pipefail

repo_dir="${REPO_DIR:-/Users/dshs_llm/exo}"
env_file="${AGENTIC_ENV_FILE:-$HOME/.agentic-local/server.env}"

if [[ -f "${env_file}" ]]; then
  if [[ "$(stat -f '%Lp' "${env_file}")" != "600" ]]; then
    echo "Agent server environment file must have mode 600: ${env_file}" >&2
    exit 1
  fi
  set -a
  # shellcheck disable=SC1090
  source "${env_file}"
  set +a
fi

cd "${repo_dir}"
exec "${repo_dir}/.venv/bin/agent-server"
