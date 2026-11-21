#!/bin/bash
set -euo pipefail

MASTER_IP="${MASTER_IP:-192.168.56.10}"

echo "=== Joining Kubernetes Cluster ==="
echo "Master IP: ${MASTER_IP}"

# Harden node file permissions (CIS 1.1.9, 1.1.12, 4.1.1, 4.1.7, 4.1.9).
# Non-fatal: failures must not block provisioning.
harden_joined_node() {
  # Cilium creates /etc/cni/net.d/*.conflist only after its agent pod starts on this node
  # (03-cni-install.sh runs on master-1 only). Wait boundedly so CIS 1.1.9 CNI file perms
  # are hardened on join; never fatal if Cilium is still converging.
  local cni_dir="${CNI_CONF_DIR:-/etc/cni/net.d}"
  local cni_wait_timeout="${CNI_WAIT_TIMEOUT:-180}"
  local cni_wait_interval="${CNI_WAIT_INTERVAL:-5}"
  case "${cni_wait_timeout}" in ''|*[!0-9]*) cni_wait_timeout=180 ;; esac
  case "${cni_wait_interval}" in ''|0|*[!0-9]*) cni_wait_interval=5 ;; esac
  local cni_elapsed=0
  local cni_ready=false
  while :; do
    if [ -d "${cni_dir}" ] && [ -n "$(find "${cni_dir}" -maxdepth 1 -type f -print -quit 2>/dev/null || true)" ]; then
      cni_ready=true
      break
    fi
    if [ "${cni_elapsed}" -ge "${cni_wait_timeout}" ]; then
      break
    fi
    sleep "${cni_wait_interval}"
    cni_elapsed=$((cni_elapsed + cni_wait_interval))
  done
  if [ "${cni_ready}" != true ]; then
    echo "WARN: no CNI config file appeared in ${cni_dir} within ${cni_wait_timeout}s; CNI file permissions (CIS 1.1.9) remain unhardened until re-run, continuing with remaining node file hardening..."
  fi

  local HARDEN_SCRIPT="${STAGE_DIR:-/home/vagrant}/scripts/cluster/harden-node-files.sh"
  if [ ! -f "${HARDEN_SCRIPT}" ]; then
    HARDEN_SCRIPT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/harden-node-files.sh"
  fi
  if [ -f "${HARDEN_SCRIPT}" ]; then
    bash "${HARDEN_SCRIPT}" || echo "WARN: harden-node-files.sh reported issues, continuing..."
  fi
}

# D-HARBOR-NODE-CA (KISA-IMG-01): synchronize Harbor containerd CA trust for late-joining
# nodes (nodes added after Phase 2 08-6-tls-routes.sh already ran). During initial Phase 1
# bring-up the CA does not exist on master-1 yet, so absence is a clean skip (08-6 will push
# it later). Once present on master-1 (or pre-staged at /tmp/harbor-ca.crt on Kakao), failure
# to install/validate is fatal so a late-joining worker never ends up unable to verify Harbor.
sync_harbor_containerd_ca() {
  local domain="${DOMAIN:-local.narwhal.internal}"
  local node_script="${STAGE_DIR:-/home/vagrant}/scripts/cluster/08-6b-harbor-containerd-tls-node.sh"
  if [ ! -f "${node_script}" ]; then
    node_script="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/08-6b-harbor-containerd-tls-node.sh"
  fi
  local harbor_ca_remote="/etc/containerd/certs.d/harbor.${domain}/ca.crt"
  local tmp_ca=""
  if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
    if [ -f /tmp/harbor-ca.crt ]; then
      tmp_ca="/tmp/harbor-ca.crt"
    else
      echo "PROVIDER=kakao: /tmp/harbor-ca.crt not staged (initial Phase 1 join; Phase 2 apply-harbor-ca-kakao.sh will configure Harbor CA)"
      return 0
    fi
  else
    if ! sshpass -p "vagrant" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "vagrant@${MASTER_IP}" "test -s '${harbor_ca_remote}'" 2>/dev/null; then
      echo "Harbor CA not yet provisioned on master-1 (${harbor_ca_remote}); 08-6-tls-routes.sh will distribute it in Phase 2"
      return 0
    fi
    tmp_ca="$(mktemp /tmp/harbor-ca.XXXXXX)"
    if ! sshpass -p "vagrant" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
      "vagrant@${MASTER_IP}:${harbor_ca_remote}" "${tmp_ca}" 2>/dev/null; then
      rm -f "${tmp_ca}"
      echo "ERROR: Harbor CA exists on master-1 (${harbor_ca_remote}) but could not be copied to this worker" >&2
      return 1
    fi
  fi
  if [ ! -f "${node_script}" ]; then
    [ "${PROVIDER:-vagrant}" = "kakao" ] || rm -f "${tmp_ca}"
    echo "ERROR: ${node_script} missing; cannot install Harbor containerd CA" >&2
    return 1
  fi
  local rc=0
  sudo env DOMAIN="${domain}" bash "${node_script}" "${tmp_ca}" "${domain}" || rc=$?
  rm -f "${tmp_ca}"
  return "${rc}"
}

# D17: idempotency guard — up.sh's node-readiness loop can call `vagrant
# provision <vm>` on a worker that already joined (e.g. if a transient
# SSH/kubectl hiccup makes k8s_ready_nodes momentarily report 0 nodes Ready,
# even though CNI is simply still converging). `kubeadm join` is not
# idempotent and hard-fails with "kubelet.conf already exists" on a node
# that's already a member. Skip straight to done if evidence of a prior
# successful join is already present.
if [[ -f /etc/kubernetes/kubelet.conf ]]; then
  echo "Node already joined (kubelet.conf present) — skipping kubeadm join"
  harden_joined_node
  sync_harbor_containerd_ca
  echo "=== Cluster Join Done (already joined) ==="
  exit 0
fi

MAX_RETRIES=30
RETRY_INTERVAL=10

# Fetch join command from master.
# Cloud has no vagrant account; the operator pre-stages /tmp/join-command.sh
# onto this node (copied off master-1 via the bastion).
if [ "${PROVIDER:-vagrant}" = "kakao" ]; then
  echo "PROVIDER=kakao: expecting pre-staged /tmp/join-command.sh (operator-supplied)"
else
  for i in $(seq 1 $MAX_RETRIES); do
    if sshpass -p "vagrant" scp -o StrictHostKeyChecking=no \
      "vagrant@${MASTER_IP}:/home/vagrant/join-command.sh" /tmp/join-command.sh 2>/dev/null; then
      echo "Join command fetched successfully"
      break
    fi
    echo "Waiting for master node... (${i}/${MAX_RETRIES})"
    sleep $RETRY_INTERVAL
  done
fi

if [[ ! -f /tmp/join-command.sh ]]; then
  echo "ERROR: Failed to get join command from master"
  exit 1
fi

# Determine correct node name and IP from hostname/network.
# Cloud: NODE_NAME and NODE_IP are supplied explicitly (fixed instance name + IP),
# since the Kakao OS hostname is host-<ip> and the network is 172.16.0.x, not
# 192.168.56.x. On Vagrant these stay auto-detected.
NODE_NAME="${NODE_NAME:-$(hostname)}"
# `|| true` so a non-matching grep does not abort under pipefail. Without it the
# script died inside this assignment on a cloud node — before reaching the emptiness
# check three lines down, which is what the author clearly intended to handle it.
NODE_IP="${NODE_IP:-$(ip -4 addr show eth1 2>/dev/null | grep -oP '(?<=inet\s)[\d.]+' || \
          ip -4 addr show | grep '192\.168\.56\.' | grep -oP '(?<=inet\s)[\d.]+' | head -1 || true)}"

if [[ -z "${NODE_IP}" ]]; then
  echo "ERROR: Could not detect node private IP (set NODE_IP explicitly on cloud)"
  exit 1
fi

echo "Node name: ${NODE_NAME}, Node IP: ${NODE_IP}"

# Join cluster with explicit node-name to prevent VMware NAT interface hostname
sudo bash /tmp/join-command.sh --node-name "${NODE_NAME}"

# Harden node file permissions after join (re-uses harden_joined_node above).
harden_joined_node
sync_harbor_containerd_ca

echo "=== Cluster Join Done ==="
