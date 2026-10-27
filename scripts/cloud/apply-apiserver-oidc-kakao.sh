#!/bin/bash
# Distribute master-1's OIDC CA certificate and apply the kube-apiserver OIDC flags
# to the remaining Kakao Cloud control-plane nodes.
#
# D-APISERVER-OIDC-ALL: on Vagrant, 11-4-keycloak-apiserver.sh propagates OIDC CA + flags
# from master-1 to master-2/3 via `sshpass -p vagrant`. Cloud images have no vagrant
# password and no node-to-node SSH trust (everything moves host -> bastion -> node; nodes
# never talk to each other), so 11-4 skips node-to-node SSH when PROVIDER=kakao and this
# operator-host script applies `11-4b-apiserver-oidc-node.sh` to master-2..N one at a time,
# stopping on the first failure, then requires exactly one kube-apiserver mirror pod per master
# (a master with none FAILS) and prints the `--oidc-*` flag count on each of them.
# Reason: in-cluster `kubernetes` Service endpoints include all masters; leaving master-2/3
# without OIDC caused ~2/3 of Dashboard/Headlamp/kubectl OIDC requests to fail with 401.
# Cost: serial kube-apiserver static-pod restart across master-2..N (~15-30s per master) on
# the first run or when the CA rotates; no-op on subsequent runs. The node script returns only
# after the kube-apiserver process was replaced and is ready, so two masters are never down at
# once. Every master must already hold that script, so a node staged before this change needs
# `./scripts/cloud/stage-kakao-nodes.sh <node-ip>` first (checked on all masters before the first
# restart).
# Escape hatch: restore `/etc/kubernetes/kube-apiserver.yaml.pre-oidc-*` on the target node.
#
# Usage (from the repo root, after 11-4-keycloak-apiserver.sh has succeeded on master-1):
#   ./scripts/cloud/apply-apiserver-oidc-kakao.sh
set -euo pipefail

TF_DIR="${TF_DIR:-csp/kakao-cloud/terraform}"
SSH_USER="${NODE_SSH_USER:-ubuntu}"
STAGE_DIR="${STAGE_DIR:-/home/vagrant}"
DOMAIN="${DOMAIN:-kakao.narwhal.internal}"

case "${STAGE_DIR}" in
  /*) : ;;
  *) echo "ERROR: STAGE_DIR must be an absolute path: ${STAGE_DIR}" >&2; exit 1 ;;
esac
case "${STAGE_DIR}" in
  *[!A-Za-z0-9_/.-]*)
    echo "ERROR: STAGE_DIR contains characters outside [A-Za-z0-9_/.-]: ${STAGE_DIR}" >&2
    exit 1
    ;;
esac
case "${DOMAIN}" in
  *[!A-Za-z0-9.-]*|"")
    echo "ERROR: invalid DOMAIN: ${DOMAIN}" >&2
    exit 1
    ;;
esac

OIDC_ISSUER_URL="${OIDC_ISSUER_URL:-https://keycloak.${DOMAIN}/realms/narwhal}"
case "${OIDC_ISSUER_URL}" in
  https://*[!A-Za-z0-9_/:.-]*)
    echo "ERROR: invalid OIDC_ISSUER_URL: ${OIDC_ISSUER_URL}" >&2
    exit 1
    ;;
  https://*) : ;;
  *)
    echo "ERROR: OIDC_ISSUER_URL must start with https://: ${OIDC_ISSUER_URL}" >&2
    exit 1
    ;;
esac

cd "$(dirname "$0")/../.."

BASTION_IP=$(cd "${TF_DIR}" && tofu output -raw bastion_public_ip)
SSH_KEY=$(cd "${TF_DIR}" && tofu output -raw ssh_key_path 2>/dev/null || true)
[ -n "${SSH_KEY}" ] || SSH_KEY="${TF_DIR}/KPAAS_KEYPAIR.pem"
case "${SSH_KEY}" in /*) ;; *) SSH_KEY="${TF_DIR}/${SSH_KEY#./}" ;; esac
[ -f "${SSH_KEY}" ] || { echo "ERROR: private key not found at ${SSH_KEY}" >&2; exit 1; }

# shellcheck source=scripts/cloud/lib-ssh.sh
source scripts/cloud/lib-ssh.sh

mapfile_masters=$(cd "${TF_DIR}" && tofu output -json master_private_ips)
read_ips() {
  printf '%s' "$1" | python3 -c 'import json,sys
for ip in json.load(sys.stdin): print(ip)'
}

MASTERS=()
while IFS= read -r _ip; do [ -n "${_ip}" ] && MASTERS+=("${_ip}"); done < <(read_ips "${mapfile_masters}")
if [ "${#MASTERS[@]}" -eq 0 ]; then
  echo "ERROR: master_private_ips is empty" >&2
  exit 1
fi
MASTER1="${MASTERS[0]}"

ssh_node() {
  local ip="$1"; shift
  ssh -i "${SSH_KEY}" \
    "${KAKAO_SSH_OPTS[@]}" \
    -o ConnectTimeout=25 -o ServerAliveInterval=30 \
    -o ProxyCommand="ssh -i ${SSH_KEY} ${KAKAO_SSH_OPTS[*]} -W %h:%p ${SSH_USER}@${BASTION_IP}" \
    "${SSH_USER}@${ip}" "$@"
}

echo "=== Collecting OIDC CA from master-1 (${MASTER1}) ==="
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
# ssh stderr stays visible (KAKAO_SSH_OPTS sets LogLevel=ERROR): a host-key or connection error
# must not be reported as a missing CA file.
if ! ssh_node "${MASTER1}" "sudo cat /etc/kubernetes/pki/oidc-ca.crt" > "${tmp}/oidc-ca.crt" || [ ! -s "${tmp}/oidc-ca.crt" ]; then
  echo "ERROR: /etc/kubernetes/pki/oidc-ca.crt missing or empty on master-1 (${MASTER1}), or ssh failed (see above) — has 11-4-keycloak-apiserver.sh finished?" >&2
  exit 1
fi
if ! openssl x509 -in "${tmp}/oidc-ca.crt" -noout -subject >/dev/null 2>&1; then
  echo "ERROR: /etc/kubernetes/pki/oidc-ca.crt on master-1 (${MASTER1}) is not a valid X.509 certificate" >&2
  exit 1
fi
echo "  oidc-ca.crt ($(wc -c < "${tmp}/oidc-ca.crt" | tr -d ' ') bytes)"

# Every master must already hold the node script (stage-kakao-nodes.sh copies it). Check all of
# them before the first restart: a node staged before this script existed has no 11-4b file, and
# failing on master-3 after master-2 was restarted would leave a half-applied rollout.
NODE_SCRIPT="${STAGE_DIR}/scripts/cluster/11-4b-apiserver-oidc-node.sh"
idx=2
for ip in "${MASTERS[@]:1}"; do
  rc=0
  ssh_node "${ip}" "sudo test -f ${NODE_SCRIPT}" || rc=$?
  if [ "${rc}" -eq 1 ]; then
    echo "ERROR: ${NODE_SCRIPT} is missing on master-${idx} (${ip}): the node was staged before the OIDC change. Re-run ./scripts/cloud/stage-kakao-nodes.sh ${ip} first, then ./scripts/cloud/apply-apiserver-oidc-kakao.sh" >&2
    exit 1
  elif [ "${rc}" -ne 0 ]; then
    echo "ERROR: could not check ${NODE_SCRIPT} on master-${idx} (${ip}): ssh exit ${rc}" >&2
    exit 1
  fi
  idx=$((idx + 1))
done

idx=2
for ip in "${MASTERS[@]:1}"; do
  echo "=== control-plane master-${idx} (${ip}) ==="
  # Unique name per run: a fixed /tmp/oidc-ca.crt left behind by an earlier root-owned run would
  # make the redirect fail for the ssh user. The remote command prints the path it created.
  if ! remote_ca="$(ssh_node "${ip}" 'f="$(mktemp /tmp/oidc-ca.XXXXXX)" && cat > "${f}" && printf "%s" "${f}"' < "${tmp}/oidc-ca.crt")"; then
    echo "ERROR: failed to stage the OIDC CA on master-${idx} (${ip})" >&2
    exit 1
  fi
  case "${remote_ca}" in
    *[!A-Za-z0-9._/-]*)
      echo "ERROR: unexpected staged CA path on master-${idx} (${ip}): ${remote_ca}" >&2
      exit 1
      ;;
    /tmp/oidc-ca.??????) : ;;
    *)
      echo "ERROR: unexpected staged CA path on master-${idx} (${ip}): ${remote_ca:-<empty>}" >&2
      exit 1
      ;;
  esac
  # The trap removes the staged CA when the remote shell exits and keeps the node script's status.
  if ! ssh_node "${ip}" "trap 'rm -f ${remote_ca}' EXIT; sudo env PROVIDER=kakao DOMAIN=${DOMAIN} NODE_IP=${ip} bash ${NODE_SCRIPT} ${OIDC_ISSUER_URL} ${remote_ca}"; then
    echo "ERROR: 11-4b-apiserver-oidc-node.sh failed on master-${idx} (${ip}). Re-run: ./scripts/cloud/apply-apiserver-oidc-kakao.sh" >&2
    exit 1
  fi
  idx=$((idx + 1))
done

echo "=== Verifying kube-apiserver mirror pod OIDC flag counts ==="
# The pod count must equal the master count (argv[1]): a master with no kube-apiserver mirror pod
# would otherwise be skipped and the remaining pods would still pass.
ssh_node "${MASTER1}" "sudo env KUBECONFIG=${STAGE_DIR}/.kube/config-local kubectl get pods -n kube-system -l component=kube-apiserver -o json" \
  | python3 -c '
import json, sys

expected = int(sys.argv[1])
items = json.load(sys.stdin).get("items", [])
if len(items) != expected:
    nodes = ", ".join(sorted(p.get("spec", {}).get("nodeName", "?") for p in items)) or "none"
    print(f"ERROR: expected {expected} kube-apiserver mirror pod(s), one per master, found {len(items)} (nodes: {nodes})", file=sys.stderr)
    sys.exit(1)

bad = 0
for pod in items:
    name = pod["metadata"]["name"]
    containers = pod.get("spec", {}).get("containers") or [{}]
    cmd = containers[0].get("command", [])
    oidc_flags = [arg for arg in cmd if arg.startswith("--oidc-")]
    print(f"  {name}: {len(oidc_flags)} --oidc-* flag(s)")
    if len(oidc_flags) < 7:
        bad = 1

if bad:
    print("ERROR: one or more kube-apiserver pods have fewer than 7 --oidc-* flags", file=sys.stderr)
    sys.exit(1)
' "${#MASTERS[@]}"
