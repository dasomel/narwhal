#!/bin/bash
# Point the private Kakao Cloud nodes at the bastion's squid proxy for egress.
#
# Three consumers, three mechanisms, because none of them read the others' config:
#   apt        -> /etc/apt/apt.conf.d/01narwhal-proxy
#   shells     -> /etc/environment (curl, helm, git in the provisioning scripts)
#   containerd -> systemd drop-in (only matters for an image the local mirror lacks)
#
# NO_PROXY has to cover everything that must NOT leave the VPC: the cluster CIDRs,
# the in-VPC registry, cluster DNS suffixes and the metadata service. A proxied
# request to 169.254.169.254 or to the pod network is a hang, not an error, so this
# list is the part worth getting right.
#
# Usage (from the repo root, after setup-bastion-proxy.sh):
#   ./scripts/cloud/configure-node-proxy.sh              # all nodes
#   ./scripts/cloud/configure-node-proxy.sh 172.16.0.10  # one node
set -euo pipefail

TF_DIR="${TF_DIR:-csp/kakao-cloud/terraform}"
SSH_USER="${NODE_SSH_USER:-ubuntu}"
PROXY_PORT="${PROXY_PORT:-3128}"
AIRGAP_REGISTRY="${AIRGAP_REGISTRY:-registry.airgap.local:5000}"

cd "$(dirname "$0")/../.."

BASTION_IP=$(cd "${TF_DIR}" && tofu output -raw bastion_public_ip)
BASTION_PRIVATE_IP=$(cd "${TF_DIR}" && tofu output -raw bastion_private_ip)
# tofu emits an absolute path; the fallback is relative to TF_DIR, so normalize
# either form against the repo root rather than trusting the caller's CWD.
SSH_KEY=$(cd "${TF_DIR}" && tofu output -raw ssh_key_path 2>/dev/null || true)
[ -n "${SSH_KEY}" ] || SSH_KEY="${TF_DIR}/KPAAS_KEYPAIR.pem"
case "${SSH_KEY}" in /*) ;; *) SSH_KEY="${TF_DIR}/${SSH_KEY#./}" ;; esac
[ -f "${SSH_KEY}" ] || { echo "ERROR: private key not found at ${SSH_KEY} - has tofu apply finished?" >&2; exit 1; }
VPC_CIDR=$(cd "${TF_DIR}" && tofu output -raw vpc_cidr 2>/dev/null || echo "172.16.0.0/16")

PROXY_URL="http://${BASTION_PRIVATE_IP}:${PROXY_PORT}"

# The CIDRs are for Go-based clients (containerd, helm, kubectl) — they parse them.
# curl does NOT: it only matches exact hosts and domain suffixes, so a VPC CIDR
# alone still sends in-VPC requests to the proxy, which then cannot reach them.
# Every in-VPC address a script actually curls is therefore listed literally too.
# 10.244.0.0/16 = podSubnet, 10.96.0.0/12 = serviceSubnet (scripts/cluster/02-init-cluster.sh).
_literal_hosts="${BASTION_PRIVATE_IP}"
_node_json=$(cd "${TF_DIR}" && tofu output -json master_private_ips && tofu output -json worker_private_ips)
while IFS= read -r _n; do
  [ -n "${_n}" ] && _literal_hosts="${_literal_hosts},${_n}"
done < <(printf '%s\n' "${_node_json}" | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if line:
        for ip in json.loads(line):
            print(ip)
')

NO_PROXY_LIST="localhost,127.0.0.1,::1,${_literal_hosts},${VPC_CIDR},10.244.0.0/16,10.96.0.0/12,169.254.169.254,${AIRGAP_REGISTRY%%:*},.svc,.svc.cluster.local,.cluster.local,.local.narwhal.internal"

if [ $# -gt 0 ]; then
  NODES=("$@")
else
  node_json=$(cd "${TF_DIR}" && tofu output -json master_private_ips && tofu output -json worker_private_ips)
  # read loop, not mapfile: macOS ships bash 3.2 and mapfile arrived in 4.0.
  NODES=()
  while IFS= read -r _ip; do
    [ -n "${_ip}" ] && NODES+=("${_ip}")
  done < <(printf '%s\n' "${node_json}" | python3 -c '
import json, sys
for line in sys.stdin:
    line = line.strip()
    if line:
        for ip in json.loads(line):
            print(ip)
')
fi

echo "=== Node proxy configuration ==="
echo "  proxy    : ${PROXY_URL}"
echo "  no_proxy : ${NO_PROXY_LIST}"
echo "  nodes    : ${NODES[*]}"

ssh_node() {
  local ip="$1"; shift
  ssh -i "${SSH_KEY}" \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
    -o ProxyCommand="ssh -i ${SSH_KEY} -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -W %h:%p ${SSH_USER}@${BASTION_IP}" \
    "${SSH_USER}@${ip}" "$@"
}

for ip in "${NODES[@]}"; do
  echo ""
  echo "=== ${ip} ==="

  # Retries matter more here than on a normal host: every package on every node
  # funnels through one squid on one bastion, and apt defaults to zero retries.
  # A 31-second proxy restart on 2026-07-26 failed 02-containerd.sh on all six
  # nodes at once — a transient blip should cost seconds, not a provisioning run.
  ssh_node "${ip}" "sudo tee /etc/apt/apt.conf.d/01narwhal-proxy >/dev/null" <<EOF
Acquire::http::Proxy "${PROXY_URL}";
Acquire::https::Proxy "${PROXY_URL}";
Acquire::Retries "5";
Acquire::http::Timeout "30";
Acquire::https::Timeout "30";
EOF

  # Appended, not overwritten: /etc/environment already carries PATH on Ubuntu.
  ssh_node "${ip}" "sudo sed -i '/^# narwhal-proxy$/,/^# end narwhal-proxy$/d' /etc/environment && \
                    sudo tee -a /etc/environment >/dev/null" <<EOF
# narwhal-proxy
http_proxy="${PROXY_URL}"
https_proxy="${PROXY_URL}"
HTTP_PROXY="${PROXY_URL}"
HTTPS_PROXY="${PROXY_URL}"
no_proxy="${NO_PROXY_LIST}"
NO_PROXY="${NO_PROXY_LIST}"
# end narwhal-proxy
EOF

  ssh_node "${ip}" "sudo mkdir -p /etc/systemd/system/containerd.service.d && \
                    sudo tee /etc/systemd/system/containerd.service.d/http-proxy.conf >/dev/null" <<EOF
[Service]
Environment="HTTP_PROXY=${PROXY_URL}"
Environment="HTTPS_PROXY=${PROXY_URL}"
Environment="NO_PROXY=${NO_PROXY_LIST}"
EOF

  # containerd may not be installed yet on a fresh node; the drop-in still applies
  # once 01-prerequisites.sh installs it, so a missing unit here is not an error.
  ssh_node "${ip}" "sudo systemctl daemon-reload && \
                    (systemctl is-active --quiet containerd && sudo systemctl restart containerd || true)"

  echo -n "  egress check: "
  ssh_node "${ip}" "curl -sS -o /dev/null -w 'HTTP %{http_code}\n' --max-time 25 \
    -x '${PROXY_URL}' https://archive.ubuntu.com/ubuntu/dists/noble/Release" \
    || echo "FAILED"
done

echo ""
echo "=== Configured ${#NODES[@]} node(s) ==="
