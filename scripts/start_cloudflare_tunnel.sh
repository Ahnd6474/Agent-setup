#!/usr/bin/env bash
set -euo pipefail

config_file="${CLOUDFLARE_CONFIG:-$HOME/.cloudflared/config.yml}"
token_file="${CLOUDFLARE_TUNNEL_TOKEN_FILE:-}"
keychain_service="${CLOUDFLARE_KEYCHAIN_SERVICE:-exo-cloudflare-tunnel-token}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is required. Install it with: brew install cloudflared" >&2
  exit 1
fi

if [[ -f "${config_file}" ]]; then
  cloudflared tunnel --config "${config_file}" ingress validate
  exec cloudflared tunnel --config "${config_file}" run
fi

if [[ -n "${token_file}" ]]; then
  if [[ ! -f "${token_file}" ]]; then
    echo "Missing Cloudflare Tunnel token file: ${token_file}" >&2
    exit 1
  fi
  exec cloudflared tunnel run --token-file "${token_file}"
fi

if [[ -z "${TUNNEL_TOKEN:-}" ]] && command -v security >/dev/null 2>&1; then
  TUNNEL_TOKEN="$(
    security find-generic-password \
      -a "${USER}" \
      -s "${keychain_service}" \
      -w 2>/dev/null || true
  )"
  export TUNNEL_TOKEN
fi

if [[ -n "${TUNNEL_TOKEN:-}" ]]; then
  exec cloudflared tunnel run
fi

echo "No Cloudflare Tunnel credentials found." >&2
echo "Provide ${config_file}, CLOUDFLARE_TUNNEL_TOKEN_FILE, or the ${keychain_service} Keychain item." >&2
exit 1
