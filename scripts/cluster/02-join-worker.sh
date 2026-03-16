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

# Join cluster
sudo bash /tmp/join-command.sh

echo "=== Cluster Join Done ==="
