#!/usr/bin/env bash

# Shared helpers for running commands over the selected control path.
# Usage:
#   connection_run type=line host=node2@10.0.0.2 command='hostname'
#   connection_run type=net role=worker2 command='hostname'

connection_type_default() {
  printf '%s\n' "${CONNECT_TYPE:-line}"
}

connection_normalize_key() {
  printf '%s' "$1" | tr '[:lower:]-' '[:upper:]_'
}

connection_host_for_role() {
  local role="$1"
  local type="${2:-$(connection_type_default)}"
  local role_key type_key typed_var legacy_var value

  role_key="$(connection_normalize_key "${role}")"
  type_key="$(connection_normalize_key "${type}")"
  typed_var="${role_key}_${type_key}_HOST"
  legacy_var="${role_key}_HOST"

  value="${!typed_var:-}"
  if [[ -z "${value}" ]]; then
    value="${!legacy_var:-}"
  fi

  if [[ -z "${value}" ]]; then
    echo "No host configured for role=${role} type=${type}" >&2
    return 1
  fi

  printf '%s\n' "${value}"
}

connection_is_local_host() {
  local host="$1"
  local target="${host#*@}"

  [[ "${target}" == "127.0.0.1" ||
     "${target}" == "localhost" ||
     "${target}" == "$(hostname)" ||
     "${target}" == "$(hostname -s)" ||
     "${target}" == "$(hostname).local" ||
     "${target}" == "$(hostname -s).local" ]]
}

connection_run() {
  local type="" host="" role="" command=""
  local arg

  for arg in "$@"; do
    case "${arg}" in
      type=*) type="${arg#type=}" ;;
      host=*) host="${arg#host=}" ;;
      role=*) role="${arg#role=}" ;;
      command=*) command="${arg#command=}" ;;
      *)
        echo "Unknown connection_run argument: ${arg}" >&2
        return 2
        ;;
    esac
  done

  type="${type:-$(connection_type_default)}"

  case "${type}" in
    line|net) ;;
    *)
      echo "type must be 'line' or 'net' (got: ${type})" >&2
      return 2
      ;;
  esac

  if [[ -z "${host}" && -n "${role}" ]]; then
    host="$(connection_host_for_role "${role}" "${type}")"
  fi

  if [[ -z "${host}" ]]; then
    echo "connection_run requires host=... or role=..." >&2
    return 2
  fi

  if [[ -z "${command}" ]]; then
    echo "connection_run requires command='...'" >&2
    return 2
  fi

  if connection_is_local_host "${host}"; then
    bash -lc "${command}"
  else
    ssh -o ConnectTimeout="${SSH_CONNECT_TIMEOUT:-5}" -o StrictHostKeyChecking=accept-new "${host}" "${command}"
  fi
}
