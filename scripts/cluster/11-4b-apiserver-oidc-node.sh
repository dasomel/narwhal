#!/bin/bash
# 11-4b-apiserver-oidc-node.sh
# Install the Keycloak OIDC CA certificate and ensure all 7 --oidc-* flags are present
# on this control-plane node's kube-apiserver static pod manifest.
#
# D-APISERVER-OIDC-ALL:
#   Reason: 11-4-keycloak-apiserver.sh previously duplicated the yq patch inline over
#   `sshpass -p vagrant` and swallowed errors (`2>/dev/null || echo WARN`). On Kakao Cloud
#   (no vagrant password and no node-to-node SSH trust), master-2 and master-3 never received
#   /etc/kubernetes/pki/oidc-ca.crt or the 7 --oidc-* flags, so ~2/3 of in-cluster OIDC
#   bearer-token requests to the `kubernetes` Service failed with 401 ("invalid bearer token").
#   When the flags already exist but the CA file changed, the apiserver must restart to reload
#   the CA: because `crictl` is not on `sudo`'s PATH on Kakao nodes and `touch` does not alter
#   the Pod spec hash (so kubelet ignores it), we trigger kubelet's static-pod file watcher by
#   moving the manifest out of the manifests directory to ${APISERVER_BACKUP_DIR} and moving it
#   back under an EXIT restore trap.
#   Cost: Sequential static-pod restart + local /readyz wait (~15-45s per control-plane node on
#   first apply or CA rotation; 0 restarts on an idempotent re-run).
#   Escape hatch: Restore /etc/kubernetes/kube-apiserver.yaml.pre-oidc-<UTC stamp> to
#   /etc/kubernetes/manifests/kube-apiserver.yaml on the affected node.
#
# Usage (run with sudo on a control-plane node):
#   sudo scripts/cluster/11-4b-apiserver-oidc-node.sh <issuer_url> [<ca_src>]
set -euo pipefail

# Ensure /usr/local/bin (where 01-prerequisites.sh installs mikefarah/yq) is reachable
# under sudo's secure_path while keeping caller PATH entries first for test stubs.
export PATH="${PATH}:/usr/local/bin"

OIDC_ISSUER_URL="${1:-${OIDC_ISSUER_URL:-}}"
CA_SRC="${2:-${OIDC_CA_SRC:-/tmp/oidc-ca.crt}}"

APISERVER_MANIFEST="${APISERVER_MANIFEST:-/etc/kubernetes/manifests/kube-apiserver.yaml}"
OIDC_CA_DEST="${OIDC_CA_DEST:-/etc/kubernetes/pki/oidc-ca.crt}"
# Backups MUST live outside the static-pod manifests directory (default: /etc/kubernetes);
# kubelet scans every non-dot file in /etc/kubernetes/manifests and would start a backup
# there as a second kube-apiserver static pod.
APISERVER_BACKUP_DIR="${APISERVER_BACKUP_DIR:-$(dirname "$(dirname "${APISERVER_MANIFEST}")")}"
READYZ_URL="${READYZ_URL:-https://127.0.0.1:6443/readyz}"
READYZ_CMD="${READYZ_CMD:-}"
READYZ_MAX_ATTEMPTS="${READYZ_MAX_ATTEMPTS:-60}"
READYZ_SLEEP="${READYZ_SLEEP:-5}"
RESTART_INITIAL_SLEEP="${RESTART_INITIAL_SLEEP:-5}"
CA_RELOAD_OUT_SLEEP="${CA_RELOAD_OUT_SLEEP:-2}"

if [ -z "${OIDC_ISSUER_URL}" ]; then
  echo "ERROR: usage: $0 <issuer_url> [<ca_src>]" >&2
  exit 1
fi
case "${OIDC_ISSUER_URL}" in
  https://*) ;;
  *)
    echo "ERROR: OIDC_ISSUER_URL must start with https:// (got: ${OIDC_ISSUER_URL})" >&2
    exit 1
    ;;
esac

if [ ! -f "${APISERVER_MANIFEST}" ]; then
  echo "ERROR: kube-apiserver manifest not found at ${APISERVER_MANIFEST}" >&2
  exit 1
fi

if [ ! -s "${CA_SRC}" ]; then
  echo "ERROR: OIDC CA certificate source missing or empty at ${CA_SRC}" >&2
  exit 1
fi
if ! grep -q -- "-----BEGIN CERTIFICATE-----" "${CA_SRC}" \
  || ! openssl x509 -in "${CA_SRC}" -noout -subject >/dev/null 2>&1; then
  echo "ERROR: OIDC CA certificate at ${CA_SRC} is not a valid PEM X.509 certificate" >&2
  exit 1
fi

manifest_dir="$(cd "$(dirname "${APISERVER_MANIFEST}")" && pwd)"
mkdir -p "${APISERVER_BACKUP_DIR}"
backup_dir_real="$(cd "${APISERVER_BACKUP_DIR}" && pwd)"
if [ "${backup_dir_real}" = "${manifest_dir}" ]; then
  echo "ERROR: APISERVER_BACKUP_DIR (${backup_dir_real}) must not be the static-pod manifests directory (${manifest_dir})" >&2
  exit 1
fi

wait_readyz() {
  local initial_sleep="${1:-0}"
  if [ "${initial_sleep}" -gt 0 ]; then
    sleep "${initial_sleep}"
  fi
  local attempt body
  for attempt in $(seq 1 "${READYZ_MAX_ATTEMPTS}"); do
    if [ -n "${READYZ_CMD}" ]; then
      body="$(eval "${READYZ_CMD}" 2>/dev/null || true)"
    else
      body="$(curl -sk --max-time 5 "${READYZ_URL}" 2>/dev/null || true)"
    fi
    if [ "${body}" = "ok" ]; then
      echo "kube-apiserver readyz=ok (attempt ${attempt}/${READYZ_MAX_ATTEMPTS})"
      return 0
    fi
    echo "Waiting for kube-apiserver readyz (${attempt}/${READYZ_MAX_ATTEMPTS})..."
    if [ "${READYZ_SLEEP}" -gt 0 ]; then
      sleep "${READYZ_SLEEP}"
    fi
  done
  echo "ERROR: kube-apiserver did not report readyz=ok after ${READYZ_MAX_ATTEMPTS} attempts (${READYZ_URL})" >&2
  return 1
}

ca_changed=false
mkdir -p "$(dirname "${OIDC_CA_DEST}")"
if [ ! -f "${OIDC_CA_DEST}" ] || ! cmp -s "${CA_SRC}" "${OIDC_CA_DEST}"; then
  ca_changed=true
  if [ "${CA_SRC}" != "${OIDC_CA_DEST}" ]; then
    install -m 0644 "${CA_SRC}" "${OIDC_CA_DEST}"
  fi
  echo "Installed OIDC CA certificate to ${OIDC_CA_DEST}"
else
  echo "OIDC CA certificate at ${OIDC_CA_DEST} already up to date"
fi
openssl x509 -in "${OIDC_CA_DEST}" -noout -subject >/dev/null

REQUIRED_FLAGS=(
  "--oidc-issuer-url=${OIDC_ISSUER_URL}"
  "--oidc-client-id=kubernetes"
  "--oidc-username-claim=preferred_username"
  "--oidc-groups-claim=groups"
  "--oidc-username-prefix=oidc:"
  "--oidc-groups-prefix=oidc:"
  "--oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt"
)

if ! grep -q -- "--oidc-issuer-url" "${APISERVER_MANIFEST}"; then
  utc_stamp="$(date -u '+%Y%m%dT%H%M%SZ')"
  backup_path="${APISERVER_BACKUP_DIR}/kube-apiserver.yaml.pre-oidc-${utc_stamp}"
  cp -p "${APISERVER_MANIFEST}" "${backup_path}"
  echo "Backed up manifest to ${backup_path}"

  # Stage as a dotfile inside the manifests directory (kubelet ignores leading-dot files)
  # so the final mv into APISERVER_MANIFEST is atomic on the same filesystem.
  stage_file="${manifest_dir}/.kube-apiserver.yaml.oidc.tmp"
  cp -p "${APISERVER_MANIFEST}" "${stage_file}"
  yq -i ".spec.containers[0].command += [
    \"--oidc-issuer-url=${OIDC_ISSUER_URL}\",
    \"--oidc-client-id=kubernetes\",
    \"--oidc-username-claim=preferred_username\",
    \"--oidc-groups-claim=groups\",
    \"--oidc-username-prefix=oidc:\",
    \"--oidc-groups-prefix=oidc:\",
    \"--oidc-ca-file=/etc/kubernetes/pki/oidc-ca.crt\"
  ]" "${stage_file}"

  staged_cmd="$(yq '.spec.containers[0].command[]' "${stage_file}")"
  for flag in "${REQUIRED_FLAGS[@]}"; do
    if ! grep -qxF -- "${flag}" <<<"${staged_cmd}"; then
      rm -f "${stage_file}"
      echo "ERROR: staged manifest is missing expected flag ${flag}" >&2
      exit 1
    fi
  done

  mv "${stage_file}" "${APISERVER_MANIFEST}"
  echo "Applied 7 OIDC flags to ${APISERVER_MANIFEST}; waiting for kube-apiserver readyz..."
  wait_readyz "${RESTART_INITIAL_SLEEP}"
else
  existing_cmd="$(yq '.spec.containers[0].command[]' "${APISERVER_MANIFEST}")"
  for flag in "${REQUIRED_FLAGS[@]}"; do
    if ! grep -qxF -- "${flag}" <<<"${existing_cmd}"; then
      echo "ERROR: ${APISERVER_MANIFEST} has --oidc-issuer-url but is missing required flag ${flag}" >&2
      exit 1
    fi
  done

  if [ "${ca_changed}" = "true" ]; then
    echo "OIDC flags already present in ${APISERVER_MANIFEST}, but CA certificate changed — restarting static pod via kubelet manifest move-out/move-in..."
    reload_stage="${APISERVER_BACKUP_DIR}/.kube-apiserver.yaml.ca-reload"
    restore_manifest() {
      if [ -f "${reload_stage}" ] && [ ! -f "${APISERVER_MANIFEST}" ]; then
        mv "${reload_stage}" "${APISERVER_MANIFEST}" || true
      fi
    }
    trap restore_manifest EXIT
    mv "${APISERVER_MANIFEST}" "${reload_stage}"
    if [ "${CA_RELOAD_OUT_SLEEP}" -gt 0 ]; then
      sleep "${CA_RELOAD_OUT_SLEEP}"
    fi
    mv "${reload_stage}" "${APISERVER_MANIFEST}"
    trap - EXIT
    wait_readyz "${RESTART_INITIAL_SLEEP}"
  else
    echo "OIDC flags and CA already configured in ${APISERVER_MANIFEST} — no-op"
    wait_readyz 0
  fi
fi
