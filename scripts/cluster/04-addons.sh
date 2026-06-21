#!/bin/bash
set -euo pipefail

NFS_SERVER_IP="${NFS_SERVER_IP:-192.168.56.10}"
NFS_SHARE_PATH="${NFS_SHARE_PATH:-/srv/nfs/k8s}"

# Component versions
METRICS_SERVER_VERSION="${METRICS_SERVER_VERSION:-v0.8.1}"
CSI_DRIVER_NFS_VERSION="${CSI_DRIVER_NFS_VERSION:-4.13.2}"
echo "=== Installing Kubernetes Addons ==="

# Use local kubeconfig (bypasses VIP) to avoid disruption during master-2 join
export KUBECONFIG=/home/vagrant/.kube/config-local

# Install Helm if not installed
HELM_VERSION="v4.2.1"
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

# Loosen metrics-server probes: default timeoutSeconds=1/failureThreshold=3 causes
# liveness flapping under load -> pod restarts -> metrics.k8s.io APIService MissingEndpoints
# -> kubectl top & portal show 0% CPU/mem. Give it more slack.
kubectl patch deployment metrics-server -n kube-system --type='json' -p='[
  {"op": "replace", "path": "/spec/template/spec/containers/0/livenessProbe/timeoutSeconds", "value": 5},
  {"op": "replace", "path": "/spec/template/spec/containers/0/livenessProbe/failureThreshold", "value": 5},
  {"op": "add",     "path": "/spec/template/spec/containers/0/livenessProbe/initialDelaySeconds", "value": 20},
  {"op": "replace", "path": "/spec/template/spec/containers/0/livenessProbe/periodSeconds", "value": 15},
  {"op": "replace", "path": "/spec/template/spec/containers/0/readinessProbe/timeoutSeconds", "value": 5},
  {"op": "replace", "path": "/spec/template/spec/containers/0/readinessProbe/failureThreshold", "value": 5},
  {"op": "replace", "path": "/spec/template/spec/containers/0/readinessProbe/periodSeconds", "value": 15}
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
kubectl get pods -A -o custom-columns=NAMESPACE:.metadata.namespace,NAME:.metadata.name,STATUS:.status.phase,READY:.status.containerStatuses[*].ready,RESTARTS:.status.containerStatuses[*].restartCount
echo ""
echo "=== Storage Classes ==="
kubectl get storageclass
