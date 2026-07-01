#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "usage: $0 secrets|envs ARCHIVE OUTPUT_DIR [KEY_FILE]" >&2
  exit 2
}

[[ $# -ge 3 ]] || usage
mode=$1
archive=$2
output_dir=$3
key_file=${4:-"$HOME/.config/agent-setup/runtime-backup.key"}

mkdir -p "$output_dir"

case "$mode" in
  secrets)
    [[ -s "$key_file" ]] || {
      echo "missing decryption key: $key_file" >&2
      exit 1
    }
    openssl enc -d -aes-256-cbc -pbkdf2 -iter 600000 \
      -pass "file:$key_file" -in "$archive" |
      zstd -d -q |
      tar -xf - -C "$output_dir"
    ;;
  envs)
    zstd -d -q -c "$archive" | tar -xf - -C "$output_dir"
    ;;
  *)
    usage
    ;;
esac
