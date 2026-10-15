#!/bin/bash
set -euo pipefail
# Narwhal common library - shared utilities for cluster scripts
# Usage: source /home/vagrant/scripts/common/lib.sh

# Detect the host OS release for version-specific provisioning branches.
# os_version_id -> "24.04" | "26.04" | "unknown"; os_codename -> "noble" | "resolute" | ...
# Pattern: ver="$(os_version_id)"; case "$ver" in 24.04) ... ;; 26.04) ... ;; esac
os_version_id() {
  ( . /etc/os-release 2>/dev/null && echo "${VERSION_ID:-unknown}" )
}
os_codename() {
  ( . /etc/os-release 2>/dev/null && echo "${VERSION_CODENAME:-unknown}" )
}

# Generate a 24-char URL-safe random password
generate_password() {
  openssl rand -base64 16 | tr -d '=/+' | head -c 24
}

# read_secret_field <secret> <ns> <data-key>: prints the decoded value ("" when the Secret or
# key does not exist). Fails closed: only a kubectl NotFound counts as "absent"; any other
# error (API blip, timeout, RBAC) returns 1 so the caller aborts instead of defaulting over
# real credentials. Values are never logged.
read_secret_field() {
  local out rc=0
  out=$(kubectl get secret "$1" -n "$2" -o "jsonpath={.data.$3}" 2>&1) || rc=$?
  if [ "${rc}" -ne 0 ]; then
    case "${out}" in
      *NotFound*|*"not found"*) return 0 ;;
    esac
    echo "ERROR: cannot read secret $2/$1 (kubectl failed, not NotFound); refusing to default credentials." >&2
    return 1
  fi
  if [ -n "${out}" ]; then
    printf '%s' "${out}" | base64 -d 2>/dev/null || { echo "ERROR: secret $2/$1 key $3 is not valid base64." >&2; return 1; }
  fi
}

# resolve_s3_credentials [<existing-secret> <ns> <access-key-field> <secret-key-field>]
# Single source of truth for the in-cluster SeaweedFS S3 credentials shared by 07-cnpg.sh
# (cnpg-s3-credentials) and 08-4-storage.sh (velero-s3-credentials).
# Order per key: env override -> velero-s3-credentials (storage) -> the optional existing
# Secret named by the arguments (07 passes its own cnpg-s3-credentials so a re-run keeps
# real values) -> "admin" (D-velero). 07 runs BEFORE 08-4 creates the Velero Secret, so on
# a first run the default must live here. Defaulting only happens on NotFound; any other
# kubectl error returns non-zero (fail closed). An empty key is also a hard failure: an
# empty aws_secret_access_key makes the AWS SDK fall through to EC2 IMDS (2026-10-16).
# Sets S3_ACCESS_KEY and S3_SECRET_KEY in the caller's shell. Never prints values.
resolve_s3_credentials() {
  local ak="${S3_ACCESS_KEY:-}" sk="${S3_SECRET_KEY:-}"
  if [ -z "${ak}" ]; then
    ak=$(read_secret_field velero-s3-credentials storage access-key) || return 1
    if [ -z "${ak}" ] && [ $# -ge 4 ]; then ak=$(read_secret_field "$1" "$2" "$3") || return 1; fi
  fi
  if [ -z "${sk}" ]; then
    sk=$(read_secret_field velero-s3-credentials storage secret-key) || return 1
    if [ -z "${sk}" ] && [ $# -ge 4 ]; then sk=$(read_secret_field "$1" "$2" "$4") || return 1; fi
  fi
  S3_ACCESS_KEY="${ak:-admin}"
  S3_SECRET_KEY="${sk:-admin}"
  if [ -z "${S3_ACCESS_KEY}" ] || [ -z "${S3_SECRET_KEY}" ]; then
    echo "ERROR: resolved S3 credentials are empty; refusing to create a secret with an empty key." >&2
    return 1
  fi
}

# Wait for Kubernetes API server to be reachable
wait_for_api_server() {
  local max_attempts="${1:-30}"
  echo "Waiting for API server..."
  for i in $(seq 1 "${max_attempts}"); do
    if kubectl get nodes &>/dev/null; then
      echo "API server is reachable"
      return 0
    fi
    echo "  Attempt ${i}/${max_attempts}: API server not ready, waiting 10s..."
    sleep 10
  done
  echo "ERROR: API server did not become ready after ${max_attempts} attempts"
  return 1
}

# Ensure a Kubernetes namespace exists
ensure_namespace() {
  local ns="$1"
  kubectl create namespace "${ns}" --dry-run=client -o yaml | kubectl apply -f -
}

# Ensure a secret key exists (create with random value if absent)
# Usage: ensure_secret_key <namespace> <secret-name> <key>
ensure_secret_key() {
  local ns="$1"
  local secret="$2"
  local key="$3"
  if ! kubectl get secret "${secret}" -n "${ns}" &>/dev/null; then
    return 1
  fi
  if ! kubectl get secret "${secret}" -n "${ns}" -o jsonpath="{.data.${key}}" 2>/dev/null | grep -q .; then
    local value
    value=$(generate_password)
    kubectl patch secret "${secret}" -n "${ns}" \
      --type='json' \
      -p="[{\"op\":\"add\",\"path\":\"/data/${key}\",\"value\":\"$(echo -n "${value}" | base64 -w0)\"}]"
  fi
}
