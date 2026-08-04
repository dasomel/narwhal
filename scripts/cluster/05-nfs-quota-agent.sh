#!/bin/bash
set -euo pipefail

# Same retry() as 03-k8s-install.sh and 03-cni-install.sh. These downloads cross the
# bastion proxy to a public CDN and fail transiently; without a retry one flaky fetch
# fails the whole stage.
retry() {
  local n=1 max=5
  until "$@"; do
    if [ "$n" -ge "$max" ]; then
      echo "ERROR: command failed after ${max} attempts: $*" >&2
      return 1
    fi
    echo "  attempt ${n}/${max} failed, retrying in 15s..." >&2
    n=$((n + 1))
    sleep 15
  done
}


NFS_SHARE_PATH="${NFS_SHARE_PATH:-/srv/nfs/k8s}"
MASTER_HOSTNAME="${MASTER_HOSTNAME:-narwhal-master}"
NFS_QUOTA_AGENT_VERSION="${NFS_QUOTA_AGENT_VERSION:-v0.3.0}"

echo "=== NFS Quota Agent Installation ==="

# Use local kubeconfig (bypasses VIP) to avoid disruption during master-2 join
export KUBECONFIG=/home/vagrant/.kube/config-local

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
retry curl -sL https://github.com/dasomel/nfs-quota-agent/archive/refs/heads/main.tar.gz | \
  tar -xz -C /tmp
mv /tmp/nfs-quota-agent-main/charts/nfs-quota-agent "${CHART_DIR}"
rm -rf /tmp/nfs-quota-agent-main

# Create namespace
kubectl create namespace nfs-quota-agent --dry-run=client -o yaml | kubectl apply -f -

# Ensure project files exist on host (must be regular files, not directories)
for f in /etc/projid /etc/projects; do
  if [ -d "$f" ]; then
    sudo rm -rf "$f"
  fi
  sudo touch "$f"
done

# Install via Helm
echo "Installing nfs-quota-agent..."
helm upgrade --install nfs-quota-agent "${CHART_DIR}" \
  --namespace nfs-quota-agent \
  --set image.repository=ghcr.io/dasomel/nfs-quota-agent \
  --set image.tag="${NFS_QUOTA_AGENT_VERSION}" \
  --set config.nfsBasePath=/export \
  --set config.nfsServerPath="${NFS_SHARE_PATH}" \
  --set config.provisionerName=nfs.csi.k8s.io \
  --set config.syncInterval=30s \
  --set nfsExport.hostPath="${NFS_SHARE_PATH}" \
  --set webUI.enabled=true \
  --set webUI.addr=":8080" \
  --set audit.enabled=true \
  --set cleanup.enabled=true \
  --set history.enabled=true \
  --set policy.enabled=true \
  --set-string nodeSelector.nfs-server=true \
  --set tolerations[0].key=node-role.kubernetes.io/control-plane \
  --set tolerations[0].operator=Exists \
  --set tolerations[0].effect=NoSchedule \
  --set tolerations[1].key=node.kubernetes.io/disk-pressure \
  --set tolerations[1].operator=Exists \
  --set tolerations[1].effect=NoSchedule

# Wait for deployment (non-blocking - pod may be pending until scheduling resolves)
echo "Waiting for nfs-quota-agent deployment..."
kubectl rollout status deployment/nfs-quota-agent -n nfs-quota-agent --timeout=120s || \
  echo "WARN: nfs-quota-agent not ready yet (may need node scheduling to resolve)"

# Cleanup
rm -rf "${CHART_DIR}"

# Verify installation
echo "=== NFS Quota Agent Status ==="
kubectl get pods -n nfs-quota-agent
kubectl get deployment -n nfs-quota-agent

echo "=== NFS Quota Agent Installation Done ==="
