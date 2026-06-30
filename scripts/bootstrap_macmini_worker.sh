#!/usr/bin/env bash
set -euo pipefail

MASTER_PUBLIC_KEY="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIKBRsymZ+Qo0T4qqVK9FKvagD4X4XH3Ws+j65BnYvQ1r dshs_llm@exo-cluster"

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"
touch "$HOME/.ssh/authorized_keys"
chmod 600 "$HOME/.ssh/authorized_keys"

if ! grep -qxF "$MASTER_PUBLIC_KEY" "$HOME/.ssh/authorized_keys"; then
  printf '%s\n' "$MASTER_PUBLIC_KEY" >> "$HOME/.ssh/authorized_keys"
fi

sudo /usr/sbin/systemsetup -setremotelogin on

echo "hostname=$(hostname)"
echo "user=$(whoami)"
echo "ips:"
ifconfig | awk '/inet / && $2 !~ /^127\./ {print "  " $2}'
echo "ssh=enabled"
echo "master_key=installed"
