#!/bin/bash
set -euo pipefail

# 08-6b-harbor-containerd-tls-node.sh — Configure containerd to verify Harbor's TLS
# certificate with the Narwhal Root CA instead of skip_verify = true (KISA-IMG-01).
#
# D-HARBOR-NODE-CA: containerd is installed in Phase 1 (scripts/common/02-containerd.sh)
# before Kubernetes and cert-manager exist, so the platform CA cannot be installed there.
# This idempotent node script runs once cert-manager has issued narwhal-wildcard-tls
# (invoked by 08-6-tls-routes.sh on initial bring-up and on CA rotation, by join scripts
# on late-joining nodes, or directly by an operator migrating an existing node).
# Cost: every node needs the CA refreshed on Root CA rotation (handled by re-running
# 08-6-tls-routes.sh on Vagrant or ./scripts/cloud/apply-harbor-ca-kakao.sh on Kakao).
# Escape hatch: re-run this script with a corrected CA PEM file; containerd reads
# /etc/containerd/certs.d dynamically on pull without restarting the daemon.
#
# Usage:
#   sudo bash scripts/cluster/08-6b-harbor-containerd-tls-node.sh [CA_PEM_FILE] [DOMAIN]
#   # If CA_PEM_FILE is omitted, reads platform-system/narwhal-wildcard-tls (.data.ca.crt)
#   # via kubectl (uses KUBECONFIG if set, else /home/vagrant/.kube/config-local or
#   # /etc/kubernetes/admin.conf).

CA_ARG="${1:-${HARBOR_CA_FILE:-}}"
DOMAIN="${2:-${DOMAIN:-local.narwhal.internal}}"
CERTS_DIR="${CERTS_DIR:-/etc/containerd/certs.d}"
CONTAINERD_CONFIG="${CONTAINERD_CONFIG:-/etc/containerd/config.toml}"

# Use sudo when non-root unless caller explicitly overrode SUDO (e.g., tests setting SUDO="").
if [ "${SUDO+x}" = "x" ]; then
  SUDO_CMD="${SUDO}"
elif [ "$(id -u)" -eq 0 ]; then
  SUDO_CMD=""
else
  SUDO_CMD="sudo"
fi

run_priv() {
  if [ -n "${SUDO_CMD}" ]; then
    "${SUDO_CMD}" "$@"
  else
    "$@"
  fi
}

case "${DOMAIN}" in
  ""|*[!A-Za-z0-9.-]*|-*|.*|*..)
    echo "ERROR: invalid DOMAIN for Harbor containerd TLS config: '${DOMAIN}'" >&2
    exit 1
    ;;
esac

TMP_CA=""
cleanup() {
  if [ -n "${TMP_CA}" ] && [ -f "${TMP_CA}" ]; then
    rm -f "${TMP_CA}"
  fi
}
trap cleanup EXIT

if [ -n "${CA_ARG}" ]; then
  if [ ! -r "${CA_ARG}" ] || [ ! -s "${CA_ARG}" ]; then
    echo "ERROR: Harbor CA file is missing, unreadable, or empty: ${CA_ARG}" >&2
    exit 1
  fi
  CA_SRC="${CA_ARG}"
else
  if ! command -v kubectl >/dev/null 2>&1; then
    echo "ERROR: no CA file given and kubectl is not on PATH" >&2
    exit 1
  fi
  if [ -z "${KUBECONFIG:-}" ]; then
    if [ -r /home/vagrant/.kube/config-local ]; then
      export KUBECONFIG=/home/vagrant/.kube/config-local
    elif [ -r /etc/kubernetes/admin.conf ]; then
      export KUBECONFIG=/etc/kubernetes/admin.conf
    fi
  fi
  CA_B64="$(kubectl get secret narwhal-wildcard-tls -n platform-system \
    -o jsonpath='{.data.ca\.crt}' 2>/dev/null || true)"
  if [ -z "${CA_B64}" ]; then
    echo "ERROR: platform-system/narwhal-wildcard-tls has no ca.crt; refusing to fall back to skip_verify" >&2
    exit 1
  fi
  TMP_CA="$(mktemp /tmp/harbor-ca.XXXXXX)"
  chmod 0600 "${TMP_CA}"
  if ! printf '%s' "${CA_B64}" | base64 -d > "${TMP_CA}" 2>/dev/null || [ ! -s "${TMP_CA}" ]; then
    echo "ERROR: failed to base64-decode ca.crt from platform-system/narwhal-wildcard-tls" >&2
    exit 1
  fi
  CA_SRC="${TMP_CA}"
fi

# Fail closed before touching /etc/containerd/certs.d: validate every PEM block and reject
# empty, non-PEM, or unparseable input so a bad CA cannot overwrite a working trust anchor.
if ! grep -q "BEGIN CERTIFICATE" "${CA_SRC}"; then
  echo "ERROR: Harbor CA input does not contain a PEM certificate block: ${CA_SRC}" >&2
  exit 1
fi
if ! openssl x509 -in "${CA_SRC}" -noout -subject >/dev/null 2>&1; then
  echo "ERROR: Harbor CA input is not a valid X.509 certificate: ${CA_SRC}" >&2
  exit 1
fi
CA_FP="$(openssl x509 -in "${CA_SRC}" -noout -fingerprint -sha256 | sed 's/^.*=//')"

if [ -f "${CONTAINERD_CONFIG}" ]; then
  if ! grep -Eq 'config_path[[:space:]]*=[[:space:]]*"/etc/containerd/certs\.d"' "${CONTAINERD_CONFIG}"; then
    echo "WARN: ${CONTAINERD_CONFIG} does not set config_path = \"/etc/containerd/certs.d\"; containerd may ignore certs.d" >&2
  fi
fi

HARBOR_HOST="harbor.${DOMAIN}"
HARBOR_DIR="${CERTS_DIR}/${HARBOR_HOST}"
CA_DEST="${HARBOR_DIR}/ca.crt"
HOSTS_DEST="${HARBOR_DIR}/hosts.toml"

# Desired hosts.toml: verifies TLS against ca.crt, never skip_verify.
DESIRED_HOSTS="$(cat <<HTOML
server = "https://${HARBOR_HOST}"

[host."https://${HARBOR_HOST}"]
  capabilities = ["pull", "resolve"]
  ca = "${CA_DEST}"
HTOML
)"

run_priv mkdir -p "${HARBOR_DIR}"

ca_changed=0
if [ ! -f "${CA_DEST}" ] || ! cmp -s "${CA_SRC}" "${CA_DEST}"; then
  tmp_ca_dest="${HARBOR_DIR}/.ca.crt.tmp.$$"
  run_priv cp "${CA_SRC}" "${tmp_ca_dest}"
  run_priv chmod 0644 "${tmp_ca_dest}"
  run_priv mv -f "${tmp_ca_dest}" "${CA_DEST}"
  ca_changed=1
fi

hosts_changed=0
tmp_hosts_src="$(mktemp /tmp/harbor-hosts.XXXXXX)"
printf '%s\n' "${DESIRED_HOSTS}" > "${tmp_hosts_src}"
if [ ! -f "${HOSTS_DEST}" ] || ! cmp -s "${tmp_hosts_src}" "${HOSTS_DEST}"; then
  tmp_hosts_dest="${HARBOR_DIR}/.hosts.toml.tmp.$$"
  run_priv cp "${tmp_hosts_src}" "${tmp_hosts_dest}"
  run_priv chmod 0644 "${tmp_hosts_dest}"
  run_priv mv -f "${tmp_hosts_dest}" "${HOSTS_DEST}"
  hosts_changed=1
fi
rm -f "${tmp_hosts_src}"

if [ "${ca_changed}" -eq 0 ] && [ "${hosts_changed}" -eq 0 ]; then
  echo "containerd Harbor TLS (${HARBOR_HOST}): already up to date (CA sha256=${CA_FP})"
else
  echo "containerd Harbor TLS (${HARBOR_HOST}): updated (ca_changed=${ca_changed}, hosts_changed=${hosts_changed}, CA sha256=${CA_FP})"
fi
