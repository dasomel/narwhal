#!/bin/bash
set -euo pipefail

MASTER_IP="${MASTER_IP:-192.168.56.10}"

echo "=== Joining Kubernetes Cluster ==="
echo "Master IP: ${MASTER_IP}"

# D17: idempotency guard — up.sh's node-readiness loop can call `vagrant
# provision <vm>` on a worker that already joined (e.g. if a transient
# SSH/kubectl hiccup makes k8s_ready_nodes momentarily report 0 nodes Ready,
# even though CNI is simply still converging). `kubeadm join` is not
# idempotent and hard-fails with "kubelet.conf already exists" on a node
# that's already a member. Skip straight to done if evidence of a prior
# successful join is already present.
if [[ -f /etc/kubernetes/kubelet.conf ]]; then
  echo "Node already joined (kubelet.conf present) — skipping kubeadm join"
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

echo "=== Cluster Join Done ==="
