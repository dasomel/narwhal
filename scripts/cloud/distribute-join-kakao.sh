#!/bin/bash
# Hand the join artifacts from master-1 to the other Kakao Cloud nodes.
#
# On Vagrant, 02-join-{control-plane,worker}.sh fetch these themselves with
# `sshpass -p vagrant scp` from master-1. A cloud image has no vagrant password and
# no node-to-node trust, so both scripts take a PROVIDER=kakao branch that expects
# the files to already be in /tmp — "operator-supplied". This is that operator.
#
# Everything moves host -> bastion -> node; nodes never talk to each other.
#
#   /home/vagrant/join-control-plane.sh  -> master-2, master-3  as /tmp/join-control-plane.sh
#   /home/vagrant/encryption-config.yaml -> master-2, master-3  as /tmp/encryption-config.yaml
#   /home/vagrant/join-command.sh        -> workers             as /tmp/join-command.sh
#
# The certificate key inside join-control-plane.sh expires with the kubeadm-certs
# secret (2h by default), and the bootstrap token after 24h. Re-run
# `kubeadm init phase upload-certs --upload-certs` on master-1 and regenerate before
# re-running this if a join fails on an expired key.
#
# Usage (from the repo root, after 02-init-cluster.sh has succeeded on master-1):
#   ./scripts/cloud/distribute-join-kakao.sh
set -euo pipefail

TF_DIR="${TF_DIR:-csp/kakao-cloud/terraform}"
SSH_USER="${NODE_SSH_USER:-ubuntu}"

cd "$(dirname "$0")/../.."

BASTION_IP=$(cd "${TF_DIR}" && tofu output -raw bastion_public_ip)
SSH_KEY=$(cd "${TF_DIR}" && tofu output -raw ssh_key_path 2>/dev/null || true)
[ -n "${SSH_KEY}" ] || SSH_KEY="${TF_DIR}/KPAAS_KEYPAIR.pem"
case "${SSH_KEY}" in /*) ;; *) SSH_KEY="${TF_DIR}/${SSH_KEY#./}" ;; esac
[ -f "${SSH_KEY}" ] || { echo "ERROR: private key not found at ${SSH_KEY}" >&2; exit 1; }

mapfile_masters=$(cd "${TF_DIR}" && tofu output -json master_private_ips)
mapfile_workers=$(cd "${TF_DIR}" && tofu output -json worker_private_ips)
read_ips() {
  printf '%s' "$1" | python3 -c 'import json,sys
for ip in json.load(sys.stdin): print(ip)'
}

MASTERS=()
while IFS= read -r _ip; do [ -n "${_ip}" ] && MASTERS+=("${_ip}"); done < <(read_ips "${mapfile_masters}")
WORKERS=()
while IFS= read -r _ip; do [ -n "${_ip}" ] && WORKERS+=("${_ip}"); done < <(read_ips "${mapfile_workers}")

MASTER1="${MASTERS[0]}"

ssh_node() {
  local ip="$1"; shift
  ssh -i "${SSH_KEY}" \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
    -o ProxyCommand="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -W %h:%p ${SSH_USER}@${BASTION_IP}" \
    "${SSH_USER}@${ip}" "$@"
}

echo "=== Collecting join artifacts from master-1 (${MASTER1}) ==="
tmp="$(mktemp -d)"
trap 'rm -rf "${tmp}"' EXIT
for f in join-control-plane.sh join-command.sh encryption-config.yaml; do
  # sudo cat, not scp: the files are owned by vagrant with mode 600.
  if ! ssh_node "${MASTER1}" "sudo cat /home/vagrant/${f}" > "${tmp}/${f}" 2>/dev/null || [ ! -s "${tmp}/${f}" ]; then
    echo "ERROR: ${f} missing or empty on master-1 — has 02-init-cluster.sh finished?" >&2
    exit 1
  fi
  echo "  ${f} ($(wc -c < "${tmp}/${f}") bytes)"
done

place() {
  local ip="$1" src="$2" dest="$3"
  ssh_node "${ip}" "cat > '${dest}' && chmod 0600 '${dest}'" < "${tmp}/${src}"
}

for ip in "${MASTERS[@]:1}"; do
  echo "=== control-plane ${ip} ==="
  place "${ip}" join-control-plane.sh /tmp/join-control-plane.sh
  place "${ip}" encryption-config.yaml /tmp/encryption-config.yaml
  ssh_node "${ip}" "ls -l /tmp/join-control-plane.sh /tmp/encryption-config.yaml | awk '{print \"    \"\$5, \$9}'"
done

for ip in "${WORKERS[@]}"; do
  echo "=== worker ${ip} ==="
  place "${ip}" join-command.sh /tmp/join-command.sh
  ssh_node "${ip}" "ls -l /tmp/join-command.sh | awk '{print \"    \"\$5, \$9}'"
done

echo ""
echo "=== Distributed to $(( ${#MASTERS[@]} - 1 )) control-plane + ${#WORKERS[@]} worker node(s) ==="
# sudo -E is refused on these images ("preserving the entire environment is not
# supported"), so the variable has to ride on `sudo env` or it never arrives.
echo "Next, on each node:"
echo "  control-plane: sudo env PROVIDER=kakao bash scripts/cluster/02-join-control-plane.sh"
echo "  worker:        sudo env PROVIDER=kakao bash scripts/cluster/02-join-worker.sh"
