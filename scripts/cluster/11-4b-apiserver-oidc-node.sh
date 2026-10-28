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
#   The apiserver reads --oidc-ca-file only at start, so a new CA needs a restart. The manifest
#   records the CA it was started for in the annotation narwhal.dasomel.io/oidc-ca-sha256; when
#   that differs from the installed CA, updating it changes the manifest content and kubelet
#   recreates the static pod (touch alone does not change the pod hash, and crictl is not on
#   sudo's PATH on Kakao). This also heals a run that died between installing the CA and the
#   restart, and the manifest never leaves the manifests directory.
#   A restart is confirmed by the kube-apiserver PID changing, not by /readyz alone: until
#   kubelet notices the changed manifest and stops it, the OLD process keeps answering `ok`, and
#   the caller (apply-apiserver-oidc-kakao.sh) would then restart the next master while this one
#   has not restarted yet, leaving two apiservers down at once.
#   Cost: one static-pod restart + PID-change wait + local /readyz wait (~15-45 s per
#   control-plane node) on first apply, on CA rotation, and once on a manifest from before the
#   annotation; 0 restarts on an idempotent re-run.
#   Escape hatch: Restore /etc/kubernetes/kube-apiserver.yaml.pre-oidc-<UTC stamp> to
#   /etc/kubernetes/manifests/kube-apiserver.yaml on the affected node. The backup is written
#   before the first apply and before every CA/annotation update, and never overwrites an
#   earlier one.
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
# Backups MUST live outside the static-pod manifests directory (default: /etc/kubernetes).
# kubelet reads every non-dot file in /etc/kubernetes/manifests and keys static pods by
# namespace/name, so a backup there is a same-name copy of kube-apiserver: it conflicts with
# or shadows the real manifest instead of adding a pod, and which copy kubelet honours is not
# something to rely on.
APISERVER_BACKUP_DIR="${APISERVER_BACKUP_DIR:-$(dirname "$(dirname "${APISERVER_MANIFEST}")")}"
READYZ_URL="${READYZ_URL:-https://127.0.0.1:6443/readyz}"
READYZ_CMD="${READYZ_CMD:-}"
READYZ_MAX_ATTEMPTS="${READYZ_MAX_ATTEMPTS:-60}"
READYZ_SLEEP="${READYZ_SLEEP:-5}"
RESTART_INITIAL_SLEEP="${RESTART_INITIAL_SLEEP:-5}"
# Runs in the node's host PID namespace (this script runs under sudo on the node); -o picks
# the OLDEST match, so the recorded PID stays the old process until it is gone.
APISERVER_PID_CMD="${APISERVER_PID_CMD:-pgrep -xo kube-apiserver}"
PID_CHANGE_MAX_ATTEMPTS="${PID_CHANGE_MAX_ATTEMPTS:-60}"
PID_CHANGE_SLEEP="${PID_CHANGE_SLEEP:-2}"
CA_ANNOTATION="narwhal.dasomel.io/oidc-ca-sha256"

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

apiserver_pid() {
  local pid
  pid="$(eval "${APISERVER_PID_CMD}" 2>/dev/null | head -n 1 | tr -d '[:space:]' || true)"
  printf '%s' "${pid}"
}

# Block until kubelet has replaced the kube-apiserver process; $1 is the PID recorded right
# before the manifest changed. /readyz alone is not a restart signal: until kubelet notices the
# change and stops it, the OLD process keeps answering `ok`.
# No PID before the change (pgrep missing, process not visible): fall back to the previous
# behaviour, a fixed sleep and then /readyz. That is best effort only, the old apiserver may
# still answer `ok`, so it applies solely when the PID cannot be read at all.
wait_apiserver_restart() {
  local old_pid="$1" attempt new_pid
  if [ -z "${old_pid}" ]; then
    echo "WARN: no kube-apiserver PID found before the manifest change; cannot confirm the restart, waiting ${RESTART_INITIAL_SLEEP}s then polling readyz"
    wait_readyz "${RESTART_INITIAL_SLEEP}" || return 1
    return 0
  fi
  for attempt in $(seq 1 "${PID_CHANGE_MAX_ATTEMPTS}"); do
    new_pid="$(apiserver_pid)"
    if [ -n "${new_pid}" ] && [ "${new_pid}" != "${old_pid}" ]; then
      echo "kube-apiserver restarted (pid ${old_pid} -> ${new_pid}); waiting for readyz..."
      wait_readyz 0 || return 1
      return 0
    fi
    echo "Waiting for kubelet to restart kube-apiserver (pid ${old_pid}) (${attempt}/${PID_CHANGE_MAX_ATTEMPTS})..."
    if [ "${PID_CHANGE_SLEEP}" -gt 0 ]; then
      sleep "${PID_CHANGE_SLEEP}"
    fi
  done
  echo "ERROR: kube-apiserver (pid ${old_pid}) was not restarted by kubelet within ${PID_CHANGE_MAX_ATTEMPTS} attempts after the manifest change" >&2
  return 1
}

# Backup goes outside the manifests directory (checked above) and never overwrites an earlier
# one: two runs inside the same UTC second get distinct names.
backup_manifest() {
  local utc_stamp backup_path n=1
  utc_stamp="$(date -u '+%Y%m%dT%H%M%SZ')"
  backup_path="${APISERVER_BACKUP_DIR}/kube-apiserver.yaml.pre-oidc-${utc_stamp}"
  while [ -e "${backup_path}" ]; do
    n=$((n + 1))
    backup_path="${APISERVER_BACKUP_DIR}/kube-apiserver.yaml.pre-oidc-${utc_stamp}-${n}"
  done
  cp -p "${APISERVER_MANIFEST}" "${backup_path}"
  echo "Backed up manifest to ${backup_path}"
}

mkdir -p "$(dirname "${OIDC_CA_DEST}")"
if [ ! -f "${OIDC_CA_DEST}" ] || ! cmp -s "${CA_SRC}" "${OIDC_CA_DEST}"; then
  if [ "${CA_SRC}" != "${OIDC_CA_DEST}" ]; then
    install -m 0644 "${CA_SRC}" "${OIDC_CA_DEST}"
  fi
  echo "Installed OIDC CA certificate to ${OIDC_CA_DEST}"
else
  echo "OIDC CA certificate at ${OIDC_CA_DEST} already up to date"
fi
openssl x509 -in "${OIDC_CA_DEST}" -noout -subject >/dev/null
ca_sha="$(openssl dgst -sha256 -r "${OIDC_CA_DEST}" | cut -d' ' -f1)"

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
  backup_manifest

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
  ] | .metadata.annotations.\"${CA_ANNOTATION}\" = \"${ca_sha}\"" "${stage_file}"

  staged_cmd="$(yq '.spec.containers[0].command[]' "${stage_file}")"
  for flag in "${REQUIRED_FLAGS[@]}"; do
    if ! grep -qxF -- "${flag}" <<<"${staged_cmd}"; then
      rm -f "${stage_file}"
      echo "ERROR: staged manifest is missing expected flag ${flag}" >&2
      exit 1
    fi
  done

  old_pid="$(apiserver_pid)"
  mv "${stage_file}" "${APISERVER_MANIFEST}"
  echo "Applied 7 OIDC flags to ${APISERVER_MANIFEST}; waiting for kube-apiserver to restart..."
  wait_apiserver_restart "${old_pid}"
else
  existing_cmd="$(yq '.spec.containers[0].command[]' "${APISERVER_MANIFEST}")"
  for flag in "${REQUIRED_FLAGS[@]}"; do
    if ! grep -qxF -- "${flag}" <<<"${existing_cmd}"; then
      echo "ERROR: ${APISERVER_MANIFEST} has --oidc-issuer-url but is missing required flag ${flag}" >&2
      exit 1
    fi
  done

  loaded_sha="$(yq ".metadata.annotations.\"${CA_ANNOTATION}\" // \"\"" "${APISERVER_MANIFEST}")"
  if [ "${loaded_sha}" != "${ca_sha}" ]; then
    echo "OIDC flags present but the manifest records CA ${loaded_sha:-<none>} (installed: ${ca_sha:0:12}) — updating ${CA_ANNOTATION} so kubelet restarts kube-apiserver with the installed CA..."
    backup_manifest
    stage_file="${manifest_dir}/.kube-apiserver.yaml.oidc.tmp"
    cp -p "${APISERVER_MANIFEST}" "${stage_file}"
    yq -i ".metadata.annotations.\"${CA_ANNOTATION}\" = \"${ca_sha}\"" "${stage_file}"
    old_pid="$(apiserver_pid)"
    mv "${stage_file}" "${APISERVER_MANIFEST}"
    wait_apiserver_restart "${old_pid}"
  else
    echo "OIDC flags and CA already configured in ${APISERVER_MANIFEST} — no-op"
    wait_readyz 0
  fi
fi
