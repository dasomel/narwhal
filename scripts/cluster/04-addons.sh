#!/bin/bash
set -euo pipefail

# Charts come from the airgap bundle, never a public repository.
# shellcheck source=/dev/null
source /home/vagrant/scripts/common/lib-charts.sh

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
  install_helm() {
    curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 \
      | DESIRED_VERSION="${HELM_VERSION}" bash
  }
  retry install_helm
fi

#=========================================
# Metrics Server
#=========================================
echo "=== Installing Metrics Server ${METRICS_SERVER_VERSION} ==="

kubectl apply -f "https://github.com/kubernetes-sigs/metrics-server/releases/download/${METRICS_SERVER_VERSION}/components.yaml"

# D10: Apply insecure-TLS + probe-loosening in a SINGLE patch so metrics-server rolls
# out exactly ONE new ReplicaSet (two separate patches created two rollouts, leaving a
# transient not-ready new pod that Phase-2 `kubectl wait ... pod -l k8s-app=metrics-server`
# would catch and time out on — the recurring clean-install failure).
# - insecure TLS: kubelet serving certs are self-signed in this local cluster.
# - probes: default timeoutSeconds=1/failureThreshold=3 causes liveness flapping under
#   load -> restarts -> metrics.k8s.io APIService MissingEndpoints -> kubectl top & portal
#   show 0% CPU/mem. Give it more slack.
kubectl patch deployment metrics-server -n kube-system --type='json' -p='[
  {"op": "add",     "path": "/spec/template/spec/containers/0/args/-", "value": "--kubelet-insecure-tls"},
  {"op": "replace", "path": "/spec/template/spec/containers/0/livenessProbe/timeoutSeconds", "value": 5},
  {"op": "replace", "path": "/spec/template/spec/containers/0/livenessProbe/failureThreshold", "value": 5},
  {"op": "add",     "path": "/spec/template/spec/containers/0/livenessProbe/initialDelaySeconds", "value": 20},
  {"op": "replace", "path": "/spec/template/spec/containers/0/livenessProbe/periodSeconds", "value": 15},
  {"op": "replace", "path": "/spec/template/spec/containers/0/readinessProbe/timeoutSeconds", "value": 5},
  {"op": "replace", "path": "/spec/template/spec/containers/0/readinessProbe/failureThreshold", "value": 5},
  {"op": "replace", "path": "/spec/template/spec/containers/0/readinessProbe/periodSeconds", "value": 15}
]'

# D10: Wait for the rollout to fully settle (old ReplicaSet gone, new pod Ready) before
# Phase 1 completes, so downstream Phase-2 readiness waits never match a transient
# terminating/starting metrics-server pod. Non-fatal: metrics-server is not a hard
# dependency for the control plane, so a slow rollout shouldn't abort provisioning.
kubectl rollout status deployment/metrics-server -n kube-system --timeout=180s \
  || echo "WARN: metrics-server rollout not settled in 180s; continuing (non-critical addon)"

#=========================================
# CSI Driver NFS
#=========================================
echo "=== Installing CSI Driver NFS ${CSI_DRIVER_NFS_VERSION} ==="


helm upgrade --install csi-driver-nfs "$(chart csi-driver-nfs)" \
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
# NFSv3, not v4.1. v4.1 deadlocks this exact topology: the server's session teardown
# (nfsd4_destroy_session -> nfsd4_probe_callback_sync -> flush_workqueue) waits for the
# shared callback workqueue to drain, and the work item in it is an RPC to the very client
# that is itself blocked in nfs4_destroy_clientid waiting for the server's reply. Each side
# holds what the other needs, so nfsd wedges in D state and every NFS mount on every node
# queues behind it. Rebooting either side alone re-forms it within minutes.
#
# v3 is stateless: no clientid, no sessions, no callback workqueue, so the cycle cannot
# form. Its locking (NLM) does call back to clients on a separate connection, which is why
# the lockd/statd ports are pinned in 01-nfs-server.sh and opened in the security group —
# on this cloud an unlisted port is DROPPED, and a dropped callback is an indefinite hang
# rather than a fast failure.
mountOptions:
  - nfsvers=3
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
kubectl get pods -A
echo ""
echo "=== Storage Classes ==="
# Informational listing — must not decide the script's exit status.
kubectl get storageclass || true
