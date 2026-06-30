#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" == "--help" ]]; then
  cat <<'EOF'
Usage:
  scripts/configure_rdma_network.sh

Configures this Mac for exo RDMA over Thunderbolt:
  - removes bridge0 members and destroys bridge0 when present
  - switches to an "exo" network location
  - creates DHCP services for each Thunderbolt hardware port
  - disables Thunderbolt Bridge if it exists

Run this once on each Mac mini. It may require sudo/admin privileges.
EOF
  exit 0
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"${repo_dir}/tmp/set_rdma_network_config.sh"

echo "RDMA network configuration applied on $(hostname)."
echo "Relevant interfaces:"
ifconfig -a | awk '
  /^[a-z0-9]+: / { iface=$1; sub(":", "", iface); block=$0; next }
  iface ~ /^(en[0-9]+|rdma_|bridge0)$/ { block=block "\n" $0 }
  /status: / && iface ~ /^en[0-9]+$/ { print block; block="" }
'

