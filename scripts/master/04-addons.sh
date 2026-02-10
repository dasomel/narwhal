#!/bin/bash
set -euo pipefail

NFS_SERVER_IP="${NFS_SERVER_IP:-192.168.56.10}"
NFS_SHARE_PATH="${NFS_SHARE_PATH:-/srv/nfs/k8s}"

# Component versions
METRICS_SERVER_VERSION="${METRICS_SERVER_VERSION:-v0.8.1}"
CSI_DRIVER_NFS_VERSION="${CSI_DRIVER_NFS_VERSION:-v4.12.1}"
NFS_QUOTA_AGENT_VERSION="${NFS_QUOTA_AGENT_VERSION:-v0.2.1}"

echo "=== Installing Kubernetes Addons ==="

export KUBECONFIG=/home/vagrant/.kube/config

# Install Helm if not installed
HELM_VERSION="v4.1.0"
if ! command -v helm &> /dev/null; then
  curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | DESIRED_VERSION="${HELM_VERSION}" bash
fi

#=========================================
# Metrics Server
#=========================================
echo "=== Installing Metrics Server ${METRICS_SERVER_VERSION} ==="

kubectl apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/${METRICS_SERVER_VERSION}/components.yaml"

# Patch for local environment (insecure TLS)
kubectl patch deployment metrics-server -n kube-system --type='json' -p='[
  {"op": "add", "path": "/spec/template/spec/containers/0/args/-", "value": "--kubelet-insecure-tls"}
]'

#=========================================
# CSI Driver NFS
#=========================================
echo "=== Installing CSI Driver NFS ${CSI_DRIVER_NFS_VERSION} ==="

helm repo add csi-driver-nfs https://raw.githubusercontent.com/kubernetes-csi/csi-driver-nfs/master/charts
helm repo update

helm upgrade --install csi-driver-nfs csi-driver-nfs/csi-driver-nfs \
  --namespace kube-system \
  --version "${CSI_DRIVER_NFS_VERSION}" \
  --set controller.replicas=1

# Wait for CSI driver pods
echo "Waiting for CSI driver pods..."
kubectl wait --for=condition=Ready pod -l app.kubernetes.io/name=csi-driver-nfs -n kube-system --timeout=120s || true

# Create StorageClass
cat <<EOF | kubectl apply -f -
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: nfs-csi
  annotations:
    storageclass.kubernetes.io/is-default-class: "true"
provisioner: nfs.csi.k8s.io
parameters:
  server: ${NFS_SERVER_IP}
  share: ${NFS_SHARE_PATH}
  subDir: \${pvc.metadata.namespace}/\${pvc.metadata.name}
reclaimPolicy: Retain
volumeBindingMode: Immediate
mountOptions:
  - nfsvers=4.1
  - hard
  - noatime
  - rsize=65536
  - wsize=65536
EOF

#=========================================
# NFS Quota Agent
#=========================================
echo "=== Installing NFS Quota Agent ${NFS_QUOTA_AGENT_VERSION} ==="

# Label master node as NFS server
kubectl label node narwhal-master nfs-server=true --overwrite

# Install via Helm OCI registry
helm upgrade --install nfs-quota-agent oci://ghcr.io/dasomel/charts/nfs-quota-agent \
  --namespace kube-system \
  --version "${NFS_QUOTA_AGENT_VERSION#v}" \
  --set image.repository=ghcr.io/dasomel/nfs-quota-agent \
  --set image.tag="${NFS_QUOTA_AGENT_VERSION}" \
  --set config.nfsBasePath=/export \
  --set config.nfsServerPath="${NFS_SHARE_PATH}" \
  --set config.provisionerName=nfs.csi.k8s.io \
  --set config.syncInterval=30s \
  --set nfsExport.hostPath="${NFS_SHARE_PATH}"

# Wait for system pods
echo "Waiting for system pods to be ready..."
kubectl wait --for=condition=Ready pod --all -n kube-system --timeout=300s || true

echo "=== Addons Installation Done ==="

# Print cluster info
echo ""
echo "=========================================="
echo "Kubernetes cluster is ready!"
echo "=========================================="
kubectl get nodes
echo ""
kubectl get pods -A
echo ""
echo "=== Storage Classes ==="
kubectl get storageclass
