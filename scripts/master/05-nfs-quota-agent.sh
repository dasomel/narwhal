#!/bin/bash
set -euo pipefail

NFS_SHARE_PATH="${NFS_SHARE_PATH:-/srv/nfs/k8s}"
MASTER_HOSTNAME="${MASTER_HOSTNAME:-narwhal-master}"

echo "=== NFS Quota Agent Installation ==="

# Wait for cluster to be ready
echo "Waiting for cluster to be ready..."
until kubectl get nodes &>/dev/null; do
  sleep 2
done

# Label master node as NFS server
echo "Labeling master node as NFS server..."
kubectl label node "${MASTER_HOSTNAME}" nfs-server=true --overwrite

# Download Helm chart from GitHub
CHART_DIR="/tmp/nfs-quota-agent-chart"
rm -rf "${CHART_DIR}"

echo "Downloading Helm chart..."
curl -sL https://github.com/dasomel/nfs-quota-agent/archive/refs/heads/main.tar.gz | \
  tar -xz -C /tmp
mv /tmp/nfs-quota-agent-main/charts/nfs-quota-agent "${CHART_DIR}"
rm -rf /tmp/nfs-quota-agent-main

# Create namespace
kubectl create namespace nfs-quota-agent --dry-run=client -o yaml | kubectl apply -f -

# Ensure project files exist on host
sudo touch /etc/projid /etc/projects 2>/dev/null || true

# Install via Helm
echo "Installing nfs-quota-agent..."
helm upgrade --install nfs-quota-agent "${CHART_DIR}" \
  --namespace nfs-quota-agent \
  --set image.repository=ghcr.io/dasomel/nfs-quota-agent \
  --set image.tag=v0.1.12 \
  --set config.nfsBasePath=/export \
  --set config.nfsServerPath="${NFS_SHARE_PATH}" \
  --set config.provisionerName=nfs.csi.k8s.io \
  --set config.syncInterval=30s \
  --set nfsExport.hostPath="${NFS_SHARE_PATH}" \
  --set webUI.enabled=true \
  --set webUI.addr=":8080" \
  --set-string nodeSelector.nfs-server=true \
  --set tolerations[0].key=node-role.kubernetes.io/control-plane \
  --set tolerations[0].operator=Exists \
  --set tolerations[0].effect=NoSchedule \
  --set tolerations[1].key=node.kubernetes.io/disk-pressure \
  --set tolerations[1].operator=Exists \
  --set tolerations[1].effect=NoSchedule \
  --wait --timeout 5m

# Cleanup
rm -rf "${CHART_DIR}"

# Verify installation
echo "=== NFS Quota Agent Status ==="
kubectl get pods -n nfs-quota-agent
kubectl get deployment -n nfs-quota-agent

echo "=== NFS Quota Agent Installation Done ==="
