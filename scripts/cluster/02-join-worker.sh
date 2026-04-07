#!/bin/bash
set -euo pipefail

MASTER_IP="${MASTER_IP:-192.168.56.10}"

echo "=== Joining Kubernetes Cluster ==="
echo "Master IP: ${MASTER_IP}"

MAX_RETRIES=30
RETRY_INTERVAL=10

# Fetch join command from master
for i in $(seq 1 $MAX_RETRIES); do
  if sshpass -p "vagrant" scp -o StrictHostKeyChecking=no \
    "vagrant@${MASTER_IP}:/home/vagrant/join-command.sh" /tmp/join-command.sh 2>/dev/null; then
    echo "Join command fetched successfully"
    break
  fi
  echo "Waiting for master node... (${i}/${MAX_RETRIES})"
  sleep $RETRY_INTERVAL
done

if [[ ! -f /tmp/join-command.sh ]]; then
  echo "ERROR: Failed to get join command from master"
  exit 1
fi

# Determine correct node name and IP from hostname/network
NODE_NAME=$(hostname)
NODE_IP=$(ip -4 addr show eth1 2>/dev/null | grep -oP '(?<=inet\s)[\d.]+' || \
          ip -4 addr show | grep '192\.168\.56\.' | grep -oP '(?<=inet\s)[\d.]+' | head -1)

if [[ -z "${NODE_IP}" ]]; then
  echo "ERROR: Could not detect private network IP (192.168.56.x)"
  exit 1
fi

echo "Node name: ${NODE_NAME}, Node IP: ${NODE_IP}"

# Join cluster with explicit node-name to prevent VMware NAT interface hostname
sudo bash /tmp/join-command.sh --node-name "${NODE_NAME}"

echo "=== Cluster Join Done ==="
