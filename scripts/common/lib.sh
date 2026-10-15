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

# resolve_s3_credentials: single source of truth for the in-cluster SeaweedFS S3 credentials
# shared by 07-cnpg.sh (cnpg-s3-credentials) and 08-4-storage.sh (velero-s3-credentials).
# Order: env override -> existing velero-s3-credentials Secret -> "admin" (D-velero).
# 07 runs BEFORE 08-4 creates that Secret, so the Secret lookup is empty on a first run;
# the default must therefore live here, not at each call site. An empty key is a hard
# failure: an empty aws_secret_access_key makes the AWS SDK fall through to EC2 IMDS and
# CNPG barman/Velero crashloop or fail over (2026-10-16).
# Sets S3_ACCESS_KEY and S3_SECRET_KEY in the caller's shell. Logs go to stderr.
resolve_s3_credentials() {
  local ak="${S3_ACCESS_KEY:-}" sk="${S3_SECRET_KEY:-}"
  if [ -z "${ak}" ]; then
    ak=$(kubectl get secret velero-s3-credentials -n storage \
      -o jsonpath='{.data.access-key}' 2>/dev/null | base64 -d 2>/dev/null || true)
  fi
  if [ -z "${sk}" ]; then
    sk=$(kubectl get secret velero-s3-credentials -n storage \
      -o jsonpath='{.data.secret-key}' 2>/dev/null | base64 -d 2>/dev/null || true)
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
