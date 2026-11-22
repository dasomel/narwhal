#!/bin/bash
# Distribute master-1's Harbor containerd CA trust to the remaining Kakao Cloud nodes.
#
# D-HARBOR-NODE-CA: on Vagrant, 08-6-tls-routes.sh propagates /etc/containerd/certs.d/harbor.<domain>/ca.crt
# from master-1 to all other nodes via sshpass. Cloud images have no vagrant password and no
# node-to-node SSH trust (everything moves host -> bastion -> node), so 08-6 skips node-to-node
# SSH when PROVIDER=kakao and this operator-host script runs 08-6b-harbor-containerd-tls-node.sh
# on every other master and worker node, stopping on the first failure.
# Cost: requires bastion SSH reachability to all nodes on initial Phase 2 and on Root CA rotation.
# Escape hatch: re-run ./scripts/cloud/apply-harbor-ca-kakao.sh after fixing the failed node.
#
# Usage (from the repo root, after 08-6-tls-routes.sh has succeeded on master-1):
#   ./scripts/cloud/apply-harbor-ca-kakao.sh
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
  ""|*[!A-Za-z0-9.-]*|-*|.*|*..)
    echo "ERROR: invalid DOMAIN: ${DOMAIN}" >&2
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
# shellcheck source=scripts/common/lib-harbor-ca.sh
source scripts/common/lib-harbor-ca.sh

read_ips() {
  printf '%s' "$1" | python3 -c 'import json,sys
data = json.load(sys.stdin)
if not isinstance(data, list) or any(not isinstance(ip, str) or not ip for ip in data):
    sys.stderr.write("ERROR: expected JSON list of non-empty IP strings\n")
    sys.exit(1)
for ip in data:
    print(ip)'
}

masters_json="$(cd "${TF_DIR}" && tofu output -json master_private_ips)"
masters_lines="$(read_ips "${masters_json}")"
workers_json="$(cd "${TF_DIR}" && tofu output -json worker_private_ips)"
workers_lines="$(read_ips "${workers_json}")"

MASTERS=(); WORKERS=()
while IFS= read -r _ip; do [ -n "${_ip}" ] && MASTERS+=("${_ip}"); done <<< "${masters_lines}"
while IFS= read -r _ip; do [ -n "${_ip}" ] && WORKERS+=("${_ip}"); done <<< "${workers_lines}"

if [ "${#MASTERS[@]}" -eq 0 ]; then
  echo "ERROR: master_private_ips is empty" >&2
  exit 1
fi
MASTER1="${MASTERS[0]}"
TARGETS=()
if [ "${#MASTERS[@]}" -gt 1 ]; then
  TARGETS+=("${MASTERS[@]:1}")
fi
if [ "${#WORKERS[@]}" -gt 0 ]; then
  TARGETS+=("${WORKERS[@]}")
fi

ssh_node() {
  local ip="$1"; shift
  ssh -i "${SSH_KEY}" \
    "${KAKAO_SSH_OPTS[@]}" \
    -o ConnectTimeout=25 -o ServerAliveInterval=30 \
    -o ProxyCommand="ssh -i ${SSH_KEY} ${KAKAO_SSH_OPTS[*]} -W %h:%p ${SSH_USER}@${BASTION_IP}" \
    "${SSH_USER}@${ip}" "$@"
}

HARBOR_CA_PATH="/etc/containerd/certs.d/harbor.${DOMAIN}/ca.crt"
echo "=== Collecting Harbor CA from master-1 (${MASTER1}) ==="
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
if ! ssh_node "${MASTER1}" "sudo cat '${HARBOR_CA_PATH}'" > "${tmp}/harbor-ca.crt" || [ ! -s "${tmp}/harbor-ca.crt" ]; then
  echo "ERROR: ${HARBOR_CA_PATH} missing or empty on master-1 (${MASTER1}) — has 08-6-tls-routes.sh finished?" >&2
  exit 1
fi
if ! validate_harbor_ca_bundle "${tmp}/harbor-ca.crt"; then
  echo "ERROR: ${HARBOR_CA_PATH} on master-1 (${MASTER1}) failed CA bundle validation" >&2
  exit 1
fi
echo "  harbor-ca.crt ($(wc -c < "${tmp}/harbor-ca.crt" | tr -d ' ') bytes)"

NODE_SCRIPT="${STAGE_DIR}/scripts/cluster/08-6b-harbor-containerd-tls-node.sh"
if [ "${#TARGETS[@]}" -eq 0 ]; then
  echo "No other nodes to update (single-node topology)."
  exit 0
fi

for ip in "${TARGETS[@]}"; do
  rc=0
  ssh_node "${ip}" "sudo test -f '${NODE_SCRIPT}'" || rc=$?
  if [ "${rc}" -eq 1 ]; then
    echo "ERROR: ${NODE_SCRIPT} is missing on ${ip}: run ./scripts/cloud/stage-kakao-nodes.sh ${ip} first" >&2
    exit 1
  elif [ "${rc}" -ne 0 ]; then
    echo "ERROR: could not check ${NODE_SCRIPT} on ${ip}: ssh exit ${rc}" >&2
    exit 1
  fi
done

for ip in "${TARGETS[@]}"; do
  echo "=== Configuring Harbor containerd TLS on ${ip} ==="
  if ! remote_ca="$(ssh_node "${ip}" 'f="$(mktemp /tmp/harbor-ca.XXXXXX)" && cat > "${f}" && printf "%s" "${f}"' < "${tmp}/harbor-ca.crt")"; then
    echo "ERROR: failed to stage Harbor CA on ${ip}" >&2
    exit 1
  fi
  case "${remote_ca}" in
    /tmp/harbor-ca.??????) : ;;
    *)
      echo "ERROR: unexpected staged CA path on ${ip}: ${remote_ca:-<empty>}" >&2
      exit 1
      ;;
  esac
  if ! ssh_node "${ip}" "trap 'rm -f ${remote_ca}' EXIT; sudo env DOMAIN=${DOMAIN} bash ${NODE_SCRIPT} ${remote_ca} ${DOMAIN}"; then
    echo "ERROR: 08-6b-harbor-containerd-tls-node.sh failed on ${ip}. Re-run: ./scripts/cloud/apply-harbor-ca-kakao.sh" >&2
    exit 1
  fi
done

echo "=== Harbor containerd TLS configured on all ${#TARGETS[@]} remaining node(s) ==="
