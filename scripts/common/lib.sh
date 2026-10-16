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
  local out err rc=0 errf
  errf=$(mktemp) || return 1
  out=$(kubectl get secret "$1" -n "$2" -o "jsonpath={.data.$3}" 2>"${errf}") || rc=$?
  err=$(cat "${errf}" 2>/dev/null || true)
  rm -f "${errf}"
  if [ "${rc}" -ne 0 ]; then
    # Only kubectl's own server NotFound counts as absent. A bare "not found" would also
    # match the shell's "kubectl: command not found" (rc 127) and fail open.
    if [ "${rc}" -ne 127 ]; then
      case "${err}" in
        *"Error from server (NotFound)"*) return 0 ;;
      esac
    fi
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

# is_cidr <value> -- strict IPv4 CIDR (octets <= 255, prefix 0-32).
is_cidr() {
  local ip prefix o
  [[ "$1" =~ ^[0-9]{1,3}(\.[0-9]{1,3}){3}/[0-9]{1,2}$ ]] || return 1
  ip="${1%/*}"; prefix="${1#*/}"
  [ "${prefix}" -le 32 ] || return 1
  for o in ${ip//./ }; do [ "$((10#${o}))" -le 255 ] || return 1; done
}

# resolve_apisix_admin_cidr -- prints the pod CIDR allowed to reach the APISIX Admin API.
# Deliberately NOT POD_NETWORK_CIDR: that value also drives pod-cidr-consistency-check, NFS
# exports and trusted proxies, and a legacy Cilium pool (Kakao: 10.0.0.0/8 pool, CiliumNodes
# 10.0.x.0/24) differs from it. Order (D1): APISIX_ADMIN_CIDR env -> cilium-config with
# ipam=cluster-pool -> cluster-pool-ipv4-cidr -> kubeadm-config podSubnet -> chart default
# 10.244.0.0/16. Defaulting happens only when a ConfigMap is NotFound; any other kubectl
# error, an unparsable value or a multi-CIDR pool returns non-zero (fail closed): silently
# defaulting on a live cluster re-creates the Admin API 403 (ResourceSyncAborted).
resolve_apisix_admin_cidr() {
  local cidr="${APISIX_ADMIN_CIDR:-}" out err ipam
  err=$(mktemp)
  if [ -z "${cidr}" ]; then
    if out=$(kubectl -n kube-system get configmap cilium-config \
        -o 'jsonpath={.data.ipam}{"\t"}{.data.cluster-pool-ipv4-cidr}' 2>"${err}"); then
      ipam="${out%%$'\t'*}"
      [ "${ipam}" = cluster-pool ] && cidr="${out#*$'\t'}"
    elif ! grep -q 'Error from server (NotFound)' "${err}"; then
      echo "ERROR: cannot read configmap/cilium-config: $(head -c 200 "${err}")" >&2
      rm -f "${err}"; return 1
    fi
  fi
  if [ -z "${cidr}" ]; then
    if out=$(kubectl -n kube-system get configmap kubeadm-config \
        -o 'jsonpath={.data.ClusterConfiguration}' 2>"${err}"); then
      cidr=$(printf '%s\n' "${out}" | awk '/^[[:space:]]*podSubnet:/ {print $2; exit}')
    elif ! grep -q 'Error from server (NotFound)' "${err}"; then
      echo "ERROR: cannot read configmap/kubeadm-config: $(head -c 200 "${err}")" >&2
      rm -f "${err}"; return 1
    fi
  fi
  rm -f "${err}"
  cidr="${cidr:-10.244.0.0/16}"
  if ! is_cidr "${cidr}"; then
    echo "ERROR: APISIX admin allowlist '${cidr}' is not a single IPv4 CIDR; set APISIX_ADMIN_CIDR" >&2
    return 1
  fi
  printf '%s\n' "${cidr}"
}

# --- APISIX Admin API keys (Secret platform-system/apisix-admin-key, fields key + viewer) ---
# The upstream APISIX/ingress-controller chart defaults are public. They are recognised by
# sha256 only (D-apisix-key: no key literal in git, not even the one to refuse) and are never
# reused: anything matching is replaced by a fresh random key, which is also the rotation path.
APISIX_DEFAULT_KEY_SHA256S="477a2767fa27931db11a3581100a4124b5720f6b6468ddd64062170cec157185 228ab1b6080ec4178302ba76f9fa4af4f2dc14fc105a364b324bdb9045af03a1"

# apisix_key_usable <value>: succeeds for a real, non-default key. Empty values, env
# placeholders (`${{APISIX_ADMIN_KEY}}`) and the public upstream defaults fail.
apisix_key_usable() {
  local h
  case "$1" in ''|*'{'*|*'$'*) return 1 ;; esac
  h=$(printf '%s' "$1" | openssl dgst -sha256 -r | cut -d' ' -f1)
  case " ${APISIX_DEFAULT_KEY_SHA256S} " in *" ${h} "*) return 1 ;; esac
  return 0
}

# apisix_cm_key <admin|viewer>: key of that role in configmap/apisix (YAML list layout:
# `- key: <k>` precedes `name: <role>`; name may be quoted). "" when absent or on any error.
apisix_cm_key() {
  kubectl get configmap apisix -n platform-system -o jsonpath='{.data.config\.yaml}' 2>/dev/null \
    | awk -v role="$1" '/^[[:space:]]*-[[:space:]]*key:/ {k=$3; gsub(/"/, "", k)} $0 ~ "^[[:space:]]*name:[[:space:]]*\"?" role "\"?[[:space:]]*$" && !f {print k; f=1}' || true
}

# shellcheck disable=SC2034  # outputs are read by the sourcing script
# resolve_apisix_keys: sets APISIX_ADMIN_KEY / APISIX_VIEWER_KEY and APISIX_KEYS_GENERATED
# (true when either was newly generated, i.e. consumers must restart). Per role: the existing
# Secret value, else the bootstrap configmap literal, else a new `openssl rand -hex 16`; a
# default/placeholder/empty value is skipped. A non-NotFound Secret read error returns 1
# (fail closed, never regenerate over real credentials). Never prints values.
resolve_apisix_keys() {
  local role field var v
  APISIX_KEYS_GENERATED=false
  for role in admin viewer; do
    field=key; var=APISIX_ADMIN_KEY
    if [ "${role}" = viewer ]; then field=viewer; var=APISIX_VIEWER_KEY; fi
    v=$(read_secret_field apisix-admin-key platform-system "${field}") || return 1
    apisix_key_usable "${v}" || v=$(apisix_cm_key "${role}")
    if ! apisix_key_usable "${v}"; then
      v=$(openssl rand -hex 16)
      APISIX_KEYS_GENERATED=true
    fi
    printf -v "${var}" '%s' "${v}"
  done
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
