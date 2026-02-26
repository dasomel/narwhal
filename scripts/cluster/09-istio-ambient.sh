#!/bin/bash
set -euo pipefail

echo "=== Installing Istio Ambient Mesh ==="

ISTIO_VERSION="${ISTIO_VERSION:-1.29.0}"

# Use local kubeconfig (bypasses VIP) to avoid disruption during master-2 join
export KUBECONFIG=/home/vagrant/.kube/config-local

#=========================================
# Wait for API server
#=========================================
echo "Waiting for API server..."
for attempt in $(seq 1 30); do
  if kubectl cluster-info &>/dev/null; then
    echo "API server ready"
    break
  fi
  echo "API server not ready, attempt ${attempt}/30..."
  sleep 10
done

#=========================================
# Create namespace
#=========================================
kubectl create namespace istio-system --dry-run=client -o yaml | kubectl apply -f -

#=========================================
# Add Istio Helm repo
#=========================================
helm repo add istio https://istio-release.storage.googleapis.com/charts
helm repo update istio

#=========================================
# 1) istio-base (CRDs)
#=========================================
echo "Installing istio-base (CRDs)..."
helm upgrade --install istio-base istio/base \
  --namespace istio-system \
  --version "${ISTIO_VERSION}" \
  --timeout 120s || echo "WARN: istio-base install issue, continuing..."

# Wait for CRDs to be established
echo "Waiting for Istio CRDs..."
for crd in peerauthentications.security.istio.io authorizationpolicies.security.istio.io; do
  for attempt in $(seq 1 20); do
    if kubectl get crd "${crd}" &>/dev/null; then
      echo "CRD ${crd} ready"
      break
    fi
    sleep 3
  done
done

#=========================================
# 2) istiod (control plane, ambient profile)
#=========================================
echo "Installing istiod (ambient profile)..."

cat > /tmp/istiod-values.yaml << 'EOF'
profile: ambient
resources:
  requests:
    cpu: 100m
    memory: 256Mi
  limits:
    memory: 512Mi
tolerations:
  - key: node.kubernetes.io/disk-pressure
    operator: Exists
    effect: NoSchedule
  - key: node.cilium.io/agent-not-ready
    operator: Exists
    effect: NoSchedule
EOF

helm upgrade --install istiod istio/istiod \
  --namespace istio-system \
  --version "${ISTIO_VERSION}" \
  -f /tmp/istiod-values.yaml \
  --timeout 180s || echo "WARN: istiod install issue, continuing..."

rm /tmp/istiod-values.yaml

# Wait for istiod to be ready
echo "Waiting for istiod..."
kubectl wait --for=condition=Ready pod -l app=istiod -n istio-system --timeout=180s || true

#=========================================
# 3) istio-cni (ambient CNI node agent)
#=========================================
echo "Installing istio-cni (ambient profile)..."

cat > /tmp/istio-cni-values.yaml << 'EOF'
profile: ambient
cni:
  ambient:
    enabled: true
tolerations:
  - key: node-role.kubernetes.io/control-plane
    operator: Exists
    effect: NoSchedule
  - key: node.kubernetes.io/disk-pressure
    operator: Exists
    effect: NoSchedule
  - key: node.cilium.io/agent-not-ready
    operator: Exists
    effect: NoSchedule
EOF

helm upgrade --install istio-cni istio/cni \
  --namespace istio-system \
  --version "${ISTIO_VERSION}" \
  -f /tmp/istio-cni-values.yaml \
  --timeout 180s || echo "WARN: istio-cni install issue, continuing..."

rm /tmp/istio-cni-values.yaml

# Wait for istio-cni DaemonSet
echo "Waiting for istio-cni DaemonSet..."
kubectl rollout status daemonset/istio-cni-node -n istio-system --timeout=180s || true

#=========================================
# 4) ztunnel (node proxy DaemonSet)
#=========================================
echo "Installing ztunnel..."

cat > /tmp/ztunnel-values.yaml << 'EOF'
resources:
  requests:
    cpu: 50m
    memory: 64Mi
  limits:
    memory: 256Mi
tolerations:
  - key: node-role.kubernetes.io/control-plane
    operator: Exists
    effect: NoSchedule
  - key: node.kubernetes.io/disk-pressure
    operator: Exists
    effect: NoSchedule
  - key: node.cilium.io/agent-not-ready
    operator: Exists
    effect: NoSchedule
EOF

helm upgrade --install ztunnel istio/ztunnel \
  --namespace istio-system \
  --version "${ISTIO_VERSION}" \
  -f /tmp/ztunnel-values.yaml \
  --timeout 180s || echo "WARN: ztunnel install issue, continuing..."

rm /tmp/ztunnel-values.yaml

# Wait for ztunnel DaemonSet
echo "Waiting for ztunnel DaemonSet..."
kubectl rollout status daemonset/ztunnel -n istio-system --timeout=180s || true

#=========================================
# 5) Label namespaces for ambient mesh
#=========================================
echo "Labeling namespaces for ambient mesh..."

AMBIENT_NAMESPACES=(
  platform-system
  iam
  devtools
  monitoring
  storage
  database
  dev
)

for ns in "${AMBIENT_NAMESPACES[@]}"; do
  if kubectl get namespace "${ns}" &>/dev/null; then
    kubectl label namespace "${ns}" istio.io/dataplane-mode=ambient --overwrite
    echo "  Labeled ${ns} for ambient mesh"
  else
    echo "  WARN: namespace ${ns} not found, skipping label"
  fi
done

#=========================================
# 6) PeerAuthentication PERMISSIVE (mesh-wide mTLS)
#=========================================
# PERMISSIVE allows non-mesh traffic (kubelet probes, MetalLB/Traefik external traffic)
# to reach mesh services. ztunnel still enforces mTLS for mesh-to-mesh communication.
echo "Applying mesh-wide PERMISSIVE mTLS..."
cat <<'EOF' | kubectl apply -f -
apiVersion: security.istio.io/v1
kind: PeerAuthentication
metadata:
  name: default
  namespace: istio-system
spec:
  mtls:
    mode: PERMISSIVE
EOF

echo ""
echo "=== Istio Ambient Mesh Installation Complete ==="
echo ""
echo "Installed components:"
echo "  - istio-base (CRDs)"
echo "  - istiod (control plane, ambient profile)"
echo "  - istio-cni (CNI node agent)"
echo "  - ztunnel (node proxy DaemonSet)"
echo ""
echo "Ambient mesh namespaces (${#AMBIENT_NAMESPACES[@]}):"
printf '  - %s\n' "${AMBIENT_NAMESPACES[@]}"
echo ""
echo "mTLS: PERMISSIVE (mesh-wide; ztunnel enforces mTLS for mesh-to-mesh)"
echo ""
