#!/bin/bash
# Turn the Kakao Cloud bastion into the egress path for the private nodes.
#
# The nodes have no public IP and provider 0.4.4 has no NAT gateway resource, but
# provisioning is not fully offline: apt pulls containerd and the kube* packages,
# GitHub serves cilium-cli / hubble / metrics-server / nfs-quota-agent, and half a
# dozen Helm repos are added. The airgap bundle only mirrors container images and
# chart tarballs, so that traffic still has to leave the VPC.
#
# squid on the bastion carries it. Only the VPC CIDR may connect (enforced twice:
# the security group rule on 3128 and squid's own ACL), and the proxy never binds
# to the public interface's routable clients because the SG has no public 3128 rule.
#
# Usage (from the repo root, after `tofu apply`):
#   ./scripts/cloud/setup-bastion-proxy.sh
set -euo pipefail

TF_DIR="${TF_DIR:-csp/kakao-cloud/terraform}"
SSH_USER="${NODE_SSH_USER:-ubuntu}"
PROXY_PORT="${PROXY_PORT:-3128}"

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

echo "=== Bastion proxy ==="
echo "  bastion     : ${BASTION_IP} (private ${BASTION_PRIVATE_IP})"
echo "  allowed     : ${VPC_CIDR}"
echo "  listen port : ${PROXY_PORT}"

ssh_bastion() {
  ssh -i "${SSH_KEY}" \
    -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR \
    "${SSH_USER}@${BASTION_IP}" "$@"
}

echo ""
echo "=== Installing squid ==="
ssh_bastion "sudo DEBIAN_FRONTEND=noninteractive apt-get update -qq && \
             sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq squid"

echo "=== Writing squid.conf ==="
# CONNECT to 443 is what makes apt-over-https, git and helm work through a forward
# proxy; without the SSL_ports/CONNECT lines squid rejects them with 403.
ssh_bastion "sudo tee /etc/squid/conf.d/narwhal.conf >/dev/null" <<EOF
# Narwhal: forward proxy for the private cluster nodes.
http_port ${PROXY_PORT}

acl narwhal_vpc src ${VPC_CIDR}
acl SSL_ports port 443
acl Safe_ports port 80 443
acl CONNECT method CONNECT

http_access deny !Safe_ports
http_access deny CONNECT !SSL_ports
http_access allow narwhal_vpc
http_access allow localhost
http_access deny all

# Cache is pointless here (large one-shot downloads) and would just fill the disk.
cache deny all
EOF

echo "=== Restarting squid ==="
ssh_bastion "sudo systemctl enable --now squid && sudo systemctl restart squid && sleep 2 && systemctl is-active squid"

echo ""
echo "=== Verifying from the bastion itself ==="
ssh_bastion "curl -sS -o /dev/null -w 'via proxy: HTTP %{http_code}\n' \
  --max-time 20 -x http://127.0.0.1:${PROXY_PORT} https://archive.ubuntu.com/ubuntu/dists/noble/Release"

echo ""
echo "Proxy endpoint for the nodes: http://${BASTION_PRIVATE_IP}:${PROXY_PORT}"
echo "Next: ./scripts/cloud/configure-node-proxy.sh"
